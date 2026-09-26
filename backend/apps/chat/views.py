"""Responsibility: Expose browser chat operations and three fixed Agent endpoints.
Implementation: Experiment mode requires no login and reads across accounts; production Session and CSRF are separate from Agent credentials; views only validate structure, paginate, and dispatch transaction services.
Relationships: ``config.urls`` registers independent paths and reuses existing unified exceptions and OpenAPI.
Directory:
- SubmitView: Explicitly submit chat question.
- SubmitView.post: Atomically create message and task.
- RequestListView: Conversation request list.
- RequestListView.get: Read requests with authorized pagination.
- RequestView: Single-request status query.
- RequestView.get: Read authoritative status.
- RetryView: Explicit failed-request retry.
- RetryView.post: Create or return unique successor.
- ClaimView: Agent atomic claim.
- ClaimView.post: Return zero or one request.
- ContextView: Agent evidence read.
- ContextView.post: Return frozen context.
- AnswerView: Agent idempotent report.
- AnswerView.post: Save validated final result.
Variable index:
- ClaimView.authentication_classes: Employee-bound service authentication.
- ContextView.authentication_classes: Independent service authentication for evidence read.
- AnswerView.authentication_classes: Independent service authentication for result write.
"""

from common.laboratory import owner_scope

from drf_spectacular.utils import extend_schema, OpenApiTypes
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from rest_framework.views import APIView

from apps.crm.access import AgentAuthentication
from apps.sales.views import paged
from . import contracts, services
from .models import AnswerRequest


# Function: Receive an explicit browser question.
# Logic: Inherits SessionAuthentication and login permission while retaining CSRF.
# Constraints: Does not call model during web request.
class SubmitView(APIView):
    # Function: Save question and queue it.
    # Inputs: ``request`` includes conversation, content, and client_key.
    # Outputs: 201 for a new task or 200 for idempotent retransmission.
    # Logic: Delegates to single-transaction service then projects state.
    # Constraints: Employee derives from login identity and unknown fields are rejected.
    @extend_schema(
        request=OpenApiTypes.OBJECT,
        responses={200: OpenApiTypes.OBJECT, 201: OpenApiTypes.OBJECT},
        operation_id="chat_submit",
    )
    def post(self, request):
        answer, created = services.submit(request.user, request.data)
        return Response(services.request_data(answer), status=201 if created else 200)


# Function: Provide pagination of answer state within conversation.
# Logic: Validates owned customer conversation before reading employee requests.
# Constraints: Does not allow company parameter to override request binding.
class RequestListView(APIView):
    # Function: Read stably ordered request list.
    # Inputs: conversation, page, and page_size query parameters from ``request``.
    # Outputs: Existing count and results pagination structure.
    # Logic: Production mode filters by user; experiment mode reads all selected-conversation requests; uses existing pagination and binding checks and invalid pagination returns 400.
    # Constraints: Rechecks binding for every result and does not output raw snapshots.
    @extend_schema(responses=OpenApiTypes.OBJECT, operation_id="chat_requests_list")
    def get(self, request):
        conversation = services.conversation_for(
            request.user, contracts.identifier(request.query_params.get("conversation"))
        )
        query = AnswerRequest.objects.filter(
            owner_scope(request.user), conversation=conversation
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


# Function: Provide polling status for one answer.
# Logic: Independent read does not trigger recomputation or retry.
# Constraints: Can access only caller-owned request whose complete binding remains valid.
class RequestView(APIView):
    # Function: Read request status and citations.
    # Inputs: Logged-in ``request`` and path UUID ``request_id``.
    # Outputs: Browser-safe state object.
    # Logic: Projects after unified authorization.
    # Constraints: No write side effects.
    @extend_schema(responses=OpenApiTypes.OBJECT, operation_id="chat_request_read")
    def get(self, request, request_id):
        return Response(
            services.request_data(services.request_for(request.user, request_id))
        )


# Function: Receive explicit failed-request retry.
# Logic: Creates new attempt and repeated submission returns same successor.
# Constraints: Does not revive old request_id.
class RetryView(APIView):
    # Function: Request answer again.
    # Inputs: Empty JSON ``request`` and original-failed-request UUID ``request_id``.
    # Outputs: 201 for new attempt or 200 for existing successor.
    # Logic: Calls transaction service after validating empty object.
    # Constraints: Original result is not overwritten and non-failed state is rejected.
    @extend_schema(
        request=OpenApiTypes.OBJECT,
        responses=OpenApiTypes.OBJECT,
        operation_id="chat_retry",
    )
    def post(self, request, request_id):
        contracts.fields(request.data, set())
        answer, created = services.retry(request.user, request_id)
        return Response(services.request_data(answer), status=201 if created else 200)


# Function: Provide fixed Agent claim endpoint.
# Logic: Service credential maps one employee.
# Constraints: Browser Session cannot call it.
class ClaimView(APIView):
    authentication_classes = [AgentAuthentication]

    # Function: Claim at most one pending request.
    # Inputs: Empty JSON ``request`` and Agent Authorization.
    # Outputs: Request object or null.
    # Logic: Returns frozen history after atomic state transition.
    # Constraints: No work is 200 and does not automatically recover failed task.
    @extend_schema(
        request=OpenApiTypes.OBJECT,
        responses=OpenApiTypes.OBJECT,
        operation_id="agent_chat_claim",
    )
    def post(self, request):
        contracts.fields(request.data, set())
        return Response({"request": services.claim(request.user)})


# Function: Provide fixed Agent context endpoint.
# Logic: Accepts only request_id and scope.
# Constraints: Does not accept arbitrary company or query overrides.
class ContextView(APIView):
    authentication_classes = [AgentAuthentication]

    # Function: Read and freeze request evidence.
    # Inputs: ``request`` contains request_id and scope.
    # Outputs: Strict ``AnswerContext``.
    # Logic: Runs transaction snapshot service after identity validation.
    # Constraints: Unenabled external returns explicit error.
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


# Function: Provide fixed Agent result-report endpoint.
# Logic: Protected service creates assistant message and does not open browser ordinary-message permission.
# Constraints: No cross-employee write or result overwrite.
class AnswerView(APIView):
    authentication_classes = [AgentAuthentication]

    # Function: Idempotently persist final answer.
    # Inputs: Strict completed or failed report ``request``.
    # Outputs: saved, duplicate, and assistant_message_id.
    # Logic: Atomically saves after validating report Schema, permission, and state without judging truth of content or citations.
    # Constraints: Successful duplicate report still returns saved=true.
    @extend_schema(
        request=OpenApiTypes.OBJECT,
        responses=OpenApiTypes.OBJECT,
        operation_id="agent_chat_answer",
    )
    def post(self, request):
        return Response(services.save_answer(request.user, request.data))
