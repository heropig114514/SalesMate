"""Responsibility: Store recoverable mailbox synchronization batches and per-message processing state.
Implementation: Each mailbox has at most one active batch, while message jobs are unique by provider message ID within a batch; QQ scope is retained in its batch.
Relationships: processing manages claims and progress, worker calls isolated Agent, and models imports this module to register Django models.
Directory:
- MailboxSyncRun: Persist synchronization requests, leases, and final summaries.
- MailboxSyncRun.Meta: Constrain active batches to one per mailbox.
- EmailProcessingJob: Store per-message stages and actionable errors.
- EmailProcessingJob.Meta: Constrain messages to one per batch.
Variable index:
- MailboxSyncRun.id: Synchronization batch UUID.
- MailboxSyncRun.mailbox: Owning mailbox, with authorization derived from owner.
- MailboxSyncRun.status: queued, running, partial, completed, or failed.
- MailboxSyncRun.requested_at: Request time.
- MailboxSyncRun.started_at: First claim time.
- MailboxSyncRun.finished_at: Terminal-state time.
- MailboxSyncRun.lease_until: Running-lease expiration.
- MailboxSyncRun.lease_token: Random credential rejecting reports from old executors.
- MailboxSyncRun.error: Batch error without credentials or original exception bodies.
- MailboxSyncRun.result: Agent summary without Gmail authorization.
- MailboxSyncRun.message_ids: Explicit-retry message scope; an empty array means ordinary scanning.
- MailboxSyncRun.sync_options: Frozen Gmail/QQ batch time and message scope and Gmail over-limit approval; legacy batches may have an empty object.
- MailboxSyncRun.Meta.constraints: One active batch per mailbox constraint.
- EmailProcessingJob.id: Per-message job UUID.
- EmailProcessingJob.run: Owning synchronization batch.
- EmailProcessingJob.gmail_message_id: Protocol field name retaining Gmail ID or QQ stable message identifier.
- EmailProcessingJob.dedupe_key: Dedupe key from canonical mailbox address plus message ID.
- EmailProcessingJob.stage: discovered, fetching, extracting, persisting, completed, or failed.
- EmailProcessingJob.status: pending, running, completed, or failed.
- EmailProcessingJob.attempt: Number of times this job has begun reading.
- EmailProcessingJob.error: Stage, code, and safe error description.
- EmailProcessingJob.created_at: Discovery time.
- EmailProcessingJob.started_at: Processing start time.
- EmailProcessingJob.finished_at: Processing terminal time.
- EmailProcessingJob.company: Company owning a saved message, used for batch profiling progress.
- EmailProcessingJob.Meta.constraints: One message per batch constraint.
"""
import uuid

from django.db import models


# Function: Store one employee mailbox synchronization batch.
# Logic: Lease credential identifies the current executor and QQ scope is frozen by batch; processing decides whether active batches merge or reject.
# Constraints: Failed batches do not retry automatically; the database guarantees active-batch mutual exclusion.
class MailboxSyncRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mailbox = models.ForeignKey("crm.Mailbox", related_name="sync_runs", on_delete=models.CASCADE)
    status = models.CharField(max_length=16, default="queued")
    requested_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    lease_until = models.DateTimeField(null=True)
    lease_token = models.UUIDField(null=True)
    error = models.JSONField(null=True)
    result = models.JSONField(default=dict)
    message_ids = models.JSONField(default=list)
    sync_options = models.JSONField(default=dict)

    # Function: Prevent duplicate clicks and multiple processes from creating parallel mailbox batches.
    # Logic: Only queued and running records participate in the mailbox uniqueness constraint.
    # Constraints: Historical terminal batches are retained permanently.
    class Meta:
        constraints = [models.UniqueConstraint(fields=["mailbox"], condition=models.Q(status__in=["queued", "running"]), name="crm_active_mailbox_run")]


# Function: Store processing progress for one message in a batch.
# Logic: Record it at discovery, update the same record with stage events, and associate its completed result with a company.
# Constraints: Does not store access tokens or raw model exceptions; the email itself remains owned by Email.
class EmailProcessingJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(MailboxSyncRun, related_name="email_jobs", on_delete=models.CASCADE)
    gmail_message_id = models.CharField(max_length=200)
    dedupe_key = models.CharField(max_length=400)
    stage = models.CharField(max_length=20, default="discovered")
    status = models.CharField(max_length=16, default="pending")
    attempt = models.PositiveIntegerField(default=0)
    error = models.JSONField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    company = models.ForeignKey("crm.Company", null=True, on_delete=models.SET_NULL)

    # Function: Ensure repeated stage registration does not increase counts.
    # Logic: Synchronization batch and Gmail message ID are jointly unique.
    # Constraints: The same message can create a new job in a later explicit-retry batch.
    class Meta:
        constraints = [models.UniqueConstraint(fields=["run", "gmail_message_id"], name="crm_run_message")]
