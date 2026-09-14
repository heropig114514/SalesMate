"""职责：持久同步 QQ 收件箱和已发送邮件。
实现：先枚举内部日期，在冻结窗口内跨文件夹按最新排序限量；只登记选中消息，再复用既有 L1/HTTP 写入。
关联：qq_mail 提供只读 IMAP；qq_models 保存独立检查点；durable_sync 保留原文与抽取缓存。
目录：
- checkpoint：登记消息并推进 QQ 文件夹游标。
- select_messages：在本批次窗口内选择未完成的最近邮件。
- sync_persisted：执行 QQ 有界同步或显式失败重试。
变量索引：
- logger：只记录批次 ID、发现数量和阶段的日志。
"""
import logging
from datetime import datetime

from django.db import transaction

from agent.tools import qq_mail
from agent.workflows.l1_email import EXTRACT_PROMPT_VERSION
from .access import Conflict, mailbox_for
from .durable_sync import PAGE_SIZE, process_page
from .models import QQSyncCheckpoint, StoredMessage
from .processing import record_event, require_run

logger = logging.getLogger("salesmate.qq_sync")


# 功能：保存一页发现结果与文件夹游标。
# 输入：`run` 为已领取批次；`folder` 为 wire 名称；`validity` 为 UID 代次；`uids` 为已发现 UID 数组。
# 输出：已保存 QQSyncCheckpoint。
# 逻辑：锁邮箱核验租约，原子登记 ID 后推进最大 UID，刷新租期。
# 约束：代次变化必须显式报错；最大 UID 仅为登记统计，不作为有界扫描的下界，避免漏掉未选历史邮件。
@transaction.atomic
def checkpoint(run, folder, validity, uids):
    mailbox = mailbox_for(run.mailbox.owner, run.mailbox_id, lock=True)
    require_run(run.pk, run.lease_token)
    state, _ = QQSyncCheckpoint.objects.get_or_create(mailbox=mailbox)
    prior = state.folders.get(folder)
    if prior and prior["uidvalidity"] != validity:
        raise Conflict("QQ 文件夹 UIDVALIDITY 已变化，停止同步以避免重复或错配，请检查邮箱状态。")
    ids = [qq_mail.message_id(folder, validity, uid) for uid in uids]
    for value in ids:
        StoredMessage.objects.get_or_create(mailbox=mailbox, message_id=value)
    state.folders[folder] = {"uidvalidity": validity, "last_uid": max([prior["last_uid"] if prior else 0, *uids])}
    state.save(update_fields=["folders"])
    mailbox.sync_state = {**mailbox.sync_state, "scope": {"provider": "qq", "folders": list(state.folders), "durable_messages": True}}
    mailbox.version += 1
    mailbox.save(update_fields=["sync_state", "version"])
    record_event(run.pk, run.lease_token, "discovered", {"message_ids": ids})
    logger.info("qq_checkpoint_saved run_id=%s discovered=%s", run.pk, len(ids))
    return state


# 功能：只用元数据选择本次允许处理的邮件。
# 输入：`run` 含排队时冻结的 sync_options；`client` 为已认证 IMAP。
# 输出：按内部日期倒序排列的 (日期, 文件夹, 代次, UID, 消息 ID) 数组。
# 逻辑：两个文件夹按日期粗筛并精确核验窗口，排除已完成当前提示词及失败记录，然后合计限量。
# 约束：不读取正文；旧 pending 同样受限；每页刷新租期；扩大范围后旧 UID 仍可被选中。
def select_messages(run, client):
    options = run.sync_options
    if not options or not options.get("until") or not (options.get("recent_days") or options.get("max_messages")):
        raise Conflict("此 QQ 批次缺少同步范围，请重新选择最近 N 天或最多 N 封。")
    since = datetime.fromisoformat(options["since"]) if options.get("since") else None
    until = datetime.fromisoformat(options["until"])
    folder_names = qq_mail.folders(client)
    state = QQSyncCheckpoint.objects.filter(mailbox_id=run.mailbox_id).first()
    # 原有文件夹改名不能当作全新文件夹静默重扫，否则历史邮件可能被重复分析。
    if state and set(state.folders) - set(folder_names):
        raise Conflict("QQ 同步文件夹发生变化，请检查已发送文件夹设置。")
    candidates = []
    for folder in folder_names:
        validity = qq_mail.select_folder(client, folder)
        checkpoint(run, folder, validity, [])
        uids = qq_mail.list_uids(client, 0, since=since)
        for offset in range(0, len(uids), PAGE_SIZE):
            dates = qq_mail.message_dates(client, uids[offset:offset + PAGE_SIZE])
            page = [(date, folder, validity, uid, qq_mail.message_id(folder, validity, uid)) for uid, date in dates.items()
                    if date <= until and (since is None or date >= since)]
            stored = {item.message_id: item for item in StoredMessage.objects.filter(mailbox_id=run.mailbox_id, message_id__in=[item[4] for item in page]).only("message_id", "status", "prompt_version")}
            for item in page:
                prior = stored.get(item[4])
                if prior and (prior.status == "failed" or (prior.status == "completed" and prior.prompt_version == EXTRACT_PROMPT_VERSION)):
                    continue
                candidates.append(item)
            # 有封数限制时只保留全局最新候选，内存规模不随历史邮件总数累积。
            candidates.sort(reverse=True)
            if options.get("max_messages"):
                candidates = candidates[:options["max_messages"]]
            record_event(run.pk, run.lease_token, "discovered", {"message_ids": []})
    logger.info("qq_scope_selected run_id=%s recent_days=%s max_messages=%s selected=%s", run.pk, options.get("recent_days"), options.get("max_messages"), len(candidates))
    return candidates


# 功能：执行一个有界 QQ 持久批次。
# 输入：`run` 为员工批次；`client` 为该邮箱已认证 IMAP；`backend` 为员工绑定的 HTTP 写入端。
# 输出：批次汇总，逐封结果由共享任务表保存。
# 逻辑：普通同步先冻结选中 ID 并全部登记，再每二十封处理；显式重试仅处理原失败 ID。
# 约束：不排空范围外 pending，不自动重试 failed；超过数量的邮件保留给下次手动同步，不改变模型参数。
def sync_persisted(run, client, backend):
    if run.message_ids:
        ids = run.message_ids
        mode = "explicit_retry"
    else:
        selected = select_messages(run, client)
        # 登记整个已选范围先于正文处理，中断时未开始的选中邮件也能明确重试。
        with transaction.atomic():
            mailbox_for(run.mailbox.owner, run.mailbox_id, lock=True)
            require_run(run.pk, run.lease_token)
            for folder in dict.fromkeys(item[1] for item in selected):
                entries = [item for item in selected if item[1] == folder]
                checkpoint(run, folder, entries[0][2], [item[3] for item in entries])
        ids = [item[4] for item in selected]
        mode = "bounded"
    for offset in range(0, len(ids), PAGE_SIZE):
        page = ids[offset:offset + PAGE_SIZE]
        records = {item.message_id: item for item in StoredMessage.objects.filter(mailbox_id=run.mailbox_id, message_id__in=page)}
        if len(records) != len(set(page)):
            raise Conflict("QQ 处理范围缺少持久消息记录。")
        process_page(run, client, backend, [records[value] for value in page], reader=qq_mail.read_email, source="qq_real")
    return {"status": "completed", "sync_mode": mode, "cursor_saved": True}
