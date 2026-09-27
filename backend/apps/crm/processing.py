"""Responsibility: Manage mailbox batches, per-message progress, and durable state required for recovery.
Implementation: Explicit retries and Worker claims are confined to the authenticated employee; row locks serialize requests and freeze user scope, disabled QQ rejects queueing and claiming, batch leases reject old executors, and counts derive from jobs.
Relationships: Gmail and QQ share the durable queue, with worker dispatching providers; processing_views provides progress and explicit retry.
Directory:
- request_run: Create a bounded mailbox batch and reject duplicate active batches.
- claim_run: Claim one queued batch.
- require_run: Validate the current execution lease.
- record_event: Idempotently update per-message processing stage.
- finish_run: Complete synchronization and save a safe summary.
- run_data: Generate overall mailbox and company-profile progress.
- retry_run: Explicitly retry messages in one failed batch.
- expire_runs: Mark interrupted leases failed while retaining per-message recovery basis.
Variable index:
- logger: Batch state-transition diagnostic logger.
- RUN_LEASE_SECONDS: 600-second mailbox batch lease renewed by every progress event.
"""

from common.laboratory import owner_scope
from datetime import timedelta
import logging
import uuid

from django.db import transaction
from django.conf import settings
from common.mail_features import require_qq_enabled
from django.db.models import Count
from django.utils import timezone
from rest_framework.exceptions import NotFound

from .access import Conflict, InvalidState, mailbox_for
from .models import Email, EmailProcessingJob, GmailCredential, QQCredential, Job, MailboxSyncRun
from .sync_scope import snapshot
from agent.tools.gmail_scope import gmail_message_limit

logger = logging.getLogger("salesmate.processing")
RUN_LEASE_SECONDS = 600


# Function: Save a synchronization request as an independent batch.
# Inputs: `owner` is an employee, `mailbox_id` is a mailbox, `message_ids` is optional explicit scope, `sync_options` limits the mailbox, and `retry_scope` is the original snapshot for internal retry.
# Outputs: Newly created MailboxSyncRun.
# Logic: Freeze scope under the mailbox lock; active batches reject replacement to prevent concurrent duplicate synchronization; retries retain the original time window and approved count; unapproved over-limit work cannot queue.
# Constraints: Requires credentials, rejects queuing when QQ is disabled, and neither starts threads nor accesses mailboxes.
@transaction.atomic
def request_run(owner, mailbox_id, message_ids=None, *, sync_options=None, retry_scope=None):
    mailbox = mailbox_for(owner, mailbox_id, lock=True)
    is_qq = QQCredential.objects.filter(mailbox=mailbox).exists()
    if is_qq:
        require_qq_enabled("request_sync")
    if not (GmailCredential.objects.filter(mailbox=mailbox).exists() or is_qq):
        raise InvalidState("该邮箱尚未完成 Gmail 授权或 QQ 连接。")
    active = mailbox.sync_runs.filter(status__in=["queued", "running"]).first()
    if active:
        raise Conflict("该邮箱仍有同步批次，请完成后再确认重新抽取或重试。")
    if retry_scope is not None:
        scope = retry_scope
    elif message_ids:
        scope = {}
    else:
        scope = snapshot(sync_options, gmail=not is_qq)
    if not scope and not message_ids:
        raise InvalidState("旧批次没有同步范围，请重新选择范围后同步。")
    if not is_qq:
        try:
            limit = gmail_message_limit(scope)
        except ValueError as error:
            raise InvalidState(str(error)) from None
        if message_ids and len(message_ids) > limit:
            raise InvalidState("重试邮件数超过本批允许的封数，请先批准相应的同步范围。")
    run = MailboxSyncRun.objects.create(mailbox=mailbox, message_ids=message_ids or [], sync_options=scope)
    mailbox.sync_state = {**mailbox.sync_state, "status": "sync_requested", "run_id": str(run.pk), "requested_at": run.requested_at.isoformat(), "error": None}
    mailbox.version += 1
    mailbox.save(update_fields=["sync_state", "version"])
    logger.info("mailbox_run_queued run_id=%s mailbox_id=%s explicit_messages=%s recent_days=%s max_messages=%s allow_large_sync=%s", run.pk, mailbox.pk, len(run.message_ids), scope.get("recent_days"), scope.get("max_messages"), scope.get("allow_large_sync", False))
    return run


