"""Responsibility: Persist independent tool authorizations, idempotent receipts, and proposals awaiting human confirmation.
Implementation: Credentials retain only digests; user and idempotency key are unique; proposals freeze tool input and have an expiry.
Relationships: ``authentication`` authenticates tool tokens; ``services`` maintains receipts and confirmation state in transactions.
Directory:
- ToolCredential: Tool permissions delegated by a user.
- ToolCall: Receipt for one logical write.
- ToolCall.Meta: Per-employee idempotency-key constraint.
- ToolProposal: Frozen change requiring human confirmation.
Variable index:
- ToolCredential.id: Authorization identifier.
- ToolCredential.owner: Authorized user.
- ToolCredential.name: User-readable name.
- ToolCredential.digest: SHA-256 digest of the token.
- ToolCredential.allowed_tools: Explicit allowlist of tool names.
- ToolCredential.expires_at: Authorization expiry time.
- ToolCredential.revoked_at: Revocation time.
- ToolCredential.created_at: Creation time.
- ToolCall.id: Receipt identifier.
- ToolCall.owner: Invoking user.
- ToolCall.key: Caller-generated idempotency UUID.
- ToolCall.tool: Fixed tool name.
- ToolCall.input_hash: Digest of input content.
- ToolCall.result: Frozen execution or proposal receipt.
- ToolCall.created_at: Completion time.
- ToolCall.Meta.constraints: Jointly unique on employee and key.
- ToolProposal.id: Proposal identifier.
- ToolProposal.owner: User required to confirm.
- ToolProposal.credential: Originating authorization; cannot approve after revocation or expiry.
- ToolProposal.tool: Proposed tool to execute.
- ToolProposal.arguments: Frozen input including stale business versions.
- ToolProposal.status: pending, approved, or cancelled.
- ToolProposal.result: Execution result after confirmation.
- ToolProposal.expires_at: Proposal expiry time.
- ToolProposal.created_at: Proposal creation time.
"""

import uuid
from django.conf import settings
from django.db import models


# Function: Isolate Agent-task credentials from business-operation authorization.
# Logic: Each authorization restricts user, tool list, and validity period.
# Constraints: Raw token is returned only once in the creation response and never stored in the database.
class ToolCredential(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    digest = models.CharField(max_length=64, unique=True)
    allowed_tools = models.JSONField()
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)


# Function: Persist a verifiable receipt of a logical write.
# Logic: The same user's same idempotency key accepts only the same tool and input.
# Constraints: Receipt is a historical execution snapshot and does not represent current state of related entities.
class ToolCall(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    key = models.UUIDField()
    tool = models.CharField(max_length=120)
    input_hash = models.CharField(max_length=64)
    result = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    # Function: Protect per-employee idempotent identity.
    # Logic: The database guarantees final uniqueness.
    # Constraints: Service separately compares input digests.
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "key"], name="agent_tools_owner_call_key"
            )
        ]


# Function: Persist a tool call requiring human confirmation.
# Logic: Input cannot be changed through a confirmation request; a stale-version conflict requires a new proposal.
# Constraints: Tool credentials cannot approve; cancellation and expiry do not execute business operations.
class ToolProposal(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    credential = models.ForeignKey(ToolCredential, null=True, on_delete=models.PROTECT)
    tool = models.CharField(max_length=120)
    arguments = models.JSONField()
    status = models.CharField(max_length=16, default="pending")
    result = models.JSONField(null=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
