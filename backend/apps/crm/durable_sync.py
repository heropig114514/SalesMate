"""职责：以数据库检查点驱动 Gmail 完整历史补采和增量抽取。
实现：每页二十封，先登记 ID 再推进游标，原文先落库；批量复用抽取、四路 L1 完成即提交。
关联：worker 负责授权和批次结束；Gmail/QQ 共用原文及 L1 页面处理，QQ 显式注入只读读取器和来源。
目录：
- BatchBackend：一页邮件的批量数据库读取适配器。
- BatchBackend.__init__：绑定授权批次、HTTP 写入端和本页缓存。
- BatchBackend.get_stored_email：按既定协议查询本页预取结果。
- BatchBackend.submit_emails：委托原 HTTP 接口保存结果。
- checkpoint：原子登记消息并推进扫描位置。
- save_raw：持久化不可变邮件原文。
- observe：持久化逐封事件与缓存处理终态。
- process_page：读取未缓存原文并完成一页并发抽取。
- drain_pending：排空已登记而未处理的原文。
- history_page：读取一页历史邮件标识。
- sync_persisted：执行一次完整历史或增量批次。
变量索引：
- PAGE_SIZE：沿用既定二十封单页处理大小，完整历史通过分页完成。
- logger：不含凭证或正文的同步日志。
"""
from functools import partial
import logging

from django.db import connections, transaction

from agent.tools.gmail import GmailHistoryExpiredError, get_profile_history_id, list_history_message_ids, read_email, resolve_mailbox_address
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


# 功能：原子保存发现消息和扫描检查点。
# 输入：`run` 为运行批次，`ids` 为发现 ID，`changes` 为明确检查点字段。
# 输出：更新的 SyncCheckpoint。
# 逻辑：先锁邮箱再核验批次；首次接管保留旧 pending/failed ID；消息与游标同事务提交。
# 约束：游标推进只意味着消息已持久登记，不意味着 L1 已完成；数据库失败整体回滚。
@transaction.atomic
def checkpoint(run, ids=(), **changes):
    mailbox = mailbox_for(run.mailbox.owner, run.mailbox_id, lock=True)
    require_run(run.pk, run.lease_token)
    state, created = SyncCheckpoint.objects.get_or_create(mailbox=mailbox)
    if created:
        legacy = mailbox.sync_state.get("scope", {})
        for key, status in [("pending_message_ids", "pending"), ("failed_message_ids", "failed")]:
            for message_id in legacy.get(key, []):
                StoredMessage.objects.get_or_create(mailbox=mailbox, message_id=message_id, defaults={"status": status})
    for message_id in ids:
        StoredMessage.objects.get_or_create(mailbox=mailbox, message_id=message_id)
    for name, value in changes.items():
        setattr(state, name, value)
    state.save()
    mailbox.sync_state = {**mailbox.sync_state, "cursor": state.cursor or None, "scope": {
        "query": "{in:inbox in:sent}", "backfill_complete": state.backfill_complete,
        "page_token": state.page_token, "durable_messages": True}}
    mailbox.version += 1
    mailbox.save(update_fields=["sync_state", "version"])
    record_event(run.pk, run.lease_token, "discovered", {"message_ids": []})
    logger.info("gmail_checkpoint_saved run_id=%s discovered=%s backfill_complete=%s", run.pk, len(ids), state.backfill_complete)
    return state


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


# 功能：处理数据库中已登记的增量积压。
# 输入：`run` 为批次，`service` 为邮箱客户端，`backend` 为 HTTP 写入端；`reader` 为可选读取器，`source` 为来源。
# 输出：无，直到当前 pending 为空。
# 逻辑：每次重查一页并传递提供方读取器，重启后无需重新扫描已登记 ID；失败等待明确重试。
# 约束：单页大小和 L1 并发保持既定值，不无限自动重试失败。
def drain_pending(run, service, backend, *, reader=None, source="gmail_real"):
    while True:
        records = list(StoredMessage.objects.filter(mailbox_id=run.mailbox_id, status="pending").order_by("pk")[:PAGE_SIZE])
        if not records:
            return
        process_page(run, service, backend, records, reader=reader, source=source)


