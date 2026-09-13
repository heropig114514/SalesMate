"""职责：管理邮箱批次、逐封进度和恢复所需的持久状态。
实现：邮箱行锁串行化请求，批次租约拒绝旧执行者，计数从任务查询派生。
关联：gmail_oauth 兼容原同步接口，worker 消费批次，processing_views 提供进度和显式重试。
目录：
- request_run：创建或复用邮箱活动批次。
- claim_run：领取一个已排队批次。
- require_run：核验当前执行租约。
- record_event：幂等更新逐封处理阶段。
- finish_run：完成同步并保存安全汇总。
- run_data：生成邮箱及公司画像整体进度。
- retry_run：明确重试一个失败批次的邮件。
- expire_runs：将中断租约标为失败，保留逐封恢复依据。
变量索引：
- logger：批次状态转换诊断日志。
- RUN_LEASE_SECONDS：邮箱批次租期 600 秒，每次进度事件续期。
"""
from datetime import timedelta
import logging
import uuid

from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from rest_framework.exceptions import NotFound

from .access import Conflict, InvalidState, mailbox_for
from .models import Email, EmailProcessingJob, GmailCredential, Job, MailboxSyncRun

logger = logging.getLogger("salesmate.processing")
RUN_LEASE_SECONDS = 600


# 功能：把同步请求保存为独立批次。
# 输入：`owner` 为员工，`mailbox_id` 为邮箱，`message_ids` 为可选明确范围。
# 输出：新建或复用的 MailboxSyncRun。
# 逻辑：邮箱锁内查询活动批次；普通重复点击合并，显式范围与活动批次冲突时拒绝。
# 约束：必须已授权；不启动线程、不访问 Gmail，排队可跨 Web 重启保留。
@transaction.atomic
def request_run(owner, mailbox_id, message_ids=None):
    mailbox = mailbox_for(owner, mailbox_id, lock=True)
    if not GmailCredential.objects.filter(mailbox=mailbox).exists():
        raise InvalidState("该邮箱尚未完成 Google 授权。")
    active = mailbox.sync_runs.filter(status__in=["queued", "running"]).first()
    if active:
        if message_ids:
            raise Conflict("该邮箱仍有同步批次，请完成后再确认重新抽取或重试。")
        return active
    run = MailboxSyncRun.objects.create(mailbox=mailbox, message_ids=message_ids or [])
    mailbox.sync_state = {**mailbox.sync_state, "status": "sync_requested", "run_id": str(run.pk), "requested_at": run.requested_at.isoformat(), "error": None}
    mailbox.version += 1
    mailbox.save(update_fields=["sync_state", "version"])
    logger.info("mailbox_run_queued run_id=%s mailbox_id=%s explicit_messages=%s", run.pk, mailbox.pk, len(run.message_ids))
    return run


# 功能：原子领取当前员工的一个批次。
# 输入：`owner` 为服务凭证关联员工。
# 输出：含邮箱的运行批次，队列为空返回 None。
# 逻辑：锁邮箱再锁批次，只有 queued 可领取，生成租约凭证。
# 约束：不自动重试失败或过期批次；避免与请求路径反向加锁。
@transaction.atomic
def claim_run(owner):
    candidate = MailboxSyncRun.objects.filter(mailbox__owner=owner, status="queued").order_by("requested_at").first()
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


# 功能：核验逐封事件对应的当前执行者。
# 输入：`run_id` 为批次 UUID，`token` 为领取凭证。
# 输出：锁定的运行批次；过期或旧执行者抛 Conflict。
# 逻辑：状态、凭证和截止时间同时满足才允许写入。
# 约束：调用者必须处于事务中，凭证不进入错误或日志。
def require_run(run_id, token):
    run = MailboxSyncRun.objects.select_for_update(of=("self",)).select_related("mailbox").get(pk=run_id)
    if run.status != "running" or str(run.lease_token) != str(token) or run.lease_until <= timezone.now():
        raise Conflict("同步批次已结束或租约过期，请刷新进度。")
    return run


# 功能：保存 Agent 观察事件并刷新批次租约。
# 输入：`run_id`、`token` 为批次凭证，`stage` 为阶段，`data` 含 message_ids 或 gmail_message_id/error。
# 输出：无；逐封记录及批次租期更新。
# 逻辑：发现事件幂等登记；失败仅更新单封，错误只保存代码和安全说明。
# 约束：事件必须来自本批次；完成后旧事件不得重新打开任务。
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


