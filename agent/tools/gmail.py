"""职责：提供 Gmail 只读授权、历史扫描和可观察的逐封读取。
实现：保留既有默认参数，独立 Worker 通过回调持久化阶段；兼容旧 CLI 调用。
关联：软件 Worker 使用本模块，Gmail 工具提供原文，后端 HTTP 客户端保存业务数据。
目录：
- GmailHistoryExpiredError：标识 Gmail 历史游标过期。
- create_service：使用短期令牌创建只读 Gmail 客户端。
- create_service_from_authorization：使用后端授权创建 Gmail 客户端。
- get_profile_address：查询授权账号地址。
- get_profile_history_id：取得增量历史起点。
- resolve_mailbox_address：解析已知业务邮箱或授权账号地址。
- read_email：读取并解析一封 Gmail raw 邮件。
- _normalize_mailbox_address：规范化一个完整邮箱地址。
- _internal_date_to_utc：转换 Gmail 毫秒时间戳。
- list_sync_message_ids：列出同步扫描的消息 ID。
- list_history_message_ids：分页枚举新增 Gmail 消息。
- read_messages：读取指定消息并可回报逐封进度。
- read_sync_emails：读取同步范围内最近收发邮件。
变量索引：
- SCOPES：Gmail 只读授权范围。
"""

import json
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses
from typing import Mapping

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from agent.tools.email_parser import parse_raw_email

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


# 功能：标识 Gmail 历史游标过期。
# 逻辑：由 History 404 转换为专用异常。
# 约束：仅表示同步历史失效，不表示邮箱授权无效。
class GmailHistoryExpiredError(RuntimeError):
    """保存的 Gmail historyId 已不可用于增量读取。"""


# 功能：使用短期令牌创建只读 Gmail 客户端。
# 输入：`access_token` 为短期访问令牌。
# 输出：返回 Gmail Service。
# 逻辑：校验非空 token 并创建 Credentials/SDK。
# 约束：SDK 构建异常转为安全 RuntimeError，不记录令牌。
def create_service(access_token: str):
    """使用前端传入的短期 access token 创建只读 Gmail Service。"""
    if not isinstance(access_token, str) or not access_token.strip():
        raise RuntimeError("Gmail access token 不能为空。")
    try:
        credentials = Credentials(token=access_token.strip(), scopes=SCOPES)
        return build("gmail", "v1", credentials=credentials, cache_discovery=False)
    except Exception:
        raise RuntimeError("Gmail access token 无法建立连接。") from None


# 功能：使用后端授权创建 Gmail 客户端。
# 输入：`authorization` 为授权或邮箱同步请求对象。
# 输出：Service 与可持久化的刷新凭证字典。
# 逻辑：只在过期且有 refresh token 时刷新，然后构建 SDK。
# 约束：刷新失败显式报错；返回凭证仅限后端保存，不给浏览器。
def create_service_from_authorization(authorization: Mapping) -> tuple[object, dict]:
    """使用后端保存的授权信息创建 Gmail Service，并返回可能刷新的凭证。"""
    if not isinstance(authorization, Mapping) or not authorization:
        raise RuntimeError("Gmail 授权信息为空。")
    try:
        credentials = Credentials.from_authorized_user_info(
            dict(authorization), SCOPES
        )
        if not credentials.valid:
            if credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
            else:
                raise RuntimeError("Gmail 授权已失效，请员工重新授权。")
        service = build(
            "gmail", "v1", credentials=credentials, cache_discovery=False
        )
        return service, json.loads(credentials.to_json())
    except RefreshError:
        raise RuntimeError("Gmail 授权刷新失败，请员工重新授权。") from None
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError("Gmail 授权信息无法建立连接。") from None


