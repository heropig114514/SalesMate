"""Responsibility: Expose graph status, entities, facts, and lineage only for the current owner.
Implementation: Read consistent snapshots and reject stale graphs while synchronization is incomplete; pagination and fixed filters bound query size.
Relationships: sales.SalesView supplies authentication and models are derived read models; no arbitrary Cypher/SQL or write endpoints.
Directory:
- GraphUnavailable: Graph-not-ready error.
- graph_status: Query the current user's synchronization state.
- begin_read: Start a consistent read and validate capture and freshness.
- fact_data: Serialize one fact.
- GraphStatusView: Synchronization status view.
- GraphStatusView.get: Read status.
- EntityView: Entity list view.
- EntityView.get: Query current entities.
- FactView: Fact list view.
- FactView.get: Query current facts by entity or relationship.
- LineageView: Fact lineage view.
- LineageView.get: Return all historical support paths and source versions for a fact.
Variable index:
- GraphUnavailable.status_code: Return 503 when the graph is unready or capture unavailable.
- GraphUnavailable.default_code: graph_unavailable error code.
- GraphUnavailable.default_detail: Message directing inspection of graph status and the worker.
"""

from uuid import UUID
from django.db import connection, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response

from apps.sales.views import SalesView, paged
from .models import Change, Entity, Fact, ProjectionState, Support
from .sync import require_capture


# Function: Explicitly report that the graph cannot be read as current results.
# Logic: Reuse the standard error envelope and 503 status.
# Constraints: Do not silently return historical results or an empty graph as successful synchronization.
class GraphUnavailable(APIException):
    status_code = 503
    default_code = "graph_unavailable"
    default_detail = "图谱尚未同步或存在失败事件，请查看 graph/status 并检查图谱 Worker。"


# Function: Return graph maintenance state for the current identity.
# Inputs: `owner_id`: authenticated current user ID.
# Outputs: ready/current, generation, timestamp, and current-user event counts.
# Logic: ready means a snapshot exists; current additionally requires no pending/failed events.
# Constraints: Do not return other-user statistics, source record keys, or raw errors.
def graph_status(owner_id):
    state = ProjectionState.objects.filter(owner_id=owner_id).first()
    pending = Change.objects.filter(owner_id=owner_id, status="pending").count()
    failed = Change.objects.filter(owner_id=owner_id, status="failed").count()
    ready = state is not None and state.ready
    return {"ready": ready, "current": ready and pending == 0 and failed == 0, "generation": state.generation if state else 0, "synced_at": state.synced_at if state else None, "pending_events": pending, "failed_events": failed}


# Function: Establish a graph read snapshot and check automatic maintenance prerequisites.
# Inputs: `owner_id`: current user; `require_current`: default True, explicitly False for the status endpoint.
# Outputs: Current synchronization state; capture failures or unreadiness raise GraphUnavailable.
# Logic: Set REPEATABLE READ as the first database operation inside the view's atomic block, then check triggers and state.
# Constraints: Each request observes one consistent point in time; later commits appear in the next request, without claiming strong cross-request consistency.
def begin_read(owner_id, require_current=True):
    if connection.vendor != "postgresql":
        raise GraphUnavailable("图谱运行需要 PostgreSQL，当前数据库不支持变更捕获。")
    with connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    try:
        require_capture()
    except RuntimeError as exc:
        raise GraphUnavailable("图谱捕获触发器不完整或已禁用，请检查数据库迁移。") from exc
    status = graph_status(owner_id)
    if require_current and not status["current"]:
        raise GraphUnavailable()
    return status


# Function: Serialize a fact and its endpoints.
# Inputs: `fact`: Fact already filtered by current owner.
# Outputs: JSON-serializable dictionary.
# Logic: Expose only stable entity IDs, relationship, value, origin category, and status.
# Constraints: Do not interpret active as a completed deal or human confirmation.
def fact_data(fact):
    return {"id": str(fact.pk), "subject": str(fact.subject_id), "predicate": fact.predicate, "object": str(fact.object_id) if fact.object_id else None, "value": fact.value, "origin": fact.origin, "status": fact.status}