# 功能：完成邮箱批次并保存刷新凭证和安全统计。
# 输入：`run_id`、`token` 为执行凭证，`result` 为 Agent 结果，`authorization` 为可选刷新凭证。
# 输出：最终批次表示。
# 逻辑：单封失败产生 partial/failed，未完成记录显式失败；凭证只保存到 GmailCredential。
# 约束：不自动重试；批次完成不宣称所有公司画像完成，后者由 run_data 查询。
@transaction.atomic
def finish_run(run_id, token, result, authorization=None):
    candidate = MailboxSyncRun.objects.select_related("mailbox").get(pk=run_id)
    mailbox = mailbox_for(candidate.mailbox.owner, candidate.mailbox_id, lock=True)
    run = require_run(run_id, token)
    if not GmailCredential.objects.filter(mailbox=mailbox).exists():
        raise InvalidState("该邮箱授权已被移除。")
    if authorization:
        GmailCredential.objects.filter(mailbox=mailbox).update(credentials=authorization, updated_at=timezone.now())
    unfinished = run.email_jobs.filter(status__in=["pending", "running"])
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


# 功能：生成不受前端分页影响的批次整体进度。
# 输入：`run` 为已授权批次。
# 输出：邮件计数、公司分析计数、逐封错误和时间。
# 逻辑：计数来自实际任务；画像按本批次关联的业务邮件公司去重并检查当前任务。
# 约束：不返回租约、凭证或原始邮件正文；分析可晚于邮箱批次完成。
def run_data(run):
    counts = dict(run.email_jobs.values("status").annotate(n=Count("id")).values_list("status", "n"))
    company_ids = run.email_jobs.filter(company__emails__business_classification="business").exclude(company=None).values_list("company_id", flat=True).distinct()
    analysis = {"pending": 0, "completed": 0, "failed": 0}
    for company_id in company_ids:
        job = Job.objects.filter(company_id=company_id).order_by("-enqueued_at").first()
        if job:
            analysis["pending" if job.status in {"pending", "running"} else "failed" if job.status == "failed" else "completed"] += 1
    errors = [{"gmail_message_id": item.gmail_message_id, **(item.error or {})} for item in run.email_jobs.filter(status="failed").order_by("created_at")]
    return {"run_id": str(run.pk), "mailbox_id": str(run.mailbox_id), "status": run.status,
            "total_count": sum(counts.values()), "pending_count": counts.get("pending", 0), "running_count": counts.get("running", 0),
            "completed_count": counts.get("completed", 0), "failed_count": counts.get("failed", 0),
            "analysis_pending_count": analysis["pending"], "analysis_completed_count": analysis["completed"], "analysis_failed_count": analysis["failed"],
            "requested_at": run.requested_at.isoformat(), "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None, "error": run.error, "email_errors": errors}


# 功能：为明确失败的邮件建立新批次。
# 输入：`owner` 为员工，`run_id` 为终态批次。
# 输出：新重试批次；无失败或仍活动时拒绝。
# 逻辑：只重试失败邮件，尚未登记邮件的批次错误重跑原范围。
# 约束：保留旧批次审计历史和原 dedupe_key，不覆盖正常邮件。
def retry_run(owner, run_id):
    run = MailboxSyncRun.objects.filter(pk=run_id, mailbox__owner=owner).first()
    if run is None:
        raise NotFound("批次不存在。")
    if run.status not in {"failed", "partial"}:
        raise Conflict("只有失败或部分完成批次可重试。")
    ids = list(run.email_jobs.filter(status="failed").values_list("gmail_message_id", flat=True))
    return request_run(owner, run.mailbox_id, ids or run.message_ids)


# 功能：显式标记租约过期批次，解除邮箱活动占用。
# 输入：`owner` 为 Worker 绑定员工。
# 输出：过期数量。
# 逻辑：逐个锁邮箱和批次后复查时间，保留已完成记录，其他记录标为失败。
# 约束：不自动重试中断工作；queued 任务不受影响，用户可从进度页明确重试。
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
            run.email_jobs.filter(status__in=["pending", "running"]).update(status="failed", stage="failed", finished_at=timezone.now(), error=run.error)
            mailbox.sync_state = {**mailbox.sync_state, "status": "failed", "error": run.error["message"]}
            mailbox.version += 1
            mailbox.save(update_fields=["sync_state", "version"])
            expired += 1
            logger.warning("mailbox_run_expired run_id=%s action=explicit_retry", run.pk)
    return expired
