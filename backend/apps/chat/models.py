"""Responsibility: Persist answer tasks, evidence snapshots, and employee-maintained internal knowledge.
Implementation: Reuse sales messages; persist tool receipts, approval checkpoints, and decisions separately. Database constraints guarantee one active task per conversation, including approval waits, and one pending approval per task.
Relationships: ``chat.services`` maintains state; Agent reads and writes this module only through HTTP.
Directory:
- AnswerRequest: One non-overwritable answer attempt.
- AnswerRequest.Meta: Task-uniqueness and state constraints.
- Citation: Ordered evidence corresponding to assistant message.
- Citation.Meta: Citation-position uniqueness constraint.
- ToolRead: Request-bound, non-overwritable tool-read record.
- ChatApproval: Frozen chat write, user decision, and resumable Agent checkpoint.
- ChatApproval.Meta: Approval state and single-pending-operation constraints.
- KnowledgeEntry: Internal knowledge with explicit version.
- KnowledgeEntry.Meta: Employee knowledge-version uniqueness constraint.
Variable index:
- AnswerRequest.id: Agent idempotency key.
- AnswerRequest.owner: Submitting employee.
- AnswerRequest.company: Fixed nullable customer binding; null indicates general question answering.
- AnswerRequest.conversation: Fixed private conversation.
- AnswerRequest.user_message: Original question; may be reused by multiple explicit attempts.
- AnswerRequest.assistant_message: At most one immutable answer per attempt.
- AnswerRequest.retry_of: Original failed request for explicit retry; each original has one successor.
- AnswerRequest.status: pending, processing, awaiting_approval, completed, failed, or cancelled.
- AnswerRequest.requested_by: User who submitted the question and may approve its writes; legacy rows use owner.
- AnswerRequest.created_at: Creation time.
- AnswerRequest.processing_started_at: Claim time.
- AnswerRequest.finished_at: Terminal-state time.
- AnswerRequest.recent_history: History before current question frozen at claim.
- AnswerRequest.context_snapshot: Complete internal response frozen at first read.
- AnswerRequest.result: Accepted normalized result for exact idempotency comparison.
- AnswerRequest.chat_prompt_version: Prompt version actually executed.
- AnswerRequest.error: Agent-supplied error code and message; only structure is checked and producer owns redaction.
- AnswerRequest.Meta.constraints: Database constraints for one active task and legal states.
- Citation.request: Answer request to which it belongs.
- Citation.position: Citation-array position starting at 1; does not validate content numbering.
- Citation.source_id: Source identifier declared by Agent, not guaranteed registered in this request snapshot.
- Citation.source_type: Source category.
- Citation.title_or_label: Source title reported by Agent.
- Citation.content: Evidence content matching this request context or tool record; empty when unmatched.
- ToolRead.id: Successful single-read UUID and source namespace.
- ToolRead.request: Owning answer request; employee identity inherits from request.
- ToolRead.tool: Executed read or explicitly approved mutation tool name.
- ToolRead.arguments: Actual Schema-validated parameter snapshot.
- ToolRead.result: Original business-tool receipt retaining pagination, status, version, and data.
- ToolRead.evidence_items: Four-field evidence array returned by this read without overwriting old sources.
- ToolRead.created_at: Creation time of successful read record.
- ChatApproval.id: Stable approval identifier and mutation idempotency key.
- ChatApproval.request: Chat request whose execution is suspended.
- ChatApproval.tool: Frozen registered write-tool name.
- ChatApproval.arguments: Exact proposed tool arguments.
- ChatApproval.schema: Tool input contract captured at proposal time.
- ChatApproval.target_fingerprint: Target version approved by the user, including in laboratory mode.
- ChatApproval.continuation: Agent loop position, observations, and call signatures before the pending write.
- ChatApproval.status: pending, approved, or rejected.
- ChatApproval.created_at: Proposal creation time.
- ChatApproval.expires_at: Approval deadline.
- ChatApproval.decided_at: Time of the explicit user decision.
- ChatApproval.decided_by: Authenticated browser user making the decision.
- ChatApproval.receipt: Executed write and canonical evidence, persisted atomically.
- ChatApproval.Meta.constraints: Single pending approval per request and valid decision states.
- Citation.Meta.constraints: Citation positions unique within request.
- KnowledgeEntry.id: Knowledge-record identifier.
- KnowledgeEntry.owner: Employee allowed to consume this knowledge.
- KnowledgeEntry.source_key: Human-stable business identifier.
- KnowledgeEntry.version: Explicit information version whose same-version content cannot be overwritten.
- KnowledgeEntry.title: Citable title.
- KnowledgeEntry.content: Information content confirmed by maintainer.
- KnowledgeEntry.active: Whether it participates in new-request retrieval.
- KnowledgeEntry.created_at: Version-import time.
- KnowledgeEntry.Meta.constraints: Unique per employee, identifier, and version.
"""

import uuid

from django.conf import settings
from django.db import models


