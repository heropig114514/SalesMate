"""职责：发布 Agent 聊天工具目录、请求绑定读取及实验维护与回答状态查询。
实现：只接受员工绑定 Agent 认证，错误分为请求级和工具级，所有响应禁止缓存。
关联：tool_reads 复用业务读取与实验维护并冻结证据；services 负责请求授权和最终回答状态。
目录：
- AgentChatView：认证、错误及缓存策略。
- AgentChatView.handle_exception：输出稳定且不泄露异常正文的请求错误。
- AgentChatView.finalize_response：禁止客户端缓存私有结果。
- ToolCatalogView：读取工具目录。
- ToolCatalogView.get：解析分页并调用请求目录服务。
- ToolReadView：执行请求绑定数据操作。
- ToolReadView.post：校验调用并保留业务 HTTP 状态。
- AgentRequestView：核对聊天回报的权威状态。
- AgentRequestView.get：只返回授权请求的浏览器安全投影。
变量索引：
- AgentChatView.authentication_classes：仅 Agent 凭证，禁止 Tool 或 Session 替代。
- logger：记录视图、异常类型及 HTTP 请求关联 ID，不记录凭证或正文。
"""

import logging

from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.crm.access import AgentAuthentication

from . import services, tool_reads

logger = logging.getLogger("salesmate.chat.tools")


# 功能：隔离员工绑定的聊天服务身份并规范错误。
# 逻辑：继承默认登录权限，仅使用 AgentAuthentication；响应禁止缓存。
# 约束：凭证标识员工而不是 Worker 进程类型，业务范围由请求与工具白名单继续限定。
class AgentChatView(APIView):
    authentication_classes = (AgentAuthentication,)

    # 功能：规范请求授权、状态、结构与服务器异常的失败信封。
    # 输入：`exc` 为视图执行异常，self.request 提供 HTTP 关联 ID。
    # 输出：保留真实 HTTP 状态的 JSON，error.scope=request；未知异常为安全 500。
    # 逻辑：DRF 异常沿用项目处理器，未知异常只记录类型并返回通用错误，不输出 Django DEBUG 页面。
    # 约束：不自动重试、不改变聊天状态，业务工具错误由 ToolReadView 原样返回且 scope=tool。
    def handle_exception(self, exc):
        logger.warning(
            "chat_endpoint_failed view=%s error_type=%s http_request_id=%s",
            type(self).__name__,
            type(exc).__name__,
            getattr(self.request, "request_id", None),
        )
        if not isinstance(exc, APIException):
            exc = APIException("聊天服务暂时不可用，请核对服务日志。")
        response = super().handle_exception(exc)
        response.data["error"]["scope"] = "request"
        return response

    # 功能：为成功和失败响应统一禁止缓存。
    # 输入：`request` 为 HTTP 请求，`response` 为响应，`args`/`kwargs` 为 DRF 扩展参数。
    # 输出：完成渲染协商且带 Cache-Control: no-store 的响应。
    # 逻辑：保留框架状态、认证头和媒体类型，再追加缓存策略。
    # 约束：不修改业务正文或错误信息。
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


# 功能：提供请求绑定的实际工具发现接口。
# 逻辑：只发布当前请求获准的读取及实验维护工具 Schema。
# 约束：需有效 processing 请求，不返回整个业务目录。
class ToolCatalogView(AgentChatView):
    # 功能：读取一页工具描述。
    # 输入：`request` 查询参数 request_id、可选 page/page_size。
    # 输出：tools/count/page/page_size 与协议、请求标识。
    # 逻辑：只转换分页整数，参数合法性及请求权限由服务验证。
    # 约束：未知字段和重复参数拒绝，不自动翻页或推断公司。
    @extend_schema(
        parameters=[
            OpenApiParameter("request_id", OpenApiTypes.UUID, required=True),
            OpenApiParameter("page", int),
            OpenApiParameter("page_size", int),
        ],
        responses=OpenApiTypes.OBJECT,
        operation_id="agent_chat_tools_catalog",
    )
    def get(self, request):
        query = request.query_params.dict()
        if any(len(values) != 1 for _, values in request.query_params.lists()):
            raise ValidationError("查询参数不能重复。")
        for key in ("page", "page_size"):
            if key in query:
                try:
                    query[key] = int(query[key])
                except ValueError:
                    raise ValidationError("page 和 page_size 必须为整数。") from None
        return Response(tool_reads.catalog_for(request.user, query))


# 功能：执行一次读取或实验维护工具并返回本次稳定来源。
# 逻辑：只从已认证身份推导 owner，原样传递参数给请求绑定服务。
# 约束：不接受客户端幂等键或身份参数；实验维护的幂等键由请求及参数派生。
class ToolReadView(AgentChatView):
    # 功能：执行获准的数据工具。
    # 输入：`request`.data 为 request_id/name/arguments 对象。
    # 输出：成功业务数据和证据，或 scope=tool 的明确失败；HTTP 状态保留。
    # 逻辑：服务事务成功后再返回，不把工具 404 转为空列表或聊天失败。
    # 约束：查询不要求聊天绑定公司；customers.context 的定位参数仍须遵循其原 Schema。
    @extend_schema(
        request={"application/json": tool_reads.CALL_SCHEMA},
        responses={
            200: OpenApiTypes.OBJECT,
            400: OpenApiTypes.OBJECT,
            403: OpenApiTypes.OBJECT,
            404: OpenApiTypes.OBJECT,
            409: OpenApiTypes.OBJECT,
            500: OpenApiTypes.OBJECT,
        },
        operation_id="agent_chat_tool_read",
    )
    def post(self, request):
        result = tool_reads.read_tool(request.user, request.data)
        return Response(result, status=result["http_status"])


# 功能：允许 Agent 在回报响应丢失后查询权威请求状态。
# 逻辑：读取原请求，不重新运行模型或创建重试任务。
# 约束：允许本人 pending/processing/终态，沿用会话访问限制。
class AgentRequestView(AgentChatView):
    # 功能：查询已授权请求的状态和最终引用。
    # 输入：`request` 为 Agent HTTP 请求，`request_id` 为路径 UUID。
    # 输出：请求状态、消息 ID、版本、错误及已引用证据。
    # 逻辑：复用浏览器安全投影，不发布完整上下文或工具调用历史。
    # 约束：员工越权统一 404，不通过请求体自报身份。
    @extend_schema(
        responses=OpenApiTypes.OBJECT, operation_id="agent_chat_request_status"
    )
    def get(self, request, request_id):
        return Response(
            services.request_data(services.request_for(request.user, request_id))
        )
