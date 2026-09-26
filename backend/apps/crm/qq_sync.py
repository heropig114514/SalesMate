"""Responsibility: Persistently synchronize QQ inbox and sent mail.
Implementation: Enumerate internal dates first, then limit messages by newest across folders in the frozen window; record selected messages only and reuse existing L1/HTTP writes.
Relationships: qq_mail supplies read-only IMAP, qq_models stores an independent checkpoint, and durable_sync retains source and extraction cache.
Directory:
- checkpoint: Record messages and advance the QQ folder cursor.
- select_messages: Select unfinished recent messages within this batch window.
- sync_persisted: Execute bounded QQ synchronization or explicit failure retry.
Variable index:
- logger: Logs only batch ID, discovery count, and stage.
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


# Function: Save one page of discoveries and a folder cursor.
# Inputs: `run` is a claimed batch; `folder` is its wire name; `validity` is the UID generation; and `uids` is an array of discovered UIDs.
# Outputs: Saved QQSyncCheckpoint.
# Logic: Lock the mailbox and validate lease, atomically record IDs then advance maximum UID and refresh lease.
# Constraints: Generation changes must explicitly fail; maximum UID is registration statistics only, never a lower bound for bounded scanning, to avoid missing unselected historical emails.
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


# Function: Use metadata only to select messages permitted for this run.
# Inputs: `run` contains sync_options frozen at queue time and `client` is authenticated IMAP.
# Outputs: Array of (date, folder, generation, UID, message ID) ordered by descending internal date.
# Logic: Coarsely filter both folders by date and precisely validate the window, exclude records completed for the current prompt and failures, then apply the total limit.
# Constraints: Does not read bodies; legacy pending records remain limited; refreshes leases per page; old UIDs can still be selected after scope expansion.
def select_messages(run, client):
    options = run.sync_options
    if not options or not options.get("until") or not (options.get("recent_days") or options.get("max_messages")):
        raise Conflict("此 QQ 批次缺少同步范围，请重新选择最近 N 天或最多 N 封。")
    since = datetime.fromisoformat(options["since"]) if options.get("since") else None
    until = datetime.fromisoformat(options["until"])
    folder_names = qq_mail.folders(client)
    state = QQSyncCheckpoint.objects.filter(mailbox_id=run.mailbox_id).first()
    # Treating a renamed existing folder as new would silently rescan it and may analyze historical mail twice.
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
            # With a message-count limit, retain only globally newest candidates so memory does not grow with all historical mail.
            candidates.sort(reverse=True)
            if options.get("max_messages"):
                candidates = candidates[:options["max_messages"]]
            record_event(run.pk, run.lease_token, "discovered", {"message_ids": []})
    logger.info("qq_scope_selected run_id=%s recent_days=%s max_messages=%s selected=%s", run.pk, options.get("recent_days"), options.get("max_messages"), len(candidates))
    return candidates


# Function: Execute a bounded durable QQ batch.
# Inputs: `run` is an employee batch, `client` is authenticated IMAP for that mailbox, and `backend` is the employee-bound HTTP writer.
# Outputs: Batch summary; the shared job table stores per-message results.
# Logic: Ordinary synchronization freezes and fully records selected IDs before processing every twenty messages; explicit retries process original failed IDs only.
# Constraints: Does not drain pending work outside scope or retry failed work automatically; messages above the limit await the next manual synchronization and model parameters remain unchanged.
def sync_persisted(run, client, backend):
    if run.message_ids:
        ids = run.message_ids
        mode = "explicit_retry"
    else:
        selected = select_messages(run, client)
        # Record the full selected scope before body processing so selected but unstarted messages can be explicitly retried after interruption.
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
