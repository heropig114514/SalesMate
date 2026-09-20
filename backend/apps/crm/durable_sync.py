"""职责：按用户明确范围驱动 Gmail 去重同步和持久抽取。
实现：默认单批最多五十封，超量须批准；每页二十封，范围内先去重再登记，原文先落库；批量复用抽取、四路 L1 完成即提交。
关联：worker 负责授权和批次结束；Gmail/QQ 共用原文及 L1 页面处理，QQ 显式注入只读读取器和来源。
目录：
- BatchBackend：一页邮件的批量数据库读取适配器。
- BatchBackend.__init__：绑定授权批次、HTTP 写入端和本页缓存。
- BatchBackend.get_stored_email：按既定协议查询本页预取结果。
- BatchBackend.submit_emails：委托原 HTTP 接口保存结果。
- checkpoint：原子登记本批次消息及进度。
- save_raw：持久化不可变邮件原文。
- observe：持久化逐封事件与缓存处理终态。
- process_page：读取未缓存原文并完成一页并发抽取。
- sync_persisted：执行有界同步或明确失败重试。
变量索引：
- PAGE_SIZE：沿用既定二十封单页处理大小，仅在用户范围内分页。
- logger：不含凭证或正文的同步日志。
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


# 功能：批量读取本页已保存抽取而保留 HTTP 写入边界。
# 逻辑：一次邮件查询和一次抽取预取替代逐封 HTTP GET。
# 约束：只允许访问构造时已授权邮箱；缓存生命周期为单页。
class BatchBackend:
    # 功能：绑定本页已授权数据。
    # 输入：`backend` 为 Agent HTTP 客户端，`run` 为授权批次，`keys` 为本页天然键；`source` 为邮件来源。
    # 输出：初始化实例状态，无外部写入。
    # 逻辑：从同一数据库批量预取最新事实；邮箱关系限定员工范围。
    # 约束：不保存凭证，不跨页或跨员工共享缓存。
    def __init__(self, backend, run, keys, source="gmail_real"):
        self.run = run
        self.source = source
        mailbox = run.mailbox
        self.backend, self.mailbox_id = backend, str(mailbox.pk)
        self.saved = {email.pk: email_data(email) for email in Email.objects.filter(mailbox=mailbox, pk__in=keys).select_related("mailbox").prefetch_related("extractions")}

    # 功能：读取本页的已保存事实。
    # 输入：`mailbox_id` 为请求邮箱，`dedupe_key` 为邮件天然键。
    # 输出：原协议邮件表示或 None。
    # 逻辑：校验邮箱后查询预取字典。
    # 约束：不回退到其他邮箱或网络查询。
    def get_stored_email(self, mailbox_id, dedupe_key):
        if str(mailbox_id) != self.mailbox_id:
            raise Conflict("批量缓存邮箱不匹配。")
        return self.saved.get(dedupe_key)

    # 功能：沿原 HTTP 契约保存单封抽取。
    # 输入：`submissions` 为 Agent 已验证的载荷数组。
    # 输出：后端提交统计。
    # 逻辑：先缓存 L1 输出，再沿 HTTP 验证和去重接口提交；QQ 显式指定 qq_real，Gmail 保留原调用。
    # 约束：缓存写入失败则不提交；提交失败保留成功抽取用于明确重试。
    def submit_emails(self, submissions):
        with transaction.atomic():
            require_run(self.run.pk, self.run.lease_token)
            for item in submissions:
                StoredMessage.objects.filter(mailbox_id=self.mailbox_id, message_id=item["gmail_message_id"]).update(submission=item)
        if self.source == "qq_real":
            return self.backend.submit_emails(submissions, source="qq_real")
        return self.backend.submit_emails(submissions)


# 功能：原子保存本批次选中的消息和逐封进度。
# 输入：`run` 为运行批次，`ids` 为去重后的选中 ID。
# 输出：无；数据库与租约错误传播。
# 逻辑：锁邮箱核验租约，以 SyncCheckpoint 标识 Worker 接管；登记原文占位及任务后保存范围，每页刷新租期。
# 约束：不导入历史 pending，不推进全局 History 游标，不将范围之外的消息带入批次。
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


# 功能：在 LLM 调用前提交原文。
# 输入：`run` 为批次，`record` 为已登记消息，`raw` 为 Gmail 或 QQ 的标准 MIME 解析结果。
# 输出：无，原文独立事务提交。
# 逻辑：核验租约和已有原文一致性；同一提供方消息 ID 的内容不能静默替换。
# 约束：不保存 OAuth 凭证；原文保存失败时不得开始 L1。
@transaction.atomic
def save_raw(run, record, raw):
    require_run(run.pk, run.lease_token)
    current = StoredMessage.objects.select_for_update().get(pk=record.pk, mailbox_id=run.mailbox_id)
    if current.raw and current.raw != raw:
        raise Conflict("已缓存邮件原文不可修改。")
    current.raw = raw
    current.save(update_fields=["raw"])


# 功能：保存逐封阶段和持久原文处理终态。
# 输入：`run` 为批次，`stage` 为 Agent 事件，`data` 为安全阶段数据。
# 输出：无；异常传播给调用方。
# 逻辑：同一事务更新任务和缓存状态，线程回调结束释放数据库连接。
# 约束：失败不会自动重排；无意义的高频正文日志被禁止。
def observe(run, stage, data):
    try:
        with transaction.atomic():
            record_event(run.pk, run.lease_token, stage, data)
            if stage in {"completed", "failed"}:
                StoredMessage.objects.filter(mailbox_id=run.mailbox_id, message_id=data["gmail_message_id"]).update(status=stage, prompt_version=EXTRACT_PROMPT_VERSION)
    finally:
        connections.close_all()


# 功能：处理最多二十封已发现邮件。
# 输入：`run` 为批次，`service` 为已授权客户端，`backend` 为 HTTP 写入端，`records` 为消息数组；`reader` 为可选读取函数，`source` 为来源。
# 输出：无；逐封结果和错误持久保存。
# 逻辑：批量查询终态和 L1 缓存；默认 Gmail、显式 QQ 读取器缓存原文后四路抽取，来源随 HTTP 提交。
# 约束：读取错误隔离到单封；数据库/租约失败传播，禁止伪造持久成功。
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


# 功能：执行有界 Gmail 同步或显式重试。
# 输入：`run` 为已领取批次，`service` 为已授权 SDK，`backend` 为 Agent HTTP 客户端。
# 输出：范围同步汇总；逐封状态由持久任务派生。
# 逻辑：验证默认 50 封或已批准上限；最新 N 封先截断，再排除已保存业务邮件及 completed/failed 原文。
# 约束：不进行全量补采、全局积压排空或自动重新抽取；失败仅通过明确重试，分页异常直接传播。
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
