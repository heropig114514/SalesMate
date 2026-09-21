"""职责：提供工具发现、调用、用户委托和人工确认 HTTP 接口。
实现：显式实验模式下无需登录或单独令牌；正式模式保留原授权。独立 Tool 或 Session 调用，文件 JSON 使用有界解析；凭证支持显式工具清单或模板快照，正式模式管理与提案批准只接受用户 Session/CSRF。
关联：services 管理幂等和权限；复用 SalesView 的安全错误映射；使用 common.laboratory 统一实验身份。
目录：
- ToolView：工具认证边界。
- CatalogView：工具目录。
- CatalogView.get：按分类分页发现工具。
- CallView：工具执行入口。
- CallView.post：验证信封并调用。
- CredentialView：用户授权管理。
- CredentialView.get：列出自己的委托。
- CredentialView.post：创建限定工具凭证。
- CredentialDetailView：撤销委托。
- CredentialDetailView.delete：撤销自己的凭证。
- ProposalView：用户提案查询。
- ProposalView.get：查询单条或待处理提案。
- ProposalDetailView：单条提案路由。
- ProposalDetailView.get：发布独立详情契约。
- DecisionView：真人决定入口。
- DecisionView.post：批准或取消冻结提案。
变量索引：
- ToolView.authentication_classes：Tool 与浏览器认证。
- CallView.parser_classes：文件 JSON 有界解析，保留原表单解析器。
- CredentialView.authentication_classes：实验模式公开身份，正式模式仅用户 Session。
- CredentialDetailView.authentication_classes：实验模式公开身份，正式模式仅用户 Session。
- ProposalView.authentication_classes：实验模式公开身份，正式模式仅用户 Session。
- DecisionView.authentication_classes：实验模式公开身份，正式模式仅用户 Session。
"""

import hashlib
import secrets
from datetime import timedelta
from django.utils import timezone
from drf_spectacular.utils import extend_schema, OpenApiTypes
from rest_framework.authentication import SessionAuthentication
from common.laboratory import LaboratoryAuthentication
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.parsers import FormParser, MultiPartParser
from apps.sales.views import SalesView, paged
from .authentication import ToolAuthentication
from .models import ToolCredential, ToolProposal
from .registry import build_registry
from .schemas import UUID, PAGE, object_schema, validate
from . import services
from .presets import permission_presets
from .parsers import ToolJSONParser


# 功能：限定工具身份。
# 逻辑：实验模式由 ToolAuthentication 提供公开身份；正式模式 Tool 凭证优先，浏览器保留 CSRF。
# 约束：正式模式不复用 Worker 的 Agent 认证。
class ToolView(SalesView):
    authentication_classes = [ToolAuthentication, SessionAuthentication]


# 功能：提供可用工具。
# 逻辑：白名单与执行一致。
# 约束：不返回凭证或业务数据。
class CatalogView(ToolView):
    # 功能：分页发现工具。
    # 输入：`request` 的 category/page/page_size。
    # 输出：tools/count/page/page_size。
    # 逻辑：先权限过滤再分页。
    # 约束：默认 30，上限 100，不隐式全量返回。
    @extend_schema(responses=OpenApiTypes.OBJECT, operation_id="agent_tools_catalog")
    def get(self, request):
        args = request.query_params.dict()
        for key in ("page", "page_size"):
            if key in args:
                args[key] = int(args[key])
        validate(args, object_schema({**PAGE, "category": {"type": "string"}}))
        entries = services.catalog(
            request.user,
            request.auth if isinstance(request.auth, ToolCredential) else None,
            args.get("category"),
        )
        page, size = args.get("page", 1), args.get("page_size", 30)
        return Response(
            {
                "contract_version": "agent-tools-v1",
                "tools": entries[(page - 1) * size : page * size],
                "count": len(entries),
                "page": page,
                "page_size": size,
            }
        )


