"""职责：提供本人业务 schema、外部信息输入及撤回接口。
实现：会话认证和 CSRF 沿用 SalesView；严格请求键，不接受 owner、任意模型地址或数据库写入动作。
关联：episodes 服务执行关联与发布；原实体/事实/血缘接口读取同一图谱。
目录：
- parse_input：验证输入信封和来源时间。
- SchemaView：业务字段目录。
- SchemaView.get：返回可接收的业务类型。
- EpisodeView：外部来源列表与自动输入入口。
- EpisodeView.get：读取本人来源分页摘要。
- EpisodeView.post：处理自然语言或不完整结构化输入。
- EpisodeDetailView：本人不可变来源详情。
- EpisodeDetailView.get：读取原文、候选和模型审计。
- EpisodeRetractView：显式来源撤回。
- EpisodeRetractView.post：撤销本人观察的支持。
变量索引：
- 无
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


# 功能：验证外部输入信封。
# 输入：`data` 为请求 JSON 字典。
# 输出：可传给 ingest 的关键词参数；失败返回 DRF ValidationError。
# 逻辑：明确来源键与带时区时间；文本和结构化记录二选一由服务再次校验。
# 约束：调用方不能指定 owner、模型、SQL 或业务状态执行方法。
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


# 功能：公开已授权业务 schema 的结构。
# 逻辑：仅返回元数据，不返回任何其他用户记录。
# 约束：目录允许观察输入，不授予相应业务写入权限。
class SchemaView(SalesView):
    # 功能：读取 schema 与不完整输入约定。
    # 输入：`request` 为已认证请求。
    # 输出：模型字段目录及语义说明。
    # 逻辑：直接读取 Django 实际字段元数据。
    # 约束：凭据和运行队列不在业务目录内。
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response({"schemas": catalog(), "partial_observations": True, "writes_business_records": False})


# 功能：接收并查看当前所有者的外部观察。
# 逻辑：模型端点由服务端本机配置决定；每次输入自动进行关联、验证与图谱同步。
# 约束：这是新来源输入，不修改既有冻结实验、CRM 订单或授权记录。
class EpisodeView(SalesView):
    # 功能：读取本人来源摘要列表。
    # 输入：`request` 含分页参数。
    # 输出：按时间倒序的分页摘要。
    # 逻辑：所有查询先按 request.user 限定；越权与不存在同为 404。
    # 约束：列表不复制完整正文，详情仅本人可读。
    @extend_schema(responses=OpenApiTypes.OBJECT, operation_id="graph_episodes_list")
    def get(self, request):
        query = Episode.objects.filter(owner=request.user)
        rows, pagination = paged(query.order_by("-created_at", "id"), request)
        return Response({**pagination, "results": [{"id": str(row.pk), "source_key": row.source_key, "observed_at": row.observed_at,
                                                   "retracted": row.retracted} for row in rows]})

    # 功能：输入一份外部信息并自动关联建图。
    # 输入：`request` 含 source_key、observed_at、text 或 records。
    # 输出：200 来源及同步状态；错误保留 400/409/502/503 语义。
    # 逻辑：服务验证幂等与上下文一致性，模型失败不写来源，图谱同步失败保持可诊断事件。
    # 约束：模型可能耗时数分钟；同键重放不重复推理，撤回来源不会因重放而恢复。
    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        return Response(episode_data(ingest(request.user.pk, **parse_input(request.data))))


# 功能：读取单个不可变来源。
# 逻辑：按本人归属定位，再返回输入、候选和模型审计。
# 约束：不接受 POST/PUT/PATCH 覆盖，撤回使用独立入口。
class EpisodeDetailView(SalesView):
    # 功能：查看本人来源详情。
    # 输入：`request` 为已认证请求；`episode_id` 为来源 UUID。
    # 输出：单条原文、候选、审计和同步状态。
    # 逻辑：越权与不存在同为404，不泄露其他用户来源。
    # 约束：同步状态是当前图谱状态，历史输入不因图谱更新而重写。
    @extend_schema(responses=OpenApiTypes.OBJECT, operation_id="graph_episode_detail")
    def get(self, request, episode_id):
        return Response(episode_data(Episode.objects.get(owner=request.user, pk=episode_id)))


# 功能：显式撤回外部观察。
# 逻辑：只撤销该来源的支持，保留独立来源和历史版本。
# 约束：沿用登录和 CSRF，不提供任意用户或批量删除。
class EpisodeRetractView(SalesView):
    # 功能：标记本人来源撤回并同步。
    # 输入：`request` 应为空对象；`episode_id` 为 UUID。
    # 输出：更新后的来源状态。
    # 逻辑：调用统一撤回服务，重复撤回幂等。
    # 约束：不删除其他观察或源业务记录。
    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    def post(self, request, episode_id):
        if request.data:
            raise ValidationError("撤回请求不接受额外字段。")
        return Response(episode_data(retract(request.user.pk, episode_id)))
