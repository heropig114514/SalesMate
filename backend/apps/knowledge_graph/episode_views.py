"""Responsibility: Expose the current user's business schema, external-input, and withdrawal endpoints.
Implementation: Reuse SalesView session authentication and CSRF, enforce request keys, and reject owner overrides, arbitrary model URLs, and database-write actions.
Relationships: The episodes service associates and publishes input; existing entity/fact/lineage endpoints read the same graph.
Directory:
- parse_input: Validate the input envelope and observation time.
- SchemaView: Business field catalog.
- SchemaView.get: Return accepted business types.
- EpisodeView: External source listing and automatic ingestion entry point.
- EpisodeView.get: Read paginated summaries of the current user's sources.
- EpisodeView.post: Process natural-language or incomplete structured input.
- EpisodeDetailView: Immutable source details for the current user.
- EpisodeDetailView.get: Read original text, candidates, and model audit data.
- EpisodeRetractView: Explicit source withdrawal.
- EpisodeRetractView.post: Withdraw support from the current user's observation.
Variable index:
- None
"""
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from apps.sales.views import SalesView, paged
from .business_schema import catalog
from .episodes import ingest, retract, episode_data
from .models import Episode


# Function: Validate an external input envelope.
# Inputs: `data`: request JSON dictionary.
# Outputs: Keyword arguments for ingest; failures raise DRF ValidationError.
# Logic: Require an explicit source key and timezone-aware observation time; the service rechecks that exactly one of text or structured records is supplied.
# Constraints: Callers cannot specify owner, model, SQL, or business-state execution methods.
def parse_input(data):
    if not isinstance(data, dict) or set(data) - {"source_key", "observed_at", "text", "records"} or not {"source_key", "observed_at"} <= set(data):
        raise ValidationError("请求只接受 source_key、observed_at 和 text 或 records。")
    if not isinstance(data["observed_at"], str):
        raise ValidationError("observed_at 必须为带时区的 ISO 时间字符串。")
    try:
        observed = parse_datetime(data["observed_at"])
    except ValueError as exc:
        raise ValidationError("observed_at 无效。") from exc
    if observed is None or timezone.is_naive(observed):
        raise ValidationError("observed_at 必须带时区。")
    return {**data, "observed_at": observed}


# Function: Expose the structure of the authorized business schema.
# Logic: Return metadata only, never other users' records.
# Constraints: The catalog permits observation input without granting corresponding business-write privileges.
class SchemaView(SalesView):
    # Function: Read schema and incomplete-input conventions.
    # Inputs: `request`: authenticated request.
    # Outputs: Model field catalog and semantic descriptions.
    # Logic: Read actual Django field metadata directly.
    # Constraints: Credentials and runtime queues are excluded from the business catalog.
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response({"schemas": catalog(), "partial_observations": True, "writes_business_records": False})


# Function: Accept and inspect external observations for the current owner.
# Logic: Server-side local configuration determines the model endpoint; each input triggers association, validation, and graph synchronization.
# Constraints: This adds a source without modifying frozen experiments, CRM orders, or authorization records.
class EpisodeView(SalesView):
    # Function: Read summaries of the current user's sources.
    # Inputs: `request`: request containing pagination parameters.
    # Outputs: Paginated summaries in reverse chronological order.
    # Logic: Scope all queries by request.user first; inaccessible and nonexistent records both return 404.
    # Constraints: Lists omit full bodies; only the owner can read details.
    @extend_schema(responses=OpenApiTypes.OBJECT, operation_id="graph_episodes_list")
    def get(self, request):
        query = Episode.objects.filter(owner=request.user)
        rows, pagination = paged(query.order_by("-created_at", "id"), request)
        return Response({**pagination, "results": [{"id": str(row.pk), "source_key": row.source_key, "observed_at": row.observed_at,
                                                   "retracted": row.retracted} for row in rows]})

    # Function: Ingest external information and automatically associate it with the graph.
    # Inputs: `request`: request containing source_key, observed_at, and text or records.
    # Outputs: 200 with source and synchronization state; preserve 400/409/502/503 error semantics.
    # Logic: Validate idempotency and context consistency; model failures create no source, while synchronization failures retain diagnosable events.
    # Constraints: Model calls may take minutes; same-key replay avoids repeated inference, and replay does not restore withdrawn sources.
    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        return Response(episode_data(ingest(request.user.pk, **parse_input(request.data))))


# Function: Read one immutable source.
# Logic: Locate the source by current ownership before returning input, candidates, and model audit data.
# Constraints: Do not accept POST/PUT/PATCH overwrites; withdrawal uses a separate endpoint.
class EpisodeDetailView(SalesView):
    # Function: Inspect source details for the current user.
    # Inputs: `request`: authenticated request; `episode_id`: source UUID.
    # Outputs: One original input, candidates, audit data, and synchronization state.
    # Logic: Inaccessible and nonexistent records both return 404 without exposing other users' sources.
    # Constraints: Synchronization state reflects the current graph; graph updates do not rewrite historical input.
    @extend_schema(responses=OpenApiTypes.OBJECT, operation_id="graph_episode_detail")
    def get(self, request, episode_id):
        return Response(episode_data(Episode.objects.get(owner=request.user, pk=episode_id)))


# Function: Explicitly withdraw an external observation.
# Logic: Withdraw only this source's support, preserving independent sources and historical versions.
# Constraints: Reuse login and CSRF protections; do not allow arbitrary-user or bulk deletion.
class EpisodeRetractView(SalesView):
    # Function: Mark the current user's source withdrawn and synchronize.
    # Inputs: `request`: expected empty object; `episode_id`: UUID.
    # Outputs: Updated source state.
    # Logic: Call the shared withdrawal service; repeated withdrawal is idempotent.
    # Constraints: Do not delete other observations or source business records.
    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    def post(self, request, episode_id):
        if request.data:
            raise ValidationError("撤回请求不接受额外字段。")
        return Response(episode_data(retract(request.user.pk, episode_id)))
