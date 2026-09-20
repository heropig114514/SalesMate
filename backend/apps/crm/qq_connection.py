"""职责：验证、加密保存及移除当前员工的 QQ 邮箱连接。
实现：能力开关控制连接和解密；固定 IMAP 验证登录及文件夹，邮箱锁保护活动同步关系。
关联：qq_views 提供 Session 接口，qq_sync/worker 消费凭证，sync_scope 冻结本次范围，复用 sales 的显式加密器。
目录：
- connect_mailbox：验证授权后保存并请求首次同步。
- authorization_code：解密已保存授权码。
- disconnect_mailbox：移除非活动 QQ 连接并保留历史。
变量索引：
- logger：只记录邮箱 ID 与连接操作。
"""
import logging

from cryptography.fernet import InvalidToken
from django.db import transaction
from django.views.decorators.debug import sensitive_variables

from agent.tools import qq_mail
from apps.sales.integrations import vault
from common.mail_features import require_qq_enabled
from .access import Conflict, InvalidState, mailbox_for
from .models import GmailCredential, Mailbox, QQCredential
from .sync_scope import snapshot

logger = logging.getLogger("salesmate.qq_connection")


# 功能：连接当前员工的 QQ 邮箱并排队首次同步。
# 输入：`owner` 为当前会话员工；`address` 为验证后地址；`code` 为授权码；`sync_options` 为本次限制。
# 输出：已授权 Mailbox；网络或配置失败不创建连接。
# 逻辑：先检查 QQ 能力，再校验范围及 IMAP 登录/文件夹，最后锁邮箱保存和排队。
# 约束：活动批次中不可换凭证；Gmail 连接不能被覆盖；网络错误只输出安全说明。
@sensitive_variables("code", "cipher", "client")
def connect_mailbox(owner, address, code, sync_options):
    require_qq_enabled("connect_mailbox")
    scope = snapshot(sync_options)
    cipher = vault()
    try:
        client = qq_mail.connect(address, code)
        try:
            for folder in qq_mail.folders(client):
                qq_mail.select_folder(client, folder)
        finally:
            qq_mail.disconnect(client)
    except qq_mail.QQMailError as error:
        raise InvalidState(str(error)) from None
    except (OSError, qq_mail.imaplib.IMAP4.error, UnicodeError) as error:
        logger.warning("qq_connection_check_failed error_type=%s action=check_imap_service", type(error).__name__)
        raise InvalidState("QQ 邮箱连接检查失败，请确认 IMAP 已开启及网络可用。") from None
    with transaction.atomic():
        mailbox, _ = Mailbox.objects.get_or_create(owner=owner, address=address)
        mailbox = mailbox_for(owner, mailbox.pk, lock=True)
        if GmailCredential.objects.filter(mailbox=mailbox).exists():
            raise Conflict("该邮箱已有 Gmail 授权，不能覆盖为 QQ 连接。")
        if mailbox.sync_runs.filter(status__in=["queued", "running"]).exists():
            raise Conflict("邮箱正在同步，请等待批次结束后重新连接。")
        QQCredential.objects.update_or_create(mailbox=mailbox, defaults={"encrypted_code": cipher.encrypt(code.encode("ascii")).decode("ascii")})
        from .processing import request_run
        request_run(owner, mailbox.pk, retry_scope=scope)
        mailbox.refresh_from_db()
        logger.info("qq_mailbox_connected mailbox_id=%s", mailbox.pk)
        return mailbox


# 功能：获取运行批次需要的 QQ 授权码。
# 输入：`credential` 为已通过邮箱 owner 校验的 QQCredential。
# 输出：明文授权码，仅用于本次内存内 IMAP 登录。
# 逻辑：先检查 QQ 能力，再通过显式 vault 密钥认证解密。
# 约束：缺失/不匹配密钥时报错，不生成新密钥、不记录密文或明文。
@sensitive_variables()
def authorization_code(credential):
    require_qq_enabled("read_authorization")
    try:
        return vault().decrypt(credential.encrypted_code.encode("ascii")).decode("ascii")
    except (InvalidToken, ValueError, UnicodeError):
        raise InvalidState("QQ 授权码无法解密，请检查 SALESMATE_VAULT_KEY 或重新连接邮箱。") from None


# 功能：移除员工 QQ 本地连接。
# 输入：`owner` 为当前员工；`mailbox_id` 为目标邮箱。
# 输出：更新后的邮箱实例。
# 逻辑：邮箱锁内拒绝活动批次，删除 QQ 密文并更新显示状态。
# 约束：保留历史邮件、检查点与画像；拒绝用此接口移除 Gmail 授权。
@transaction.atomic
def disconnect_mailbox(owner, mailbox_id):
    mailbox = mailbox_for(owner, mailbox_id, lock=True)
    if not QQCredential.objects.filter(mailbox=mailbox).exists():
        raise InvalidState("该邮箱没有 QQ 连接。")
    if mailbox.sync_runs.filter(status__in=["queued", "running"]).exists():
        raise Conflict("邮箱正在同步，请等待批次结束后移除连接。")
    QQCredential.objects.filter(mailbox=mailbox).delete()
    mailbox.sync_state = {**mailbox.sync_state, "status": "authorization_required", "error": None}
    mailbox.version += 1
    mailbox.save(update_fields=["sync_state", "version"])
    logger.info("qq_mailbox_disconnected mailbox_id=%s", mailbox.pk)
    return mailbox
