"""Responsibility: Persist answer tasks, evidence snapshots, and employee-maintained internal knowledge.
Implementation: Reuse sales conversations and messages; independently persist result and evidence for every tool read, while database constraints guarantee one task per conversation and a unique answer.
Relationships: ``chat.services`` maintains state; Agent reads and writes this module only through HTTP.
Directory:
- AnswerRequest: One non-overwritable answer attempt.
- AnswerRequest.Meta: Task-uniqueness and state constraints.
- Citation: Ordered evidence corresponding to assistant message.
- Citation.Meta: Citation-position uniqueness constraint.
- ToolRead: Request-bound, non-overwritable tool-read record.
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
- AnswerRequest.status: pending, processing, completed, or failed.
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
- ToolRead.tool: Actual executed read-only tool name.
- ToolRead.arguments: Actual Schema-validated parameter snapshot.
- ToolRead.result: Original business-tool receipt retaining pagination, status, version, and data.
- ToolRead.evidence_items: Four-field evidence array returned by this read without overwriting old sources.
- ToolRead.created_at: Creation time of successful read record.
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
# Logic: Empty company binding denotes general conversation and nonempty binding a fixed customer; active state is unique per conversation; user message may be reused by a new attempt after failure.
# Constraints: Terminal state cannot be overwritten and protected services alone maintain snapshots and results.
class AnswerRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
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
    # Logic: Database prevents concurrent submission from bypassing conversation serialization.
    # Constraints: Services still check authorization and cross-model bindings.
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["conversation"],
                condition=models.Q(status__in=["pending", "processing"]),
                name="chat_one_active_conversation",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["pending", "processing", "completed", "failed"]
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
# Constraints: Created only by chat read-only service and cannot be read directly by browser; later company changes do not refresh this record.
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