# 功能：查询授权账号地址。
# 输入：`service` 为已授权 Gmail SDK 客户端。
# 输出：规范化邮箱字符串。
# 逻辑：读取 profile 后验证完整邮箱地址。
# 约束：网络或字段无效抛安全 RuntimeError。
def get_profile_address(service) -> str:
    """返回当前已授权 Gmail 账号的有效完整邮箱地址。"""
    try:
        profile = service.users().getProfile(userId="me").execute()
    except Exception:
        raise RuntimeError("Gmail profile 读取失败。") from None

    email_address = profile.get("emailAddress") if isinstance(profile, dict) else None
    normalized = _normalize_mailbox_address(email_address)
    if normalized is None:
        raise RuntimeError("Gmail profile 未返回邮箱地址。")
    return normalized


# 功能：取得增量历史起点。
# 输入：`service` 为已授权 Gmail SDK 客户端。
# 输出：非空历史 ID 字符串。
# 逻辑：从 profile 提取字符串或整数 historyId。
# 约束：不接受布尔值；网络或数据无效明确报错。
def get_profile_history_id(service) -> str:
    """读取当前邮箱可作为下一次增量起点的 Gmail historyId。"""
    try:
        profile = service.users().getProfile(userId="me").execute()
    except Exception:
        raise RuntimeError("Gmail profile 历史游标读取失败。") from None

    history_id = profile.get("historyId") if isinstance(profile, dict) else None
    if isinstance(history_id, bool) or not isinstance(history_id, (str, int)):
        raise RuntimeError("Gmail profile 未返回有效历史游标。")
    normalized = str(history_id).strip()
    if not normalized:
        raise RuntimeError("Gmail profile 未返回有效历史游标。")
    return normalized


# 功能：解析已知业务邮箱或授权账号地址。
# 输入：`service` 为已授权 Gmail SDK 客户端；`explicit_address` 为可选显式邮箱地址。
# 输出：合法 bare address。
# 逻辑：先验证显式地址，无效才调用 profile。
# 约束：保留既有地址选择规则；profile 失败向上抛出。
def resolve_mailbox_address(service, explicit_address: str | None) -> str:
    """有效显式邮箱优先；否则只回退到 Gmail profile。"""
    normalized = _normalize_mailbox_address(explicit_address)
    if normalized is not None:
        return normalized
    return get_profile_address(service)


# 功能：读取并解析一封 Gmail raw 邮件。
# 输入：`service` 为已授权 Gmail SDK 客户端；`message_id` 为指定 Gmail 消息 ID。
# 输出：标准邮件字典。
# 逻辑：读取 raw 与 ID，规范化内部时间后交 MIME 解析器。
# 约束：SDK 异常脱敏为 RuntimeError；无发送、删除或已读修改。
def read_email(service, message_id: str) -> dict:
    """用一次 raw 请求读取指定消息，并规范化 Gmail resource 元数据。"""
    try:
        message = service.users().messages().get(
            userId="me", id=message_id, format="raw"
        ).execute()
    except Exception:
        raise RuntimeError("Gmail 邮件读取失败。") from None

    if not isinstance(message, dict):
        raise RuntimeError("Gmail 邮件响应无效。")
    raw = message.get("raw")
    returned_id = message.get("id")
    if (
        not isinstance(raw, str)
        or not raw.strip()
        or not isinstance(returned_id, str)
        or not returned_id.strip()
    ):
        raise RuntimeError("Gmail 邮件响应无效。")

    thread_id = message.get("threadId")
    if not isinstance(thread_id, str) or not thread_id.strip():
        thread_id = None

    return parse_raw_email(
        raw,
        message_id=returned_id,
        thread_id=thread_id,
        received_at=_internal_date_to_utc(message.get("internalDate")),
    )


# 功能：规范化一个完整邮箱地址。
# 输入：`value` 为待验证原值。
# 输出：合法地址或 None。
# 逻辑：要求仅一个地址、一个 @ 且两侧非空无空白。
# 约束：不推断公司归属，无网络副作用。
def _normalize_mailbox_address(value) -> str | None:
    """将单个基本合法的完整 mailbox 规范化为 bare address。"""
    if not isinstance(value, str) or not value.strip():
        return None
    addresses = getaddresses([value])
    if len(addresses) != 1:
        return None
    address = addresses[0][1].strip()
    if address.count("@") != 1:
        return None
    local_part, domain = address.rsplit("@", 1)
    if not local_part or not domain or any(character.isspace() for character in address):
        return None
    return address


