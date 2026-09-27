"""Responsibility: Expose employee-only review and decisions for independent chat proposals.
Implementation: Session authentication and CSRF protect decisions; strict schemas and ownership protect lists/details in all deployment modes.
Relationships: action_services owns transactions; the Agent uses tool_reads rather than these browser endpoints.
Directory:
- ProposalView: Session-only response and error policy.
- ProposalView.finalize_response: Prevent caching of private message content.
- ProposalView.handle_exception: Log safe failure context.
- ProposalListView: Paginated conversation proposal recovery.
- ProposalListView.get: Read proposals for the authenticated employee's conversation.
- ProposalDetailView: Read one employee proposal.
- ProposalDetailView.get: Return the current frozen proposal and execution state.
- ProposalDecisionView: Accept explicit employee decisions.
- ProposalDecisionView.post: Validate the displayed revision and decision.
Variable index:
- logger: Redacted browser-decision diagnostics.
- DECISION_SCHEMA: Closed approve/cancel/revision input.
- LIST_SCHEMA: Closed conversation and pagination query.
- ProposalView.authentication_classes: Session identity only.
"""

import logging
from django.db.models import Q
from drf_spectacular.utils import extend_schema, OpenApiTypes, OpenApiParameter
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.agent_tools.schemas import PAGE, UUID, REVISION, object_schema, validate
from apps.sales.models import Conversation
from .models import ActionProposal
from . import action_services

logger = logging.getLogger("salesmate.chat.actions")
DECISION_SCHEMA = object_schema({"decision": {"enum": ["approve", "cancel"]}, "revision": REVISION}, ["decision", "revision"])
LIST_SCHEMA = object_schema({"conversation_id": UUID, **PAGE}, ["conversation_id"])


# Function: Restrict review interfaces to authenticated employees.
# Logic: SessionAuthentication enforces CSRF on mutation; no laboratory or machine identity can approve.
# Constraints: Responses contain private email content and must never be cached.
class ProposalView(APIView):
    authentication_classes = [SessionAuthentication]

    # Function: Mark every result private and noncacheable.
    # Inputs: HTTP `request`, `response`, and framework `args`/`kwargs`.
    # Outputs: Negotiated response with no-store policy.
    # Logic: Apply the framework renderer then set cache policy for successes and failures.
    # Constraints: Does not change business state or status codes.
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    # Function: Record a failure without logging private proposal contents.
    # Inputs: Framework `exc`; instance request supplies the employee and route.
    # Outputs: Original framework error response or propagated unexpected exception.
    # Logic: Record location and exception class only; retain normal business status semantics.
    # Constraints: No retries and no token, recipient or body logging.
    def handle_exception(self, exc):
        logger.warning("chat_proposal_endpoint_failed view=%s owner_id=%s proposal_id=%s error_type=%s", type(self).__name__,
            getattr(self.request.user, "pk", None), self.kwargs.get("proposal_id"), type(exc).__name__)
        return super().handle_exception(exc)


# Function: Recover persisted proposals independently of assistant text.
# Logic: Query only the employee's workspace conversation; pagination preserves historical decisions.
# Constraints: No UUID parsing from generated text and no cross-employee laboratory access.
class ProposalListView(ProposalView):
    # Function: List frozen actions and actual execution status.
    # Inputs: Session `request` with conversation_id and optional page/page_size.
    # Outputs: Paginated records with proposal data plus originating request/message identifiers.
    # Logic: Verify conversation ownership before selecting proposals; no write or provider call occurs.
    # Constraints: Extra/repeated query fields are rejected; an empty list does not conceal unauthorized access.
    @extend_schema(parameters=[OpenApiParameter("conversation_id", OpenApiTypes.UUID, required=True),
        OpenApiParameter("page", int), OpenApiParameter("page_size", int)], responses=OpenApiTypes.OBJECT,
        operation_id="chat_action_proposals_list")
    def get(self, request):
        query = request.query_params.dict()
        if any(len(values) != 1 for _, values in request.query_params.lists()):
            raise ValidationError("查询参数不得重复。")
        for field in ("page", "page_size"):
            if field in query:
                try:
                    query[field] = int(query[field])
                except ValueError:
                    raise ValidationError("分页参数必须为整数。") from None
        validate(query, LIST_SCHEMA)
        if not Conversation.objects.filter(pk=query["conversation_id"], owner=request.user, archived=False, company__isnull=True).exists():
            raise NotFound("会话不存在。")
        rows = ActionProposal.objects.filter(request__conversation_id=query["conversation_id"], request__owner=request.user).filter(
            Q(request__requested_by=request.user) | Q(request__requested_by__isnull=True)).select_related("request", "action").order_by("created_at", "pk")
        page, size = query.get("page", 1), query.get("page_size", 30)
        return Response({"count": rows.count(), "page": page, "page_size": size,
            "results": [{**action_services.proposal_data(row), "request_id": str(row.request_id),
                "user_message_id": str(row.request.user_message_id)} for row in rows[(page - 1) * size:page * size]]})


# Function: Expose one employee proposal independently of its originating answer status.
# Logic: Delegate all ownership checks and authoritative state projection to the service.
# Constraints: This read cannot approve or execute an action.
class ProposalDetailView(ProposalView):
    # Function: Read a frozen proposal and its execution state.
    # Inputs: Session `request` and path `proposal_id`.
    # Outputs: Closed proposal data or 404.
    # Logic: Resolve the exact authenticated employee binding.
    # Constraints: Machine credentials cannot substitute for the browser Session.
    @extend_schema(responses=OpenApiTypes.OBJECT, operation_id="chat_action_proposals_detail")
    def get(self, request, proposal_id):
        return Response(action_services.proposal_data(action_services.proposal_for(request.user, proposal_id)))


# Function: Accept explicit employee authorization after reviewing a concrete proposal.
# Logic: The decision only selects approve/cancel for the displayed revision; content cannot be changed here.
# Constraints: Session CSRF is mandatory and external execution remains asynchronous.
class ProposalDecisionView(ProposalView):
    # Function: Commit one version-bound decision.
    # Inputs: Session `request` containing decision/revision and path `proposal_id`.
    # Outputs: Authoritative proposal status or original authorization/validation/conflict error.
    # Logic: Validate the closed schema before calling the atomic service.
    # Constraints: Never accept owner overrides, approval flags or replacement content.
    @extend_schema(request={"application/json": DECISION_SCHEMA}, responses=OpenApiTypes.OBJECT, operation_id="chat_action_proposals_decide")
    def post(self, request, proposal_id):
        validate(request.data, DECISION_SCHEMA)
        return Response(action_services.decide(request.user, proposal_id, request.data["decision"], request.data["revision"]))
