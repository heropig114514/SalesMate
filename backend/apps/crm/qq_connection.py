"""Responsibility: Validate, encrypt and store, and remove the current employee's QQ mailbox connection.
Implementation: A capability switch controls connection and decryption; fixed IMAP checks validate login and folders, and the mailbox lock protects active synchronization relationships.
Relationships: qq_views provides the Session interface, qq_sync and worker consume credentials, sync_scope freezes this scope, and the explicit sales encryptor is reused.
Directory:
- connect_mailbox: Save verified authorization and request the initial synchronization.
- authorization_code: Decrypt a stored authorization code.
- disconnect_mailbox: Remove an inactive QQ connection while preserving history.
Variable index:
- logger: Records only mailbox ID and connection operation.
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


# Function: Connect the current employee's QQ mailbox and queue its initial synchronization.
# Inputs: `owner` is the current session employee; `address` is the validated address; `code` is an authorization code; and `sync_options` limits this run.
# Outputs: An authorized Mailbox; network or configuration failure does not create a connection.
# Logic: Check QQ capability, validate scope and IMAP login/folders, then lock the mailbox to save and queue.
# Constraints: Credentials cannot change during an active batch, Gmail connections cannot be overwritten, and network errors expose only safe guidance.
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


# Function: Obtain the QQ authorization code needed by a running batch.
# Inputs: `credential` is a QQCredential already validated through its mailbox owner.
# Outputs: Plaintext authorization code for this in-memory IMAP login only.
# Logic: Check QQ capability first, then decrypt with authentication through the explicit vault key.
# Constraints: Raise for absent or mismatched keys; do not generate a new key or log ciphertext or plaintext.
@sensitive_variables()
def authorization_code(credential):
    require_qq_enabled("read_authorization")
    try:
        return vault().decrypt(credential.encrypted_code.encode("ascii")).decode("ascii")
    except (InvalidToken, ValueError, UnicodeError):
        raise InvalidState("QQ 授权码无法解密，请检查 SALESMATE_VAULT_KEY 或重新连接邮箱。") from None


# Function: Remove an employee's local QQ connection.
# Inputs: `owner` is the current employee and `mailbox_id` is the target mailbox.
# Outputs: Updated mailbox instance.
# Logic: Reject active batches while holding the mailbox lock, delete QQ ciphertext, and update display state.
# Constraints: Retains historical emails, checkpoints, and profiles; rejects removal of Gmail authorization through this interface.
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