# Function: Record one answer attempt and authoritative bindings.
# Logic: Empty company binding denotes general conversation and nonempty binding a fixed customer; requested_by preserves the question submitter independently of shared ownership. Approval waits retain the unique active slot; failure may create a new attempt for the same message.
# Constraints: Completed, failed, and cancelled states are terminal; protected services alone maintain snapshots, approval transitions, and results.
class AnswerRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True,
        related_name="submitted_chat_requests",
    )
    company = models.ForeignKey(
        "crm.Company", on_delete=models.PROTECT, null=True, blank=True
    )
    conversation = models.ForeignKey("sales.Conversation", on_delete=models.PROTECT)
    user_message = models.ForeignKey(
        "sales.Message", on_delete=models.PROTECT, related_name="chat_requests"
    )
    assistant_message = models.OneToOneField(
        "sales.Message", null=True, on_delete=models.PROTECT, related_name="chat_answer"
    )
    retry_of = models.OneToOneField(
        "self", null=True, on_delete=models.PROTECT, related_name="retry_request"
    )
    status = models.CharField(max_length=20, default="pending", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    processing_started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    recent_history = models.JSONField(default=list)
    context_snapshot = models.JSONField(null=True)
    result = models.JSONField(null=True)
    chat_prompt_version = models.CharField(max_length=100, blank=True)
    error = models.JSONField(null=True)

    # Function: Declare active-request and enum constraints.
    # Logic: Database serializes pending, processing, and awaiting_approval requests per conversation and accepts cancelled as a terminal state.
    # Constraints: Services still check authorization and cross-model bindings.
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["conversation"],
                condition=models.Q(status__in=["pending", "processing", "awaiting_approval"]),
                name="chat_one_active_conversation",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["pending", "processing", "awaiting_approval", "completed", "failed", "cancelled"]
                ),
                name="chat_request_status",
            ),
        ]


# Function: Persist citations declared by answer and matchable evidence.
# Logic: Attach content only from this request context or tool records; unregistered source retains metadata and empty content and does not accept Agent self-reported content.
# Constraints: Created in the same transaction as assistant message and request terminal state.
class Citation(models.Model):
    request = models.ForeignKey(
        AnswerRequest, on_delete=models.PROTECT, related_name="citations"
    )
    position = models.PositiveIntegerField()
    source_id = models.TextField()
    source_type = models.CharField(max_length=80)
    title_or_label = models.TextField()
    content = models.TextField()

    # Function: Ensure a citation number corresponds to only one evidence entry.
    # Logic: Request and position are jointly unique.
    # Constraints: Citations are consecutively numbered from 1 by reporting service.
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["request", "position"], name="chat_citation_position"
            )
        ]


# Function: Persist one successful tool read and source content actually returned in that read.
# Logic: Every read creates a new UUID without overwriting existing rows; shares transaction with request-state lock and failed reads are not registered.
# Constraints: Created by chat reads or approval execution; browsers see cited evidence only and later data changes never refresh this record.
class ToolRead(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(
        AnswerRequest, on_delete=models.PROTECT, related_name="tool_reads"
    )
    tool = models.CharField(max_length=120)
    arguments = models.JSONField()
    result = models.JSONField()
    evidence_items = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)


# Function: Persist a write awaiting an independent browser decision.
# Logic: Keep exact arguments and the Agent checkpoint; approval execution and receipt commit together.
# Constraints: Only chat.approvals mutates decisions; a rejected write never executes and cannot be revived.
class ChatApproval(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(AnswerRequest, on_delete=models.PROTECT, related_name="approvals")
    tool = models.CharField(max_length=120)
    arguments = models.JSONField()
    schema = models.JSONField()
    target_fingerprint = models.CharField(max_length=64, blank=True)
    continuation = models.JSONField()
    status = models.CharField(max_length=20, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    decided_at = models.DateTimeField(null=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    receipt = models.JSONField(null=True)

    # Function: Enforce legal decisions and one outstanding approval per chat request.
    # Logic: Partial uniqueness complements the request lock used by the approval service.
    # Constraints: Database constraints do not replace authentication or frozen-argument validation.
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["request"], condition=models.Q(status="pending"), name="chat_one_pending_approval"),
            models.CheckConstraint(condition=models.Q(status__in=["pending", "approved", "rejected"]), name="chat_approval_status"),
        ]


# Function: Persist explicitly imported internal-knowledge versions.
# Logic: A new version for the same business identifier deactivates old version while old snapshots retain original evidence.
# Constraints: Does not prefill policy or call external retrieval or models.
class KnowledgeEntry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    source_key = models.CharField(max_length=160)
    version = models.CharField(max_length=80)
    title = models.CharField(max_length=240)
    content = models.TextField()
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Function: Restrict knowledge-version identity.
    # Logic: Persist same identifier and version once per employee.
    # Constraints: Service separately forbids reusing a version with different content.
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "source_key", "version"], name="chat_knowledge_version"
            )
        ]
