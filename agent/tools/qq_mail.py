"""职责：通过固定 QQ IMAP TLS 服务只读发现和解析收发邮件。
实现：验证授权码，EXAMINE 文件夹，以 UIDVALIDITY/UID 定位并用 BODY.PEEK[] 读取。
关联：qq_sync 持久保存扫描位置；复用 email_parser 的 MIME 和正文边界规则。
目录：
- QQMailError：可直接展示且不含服务端原文的连接错误。
- connect：建立已登录的 TLS 连接。
- disconnect：释放连接并记录脱敏关闭错误。
- folders：发现收件箱及唯一已发送文件夹。
- select_folder：只读选择文件夹并读取 UIDVALIDITY。
- message_id：构造协议兼容的 QQ 消息标识。
- split_message_id：严格解析消息标识。
- list_uids：按可选日期粗筛游标后的 UID。
- message_dates：只读批量查询消息的内部日期。
- read_email：按持久消息 ID 读取原文。
变量索引：
- IMAP_HOST：固定 QQ 主机，禁止客户端指定任意目标。
- IMAP_PORT：TLS 端口 993。
- TIMEOUT：单次网络等待上限 30 秒。
- logger：连接生命周期脱敏日志。
"""
import base64
from datetime import datetime, timedelta, timezone
import imaplib
import logging
import re
import ssl

from .email_parser import parse_raw_email

IMAP_HOST = "imap.qq.com"
IMAP_PORT = 993
TIMEOUT = 30
logger = logging.getLogger("salesmate.qq_imap")


# 功能：描述安全的 QQ 连接失败。
# 逻辑：只携带受控说明。
# 约束：不包含授权码或 IMAP 原始异常。
class QQMailError(RuntimeError):
    pass


# 功能：连接 QQ 并验证邮箱授权码。
# 输入：`address` 为完整 QQ/foxmail 地址；`authorization_code` 为 16 位客户端授权码。
# 输出：已登录 IMAP4_SSL；失败抛 QQMailError。
# 逻辑：固定主机、证书验证和超时，登录失败关闭套接字。
# 约束：不接受账号密码，不输出底层认证响应，不进行自动重试。
def connect(address, authorization_code):
    if not re.fullmatch(r"[^\s@]+@(qq|foxmail)\.com", address, re.IGNORECASE):
        raise QQMailError("请输入完整的 @qq.com 或 @foxmail.com 邮箱地址。")
    if not re.fullmatch(r"[A-Za-z]{16}", authorization_code):
        raise QQMailError("请输入 QQ 邮箱生成的 16 位授权码，而非账号密码。")
    client = None
    try:
        client = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=ssl.create_default_context(), timeout=TIMEOUT)
        status, _ = client.login(address, authorization_code)
        if status != "OK":
            raise QQMailError("QQ 邮箱登录失败，请检查 IMAP 服务和授权码。")
        return client
    except (OSError, imaplib.IMAP4.error, QQMailError) as error:
        logger.warning("qq_connect_failed error_type=%s action=check_imap_network_and_authorization", type(error).__name__)
        if client is not None:
            disconnect(client)
        raise QQMailError("无法连接 QQ 邮箱，请检查 IMAP 已开启、授权码有效及服务器可访问 imap.qq.com:993。") from None


# 功能：释放只读 IMAP 连接。
# 输入：`client` 为已创建连接。
# 输出：无；关闭失败仅记安全日志。
# 逻辑：LOGOUT 失败时关闭传输，避免泄露资源。
# 约束：清理失败不改变已完成业务结果，也不掩盖正在传播的业务异常。
def disconnect(client):
    try:
        client.logout()
    except (OSError, imaplib.IMAP4.error) as error:
        logger.warning("qq_logout_failed error_type=%s", type(error).__name__)
        try:
            client.shutdown()
        except OSError as close_error:
            logger.warning("qq_socket_close_failed error_type=%s", type(close_error).__name__)