# Function: Atomically claim one batch for the current employee.
# Inputs: `owner` is the employee attached to the service credential; `gmail_only` is the explicit legacy Gmail CLI filter.
# Outputs: Running batch with mailbox, or None when the queue is empty.
# Logic: Skip QQ queues when disabled; lock mailbox then batch, claim only queued records, and generate a lease credential.
# Constraints: Does not retry failed or expired batches automatically and avoids reverse lock ordering against request paths.
@transaction.atomic
def claim_run(owner, *, gmail_only=False):
    candidates = MailboxSyncRun.objects.filter(mailbox__owner=owner, status="queued")
    if not settings.QQ_MAIL_ENABLED:
        candidates = candidates.filter(mailbox__qq_credential__isnull=True)
    if gmail_only:
        candidates = candidates.filter(mailbox__gmail_credential__isnull=False)
    candidate = candidates.order_by("requested_at").first()
    if candidate is None:
        return None
    mailbox = mailbox_for(owner, candidate.mailbox_id, lock=True)
    run = MailboxSyncRun.objects.select_for_update().get(pk=candidate.pk)
    if run.status != "queued":
        return None
    run.status, run.started_at = "running", timezone.now()
    run.lease_until, run.lease_token = timezone.now() + timedelta(seconds=RUN_LEASE_SECONDS), uuid.uuid4()
    run.save(update_fields=["status", "started_at", "lease_until", "lease_token"])
    mailbox.sync_state = {**mailbox.sync_state, "status": "sync_running", "run_id": str(run.pk), "started_at": run.started_at.isoformat()}
    mailbox.version += 1
    mailbox.save(update_fields=["sync_state", "version"])
    logger.info("mailbox_run_claimed run_id=%s mailbox_id=%s", run.pk, mailbox.pk)
    return run


# Function: Validate the current executor for a per-message event.
# Inputs: `run_id` is the batch UUID and `token` is the claim credential.
# Outputs: Locked running batch; expired or old executors raise Conflict.
# Logic: Permit writes only when status, credential, and expiration all match.
# Constraints: Callers must be in a transaction and credentials never enter errors or logs.
def require_run(run_id, token):
    run = MailboxSyncRun.objects.select_for_update(of=("self",)).select_related("mailbox").get(pk=run_id)
    if run.status != "running" or str(run.lease_token) != str(token) or run.lease_until <= timezone.now():
        raise Conflict("同步批次已结束或租约过期，请刷新进度。")
    return run


# Function: Save an Agent observation event and refresh the batch lease.
# Inputs: `run_id` and `token` are batch credentials, `stage` is the stage, and `data` contains message_ids or gmail_message_id/error.
# Outputs: None; updates per-message records and batch lease.
# Logic: Register discovery events idempotently; failures update one message only and retain only code and safe guidance.
# Constraints: Events must come from this batch; old events cannot reopen a completed job.
@transaction.atomic
def record_event(run_id, token, stage, data):
    run = require_run(run_id, token)
    run.lease_until = timezone.now() + timedelta(seconds=RUN_LEASE_SECONDS)
    run.save(update_fields=["lease_until"])
    if stage == "discovered":
        for message_id in data["message_ids"]:
            EmailProcessingJob.objects.get_or_create(run=run, gmail_message_id=message_id, defaults={"dedupe_key": f"{run.mailbox.address.casefold()}:{message_id}"})
        return
    item = run.email_jobs.select_for_update().get(gmail_message_id=data["gmail_message_id"])
    if item.status == "completed":
        return
    if stage not in {"fetching", "extracting", "persisting", "completed", "failed"}:
        raise InvalidState("未知邮件处理阶段。")
    item.stage = stage
    item.status = stage if stage in {"completed", "failed"} else "running"
    if stage == "fetching":
        item.started_at, item.attempt = timezone.now(), item.attempt + 1
    if stage in {"completed", "failed"}:
        item.finished_at = timezone.now()
        item.company_id = Email.objects.filter(pk=item.dedupe_key, mailbox=run.mailbox).values_list("company_id", flat=True).first()
    if stage == "failed":
        item.error = {"code": data.get("code", "email_processing_failed"), "stage": data.get("stage", "processing"), "message": "单封邮件处理失败，可查看阶段并明确重试。"}
        logger.warning("email_processing_failed run_id=%s job_id=%s stage=%s code=%s", run.pk, item.pk, item.error["stage"], item.error["code"])
    item.save()


