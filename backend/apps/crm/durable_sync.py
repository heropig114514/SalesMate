"""Responsibility: Drive Gmail deduplicated synchronization and durable extraction within explicit user scope.
Implementation: Default to at most fifty messages per batch and require approval to exceed it; process twenty per page, deduplicate before registration within scope, persist sources first, reuse extraction in batches, and submit L1 as four lanes finish.
Relationships: worker handles authorization and batch completion; Gmail and QQ share source and L1 page handling, with QQ explicitly injecting a read-only reader and source.
Directory:
- BatchBackend: Bulk database-read adapter for one email page.
- BatchBackend.__init__: Bind authorized batch, HTTP writer, and page cache.
- BatchBackend.get_stored_email: Query prefetched page results through the established protocol.
- BatchBackend.submit_emails: Delegate result saving to the original HTTP interface.
- checkpoint: Atomically register messages and progress for this batch.
- save_raw: Persist immutable email source text.
- observe: Persist per-message events and cached processing terminal state.
- process_page: Read uncached source text and complete concurrent extraction for one page.
- sync_persisted: Execute bounded synchronization or explicit failed-work retry.
Variable index:
- PAGE_SIZE: Established twenty-message page size, paginating only within user scope.
- logger: Synchronization logger without credentials or bodies.
"""
from functools import partial
import logging

from django.db import connections, transaction

from agent.tools.gmail import read_email, resolve_mailbox_address
from agent.tools.gmail_scope import gmail_message_limit, scoped_message_pages
from agent.workflows.gmail_sync import _can_reuse_stored_extraction, _extract_new_or_retryable_emails
from agent.workflows.l1_email import EXTRACT_PROMPT_VERSION, bailian_extraction_provider

from .access import Conflict, mailbox_for
from .durable_models import StoredMessage, SyncCheckpoint
from .models import Email
from .processing import record_event, require_run
from .selectors import email_data

PAGE_SIZE = 20
logger = logging.getLogger("salesmate.durable_sync")


# Function: Bulk read persisted extractions for this page while retaining the HTTP write boundary.
# Logic: Replace per-message HTTP GET calls with one email query and one extraction prefetch.
# Constraints: Access only the mailbox authorized at construction; cache lifetime is one page.
class BatchBackend:
    # Function: Bind authorized data for this page.
    # Inputs: `backend` is the Agent HTTP client, `run` is an authorized batch, `keys` are page natural keys, and `source` is the email origin.
    # Outputs: Initializes instance state without external writes.
    # Logic: Bulk prefetch latest facts from the same database; mailbox relationship limits employee scope.
    # Constraints: Does not store credentials or share cache across pages or employees.
    def __init__(self, backend, run, keys, source="gmail_real"):
        self.run = run
        self.source = source
        mailbox = run.mailbox
        self.backend, self.mailbox_id = backend, str(mailbox.pk)
        self.saved = {email.pk: email_data(email) for email in Email.objects.filter(mailbox=mailbox, pk__in=keys).select_related("mailbox").prefetch_related("extractions")}

    # Function: Read persisted facts for this page.
    # Inputs: `mailbox_id` is the requested mailbox and `dedupe_key` is the email natural key.
    # Outputs: Original-protocol email representation or None.
    # Logic: Validate mailbox and query the prefetched dictionary.
    # Constraints: Does not fall back to another mailbox or network query.
    def get_stored_email(self, mailbox_id, dedupe_key):
        if str(mailbox_id) != self.mailbox_id:
            raise Conflict("批量缓存邮箱不匹配。")
        return self.saved.get(dedupe_key)

    # Function: Save per-message extraction through the original HTTP contract.
    # Inputs: `submissions` is an array of Agent-validated payloads.
    # Outputs: Backend submission statistics.
    # Logic: Cache L1 output first, then submit through HTTP validation and deduplication; QQ explicitly specifies qq_real while Gmail retains the original call.
    # Constraints: Does not submit when cache writing fails; retains successful extractions for explicit retry when submission fails.
    def submit_emails(self, submissions):
        with transaction.atomic():
            require_run(self.run.pk, self.run.lease_token)
            for item in submissions:
                StoredMessage.objects.filter(mailbox_id=self.mailbox_id, message_id=item["gmail_message_id"]).update(submission=item)
        if self.source == "qq_real":
            return self.backend.submit_emails(submissions, source="qq_real")
        return self.backend.submit_emails(submissions)