# 功能：发现本次只读同步的两个文件夹。
# 输入：`client` 为已登录连接。
# 输出：INBOX 和已发送文件夹的 IMAP wire 名称。
# 逻辑：解析 LIST，优先使用服务器 Sent 特殊用途标记，否则匹配 QQ 已知的已发送名称。
# 约束：未知格式或无法唯一定位已发送文件夹时明确失败，不缩小为仅收件箱。
def folders(client):
    status, rows = client.list()
    if status != "OK":
        raise QQMailError("QQ 文件夹列表读取失败。")
    marked, named = [], []
    for row in rows or []:
        if not isinstance(row, bytes):
            raise QQMailError("QQ 文件夹列表格式不受支持。")
        match = re.fullmatch(rb'\(([^)]*)\) (?:"(?:[^"\\]|\\.)*"|NIL) (.+)', row)
        if not match:
            raise QQMailError("QQ 文件夹列表格式不受支持。")
        flags, name = match.groups()
        if b"\\noselect" in flags.lower().split():
            continue
        name = name.decode("ascii")
        if name.startswith('"') and name.endswith('"'):
            name = re.sub(r'\\(.)', r'\1', name[1:-1])
        if b"\\sent" in flags.lower().split():
            marked.append(name)
        if name.casefold() in {"sent", "sent messages", "&xfjt0zab-"}:
            named.append(name)
    candidates = marked if marked else named
    if len(set(candidates)) != 1:
        raise QQMailError("无法唯一识别 QQ 已发送文件夹，请检查邮箱的 IMAP 文件夹设置。")
    return ["INBOX", candidates[0]]


# 功能：只读选中一个文件夹。
# 输入：`client` 为连接；`folder` 为 LIST 返回的 wire 名称。
# 输出：正整数 UIDVALIDITY。
# 逻辑：EXAMINE 并检查稳定身份代次。
# 约束：不修改已读标志；缺失代次不能继续同步。
def select_folder(client, folder):
    quoted = '"' + folder.replace('\\', '\\\\').replace('"', '\\"') + '"'
    status, _ = client.select(quoted, readonly=True)
    _, values = client.response("UIDVALIDITY")
    if status != "OK" or not values or not isinstance(values[0], bytes) or not values[0].isdigit() or int(values[0]) <= 0:
        raise QQMailError("QQ 文件夹无法只读打开或缺少 UIDVALIDITY。")
    return int(values[0])


# 功能：构造跨文件夹不冲突的消息 ID。
# 输入：`folder` 为 wire 名称；`validity` 为代次；`uid` 为正整数消息 UID。
# 输出：qq 前缀的 ASCII 字符串。
# 逻辑：文件夹 Base64URL 编码后与代次和 UID 组合。
# 约束：长度须符合现有 200 字符协议；不伪装为 Gmail 服务端 ID。
def message_id(folder, validity, uid):
    encoded = base64.urlsafe_b64encode(folder.encode("ascii")).decode("ascii").rstrip("=")
    value = f"qq:{encoded}:{validity}:{uid}"
    if len(value) > 200:
        raise QQMailError("QQ 文件夹名称超出现有消息标识长度限制。")
    return value


# 功能：解析持久 QQ 消息标识。
# 输入：`value` 为协议消息 ID。
# 输出：文件夹、UIDVALIDITY、UID 三元组。
# 逻辑：严格验证前缀、数字和规范编码，避免命令注入。
# 约束：非法标识立即失败，不解释为其他邮箱提供方。
def split_message_id(value):
    match = re.fullmatch(r"qq:([A-Za-z0-9_-]+):([1-9][0-9]*):([1-9][0-9]*)", value)
    if not match:
        raise QQMailError("QQ 消息标识无效。")
    encoded, validity, uid = match.groups()
    try:
        folder = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True).decode("ascii")
    except (ValueError, UnicodeError):
        raise QQMailError("QQ 消息标识编码无效。") from None
    if any(ord(char) < 32 for char in folder) or message_id(folder, int(validity), int(uid)) != value:
        raise QQMailError("QQ 消息标识编码无效。")
    return folder, int(validity), int(uid)