# 功能：接收结构化业务调用。
# 逻辑：使用文件工具有界 JSON 解析，不执行任意 URL 或函数。
# 约束：名称来自目录。
class CallView(ToolView):
    parser_classes = [ToolJSONParser, FormParser, MultiPartParser]

    # 功能：执行一次工具调用。
    # 输入：`request` 含 name/arguments 和写入幂等键。
    # 输出：结构化执行或待确认回执。
    # 逻辑：认证身份传入服务。
    # 约束：保留真实 HTTP 错误，不自动重试。
    @extend_schema(
        request=OpenApiTypes.OBJECT,
        responses=OpenApiTypes.OBJECT,
        operation_id="agent_tools_call",
    )
    def post(self, request):
        validate(
            request.data,
            object_schema(
                {
                    "name": {"type": "string", "maxLength": 120},
                    "arguments": {"type": "object"},
                    "idempotency_key": UUID,
                },
                ["name", "arguments"],
            ),
        )
        result = services.invoke(
            request.user,
            request.auth if isinstance(request.auth, ToolCredential) else None,
            request.data["name"],
            request.data["arguments"],
            request.data.get("idempotency_key"),
        )
        return Response(result)


# 功能：用户委托工具权限。
# 逻辑：实验模式使用公开选择的身份管理委托；正式模式只接受当前 Session 用户。
# 约束：Tool token 不能发放新权限。
class CredentialView(SalesView):
    authentication_classes = [LaboratoryAuthentication, SessionAuthentication]

    # 功能：查询自己的授权。
    # 输入：`request` 分页。
    # 输出：无 token/digest 的元数据。
    # 逻辑：按创建时间排序。
    # 约束：不展示原始密钥。
    @extend_schema(
        responses=OpenApiTypes.OBJECT, operation_id="agent_tools_credentials_list"
    )
    def get(self, request):
        rows, pagination = paged(
            ToolCredential.objects.filter(owner=request.user)
            .order_by("-created_at")
            .values(
                "id", "name", "allowed_tools", "expires_at", "revoked_at", "created_at"
            ),
            request,
        )
        return Response({**pagination, "results": rows})

    # 功能：创建明确范围的 token。
    # 输入：`request` 的 name、expires_in_hours，以及 allowed_tools 或 preset 二选一。
    # 输出：授权 ID、一次性 token、期限。
    # 逻辑：模板解析为当下确切工具名再冻结，显式清单保持原行为；仅保存 token 摘要。
    # 约束：有效期须明确为 1..720 小时；不允许星号或自动扩大权限。
    @extend_schema(
        request=OpenApiTypes.OBJECT,
        responses=OpenApiTypes.OBJECT,
        operation_id="agent_tools_credentials_create",
    )
    def post(self, request):
        validate(
            request.data,
            object_schema(
                {
                    "name": {"type": "string", "minLength": 1, "maxLength": 120},
                    "allowed_tools": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {"enum": list(build_registry())},
                    },
                    "preset": {"enum": ["read_only", "data_management"]},
                    "expires_in_hours": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 720,
                    },
                },
                ["name", "expires_in_hours"],
            ),
        )
        from rest_framework.exceptions import ValidationError
        if ("allowed_tools" in request.data) == ("preset" in request.data):
            raise ValidationError("allowed_tools 与 preset 必须明确提供且只能提供一项。")
        allowed_tools = (request.data["allowed_tools"] if "allowed_tools" in request.data
                         else permission_presets(build_registry())[request.data["preset"]]["allowed_tools"])
        token = secrets.token_urlsafe(32)
        item = ToolCredential.objects.create(
            owner=request.user,
            name=request.data["name"],
            allowed_tools=allowed_tools,
            expires_at=timezone.now()
            + timedelta(hours=request.data["expires_in_hours"]),
            digest=hashlib.sha256(token.encode()).hexdigest(),
        )
        services.logger.info(
            "tool_credential_created owner_id=%s credential_id=%s tool_count=%s",
            request.user.pk,
            item.pk,
            len(item.allowed_tools),
        )
        response = Response(
            {
                "id": str(item.pk),
                "token": token,
                "expires_at": item.expires_at,
                "allowed_tools": item.allowed_tools,
            },
            status=201,
        )
        response["Cache-Control"] = "no-store"
        return response