# Function: Atomically save selected messages and per-message progress for this batch.
# Inputs: `run` is the running batch and `ids` are deduplicated selected IDs.
# Outputs: None; database and lease errors propagate.
# Logic: Lock mailbox and validate lease, mark Worker takeover through SyncCheckpoint, then register source placeholders and jobs and save scope, refreshing lease per page.
# Constraints: Does not import historical pending work, advance a global History cursor, or pull messages outside scope into the batch.
@transaction.atomic
def checkpoint(run, ids=()):
    mailbox = mailbox_for(run.mailbox.owner, run.mailbox_id, lock=True)
    require_run(run.pk, run.lease_token)
    SyncCheckpoint.objects.get_or_create(mailbox=mailbox)
    for message_id in ids:
        StoredMessage.objects.get_or_create(mailbox=mailbox, message_id=message_id)
    record_event(run.pk, run.lease_token, "discovered", {"message_ids": list(ids)})
    mailbox.sync_state = {**mailbox.sync_state, "scope": {
        "query": "{in:inbox in:sent}", "sync_options": run.sync_options, "durable_messages": True}}
    mailbox.version += 1
    mailbox.save(update_fields=["sync_state", "version"])
    logger.info("gmail_scope_checkpoint run_id=%s discovered=%s", run.pk, len(ids))


# Function: Commit source text before an LLM call.
# Inputs: `run` is a batch, `record` is a registered message, and `raw` is normalized MIME parsing output from Gmail or QQ.
# Outputs: None; commits source in an independent transaction.
# Logic: Validate lease and consistency with an existing source; content for the same provider message ID cannot silently change.
# Constraints: Does not save OAuth credentials; L1 cannot start if source persistence fails.
@transaction.atomic
def save_raw(run, record, raw):
    require_run(run.pk, run.lease_token)
    current = StoredMessage.objects.select_for_update().get(pk=record.pk, mailbox_id=run.mailbox_id)
    if current.raw and current.raw != raw:
        raise Conflict("已缓存邮件原文不可修改。")
    current.raw = raw
    current.save(update_fields=["raw"])


# Function: Save a per-message stage and durable source processing terminal state.
# Inputs: `run` is a batch, `stage` is an Agent event, and `data` is safe stage data.
# Outputs: None; exceptions propagate to caller.
# Logic: Update job and cache state in one transaction, then release database connections when the thread callback ends.
# Constraints: Failures are not automatically rescheduled and meaningless high-frequency body logs are prohibited.
def observe(run, stage, data):
    try:
        with transaction.atomic():
            record_event(run.pk, run.lease_token, stage, data)
            if stage in {"completed", "failed"}:
                StoredMessage.objects.filter(mailbox_id=run.mailbox_id, message_id=data["gmail_message_id"]).update(status=stage, prompt_version=EXTRACT_PROMPT_VERSION)
    finally:
        connections.close_all()