# 功能：发现当前文件夹的新消息 UID。
# 输入：`client` 为已选中文件夹连接；`after` 为 UID 下界；`since` 为可选带时区时间下界。
# 输出：升序且去重的新增 UID 列表。
# 逻辑：UID SEARCH 可附带 SINCE；日期回退一天覆盖服务器时区边界，精确时间由调用方筛选。
# 约束：空邮箱返回空列表；错误响应不能当作同步成功；不读取正文。
def list_uids(client, after, since=None):
    criteria = ["UID", f"{after + 1}:*"]
    if since:
        day = since.date() - timedelta(days=1) if since.date() > datetime.min.date() else since.date()
        month = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()[day.month - 1]
        criteria.extend(["SINCE", f"{day.day:02d}-{month}-{day.year:04d}"])
    status, rows = client.uid("search", None, *criteria)
    if status != "OK" or not rows or not isinstance(rows[0], bytes):
        raise QQMailError("QQ 邮件 UID 列表读取失败。")
    items = rows[0].split()
    if any(not item.isdigit() or int(item) <= 0 for item in items):
        raise QQMailError("QQ 邮件 UID 列表格式无效。")
    return sorted({int(item) for item in items if int(item) > after})


# 功能：取得一页 UID 对应的内部日期而不读取正文。
# 输入：`client` 为选中目标文件夹的连接；`uids` 为正整数 UID 数组。
# 输出：UID 到带 UTC 时区 datetime 的映射。
# 逻辑：UID FETCH 仅请求 UID 和 INTERNALDATE，严格核对响应集合及重复项。
# 约束：邮件移动或删除造成缺项时明确失败；不修改已读标记，不调用模型。
def message_dates(client, uids):
    if not uids:
        return {}
    if any(type(uid) is not int or uid <= 0 for uid in uids):
        raise QQMailError("QQ 日期查询 UID 无效。")
    status, rows = client.uid("fetch", ",".join(map(str, uids)), "(UID INTERNALDATE)")
    dates = {}
    if status != "OK":
        raise QQMailError("QQ 邮件日期查询失败。")
    for row in rows or []:
        if not isinstance(row, bytes):
            raise QQMailError("QQ 邮件日期响应格式无效。")
        uid_match = re.search(rb"\bUID (\d+)\b", row)
        date_match = re.search(rb'INTERNALDATE "([^"]+)"', row)
        if not uid_match or not date_match or int(uid_match[1]) in dates:
            raise QQMailError("QQ 邮件日期响应缺少唯一 UID 或日期。")
        try:
            dates[int(uid_match[1])] = datetime.strptime(date_match[1].decode("ascii"), "%d-%b-%Y %H:%M:%S %z").astimezone(timezone.utc)
        except (ValueError, UnicodeError):
            raise QQMailError("QQ 邮件内部日期无效。") from None
    if set(dates) != set(uids):
        raise QQMailError("QQ 日期查询结果不完整，邮件可能已被移动或删除。")
    return dates


# 功能：只读获取指定 QQ 邮件原文并标准化。
# 输入：`client` 为已认证连接；`value` 为持久消息标识。
# 输出：现有 L1 所需邮件字典，thread_id 为 None。
# 逻辑：复查代次后 UID FETCH BODY.PEEK[]，使用 INTERNALDATE 保留接收时间，复用 MIME 解析器。
# 约束：不存在、代次改变或响应不匹配均失败；不标记已读、不删除、不推测会话关系。
def read_email(client, value):
    folder, validity, uid = split_message_id(value)
    if select_folder(client, folder) != validity:
        raise QQMailError("QQ 文件夹 UIDVALIDITY 已变化，请检查同步状态后再处理。")
    status, rows = client.uid("fetch", str(uid), "(UID INTERNALDATE BODY.PEEK[])")
    parts = [item for item in rows or [] if isinstance(item, tuple)]
    if status != "OK" or len(parts) != 1:
        raise QQMailError("QQ 邮件读取失败，邮件可能已被移动或删除。")
    meta, raw = parts[0]
    uid_match = re.search(rb"\bUID (\d+)\b", meta)
    date_match = re.search(rb'INTERNALDATE "([^"]+)"', meta)
    if not uid_match or int(uid_match[1]) != uid or not date_match or not isinstance(raw, bytes):
        raise QQMailError("QQ 邮件读取响应缺少 UID 或接收时间。")
    try:
        received = datetime.strptime(date_match[1].decode("ascii"), "%d-%b-%Y %H:%M:%S %z").astimezone(timezone.utc).isoformat()
    except (ValueError, UnicodeError):
        raise QQMailError("QQ 邮件接收时间无效。") from None
    return parse_raw_email(base64.urlsafe_b64encode(raw).decode("ascii"), message_id=value, thread_id=None, received_at=received)