# 功能：分页读取历史收件及发件 ID。
# 输入：`service` 为 Gmail 客户端，`token` 为已保存下一页标识。
# 输出：ID 数组和下一页标识，空标识表示历史枚举结束。
# 逻辑：沿用 inbox/sent 范围和每页二十封；结构或分页异常直接报错。
# 约束：不含垃圾箱和额外邮箱范围，不把无效分页静默当成完成。
def history_page(service, token):
    arguments = {"userId": "me", "maxResults": PAGE_SIZE, "q": "{in:inbox in:sent}"}
    if token:
        arguments["pageToken"] = token
    response = service.users().messages().list(**arguments).execute()
    if not isinstance(response, dict) or not isinstance(response.get("messages", []), list):
        raise RuntimeError("Gmail 历史列表响应无效。")
    ids = [item.get("id") if isinstance(item, dict) else None for item in response.get("messages", [])]
    next_token = response.get("nextPageToken", "")
    if any(not isinstance(item, str) or not item.strip() for item in ids) or not isinstance(next_token, str) or (next_token and next_token == token):
        raise RuntimeError("Gmail 历史分页信息无效。")
    return list(dict.fromkeys(ids)), next_token


# 功能：执行持久历史补采或增量同步。
# 输入：`run` 为已领取批次，`service` 为已授权 SDK，`backend` 为 Agent HTTP 客户端。
# 输出：批次汇总，逐封最终计数由任务表派生。
# 逻辑：先恢复积压；首次逐页补历史，再从扫描前锚点补增量；后续仅读取 History 新增 ID。
# 约束：明确重试只处理指定 ID；History 404 才重新补历史且保留原文；其他异常强失败。
def sync_persisted(run, service, backend):
    resolved = resolve_mailbox_address(service, run.mailbox.address)
    if resolved.casefold() != run.mailbox.address.casefold():
        raise Conflict("Gmail 授权邮箱与批次不匹配。")
    if run.message_ids:
        checkpoint(run, run.message_ids)
        for offset in range(0, len(run.message_ids), PAGE_SIZE):
            records = list(StoredMessage.objects.filter(mailbox_id=run.mailbox_id, message_id__in=run.message_ids[offset:offset + PAGE_SIZE]))
            process_page(run, service, backend, records)
        return {"status": "completed", "sync_mode": "explicit_retry", "cursor_saved": True}
    state = checkpoint(run)
    StoredMessage.objects.filter(mailbox_id=run.mailbox_id, status="completed").exclude(prompt_version=EXTRACT_PROMPT_VERSION).update(status="pending")
    drain_pending(run, service, backend)
    mode = "incremental" if state.backfill_complete else "historical_backfill"
    recovered = False
    while True:
        if not state.backfill_complete:
            if not state.anchor:
                state = checkpoint(run, anchor=get_profile_history_id(service))
            seen_tokens = set()
            while not state.backfill_complete:
                if state.page_token in seen_tokens:
                    raise RuntimeError("Gmail 历史分页循环。")
                seen_tokens.add(state.page_token)
                ids, token = history_page(service, state.page_token)
                state = checkpoint(run, ids, page_token=token, backfill_complete=not bool(token), cursor=state.anchor if not token else state.cursor)
                drain_pending(run, service, backend)
        try:
            ids, cursor = list_history_message_ids(service, state.cursor)
        except GmailHistoryExpiredError:
            if recovered:
                raise
            recovered, mode = True, "history_recovered"
            logger.warning("gmail_history_expired run_id=%s action=durable_backfill", run.pk)
            state = checkpoint(run, anchor="", page_token="", backfill_complete=False)
            continue
        checkpoint(run, ids, cursor=cursor)
        drain_pending(run, service, backend)
        return {"status": "completed", "sync_mode": mode, "cursor_saved": True,
                "pending_message_count": StoredMessage.objects.filter(mailbox_id=run.mailbox_id, status="pending").count()}