# 功能：转换 Gmail 毫秒时间戳。
# 输入：`value` 为待验证原值。
# 输出：UTC ISO 字符串或 None。
# 逻辑：检查整数及 ASCII 数字表示，从 Unix epoch 加毫秒。
# 约束：布尔、格式错误和越界返回 None，不推算未知时间。
def _internal_date_to_utc(value) -> str | None:
    """独立将可表示的 Gmail epoch 毫秒转换为 UTC ISO8601。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        milliseconds = value
    elif isinstance(value, str):
        candidate = value.strip()
        digits = candidate[1:] if candidate[:1] in {"+", "-"} else candidate
        if not digits or not digits.isascii() or not digits.isdigit():
            return None
        try:
            milliseconds = int(candidate)
        except ValueError:
            return None
    else:
        return None

    try:
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        return (epoch + timedelta(milliseconds=milliseconds)).isoformat()
    except (OverflowError, ValueError):
        return None


# 功能：列出同步扫描的消息 ID。
# 输入：`service` 为已授权 Gmail SDK 客户端；`limit` 为调用方明确的数量上限。
# 输出：最多二十个字符串 ID。
# 逻辑：固定 inbox/sent 查询并验证响应结构。
# 约束：非法数量和响应报错；不读取正文。
def list_sync_message_ids(service, limit: int = 20) -> list[str]:
    """列出最近收件和发件的 Gmail message ID，不读取邮件正文。"""
    if type(limit) is not int:
        raise RuntimeError("Gmail 同步数量必须是整数。")
    max_results = max(1, min(limit, 20))
    try:
        response = service.users().messages().list(
            userId="me",
            maxResults=max_results,
            q="{in:inbox in:sent}",
        ).execute()
    except Exception:
        raise RuntimeError("Gmail 同步邮件列表读取失败。") from None

    if not isinstance(response, dict):
        raise RuntimeError("Gmail 同步邮件列表响应无效。")
    items = response.get("messages", [])
    if not isinstance(items, list):
        raise RuntimeError("Gmail 同步邮件列表响应无效。")

    message_ids: list[str] = []
    for item in items[:max_results]:
        message_id = item.get("id") if isinstance(item, dict) else None
        if not isinstance(message_id, str) or not message_id.strip():
            raise RuntimeError("Gmail 同步邮件列表响应无效。")
        message_ids.append(message_id)
    return message_ids


# 功能：分页枚举新增 Gmail 消息。
# 输入：`service` 为已授权 Gmail SDK 客户端；`start_history_id` 为已保存 Gmail 历史游标。
# 输出：新增 ID 列表与下一历史游标。
# 逻辑：遍历 messageAdded 历史，按 inbox/sent 标签筛选并去重。
# 约束：404 专门抛游标过期异常，分页循环或结构错误报错。
def list_history_message_ids(
    service, start_history_id: str
) -> tuple[list[str], str]:
    """列出 historyId 之后新增的收件和发件 ID，并返回最新游标。"""
    if not isinstance(start_history_id, str) or not start_history_id.strip():
        raise RuntimeError("Gmail 历史游标不能为空。")

    message_ids: list[str] = []
    seen_message_ids: set[str] = set()
    seen_page_tokens: set[str] = set()
    page_token: str | None = None
    latest_history_id = start_history_id.strip()
    while True:
        arguments = {
            "userId": "me",
            "startHistoryId": start_history_id.strip(),
            "historyTypes": ["messageAdded"],
            "maxResults": 100,
        }
        if page_token is not None:
            arguments["pageToken"] = page_token
        try:
            response = service.users().history().list(**arguments).execute()
        except Exception as error:
            status = getattr(getattr(error, "resp", None), "status", None)
            if status == 404:
                raise GmailHistoryExpiredError(
                    "Gmail 历史游标已过期，需要重新扫描最近邮件。"
                ) from None
            raise RuntimeError("Gmail 增量历史读取失败。") from None

        if not isinstance(response, dict):
            raise RuntimeError("Gmail 增量历史响应无效。")
        returned_history_id = response.get("historyId")
        if isinstance(returned_history_id, (str, int)) and not isinstance(
            returned_history_id, bool
        ):
            candidate = str(returned_history_id).strip()
            if candidate:
                latest_history_id = candidate

        history = response.get("history", [])
        if not isinstance(history, list):
            raise RuntimeError("Gmail 增量历史响应无效。")
        for record in history:
            additions = record.get("messagesAdded", []) if isinstance(record, dict) else []
            if not isinstance(additions, list):
                raise RuntimeError("Gmail 增量历史响应无效。")
            for addition in additions:
                message = addition.get("message") if isinstance(addition, dict) else None
                if not isinstance(message, dict):
                    raise RuntimeError("Gmail 增量历史响应无效。")
                message_id = message.get("id")
                if not isinstance(message_id, str) or not message_id.strip():
                    raise RuntimeError("Gmail 增量历史响应无效。")
                labels = message.get("labelIds")
                if isinstance(labels, list) and labels and not {
                    "INBOX",
                    "SENT",
                }.intersection(labels):
                    continue
                if message_id not in seen_message_ids:
                    seen_message_ids.add(message_id)
                    message_ids.append(message_id)

        next_page_token = response.get("nextPageToken")
        if next_page_token is None:
            break
        if (
            not isinstance(next_page_token, str)
            or not next_page_token
            or next_page_token in seen_page_tokens
        ):
            raise RuntimeError("Gmail 增量历史分页信息无效。")
        seen_page_tokens.add(next_page_token)
        page_token = next_page_token

    return message_ids, latest_history_id


# 功能：读取指定消息并可回报逐封进度。
# 输入：`service` 为已授权 Gmail SDK 客户端；`message_ids` 为指定 Gmail ID 数组，None 表示既有扫描范围；`progress` 为可选阶段回调。
# 输出：成功解析的邮件数组。
# 逻辑：启用回调时先登记全体 ID，再逐封回报读取和安全错误并继续其余邮件。
# 约束：无回调保留旧异常传播；回调写入失败向上传播，不能伪造持久进度。
def read_messages(service, message_ids: list[str], progress=None) -> list[dict]:
    """按给定顺序读取 Gmail raw 邮件。"""
    if not isinstance(message_ids, list) or any(
        not isinstance(message_id, str) or not message_id.strip()
        for message_id in message_ids
    ):
        raise RuntimeError("Gmail message ID 列表无效。")
    if progress is None:
        return [read_email(service, message_id) for message_id in message_ids]
    progress("discovered", {"message_ids": message_ids})
    emails = []
    for message_id in message_ids:
        progress("fetching", {"gmail_message_id": message_id})
        try:
            email = read_email(service, message_id)
        except Exception:
            progress("failed", {"gmail_message_id": message_id, "stage": "fetching", "code": "gmail_read_failed"})
            continue
        emails.append(email)
    return emails


# 功能：读取同步范围内最近收发邮件。
# 输入：`service` 为已授权 Gmail SDK 客户端；`limit` 为调用方明确的数量上限；`progress` 为可选阶段回调。
# 输出：成功邮件数组。
# 逻辑：复用 ID 列表和逐封读取，传递可选进度回调。
# 约束：保持二十封上限；无回调保持原调用约定。
def read_sync_emails(service, limit: int = 20, progress=None) -> list[dict]:
    """读取本次同步使用的最近收件和发件邮件，最多二十封。"""
    return read_messages(service, list_sync_message_ids(service, limit), progress=progress) if progress else read_messages(service, list_sync_message_ids(service, limit))