# Function: Complete a mailbox batch and save refreshed credentials and safe statistics.
# Inputs: `run_id` and `token` are execution credentials, `result` is the Agent result, and `authorization` is an optional refreshed credential.
# Outputs: Terminal batch representation.
# Logic: Gmail or QQ per-message failure produces partial or failed; unfinished jobs and sources fail explicitly; only Gmail can submit refreshed credentials.
# Constraints: Does not retry automatically; batch completion does not claim every company profile is complete, which run_data queries separately.
@transaction.atomic
def finish_run(run_id, token, result, authorization=None):
    candidate = MailboxSyncRun.objects.select_related("mailbox").get(pk=run_id)
    mailbox = mailbox_for(candidate.mailbox.owner, candidate.mailbox_id, lock=True)
    run = require_run(run_id, token)
    gmail_connected = GmailCredential.objects.filter(mailbox=mailbox).exists()
    if not (gmail_connected or QQCredential.objects.filter(mailbox=mailbox).exists()):
        raise InvalidState("该邮箱授权已被移除。")
    if authorization:
        if not gmail_connected:
            raise InvalidState("QQ 批次不能保存 Google 刷新凭证。")
        GmailCredential.objects.filter(mailbox=mailbox).update(credentials=authorization, updated_at=timezone.now())
    unfinished = run.email_jobs.filter(status__in=["pending", "running"])
    from .durable_models import StoredMessage
    StoredMessage.objects.filter(mailbox=mailbox, message_id__in=unfinished.values("gmail_message_id")).update(status="failed")
    unfinished.update(status="failed", stage="failed", finished_at=timezone.now(), error={"code": "batch_interrupted", "stage": "processing", "message": "批次未完成本邮件，请明确重试。"})
    counts = dict(run.email_jobs.values("status").annotate(n=Count("id")).values_list("status", "n"))
    failed = counts.get("failed", 0) if counts else int(result.get("failed_email_count", 0))
    completed = counts.get("completed", 0)
    run.status = "partial" if failed and completed else "failed" if failed or result.get("status") == "failed" else "completed"
    run.error = {"code": (result.get("error") or {}).get("code", "sync_failed"), "message": "同步未完整完成，请检查逐封任务或 Worker 日志。"} if result.get("status") == "failed" else None
    run.result = {key: result[key] for key in ("created_count", "updated_count", "duplicate_count", "fetched_count", "cursor_saved", "pending_message_count", "failed_email_count", "sync_mode") if key in result}
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "error", "result", "finished_at"])
    mailbox.sync_state = {**mailbox.sync_state, "run_id": str(run.pk), "status": run.status, "finished_at": run.finished_at.isoformat(), "error": run.error["message"] if run.error else None, "last_result": run.result}
    if run.status == "completed":
        mailbox.sync_state["last_synced_at"] = run.finished_at.isoformat()
    mailbox.version += 1
    mailbox.save(update_fields=["sync_state", "version"])
    logger.info("mailbox_run_finished run_id=%s status=%s completed=%s failed=%s", run.pk, run.status, completed, failed)
    return run_data(run)


