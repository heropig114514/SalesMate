"""Responsibility: Publish Agent chat tool catalog, request-bound business tools, and answer-status query.
Implementation: Accept Agent authentication, distinguish tool/request errors, return 201 for independent proposals or 202 for generic write approval suspension, and prohibit response caching.
Relationships: tool_reads executes reads or creates approval checkpoints; services owns request state and canonical evidence, and approvals alone accepts browser write decisions.
Directory:
- AgentChatView: Authentication, error, and cache policy.
- AgentChatView.handle_exception: Return stable request error without exposing exception content.
- AgentChatView.finalize_response: Prohibit client caching of private results.
- ToolCatalogView: Read tool catalog.
- ToolCatalogView.get: Parse pagination and call request-catalog service.
- ToolReadView: Execute request-bound data operation.
- ToolReadView.post: Validate invocation and retain business HTTP status.
- AgentRequestView: Check authoritative chat-report status.
- AgentRequestView.get: Return browser-safe projection only for authorized request.
Variable index:
- ToolReadView.parser_classes: Reuse MCP bounded JSON parsing, including published document uploads.
- AgentChatView.authentication_classes: Agent credentials only; Tool or Session cannot substitute.
- logger: Records view, exception type, and HTTP request correlation ID without credential or content.
"""

import logging

from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.crm.access import AgentAuthentication
from apps.agent_tools.parsers import ToolJSONParser

from . import services, tool_reads

logger = logging.getLogger("salesmate.chat.tools")


# Function: Isolate employee-bound chat-service identity and normalize errors.
# Logic: Inherit default login permission, use only ``AgentAuthentication``, and prohibit response caching.
# Constraints: Credential identifies employee rather than Worker process type; request ownership and native business authorization remain enforced.
class AgentChatView(APIView):
    authentication_classes = (AgentAuthentication,)

    # Function: Normalize failure envelope for request authorization, state, structure, and server exceptions.
    # Inputs: View exception ``exc``; ``self.request`` supplies HTTP correlation ID.
    # Outputs: JSON retaining actual HTTP status with error.scope=request; unknown exceptions become safe 500.
    # Logic: DRF exceptions use project handler; unknown exception logs type only and returns generic error without Django DEBUG page.
    # Constraints: Does not retry or change chat state; ``ToolReadView`` returns business-tool errors unchanged with scope=tool.
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

    # Function: Uniformly prohibit caching for successful and failed responses.
    # Inputs: HTTP ``request``, ``response``, and DRF extension ``args`` and ``kwargs``.
    # Outputs: Response after rendering negotiation with ``Cache-Control: no-store``.
    # Logic: Retain framework state, authentication headers, and media type then append cache policy.
    # Constraints: Does not modify business content or error information.
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


# Function: Provide request-bound actual tool-discovery endpoint.
# Logic: Publish all authorized MCP schemas plus eligible independent order/email proposals.
# Constraints: Requires a valid processing request; pagination includes the complete business catalog.
class ToolCatalogView(AgentChatView):
    # Function: Read one page of tool descriptions.
    # Inputs: request_id and optional page and page_size query parameters from ``request``.
    # Outputs: tools, count, page, page_size, protocol, and request identifier.
    # Logic: Converts only pagination integers; service validates parameter legality and request permission.
    # Constraints: Rejects unknown fields and repeated parameters and does not automatically advance pages or infer company.
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


# Function: Execute a read, prepare an independent business proposal, or suspend a generic business write.
# Logic: Derive identity from Agent authentication; generic write/confirm operations require the continuation checkpoint, and file uploads reuse bounded MCP JSON parsing.
# Constraints: No write executes through this view; the independent decision transaction uses the frozen approval UUID for idempotency.
class ToolReadView(AgentChatView):
    parser_classes = (ToolJSONParser,)

    # Function: Execute an authorized data tool.
    # Inputs: ``request`` data is request_id, name, and arguments object.
    # Outputs: Read data/evidence, 201 pending business proposal, 202 experiment approval_required, or explicit failure with scope=tool.
    # Logic: Return the committed service receipt; proposal/approval creation never means business execution completed.
    # Constraints: Query does not require a chat-bound company; ``customers.context`` location parameters still follow original Schema.
    @extend_schema(
        request={"application/json": tool_reads.CALL_SCHEMA},
        responses={
            200: OpenApiTypes.OBJECT,
            201: OpenApiTypes.OBJECT,
            202: OpenApiTypes.OBJECT,
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


# Function: Allow Agent to query authoritative request status after losing report response.
# Logic: Reads original request without rerunning model or creating retry job.
# Constraints: Permits caller's pending, processing, and terminal states and retains conversation access restrictions.
class AgentRequestView(AgentChatView):
    # Function: Query status and final citations for an authorized request.
    # Inputs: Agent HTTP ``request`` and path UUID ``request_id``.
    # Outputs: Request status, message ID, version, error, and cited evidence.
    # Logic: Reuses browser-safe projection and does not publish complete context or tool-invocation history.
    # Constraints: Employee unauthorized access consistently returns 404 and request body cannot self-report identity.
    @extend_schema(
        responses=OpenApiTypes.OBJECT, operation_id="agent_chat_request_status"
    )
    def get(self, request, request_id):
        return Response(
            services.request_data(services.request_for(request.user, request_id))
        )
