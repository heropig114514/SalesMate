"""职责：通过固定 QQ SMTP TLS 服务发送已确认的邮件并核对发送副本。
实现：QQ 能力启用且全部收件人获准后提交 DATA；区分明确拒绝与结果未知，不自动重发。
关联：qq_connection 验证账号；actions 提供冻结快照；agent.tools.qq_mail 仅用于只读查询发送目录。
目录：
- QQSMTPError：携带受控失败阶段与不确定性。
- QQSMTPError.__init__：初始化可持久化的安全错误。
- validate_credentials：检查 QQ 地址及授权码格式。
- connect：验证 TLS 和 SMTP 登录，不发送邮件。
- close：关闭会话而不覆盖发送结果。
- send：发送冻结的收件人、主题和正文。
- verify_sent：只读核对唯一已发送副本。
变量索引：
- SMTP_HOST：固定 smtp.qq.com，禁止请求指定服务器。
- SMTP_PORT：固定 TLS 端口 465。
- TIMEOUT：单次网络等待上限 30 秒。
- logger：只记录阶段、错误类型和动作 ID。
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


# 功能：表达可安全展示的 SMTP 失败。
# 逻辑：uncertain 仅在正文提交结果无法确认时为真。
# 约束：不保存服务端原文、收件人或授权码。
class QQSMTPError(RuntimeError):
    # 功能：固定错误的业务含义。
    # 输入：`stage` 为失败阶段；`uncertain` 为是否可能已接受正文。
    # 输出：初始化错误实例。
    # 逻辑：构造受控说明，供动作状态机与连接入口消费。
    # 约束：不接受外部错误正文。
    def __init__(self, stage, uncertain=False):
        self.stage, self.uncertain = stage, uncertain
        super().__init__("QQ 发送结果未知，请核对已发送邮件，勿直接重发。" if uncertain else f"QQ SMTP 在 {stage} 阶段失败，请检查授权、收件地址或邮箱服务状态。")


# 功能：校验固定服务的账号和授权码。
# 输入：`address` 为完整 QQ/foxmail 地址；`code` 为客户端授权码。
# 输出：无；非法输入抛 InvalidState。
# 逻辑：检查 QQ 能力并限制 ASCII 地址与 16 位字母，防止协议注入。
# 约束：格式通过不代表服务器已认证。
@sensitive_variables("code")
def validate_credentials(address, code):
    require_qq_enabled("smtp_credentials")
    if not isinstance(address, str) or not re.fullmatch(r"[A-Za-z0-9_.+-]+@(qq|foxmail)\.com", address, re.IGNORECASE):
        raise InvalidState("请输入完整的 QQ 或 foxmail 邮箱地址。")
    if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z]{16}", code):
        raise InvalidState("请输入 QQ 客户端的 16 位授权码。")


# 功能：建立只做认证的 SMTP 会话。
# 输入：`address` 为发件账号；`code` 为授权码。
# 输出：已登录 SMTP_SSL；失败抛受控 QQSMTPError。
# 逻辑：固定主机、证书验证与超时，EHLO 成功后执行认证。
# 约束：不调用 MAIL、RCPT、DATA；连接失败关闭传输，不重试。
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


# 功能：释放 SMTP 连接并保持已确认的提交结果。
# 输入：`client` 为已创建 SMTP 对象。
# 输出：无；清理错误只记录类型。
# 逻辑：QUIT 后关闭传输，失败不重发、不推翻服务器已接受的 DATA。
# 约束：不记录服务端错误正文。
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


# 功能：执行一次已批准的 QQ 发信。
# 输入：`action` 含冻结发件账号、收件人、主题、正文；`credentials` 含解密授权码。
# 输出：稳定 Message-ID 及 smtp_accepted 状态；不代表最终送达收件箱。
# 逻辑：UTF-8 MIME 使用 SMTP CRLF 与 base64 正文；全部 RCPT 接受后才 DATA，最终 250 才成功。
# 约束：任一收件人拒绝则不发正文；DATA 中断为 uncertain；不自行追加发送副本或自动重试。
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


# 功能：只读核对结果未知的 QQ 发信是否有唯一发送副本。
# 输入：`action` 为冻结动作；`credentials` 为独立发信连接授权码。
# 输出：确认副本的 Message-ID 与 QQ 持久标识。
# 逻辑：IMAP 已发送目录按稳定 Message-ID 查询，复查 UID、头部标识、发件人、收件人与主题。
# 约束：未找到不能证明未发送；不读正文、不 APPEND、不重发；需要账号开启 IMAP 并保留发送副本。
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
