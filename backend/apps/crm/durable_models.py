"""Responsibility: Persist Gmail source messages, Worker takeover markers and legacy checkpoints, extraction repair jobs, and analysis lineage.
Implementation: Deduplicate by natural message key and atomically commit takeover markers with discovery records; lineage edges retain exact extraction and human-decision versions.
Relationships: durable_sync consumes sources, lineage maintains repairs and invalidation, and models imports this module to register models.
Directory:
- StoredMessage: Durable source before extraction and its processing terminal state.
- StoredMessage.Meta: Mailbox-local message uniqueness constraint.
- SyncCheckpoint: Worker takeover marker and legacy cursor audit data.
- SnapshotSource: Versioned source edge from an analysis snapshot to an email extraction.
- SnapshotSource.Meta: Snapshot-email uniqueness constraint.
- SnapshotInvalidation: Retain snapshot invalidation reason and time.
- ExtractionRepair: Traceable extraction job created by human correction.
- ExtractionRepair.Meta: At most one active repair job per email.
Variable index:
- StoredMessage.mailbox: Mailbox owning authorization.
- StoredMessage.message_id: Gmail message identifier.
- StoredMessage.raw: Parsed complete message, including extractable body and raw headers; empty object means not yet read.
- StoredMessage.submission: Completed L1 submission payload reused after backend write failure to avoid repeated model calls.
- StoredMessage.status: pending, completed, or failed; failed work does not retry automatically.
- StoredMessage.prompt_version: Most recently processed real L1 prompt version.
- StoredMessage.created_at: Discovery time.
- StoredMessage.Meta.constraints: Natural-key message uniqueness constraint.
- SyncCheckpoint.mailbox: Unique checkpoint per mailbox.
- SyncCheckpoint.cursor: History cursor for legacy registered messages, retained only for audit and not driving bounded sync.
- SyncCheckpoint.anchor: History baseline before legacy scanning, retained only for audit.
- SyncCheckpoint.page_token: Legacy historical pagination position, retained only for audit.
- SyncCheckpoint.backfill_complete: Legacy historical enumeration completion state, retained only for audit.
- SnapshotSource.snapshot: L2 snapshot dependent on an email.
- SnapshotSource.email: Source email.
- SnapshotSource.extraction: Exact extraction record used at the time.
- SnapshotSource.review_revision: Human-decision version at the time.
- SnapshotSource.Meta.constraints: One source edge per snapshot and email.
- SnapshotInvalidation.snapshot: Invalidated L2, making associated L3/L4 unavailable too.
- SnapshotInvalidation.reason: Controlled invalidation reason.
- SnapshotInvalidation.created_at: Invalidation time.
- ExtractionRepair.email: Business email awaiting repair extraction.
- ExtractionRepair.source: Original extraction record replaced by repair.
- ExtractionRepair.review_revision: Human-decision version when queued.
- ExtractionRepair.status: pending, running, completed, failed, or skipped.
- ExtractionRepair.lease_until: Execution lease that explicitly fails on expiration.
- ExtractionRepair.error: Controlled error code without raw model exceptions.
- ExtractionRepair.created_at: Job queue time.
- ExtractionRepair.Meta.constraints: Active repair uniqueness constraint.
"""
from django.db import models


# Function: Save source message before a model call.
# Logic: Register at discovery, fill raw after reading, and save submission before L1 completion; commit terminal state with the per-message event.
# Constraints: Source cannot be overwritten; authorization always validates through mailbox.owner and no authorization credentials are stored.
class StoredMessage(models.Model):
    mailbox = models.ForeignKey("crm.Mailbox", on_delete=models.CASCADE, related_name="stored_messages")
    message_id = models.CharField(max_length=200)
    raw = models.JSONField(default=dict)
    submission = models.JSONField(default=dict)
    status = models.CharField(max_length=16, default="pending")
    prompt_version = models.CharField(max_length=100, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    # Function: Ensure repeated discovery by incremental and historical scans does not duplicate messages.
    # Logic: Mailbox and message ID are jointly unique.
    # Constraints: Different mailboxes may store the same Gmail ID.
    class Meta:
        constraints = [models.UniqueConstraint(fields=["mailbox", "message_id"], name="crm_stored_message")]


# Function: Mark Worker takeover and retain legacy synchronization audit state.
# Logic: Bounded sync creates only a takeover marker; ingestion uses it to reject legacy CLI cursor double writes, and legacy fields stop advancing.
# Constraints: Occurs in the same transaction as message registration; retains existing history fields so upgrade does not delete audit data and performs no full backfill.
class SyncCheckpoint(models.Model):
    mailbox = models.OneToOneField("crm.Mailbox", primary_key=True, on_delete=models.CASCADE, related_name="checkpoint")
    cursor = models.CharField(max_length=200, default="")
    anchor = models.CharField(max_length=200, default="")
    page_token = models.TextField(default="")
    backfill_complete = models.BooleanField(default=False)


# Function: Save exact email sources of analysis input.
# Logic: Bind email, extraction record, and review version instead of relying only on company revision.
# Constraints: Create only in the transaction validating L2 input; retain old edges for audit.
class SnapshotSource(models.Model):
    snapshot = models.ForeignKey("crm.AnalysisInput", related_name="sources", on_delete=models.CASCADE)
    email = models.ForeignKey("crm.Email", on_delete=models.CASCADE, related_name="snapshot_sources")
    extraction = models.ForeignKey("crm.Extraction", on_delete=models.PROTECT)
    review_revision = models.PositiveIntegerField()

    # Function: Prevent repeated registration of a source for the same input.
    # Logic: Snapshot and email are unique.
    # Constraints: Different snapshots may bind the same extraction.
    class Meta:
        constraints = [models.UniqueConstraint(fields=["snapshot", "email"], name="crm_snapshot_source")]


# Function: Invalidate one snapshot and its downstream analysis and score.
# Logic: Append a separate invalidation record without rewriting original results or source edges.
# Constraints: Invalidated snapshots cannot hit cache again; regeneration uses a new company revision.
class SnapshotInvalidation(models.Model):
    snapshot = models.OneToOneField("crm.AnalysisInput", related_name="invalidation", on_delete=models.CASCADE)
    reason = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)


# Function: Persist repair extraction work triggered by human confirmation.
# Logic: Freeze original extraction and review version at runtime, then recheck before writeback to reject stale results.
# Constraints: Does not overwrite original extractions; failed work is rescheduled only after an employee explicitly reconfirms business status.
class ExtractionRepair(models.Model):
    email = models.ForeignKey("crm.Email", related_name="repairs", on_delete=models.CASCADE)
    source = models.ForeignKey("crm.Extraction", on_delete=models.PROTECT)
    review_revision = models.PositiveIntegerField()
    status = models.CharField(max_length=16, default="pending")
    lease_until = models.DateTimeField(null=True)
    error = models.CharField(max_length=80, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    # Function: Merge active jobs created by repeated confirmation.
    # Logic: Only pending and running jobs participate in the uniqueness constraint.
    # Constraints: Retain failed and completed jobs as history, and successors can be explicitly created.
    class Meta:
        constraints = [models.UniqueConstraint(fields=["email"], condition=models.Q(status__in=["pending", "running"]), name="crm_active_repair")]
