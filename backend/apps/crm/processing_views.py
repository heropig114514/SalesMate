"""Responsibility: Provide employee synchronization progress, explicit retry, and email human-review interfaces.
Implementation: Private processing scope always uses the authenticated Session employee as mailbox owner. A mailbox can display all persisted messages, and review uses If-Match to avoid overwriting concurrent judgments.
Relationships: urls registers explicit paths, while processing and classification own database transactions.
Directory:
- ReviewRequestSerializer: Declare human-confirmation payload.
- SyncRunView: Query or explicitly retry a synchronization batch.
- SyncRunView.get: Return overall batch progress.
- SyncRunView.post: Explicitly retry failed messages.
- EmailReviewsView: Query pending-review, hidden, or all persisted messages.
- EmailReviewsView.get: Return source evidence in pages by employee and optional mailbox.
- EmailReviewView: Save a human decision for one email.
- EmailReviewView.patch: Validate version and persist human confirmation.
Variable index:
- ReviewRequestSerializer.review_status: Two allowed human decisions.
- OBJECT: Generic OpenAPI object representation.
"""

from common.laboratory import owner_scope
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import serializers

from .access import mailbox_for
from .classification import review_data, review_email
from .models import Email, MailboxSyncRun
from .processing import retry_run, run_data
from .serializers import StrictSerializer

OBJECT = OpenApiTypes.OBJECT


# Function: Declare an explicit human classification decision.
# Logic: Allow confirmation only as business or non-business; reject self-reported identity and arbitrary model fields.
# Constraints: Transactional service validates mailbox authorization and version.
class ReviewRequestSerializer(StrictSerializer):
    review_status = serializers.ChoiceField(choices=["confirmed_business", "confirmed_non_business"])


# Function: Provide batch state independent of company pagination.
# Logic: Query only current employee mailbox batches and use POST for explicit retry.
# Constraints: Does not return authorization or lease credentials.
class SyncRunView(APIView):
    # Function: Query overall synchronization and profiling progress.
    # Inputs: `request` is an employee session and `run_id` is a batch UUID.
    # Outputs: Batch counts and safe per-message errors.
    # Logic: Query the batch ID together with authenticated mailbox ownership, then generate derived statistics.
    # Constraints: Absent and unauthorized records both return 404 with no write side effects.
    @extend_schema(responses=OBJECT, tags=["processing"])
    def get(self, request, run_id):
        run = MailboxSyncRun.objects.filter(owner_scope(request.user, "mailbox__owner"), pk=run_id).first()
        if run is None:
            raise NotFound("批次不存在。")
        return Response(run_data(run))

    # Function: Explicitly retry failed messages.
    # Inputs: `request` is an employee session and `run_id` is a failed batch UUID.
    # Outputs: HTTP 202 and newly queued batch.
    # Logic: Reuse original message IDs and retain old failure records.
    # Constraints: Reject non-failed batches or existing active synchronization and do not start Web threads.
    @extend_schema(request=None, responses={202: OBJECT}, tags=["processing"])
    def post(self, request, run_id):
        return Response(run_data(retry_run(request.user, run_id)), status=202)


# Function: Provide a paginated human-review list.
# Logic: Default to needs_review and permit hidden email or all persisted message selection explicitly.
# Constraints: Employees access source text and evidence from their own mailboxes only.
class EmailReviewsView(APIView):
    # Function: List reviewable messages by mailbox and status.
    # Inputs: `request` may include status and page, while `mailbox_id` may limit one mailbox.
    # Outputs: At most 20 items, total count, and pending-review count.
    # Logic: Always filter by authenticated mailbox owner; all retains review scope, saved includes business messages, and results order by received time descending.
    # Constraints: Does not parse bodies as HTML; invalid status or pagination returns 400.
    @extend_schema(responses=OBJECT, tags=["processing"], parameters=[OpenApiParameter("status", str, enum=["pending", "non_business", "all", "saved"]), OpenApiParameter("page", int)])
    def get(self, request, mailbox_id=None):
        query = Email.objects.filter(owner_scope(request.user, "mailbox__owner"))
        if mailbox_id:
            query = query.filter(mailbox=mailbox_for(request.user, mailbox_id))
        pending_count = query.filter(business_classification="needs_review").count()
        status = request.query_params.get("status", "pending")
        if status == "pending":
            query = query.filter(business_classification="needs_review")
        elif status == "non_business":
            query = query.filter(business_classification="non_business")
        elif status == "all":
            query = query.exclude(business_classification="business", review_status="")
        elif status == "saved":
            pass
        else:
            raise ValidationError("status 必须是 pending、non_business、all 或 saved。")
        try:
            page = int(request.query_params.get("page", 1))
        except (ValueError, TypeError):
            raise ValidationError("page 必须为正整数。") from None
        if page < 1:
            raise ValidationError("page 必须为正整数。")
        count = query.count()
        items = query.order_by("-received_at", "dedupe_key")[(page - 1) * 20:page * 20]
        return Response({"results": [review_data(item) for item in items], "count": count, "pending_count": pending_count, "page": page, "page_size": 20})


# Function: Handle versioned human decisions.
# Logic: Serializer allowlist and transactional authorization validate together.
# Constraints: Follows Session/CSRF and does not call Gmail or LLM.
class EmailReviewView(APIView):
    # Function: Confirm a business or non-business email.
    # Inputs: `request` contains review_status and If-Match, and `email_id` is the complete deduplication key.
    # Outputs: New review version and decision.
    # Logic: Save a human-priority result and trigger or suppress profiling as needed.
    # Constraints: Concurrent conflict returns 409, while unknown or unauthorized email returns 404.
    @extend_schema(request=ReviewRequestSerializer, responses=OBJECT, tags=["processing"], parameters=[OpenApiParameter("If-Match", int, OpenApiParameter.HEADER, required=True)])
    def patch(self, request, email_id):
        data = ReviewRequestSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return Response(review_email(request.user, email_id, data.validated_data["review_status"], request.headers.get("If-Match")))
