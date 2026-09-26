"""Responsibility: Send confirmed emails through the fixed QQ SMTP TLS service and verify sent copies.
Implementation: Submit DATA only when QQ capabilities are enabled and every recipient is accepted. Distinguish explicit rejection from unknown outcome; never resend automatically.
Relationships: qq_connection validates accounts; actions supplies frozen snapshots; agent.tools.qq_mail is used only for read-only sent-folder queries.
Directory:
- QQSMTPError: Carry controlled failure stages and uncertainty.
- QQSMTPError.__init__: Initialize a safe, persistable error.
- validate_credentials: Check QQ address and authorization-code format.
- connect: Validate TLS and SMTP login without sending email.
- close: Close the session without overwriting the sending outcome.
- send: Send frozen recipients, subject, and body.
- verify_sent: Read-only verification of a unique sent copy.
Variable index:
- SMTP_HOST: Fixed smtp.qq.com host; requests cannot select servers.
- SMTP_PORT: Fixed TLS port 465.
- TIMEOUT: Maximum network wait of 30 seconds per operation.
- logger: Log only stage, error type, and action ID.
"""
import logging
import re
import smtplib
import ssl
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import format_datetime, getaddresses

from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from agent.tools import qq_mail
from apps.crm.access import InvalidState
from common.mail_features import require_qq_enabled

SMTP_HOST = "smtp.qq.com"
SMTP_PORT = 465
TIMEOUT = 30
logger = logging.getLogger("salesmate.qq_smtp")


# Function: Represent SMTP failures safe for display.
# Logic: uncertain is true only when body submission cannot be confirmed.
# Constraints: Do not retain raw server text, recipients, or authorization codes.
class QQSMTPError(RuntimeError):
    # Function: Fix the business meaning of the error.
    # Inputs: `stage`: failure stage; `uncertain`: whether the body may have been accepted.
    # Outputs: Initialized error instance.
    # Logic: Construct a controlled description consumed by the action state machine and connection endpoint.
    # Constraints: Do not accept external error bodies.
    def __init__(self, stage, uncertain=False):
        self.stage, self.uncertain = stage, uncertain
        super().__init__("QQ 发送结果未知，请核对已发送邮件，勿直接重发。" if uncertain else f"QQ SMTP 在 {stage} 阶段失败，请检查授权、收件地址或邮箱服务状态。")


# Function: Validate the fixed service's account and authorization code.
# Inputs: `address`: complete QQ/foxmail address; `code`: client authorization code.
# Outputs: None; invalid input raises InvalidState.
# Logic: Check QQ capabilities and require ASCII addresses and 16 letters to prevent protocol injection.
# Constraints: Valid format does not imply server authentication.
@sensitive_variables("code")
def validate_credentials(address, code):
    require_qq_enabled("smtp_credentials")
    if not isinstance(address, str) or not re.fullmatch(r"[A-Za-z0-9_.+-]+@(qq|foxmail)\.com", address, re.IGNORECASE):
        raise InvalidState("请输入完整的 QQ 或 foxmail 邮箱地址。")
    if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z]{16}", code):
        raise InvalidState("请输入 QQ 客户端的 16 位授权码。")


# Function: Establish an authentication-only SMTP session.
# Inputs: `address`: sender account; `code`: authorization code.
# Outputs: Authenticated SMTP_SSL; failures raise controlled QQSMTPError.
# Logic: Use a fixed host, certificate verification, and timeout; authenticate after successful EHLO.
# Constraints: Never call MAIL, RCPT, or DATA; close transport on connection failure without retries.
@sensitive_variables("code", "client")
def connect(address, code):
    validate_credentials(address, code)
    client = None
    try:
        client = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT, context=ssl.create_default_context())
        if client.ehlo()[0] != 250:
            raise QQSMTPError("hello")
        client.login(address, code)
        return client
    except (OSError, smtplib.SMTPException, QQSMTPError) as error:
        logger.warning("qq_smtp_connect_failed error_type=%s", type(error).__name__)
        if client is not None:
            close(client)
        raise QQSMTPError("authentication") from None


# Function: Release the SMTP connection while preserving confirmed submission results.
# Inputs: `client`: created SMTP object.
# Outputs: None; cleanup errors log types only.
# Logic: Close transport after QUIT; failures never resend or invalidate already accepted DATA.
# Constraints: Do not log raw server error text.
def close(client):
    try:
        client.quit()
    except (OSError, smtplib.SMTPException) as error:
        logger.warning("qq_smtp_quit_failed error_type=%s", type(error).__name__)
    finally:
        try:
            client.close()
        except OSError as error:
            logger.warning("qq_smtp_close_failed error_type=%s", type(error).__name__)