# 功能：用户撤销委托。
# 逻辑：实验模式使用公开选择的身份撤销委托；正式模式要求当前用户 Session，归属仍按所选身份定位。
# 约束：只能撤销自己的授权。
class CredentialDetailView(SalesView):
    authentication_classes = [LaboratoryAuthentication, SessionAuthentication]

    # 功能：撤销一份 token。
    # 输入：`request`、`credential_id`。
    # 输出：204。
    # 逻辑：标记撤销时间。
    # 约束：不能停止已经执行的业务或自动撤销外部动作。
    @extend_schema(responses={204: None}, operation_id="agent_tools_credentials_revoke")
    def delete(self, request, credential_id):
        if not ToolCredential.objects.filter(
            pk=credential_id, owner=request.user
        ).update(revoked_at=timezone.now()):
            raise NotFound("授权不存在。")
        services.logger.info(
            "tool_credential_revoked owner_id=%s credential_id=%s",
            request.user.pk,
            credential_id,
        )
        return Response(status=204)


# 功能：向用户展示待确认内容。
# 逻辑：实验模式按公开选择的身份读取提案；正式模式要求对应用户 Session。
# 约束：凭证不能通过此接口读取或批准。
class ProposalView(SalesView):
    authentication_classes = [LaboratoryAuthentication, SessionAuthentication]

    # 功能：读取提案。
    # 输入：`request` 分页、`proposal_id` 可选路径标识。
    # 输出：冻结参数及状态。
    # 逻辑：列表仅列 pending，单条支持核对终态。
    # 约束：越权与不存在均为 404。
    @extend_schema(
        responses=OpenApiTypes.OBJECT, operation_id="agent_tools_proposals_read"
    )
    def get(self, request, proposal_id=None):
        query = ToolProposal.objects.filter(owner=request.user).order_by("-created_at")
        if proposal_id:
            return Response(services.proposal_data(query.get(pk=proposal_id)))
        rows, pagination = paged(query.filter(status="pending"), request)
        return Response(
            {**pagination, "results": [services.proposal_data(row) for row in rows]}
        )


# 功能：接受独立用户确认。
# 逻辑：实验模式按公开选择的身份提交决定；正式模式要求用户 Session 和 CSRF，实际执行仍委托 decide。
# 约束：不注册为 Agent 工具。
class DecisionView(SalesView):
    authentication_classes = [LaboratoryAuthentication, SessionAuthentication]

    # 功能：确认或取消一次提案。
    # 输入：`request` 含 decision，`proposal_id`。
    # 输出：处理后的提案。
    # 逻辑：事务重验权限及冻结版本。
    # 约束：需浏览器明确提交，不读取聊天文字推断批准。
    @extend_schema(
        request=OpenApiTypes.OBJECT,
        responses=OpenApiTypes.OBJECT,
        operation_id="agent_tools_proposal_decide",
    )
    def post(self, request, proposal_id):
        validate(
            request.data,
            object_schema({"decision": {"enum": ["approve", "cancel"]}}, ["decision"]),
        )
        return Response(
            services.decide(request.user, proposal_id, request.data["decision"])
        )


# 功能：提供提案详情。
# 逻辑：复用 Session-only 查询实现，独立 OpenAPI 标识。
# 约束：不额外授予工具凭证权限。
class ProposalDetailView(ProposalView):
    # 功能：读取一个提案。
    # 输入：`request`、`proposal_id`。
    # 输出：冻结参数及当前状态。
    # 逻辑：委托父类完成归属查询。
    # 约束：不存在或越权均为 404。
    @extend_schema(
        responses=OpenApiTypes.OBJECT, operation_id="agent_tools_proposal_detail"
    )
    def get(self, request, proposal_id):
        return super().get(request, proposal_id)