# Function: Process up to twenty discovered messages.
# Inputs: `run` is a batch, `service` is an authorized client, `backend` is the HTTP writer, and `records` is a message array; `reader` is an optional read function and `source` is origin.
# Outputs: None; per-message results and errors persist.
# Logic: Bulk query terminal states and L1 cache; default Gmail and explicit QQ readers cache source before four-lane extraction, with origin sent through HTTP submission.
# Constraints: Isolate read errors to individual messages; database and lease failures propagate and may not fabricate durable success.
def process_page(run, service, backend, records, *, reader=None, source="gmail_real"):
    notify = partial(observe, run)
    notify("discovered", {"message_ids": [record.message_id for record in records]})
    prefix = f"{run.mailbox.address.casefold()}:"
    cached = BatchBackend(backend, run, [prefix + record.message_id for record in records], source=source)
    emails = []
    for record in records:
        if _can_reuse_stored_extraction(cached.get_stored_email(str(run.mailbox_id), prefix + record.message_id)):
            notify("completed", {"gmail_message_id": record.message_id})
            continue
        if _can_reuse_stored_extraction(record.submission or None):
            notify("persisting", {"gmail_message_id": record.message_id})
            try:
                cached.submit_emails([record.submission])
            except Exception as error:
                logger.warning("cached_submission_failed run_id=%s error_type=%s", run.pk, type(error).__name__)
                notify("failed", {"gmail_message_id": record.message_id, "stage": "persisting", "code": "backend_submit_failed"})
            else:
                notify("completed", {"gmail_message_id": record.message_id})
            continue
        notify("fetching", {"gmail_message_id": record.message_id})
        if record.raw:
            raw = record.raw
        else:
            try:
                raw = (reader or read_email)(service, record.message_id)
            except Exception as error:
                logger.warning("mail_message_read_failed run_id=%s source=%s error_type=%s", run.pk, source, type(error).__name__)
                notify("failed", {"gmail_message_id": record.message_id, "stage": "fetching", "code": "qq_read_failed" if source == "qq_real" else "gmail_read_failed"})
                continue
            save_raw(run, record, raw)
        emails.append(raw)
    _extract_new_or_retryable_emails(emails, run.mailbox.address, str(run.mailbox_id), cached, bailian_extraction_provider, progress=notify)


# Function: Execute bounded Gmail synchronization or explicit retry.
# Inputs: `run` is a claimed batch, `service` is an authorized SDK, and `backend` is the Agent HTTP client.
# Outputs: Scope synchronization summary; persistent jobs derive per-message state.
# Logic: Validate the default 50-message or approved limit; truncate newest N first, then exclude saved business emails and completed or failed sources.
# Constraints: Does not run full backfill, drain global backlog, or automatically re-extract; failures require explicit retry and pagination exceptions propagate directly.
def sync_persisted(run, service, backend):
    resolved = resolve_mailbox_address(service, run.mailbox.address)
    if resolved.casefold() != run.mailbox.address.casefold():
        raise Conflict("Gmail 授权邮箱与批次不匹配。")
    explicit = bool(run.message_ids)
    logger.info("gmail_scope_started run_id=%s recent_days=%s max_messages=%s explicit_retry=%s allow_large_sync=%s", run.pk, run.sync_options.get("recent_days"), run.sync_options.get("max_messages"), explicit, run.sync_options.get("allow_large_sync", False))
    if explicit:
        if len(run.message_ids) > gmail_message_limit(run.sync_options):
            raise Conflict("重试邮件数超过本批允许的封数，请先批准相应的同步范围。")
        pages = (run.message_ids[offset:offset + PAGE_SIZE] for offset in range(0, len(run.message_ids), PAGE_SIZE))
    else:
        pages = scoped_message_pages(service, run.sync_options, PAGE_SIZE)
    skipped, selected = 0, 0
    for ids in pages:
        if not explicit:
            stored = set(StoredMessage.objects.filter(mailbox_id=run.mailbox_id, message_id__in=ids, status__in=["completed", "failed"]).values_list("message_id", flat=True))
            prefix = f"{run.mailbox.address.casefold()}:"
            saved = set(Email.objects.filter(mailbox_id=run.mailbox_id, pk__in=[prefix + value for value in ids]).values_list("pk", flat=True))
            pending = [value for value in ids if value not in stored and prefix + value not in saved]
            skipped += len(ids) - len(pending)
            ids = pending
        checkpoint(run, ids)
        records = {item.message_id: item for item in StoredMessage.objects.filter(mailbox_id=run.mailbox_id, message_id__in=ids)}
        if ids:
            process_page(run, service, backend, [records[value] for value in ids])
        selected += len(ids)
    logger.info("gmail_scope_completed run_id=%s selected=%s skipped=%s explicit_retry=%s", run.pk, selected, skipped, explicit)
    return {"status": "completed", "sync_mode": "explicit_retry" if explicit else "bounded",
            "cursor_saved": False, "fetched_count": selected, "duplicate_count": skipped}