# Function: Execute one approved QQ send.
# Inputs: `action`: frozen sender, recipients, subject, and body; `credentials`: decrypted authorization code.
# Outputs: Stable Message-ID and smtp_accepted state, which does not imply final inbox delivery.
# Logic: UTF-8 MIME uses SMTP CRLF and base64 bodies; submit DATA only after all RCPT responses accept, and succeed only on final 250.
# Constraints: If any recipient rejects, do not send the body; DATA interruption is uncertain. No automatic sent-copy append or retry.
@sensitive_variables("credentials", "client")
def send(action, credentials):
    data = action.parameters
    message = EmailMessage(policy=policy.SMTP)
    message["From"], message["To"], message["Subject"] = data["account"], ", ".join(data["to"]), data["subject"]
    message["Message-ID"] = f"<{action.pk}@salesmate.local>"
    message["Date"] = format_datetime(action.approved_at or timezone.now())
    message.set_content(data["body"], charset="utf-8", cte="base64")
    raw = message.as_bytes()
    client = connect(data["account"], credentials["authorization_code"])
    stage = "sender"
    try:
        if client.mail(data["account"])[0] != 250:
            raise QQSMTPError(stage)
        stage = "recipients"
        for address in data["to"]:
            if client.rcpt(address)[0] not in (250, 251):
                raise QQSMTPError(stage)
        stage = "data"
        code, _ = client.data(raw)
        if code != 250:
            raise QQSMTPError(stage, uncertain=not 400 <= code <= 599)
        logger.info("qq_smtp_accepted action_id=%s", action.pk)
        return {"message_id": str(message["Message-ID"]), "submission_status": "smtp_accepted"}
    except QQSMTPError:
        raise
    except smtplib.SMTPDataError as error:
        raise QQSMTPError(stage, uncertain=not 400 <= error.smtp_code <= 599) from None
    except (OSError, smtplib.SMTPException) as error:
        logger.warning("qq_smtp_send_failed action_id=%s stage=%s error_type=%s", action.pk, stage, type(error).__name__)
        raise QQSMTPError(stage, uncertain=stage == "data") from None
    finally:
        close(client)


# Function: Read-only verification of a unique sent copy for an uncertain QQ send.
# Inputs: `action`: frozen action; `credentials`: independent sending-connection authorization code.
# Outputs: Confirmed copy's Message-ID and persistent QQ identifier.
# Logic: Query the IMAP sent folder by stable Message-ID and recheck UID, header identifiers, sender, recipients, and subject.
# Constraints: Absence does not prove the email was unsent; no body reads, APPEND, or resending. The account must enable IMAP and retain sent copies.
@sensitive_variables("credentials", "client")
def verify_sent(action, credentials):
    client = qq_mail.connect(action.parameters["account"], credentials["authorization_code"])
    try:
        folder = qq_mail.folders(client)[1]
        validity = qq_mail.select_folder(client, folder)
        message_id = f"<{action.pk}@salesmate.local>"
        status, rows = client.uid("search", None, "HEADER", "Message-ID", f'"{message_id}"')
        uids = rows[0].split() if status == "OK" and rows and isinstance(rows[0], bytes) else []
        if len(uids) != 1 or not uids[0].isdigit() or int(uids[0]) < 1:
            raise InvalidState("QQ 已发送目录中未找到唯一副本，结果仍未知；请人工核对，不要直接重发。")
        uid = int(uids[0])
        status, rows = client.uid("fetch", str(uid), "(UID BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM TO SUBJECT)])")
        parts = [row for row in rows or [] if isinstance(row, tuple)]
        if status != "OK" or len(parts) != 1 or not re.search(rb"\bUID " + str(uid).encode() + rb"\b", parts[0][0]):
            raise InvalidState("QQ 发送副本标识无法确认，结果仍未知。")
        headers = message_from_bytes(parts[0][1], policy=policy.default)
        if (headers.get("Message-ID") != message_id or str(headers.get("Subject", "")) != action.parameters["subject"]
                or [address for _, address in getaddresses(headers.get_all("From", []))] != [action.parameters["account"]]
                or sorted(address for _, address in getaddresses(headers.get_all("To", []))) != sorted(action.parameters["to"])):
            raise InvalidState("QQ 发送副本与已确认内容不匹配，结果仍未知。")
        return {"message_id": message_id, "sent_copy_id": qq_mail.message_id(folder, validity, uid), "submission_status": "confirmed_in_sent"}
    finally:
        qq_mail.disconnect(client)
