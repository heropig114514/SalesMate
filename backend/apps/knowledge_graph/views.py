"""职责：提供仅限当前所有者的图谱状态、实体、事实和血缘查询。
实现：读取一致快照，未完成同步时拒绝返回旧图；分页与固定过滤条件限制查询规模。
关联：sales.SalesView 提供认证；models 为派生读模型；不暴露任意 Cypher/SQL 或写接口。
目录：
- GraphUnavailable：图谱未就绪错误。
- graph_status：查询当前用户同步状态。
- begin_read：开启一致读取并验证捕获及新鲜度。
- fact_data：序列化一条事实。
- GraphStatusView：同步状态视图。
- GraphStatusView.get：读取状态。
- EntityView：实体列表视图。
- EntityView.get：查询当前实体。
- FactView：事实列表视图。
- FactView.get：按实体或关系查询当前事实。
- LineageView：事实血缘视图。
- LineageView.get：返回事实的全部历史支持路径与来源版本。
变量索引：
- GraphUnavailable.status_code：未就绪或捕获不可用时返回 503。
- GraphUnavailable.default_code：graph_unavailable 错误代码。
- GraphUnavailable.default_detail：提示查看图谱状态及 Worker。
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


# 功能：明确报告图谱不能作为当前结果读取。
# 逻辑：沿用统一错误信封与 503。
# 约束：不静默返回历史结果或空图冒充同步成功。
class GraphUnavailable(APIException):
    status_code = 503
    default_code = "graph_unavailable"
    default_detail = "图谱尚未同步或存在失败事件，请查看 graph/status 并检查图谱 Worker。"


# 功能：返回当前身份的图谱维护状态。
# 输入：`owner_id` 为经过认证的当前用户 ID。
# 输出：ready/current、代次、时间与本人事件数量。
# 逻辑：ready 表示已有快照，current 还要求没有 pending/failed 事件。
# 约束：不返回其他用户统计、源记录键或错误原文。
def graph_status(owner_id):
    state = ProjectionState.objects.filter(owner_id=owner_id).first()
    pending = Change.objects.filter(owner_id=owner_id, status="pending").count()
    failed = Change.objects.filter(owner_id=owner_id, status="failed").count()
    ready = state is not None and state.ready
    return {"ready": ready, "current": ready and pending == 0 and failed == 0, "generation": state.generation if state else 0, "synced_at": state.synced_at if state else None, "pending_events": pending, "failed_events": failed}


# 功能：建立图谱读取快照并检查自动维护前提。
# 输入：`owner_id` 为当前用户；`require_current` 默认 True，状态接口显式 False。
# 输出：当前同步状态；捕获异常或未就绪时抛 GraphUnavailable。
# 逻辑：视图 atomic 块内首条数据库操作设定 REPEATABLE READ，再检查触发器与状态。
# 约束：每个请求观察一个一致时间点；提交后的新变更由下一请求反映，不能声明跨请求强一致。
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


# 功能：序列化事实及其端点。
# 输入：`fact` 为已按当前 owner 过滤的 Fact。
# 输出：JSON 可编码字典。
# 逻辑：仅展示稳定实体 ID、关系、值、来源类别及状态。
# 约束：不把 active 解释为已成交或人工确认。
def fact_data(fact):
    return {"id": str(fact.pk), "subject": str(fact.subject_id), "predicate": fact.predicate, "object": str(fact.object_id) if fact.object_id else None, "value": fact.value, "origin": fact.origin, "status": fact.status}


# 功能：展示当前用户图谱状态。
# 逻辑：沿用系统认证身份但始终按 owner 隔离。
# 约束：实验公开身份也不会自动获得其他 owner 的图谱。
class GraphStatusView(SalesView):
    # 功能：返回同步状态。
    # 输入：`request` 为已认证请求。
    # 输出：200 状态；数据库或触发器不支持时 503。
    # 逻辑：允许查询尚未建立或失败的状态。
    # 约束：不自动触发回填。
    @extend_schema(responses=OpenApiTypes.OBJECT)
    @transaction.atomic
    def get(self, request):
        return Response(begin_read(request.user.pk, require_current=False))


# 功能：查询当前有效实体。
# 逻辑：仅支持 kind 与 q 过滤及既有分页。
# 约束：忽略实验跨账号浏览能力，邮件证据只面向本人。
class EntityView(SalesView):
    # 功能：分页读取当前实体。
    # 输入：`request` 的 kind、q、page、page_size 查询参数。
    # 输出：实体列表和分页、同步状态。
    # 逻辑：检查同步后按稳定 ID 排序；q 只查询显示标签。
    # 约束：不返回已归档或已删除实体。
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


# 功能：查询当前关系和属性事实。
# 逻辑：可按 entity 查入边与出边，或按 predicate 筛选。
# 约束：待复核候选保留 needs_review，已失效事实不混入当前结果。
class FactView(SalesView):
    # 功能：分页返回事实。
    # 输入：`request` 的 entity UUID、predicate、page、page_size。
    # 输出：当前事实列表和同步状态。
    # 逻辑：仅查询本人非 unsupported 事实，实体过滤同时匹配两个端点。
    # 约束：实体不是 UUID 时 400；不会通过其他用户实体 ID 访问其关系。
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


# 功能：返回某条事实的可审计来源路径。
# 逻辑：历史事实也可定位，支持路径分页，来源版本保留原快照。
# 约束：只有事实 owner 可读，不允许根据来源 ID 绕过归属。
class LineageView(SalesView):
    # 功能：读取事实的当前和历史派生。
    # 输入：`request` 为当前身份与分页参数；`fact_id` 为事实 UUID。
    # 输出：事实、支持路径、输入版本及字段证据。
    # 逻辑：同步完成后核验 owner；路径按创建时间排序，输入快照不以当前值替换。
    # 约束：历史版本明确标记 current=False；旧引用不证明其仍有效。
    @extend_schema(responses=OpenApiTypes.OBJECT)
    @transaction.atomic
    def get(self, request, fact_id):
        status = begin_read(request.user.pk)
        fact = get_object_or_404(Fact, pk=fact_id, owner=request.user)
        query = Support.objects.filter(fact=fact).select_related("derivation").prefetch_related("derivation__inputs").order_by("-derivation__created_at", "pk")
        rows, pagination = paged(query, request)
        paths = [{"derivation_id": str(row.derivation_id), "active": row.derivation.active, "rule": row.derivation.rule, "evidence": row.derivation.evidence, "inputs": [{"id": str(source.pk), "kind": source.kind, "source_id": source.source_id, "current": source.current, "recorded_at": source.recorded_at, "snapshot": source.snapshot} for source in row.derivation.inputs.all()]} for row in rows]
        return Response({**pagination, "sync": status, "fact": fact_data(fact), "results": paths})
