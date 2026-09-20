"""职责：暴露浏览器聊天操作和固定 Agent 三接口。
实现：Session/CSRF 与 Agent 凭证分离，视图只负责结构校验、分页及事务服务分派。
关联：config.urls 注册独立路径，复用既有统一异常与 OpenAPI。
目录：
- SubmitView：显式提交聊天问题。
- SubmitView.post：原子创建消息及任务。
- RequestListView：会话请求列表。
- RequestListView.get：授权分页读取请求。
- RequestView：单请求状态查询。
- RequestView.get：读取权威状态。
- RetryView：显式失败重试。
- RetryView.post：创建或返回唯一后继。
- ClaimView：Agent 原子领取。
- ClaimView.post：返回零个或一个请求。
- ContextView：Agent 证据读取。
- ContextView.post：返回冻结上下文。
- AnswerView：Agent 幂等回报。
- AnswerView.post：保存经验证的最终结果。
变量索引：
- ClaimView.authentication_classes：员工绑定服务认证。
- ContextView.authentication_classes：证据读取的独立服务认证。
- AnswerView.authentication_classes：结果写入的独立服务认证。
"""

from drf_spectacular.utils import extend_schema, OpenApiTypes
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from rest_framework.views import APIView

from apps.crm.access import AgentAuthentication
from apps.sales.views import paged
from . import contracts, services
from .models import AnswerRequest


# 功能：接收浏览器的明确提问。
# 逻辑：继承 SessionAuthentication 和登录权限，保留 CSRF。
# 约束：不在 Web 请求中调用模型。
class SubmitView(APIView):
    # 功能：保存问题并入队。
    # 输入：`request` 带会话、正文和 client_key。
    # 输出：201 新任务或 200 幂等重传。
    # 逻辑：委托单事务服务后投影状态。
    # 约束：员工从登录身份确定，未知字段拒绝。
    @extend_schema(
        request=OpenApiTypes.OBJECT,
        responses={200: OpenApiTypes.OBJECT, 201: OpenApiTypes.OBJECT},
        operation_id="chat_submit",
    )
    def post(self, request):
        answer, created = services.submit(request.user, request.data)
        return Response(services.request_data(answer), status=201 if created else 200)


# 功能：提供会话内回答状态分页。
# 逻辑：先核验自有客户会话，再读取员工请求。
# 约束：不允许通过 company 参数覆盖请求绑定。
class RequestListView(APIView):
    # 功能：读取稳定排序的请求列表。
    # 输入：`request` 查询参数 conversation/page/page_size。
    # 输出：现有 count/results 分页结构。
    # 逻辑：复用分页限制，以创建时间和 UUID 排序，非法分页数字转换成 400。
    # 约束：每条结果再次核验绑定，不输出原始快照。
    @extend_schema(responses=OpenApiTypes.OBJECT, operation_id="chat_requests_list")
    def get(self, request):
        conversation = services.conversation_for(
            request.user, contracts.identifier(request.query_params.get("conversation"))
        )
        query = AnswerRequest.objects.filter(
            owner=request.user, conversation=conversation
        ).order_by("created_at", "id")
        try:
            rows, pagination = paged(query, request)
        except (ValueError, TypeError):
            raise ValidationError("page 和 page_size 必须为整数。") from None
        return Response(
            {
                **pagination,
                "results": [
                    services.request_data(services.request_for(request.user, row.pk))
                    for row in rows
                ],
            }
        )


# 功能：提供单个回答的轮询状态。
# 逻辑：独立读取不触发重算或重试。
# 约束：只能访问完整绑定仍有效的自有请求。
class RequestView(APIView):
    # 功能：读取请求状态与引用。
    # 输入：`request` 登录请求，`request_id` 路径 UUID。
    # 输出：浏览器安全状态对象。
    # 逻辑：统一授权后投影。
    # 约束：无写入副作用。
    @extend_schema(responses=OpenApiTypes.OBJECT, operation_id="chat_request_read")
    def get(self, request, request_id):
        return Response(
            services.request_data(services.request_for(request.user, request_id))
        )


# 功能：接收明确的失败重试操作。
# 逻辑：创建新尝试，重复提交返回同一后继。
# 约束：不会复活旧 request_id。
class RetryView(APIView):
    # 功能：重新请求回答。
    # 输入：`request` 空 JSON，`request_id` 原失败请求 UUID。
    # 输出：201 新尝试或 200 已存在后继。
    # 逻辑：校验空对象后调用事务服务。
    # 约束：原结果不覆盖，非失败状态拒绝。
    @extend_schema(
        request=OpenApiTypes.OBJECT,
        responses=OpenApiTypes.OBJECT,
        operation_id="chat_retry",
    )
    def post(self, request, request_id):
        contracts.fields(request.data, set())
        answer, created = services.retry(request.user, request_id)
        return Response(services.request_data(answer), status=201 if created else 200)


# 功能：提供 Agent 固定领取接口。
# 逻辑：服务凭证映射单个员工。
# 约束：浏览器 Session 不能调用。
class ClaimView(APIView):
    authentication_classes = [AgentAuthentication]

    # 功能：领取至多一条 pending 请求。
    # 输入：`request` 空 JSON 和 Agent Authorization。
    # 输出：request 对象或 null。
    # 逻辑：原子状态转换后返回冻结历史。
    # 约束：无工作为 200，不自动恢复失败任务。
    @extend_schema(
        request=OpenApiTypes.OBJECT,
        responses=OpenApiTypes.OBJECT,
        operation_id="agent_chat_claim",
    )
    def post(self, request):
        contracts.fields(request.data, set())
        return Response({"request": services.claim(request.user)})


# 功能：提供 Agent 固定上下文接口。
# 逻辑：只接受 request_id 和 scope。
# 约束：不接受任意 company/query 覆盖。
class ContextView(APIView):
    authentication_classes = [AgentAuthentication]

    # 功能：读取并冻结请求证据。
    # 输入：`request` 含 request_id/scope。
    # 输出：严格 AnswerContext。
    # 逻辑：校验身份后执行事务快照服务。
    # 约束：未启用的 external 返回明确错误。
    @extend_schema(
        request=OpenApiTypes.OBJECT,
        responses=OpenApiTypes.OBJECT,
        operation_id="agent_chat_context",
    )
    def post(self, request):
        contracts.fields(request.data, {"request_id", "scope"})
        return Response(
            services.context_for(
                request.user,
                contracts.identifier(request.data["request_id"]),
                request.data["scope"],
            )
        )


# 功能：提供 Agent 固定结果回报接口。
# 逻辑：受保护服务创建 assistant 消息，不放开浏览器普通消息权限。
# 约束：无跨员工写入或结果覆盖。
class AnswerView(APIView):
    authentication_classes = [AgentAuthentication]

    # 功能：幂等持久化最终回答。
    # 输入：`request` 严格 completed/failed 回报。
    # 输出：saved/duplicate/assistant_message_id。
    # 逻辑：校验回报 Schema、权限和状态后原子保存，不判断正文和引用真实性。
    # 约束：成功重复回报仍返回 saved=true。
    @extend_schema(
        request=OpenApiTypes.OBJECT,
        responses=OpenApiTypes.OBJECT,
        operation_id="agent_chat_answer",
    )
    def post(self, request):
        return Response(services.save_answer(request.user, request.data))
