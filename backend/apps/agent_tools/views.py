"""Responsibility: Provide HTTP endpoints for tool discovery, invocation, user delegation, and human confirmation.
Implementation: Explicit experiment mode needs no login or separate token while production mode retains original authorization. Calls use independent Tool or Session identity, file JSON uses bounded parsing, credentials support an explicit tool list or preset snapshot, and production management and proposal approval accept only user Session and CSRF.
Relationships: ``services`` manages idempotency and permissions; reuses ``SalesView`` safe error mapping and ``common.laboratory`` unified experiment identity.
Directory:
- ToolView: Tool authentication boundary.
- CatalogView: Tool catalog.
- CatalogView.get: Discover tools by category with pagination.
- CallView: Tool-execution entry point.
- CallView.post: Validate envelope and invoke.
- CredentialView: User authorization management.
- CredentialView.get: List the caller's delegations.
- CredentialView.post: Create a restricted tool credential.
- CredentialDetailView: Revoke a delegation.
- CredentialDetailView.delete: Revoke the caller's credential.
- ProposalView: Query user proposals.
- ProposalView.get: Query an individual or pending proposal.
- ProposalDetailView: Individual-proposal route.
- ProposalDetailView.get: Publish an independent detail contract.
- DecisionView: Human-decision entry point.
- DecisionView.post: Approve or cancel a frozen proposal.
Variable index:
- ToolView.authentication_classes: Tool and browser authentication.
- CallView.parser_classes: Bounded file-JSON parsing while retaining original form parsers.
- CredentialView.authentication_classes: Public identity in experiment mode and user Session only in production mode.
- CredentialDetailView.authentication_classes: Public identity in experiment mode and user Session only in production mode.
- ProposalView.authentication_classes: Public identity in experiment mode and user Session only in production mode.
- DecisionView.authentication_classes: Public identity in experiment mode and user Session only in production mode.
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


# Function: Constrain tool identity.
# Logic: ``ToolAuthentication`` provides public identity in experiment mode; production mode prefers Tool credentials while browsers retain CSRF.
# Constraints: Production mode does not reuse Worker Agent authentication.
class ToolView(SalesView):
    authentication_classes = [ToolAuthentication, SessionAuthentication]


# Function: Provide available tools.
# Logic: The allowlist matches execution.
# Constraints: Returns no credentials or business data.
class CatalogView(ToolView):
    # Function: Discover tools with pagination.
    # Inputs: ``request`` supplies category, page, and page_size query fields.
    # Outputs: tools, count, page, and page_size.
    # Logic: Filter permissions before pagination.
    # Constraints: Default is 30, maximum is 100, and does not implicitly return all items.
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


# Function: Receive a structured business invocation.
# Logic: Uses bounded JSON parsing for file tools and executes no arbitrary URL or function.
# Constraints: Name must come from the catalog.
class CallView(ToolView):
    parser_classes = [ToolJSONParser, FormParser, MultiPartParser]

    # Function: Execute one tool invocation.
    # Inputs: ``request`` contains name and arguments and the idempotency key for a write.
    # Outputs: Structured execution or pending-confirmation receipt.
    # Logic: Pass authenticated identity to service.
    # Constraints: Retains actual HTTP errors and does not retry automatically.
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


# Function: Delegate tool permissions for a user.
# Logic: Experiment mode manages delegations through publicly selected identity; production mode accepts only the current Session user.
# Constraints: A Tool token cannot issue new permission.
class CredentialView(SalesView):
    authentication_classes = [LaboratoryAuthentication, SessionAuthentication]

    # Function: Query the caller's authorizations.
    # Inputs: Pagination from ``request``.
    # Outputs: Metadata without token or digest.
    # Logic: Sort by creation time.
    # Constraints: Does not reveal the raw secret.
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

    # Function: Create a token with an explicit scope.
    # Inputs: ``request`` supplies name and expires_in_hours fields, plus exactly one of the allowed_tools or preset fields.
    # Outputs: Authorization ID, one-time token, and expiry.
    # Logic: Resolve a preset to exact current tool names and freeze it; explicit lists retain existing behavior; persist only the token digest.
    # Constraints: Expiry must be explicitly 1..720 hours; wildcards and automatic permission expansion are forbidden.
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


# Function: Revoke a user's delegation.
# Logic: Experiment mode revokes through public selected identity; production mode requires current-user Session while ownership remains located by selected identity.
# Constraints: Can revoke only the caller's own authorization.
class CredentialDetailView(SalesView):
    authentication_classes = [LaboratoryAuthentication, SessionAuthentication]

    # Function: Revoke one token.
    # Inputs: ``request`` and ``credential_id``.
    # Outputs: HTTP 204.
    # Logic: Mark revocation time.
    # Constraints: Cannot stop a business operation already executed or automatically revoke an external action.
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


# Function: Present pending-confirmation content to a user.
# Logic: Experiment mode reads proposals by public selected identity; production mode requires the corresponding user's Session.
# Constraints: Credentials cannot read or approve through this endpoint.
class ProposalView(SalesView):
    authentication_classes = [LaboratoryAuthentication, SessionAuthentication]

    # Function: Read a proposal.
    # Inputs: Pagination from ``request`` and optional path identifier ``proposal_id``.
    # Outputs: Frozen arguments and status.
    # Logic: Lists only pending proposals; individual reads support reviewing terminal state.
    # Constraints: Unauthorized and missing cases both return 404.
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


# Function: Accept independent user confirmation.
# Logic: Experiment mode submits a decision under public selected identity; production mode requires user Session and CSRF, while actual execution still delegates to ``decide``.
# Constraints: Is not registered as an Agent tool.
class DecisionView(SalesView):
    authentication_classes = [LaboratoryAuthentication, SessionAuthentication]

    # Function: Approve or cancel one proposal.
    # Inputs: ``request`` contains decision and ``proposal_id``.
    # Outputs: Processed proposal.
    # Logic: Transaction revalidates permission and frozen version.
    # Constraints: Requires explicit browser submission and does not infer approval from chat text.
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


# Function: Provide proposal detail.
# Logic: Reuses the Session-only query implementation with an independent OpenAPI identifier.
# Constraints: Does not grant additional tool-credential permission.
class ProposalDetailView(ProposalView):
    # Function: Read one proposal.
    # Inputs: ``request`` and ``proposal_id``.
    # Outputs: Frozen arguments and current status.
    # Logic: Delegate ownership query to parent class.
    # Constraints: Missing and unauthorized cases both return 404.
    @extend_schema(
        responses=OpenApiTypes.OBJECT, operation_id="agent_tools_proposal_detail"
    )
    def get(self, request, proposal_id):
        return super().get(request, proposal_id)