# Function: Display the current user's graph state.
# Logic: Reuse authenticated system identity while always isolating by owner.
# Constraints: Experimental public identities do not automatically gain access to other owners' graphs.
class GraphStatusView(SalesView):
    # Function: Return synchronization state.
    # Inputs: `request`: authenticated request.
    # Outputs: 200 with state; 503 for unsupported database or triggers.
    # Logic: Allow querying unbuilt or failed state.
    # Constraints: Do not trigger backfill automatically.
    @extend_schema(responses=OpenApiTypes.OBJECT)
    @transaction.atomic
    def get(self, request):
        return Response(begin_read(request.user.pk, require_current=False))


# Function: Query currently active entities.
# Logic: Support only kind/q filters and existing pagination.
# Constraints: Ignore experimental cross-account browsing; email evidence remains owner-only.
class EntityView(SalesView):
    # Function: Read current entities with pagination.
    # Inputs: `request`: kind, q, page, and page_size query parameters.
    # Outputs: Entity list, pagination, and synchronization state.
    # Logic: Check synchronization, then order by stable ID; q searches display labels only.
    # Constraints: Do not return archived or deleted entities.
    @extend_schema(responses=OpenApiTypes.OBJECT)
    @transaction.atomic
    def get(self, request):
        status = begin_read(request.user.pk)
        query = Entity.objects.filter(owner=request.user, active=True).order_by("id")
        if request.query_params.get("kind"):
            query = query.filter(kind=request.query_params["kind"])
        if request.query_params.get("q"):
            query = query.filter(label__icontains=request.query_params["q"])
        rows, pagination = paged(query, request)
        return Response({**pagination, "sync": status, "results": [{"id": str(row.pk), "kind": row.kind, "source_id": row.source_id, "label": row.label} for row in rows]})


# Function: Query current relationship and attribute facts.
# Logic: Filter incoming/outgoing edges by entity or filter by predicate.
# Constraints: Retain needs_review on review candidates and exclude unsupported facts from current results.
class FactView(SalesView):
    # Function: Return paginated facts.
    # Inputs: `request`: entity UUID, predicate, page, and page_size parameters.
    # Outputs: Current fact list and synchronization state.
    # Logic: Query only the current owner's non-unsupported facts; entity filtering matches both endpoints.
    # Constraints: Non-UUID entities return 400; another user's entity ID cannot expose their relationships.
    @extend_schema(responses=OpenApiTypes.OBJECT)
    @transaction.atomic
    def get(self, request):
        status = begin_read(request.user.pk)
        query = Fact.objects.filter(owner=request.user).exclude(status="unsupported").order_by("id")
        if request.query_params.get("entity"):
            try:
                entity_id = UUID(request.query_params["entity"])
            except ValueError as exc:
                raise ValidationError("entity 必须为 UUID。") from exc
            query = query.filter(Q(subject_id=entity_id) | Q(object_id=entity_id))
        if request.query_params.get("predicate"):
            query = query.filter(predicate=request.query_params["predicate"])
        rows, pagination = paged(query, request)
        return Response({**pagination, "sync": status, "results": [fact_data(row) for row in rows]})


# Function: Return auditable source paths for one fact.
# Logic: Historical facts remain addressable; paginate support paths and retain original source-version snapshots.
# Constraints: Only the fact owner may read it; source IDs cannot bypass ownership.
class LineageView(SalesView):
    # Function: Read current and historical derivations of a fact.
    # Inputs: `request`: current identity and pagination; `fact_id`: fact UUID.
    # Outputs: Fact, support paths, input versions, and field evidence.
    # Logic: After synchronization, validate owner and sort paths by creation time; do not replace input snapshots with current values.
    # Constraints: Explicitly mark historical versions current=False; old citations do not prove continued validity.
    @extend_schema(responses=OpenApiTypes.OBJECT)
    @transaction.atomic
    def get(self, request, fact_id):
        status = begin_read(request.user.pk)
        fact = get_object_or_404(Fact, pk=fact_id, owner=request.user)
        query = Support.objects.filter(fact=fact).select_related("derivation").prefetch_related("derivation__inputs").order_by("-derivation__created_at", "pk")
        rows, pagination = paged(query, request)
        paths = [{"derivation_id": str(row.derivation_id), "active": row.derivation.active, "rule": row.derivation.rule, "evidence": row.derivation.evidence, "inputs": [{"id": str(source.pk), "kind": source.kind, "source_id": source.source_id, "current": source.current, "recorded_at": source.recorded_at, "snapshot": source.snapshot} for source in row.derivation.inputs.all()]} for row in rows]
        return Response({**pagination, "sync": status, "fact": fact_data(fact), "results": paths})