# Function: Generate overall batch progress independent of frontend pagination.
# Inputs: `run` is an authorized batch.
# Outputs: Message counts, company-analysis counts, per-message errors, times, and frozen synchronization scope.
# Logic: Derive counts from actual jobs; deduplicate companies with business messages in this batch and inspect their current jobs for profiling.
# Constraints: Does not return leases, credentials, or original email bodies; analysis can finish after the mailbox batch.
def run_data(run):
    counts = dict(run.email_jobs.values("status").annotate(n=Count("id")).values_list("status", "n"))
    company_ids = run.email_jobs.filter(company__emails__business_classification="business").exclude(company=None).values_list("company_id", flat=True).distinct()
    analysis = {"pending": 0, "completed": 0, "failed": 0}
    for company_id in company_ids:
        job = Job.objects.filter(company_id=company_id).order_by("-enqueued_at").first()
        if job:
            analysis["pending" if job.status in {"pending", "running"} else "failed" if job.status == "failed" else "completed"] += 1
    errors = [{"gmail_message_id": item.gmail_message_id, **(item.error or {})} for item in run.email_jobs.filter(status="failed").order_by("created_at")]
    return {"run_id": str(run.pk), "mailbox_id": str(run.mailbox_id), "status": run.status, "sync_options": run.sync_options,
            "total_count": sum(counts.values()), "pending_count": counts.get("pending", 0), "running_count": counts.get("running", 0),
            "completed_count": counts.get("completed", 0), "failed_count": counts.get("failed", 0),
            "analysis_pending_count": analysis["pending"], "analysis_completed_count": analysis["completed"], "analysis_failed_count": analysis["failed"],
            "requested_at": run.requested_at.isoformat(), "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None, "error": run.error, "email_errors": errors}


# Function: Create a new batch for explicitly failed messages.
# Inputs: `owner` is the employee and `run_id` is a terminal batch.
# Outputs: New retry batch; rejects absent failures or an active batch.
# Logic: Always limit failed-batch reads to the original employee mailbox; explicit retries reuse original message identifiers and retain history.
# Constraints: Retains old batch audit history and original dedupe_key without overwriting healthy messages.
def retry_run(owner, run_id):
    run = MailboxSyncRun.objects.filter(owner_scope(owner, "mailbox__owner"), pk=run_id).first()
    if run is None:
        raise NotFound("批次不存在。")
    if run.status not in {"failed", "partial"}:
        raise Conflict("只有失败或部分完成批次可重试。")
    ids = list(run.email_jobs.filter(status="failed").values_list("gmail_message_id", flat=True))
    return request_run(owner, run.mailbox_id, ids or run.message_ids, retry_scope=run.sync_options)


# Function: Explicitly mark lease-expired batches failed and release mailbox active occupancy.
# Inputs: `owner` is the employee bound to Worker.
# Outputs: Expired count.
# Logic: Recheck time after locking each mailbox and batch and retain completed records; interrupted jobs and source states fail together to prevent implicit retry on the next synchronization.
# Constraints: Does not retry interrupted work automatically; queued jobs remain unaffected and users can explicitly retry from the progress page.
def expire_runs(owner):
    candidates = MailboxSyncRun.objects.filter(mailbox__owner=owner, status="running", lease_until__lte=timezone.now())
    expired = 0
    for candidate in list(candidates):
        with transaction.atomic():
            mailbox = mailbox_for(owner, candidate.mailbox_id, lock=True)
            run = MailboxSyncRun.objects.select_for_update().get(pk=candidate.pk)
            if run.status != "running" or run.lease_until > timezone.now():
                continue
            run.status, run.finished_at = "failed", timezone.now()
            run.error = {"code": "worker_interrupted", "message": "同步租约过期，请明确重试未完成邮件。"}
            run.save(update_fields=["status", "finished_at", "error"])
            from .durable_models import StoredMessage
            StoredMessage.objects.filter(mailbox=mailbox, message_id__in=run.email_jobs.filter(status__in=["pending", "running"]).values("gmail_message_id")).update(status="failed")
            run.email_jobs.filter(status__in=["pending", "running"]).update(status="failed", stage="failed", finished_at=timezone.now(), error=run.error)
            mailbox.sync_state = {**mailbox.sync_state, "status": "failed", "error": run.error["message"]}
            mailbox.version += 1
            mailbox.save(update_fields=["sync_state", "version"])
            expired += 1
            logger.warning("mailbox_run_expired run_id=%s action=explicit_retry", run.pk)
    return expired
