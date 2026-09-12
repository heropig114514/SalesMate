"""Gmail 只读工具：本地 OAuth、授权邮箱和完整邮件读取。"""

import json
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses
from typing import Mapping

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from agent.config import AGENT_DIR
from agent.tools.email_parser import parse_raw_email

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def create_service(access_token: str):
    """使用前端传入的短期 access token 创建只读 Gmail Service。"""
    if not isinstance(access_token, str) or not access_token.strip():
        raise RuntimeError("Gmail access token 不能为空。")
    try:
        credentials = Credentials(token=access_token.strip(), scopes=SCOPES)
        return build("gmail", "v1", credentials=credentials, cache_discovery=False)
    except Exception:
        raise RuntimeError("Gmail access token 无法建立连接。") from None


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


def connect_gmail():
    """使用 agent 目录中的 OAuth 配置，并复用或刷新本地令牌。"""
    credentials_path = AGENT_DIR / "credentials.json"
    token_path = AGENT_DIR / "gmail_token.json"

    try:
        credentials = None
        if token_path.exists():
            credentials = Credentials.from_authorized_user_file(str(token_path), SCOPES)

        if not credentials or not credentials.valid:
            if credentials and credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
            else:
                if not credentials_path.exists():
                    raise RuntimeError("Gmail OAuth 配置不存在。")
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(credentials_path), SCOPES
                )
                credentials = flow.run_local_server(port=0)
            token_path.write_text(credentials.to_json(), encoding="utf-8")

        return build("gmail", "v1", credentials=credentials, cache_discovery=False)
    except RefreshError:
        raise RuntimeError("Gmail 授权刷新失败，请重新授权。") from None
    except RuntimeError as error:
        if str(error) == "Gmail OAuth 配置不存在。":
            raise
        raise RuntimeError("Gmail 授权失败。") from None
    except Exception:
        raise RuntimeError("Gmail 授权失败。") from None


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


def resolve_mailbox_address(service, explicit_address: str | None) -> str:
    """有效显式邮箱优先；否则只回退到 Gmail profile。"""
    normalized = _normalize_mailbox_address(explicit_address)
    if normalized is not None:
        return normalized
    return get_profile_address(service)


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


def read_recent_emails(service, limit: int = 5) -> list[dict]:
    """一次列出至多五个消息 ID，并按 API 返回顺序读取完整 raw 邮件。"""
    max_results = max(0, min(limit, 5))
    try:
        response = service.users().messages().list(
            userId="me", maxResults=max_results
        ).execute()
    except Exception:
        raise RuntimeError("Gmail 最近邮件读取失败。") from None

    if not isinstance(response, dict):
        raise RuntimeError("Gmail 最近邮件响应无效。")
    listed_messages = response.get("messages", [])
    if not isinstance(listed_messages, list):
        raise RuntimeError("Gmail 最近邮件响应无效。")

    emails = []
    for item in listed_messages[:max_results]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise RuntimeError("Gmail 最近邮件响应无效。")
        emails.append(read_email(service, item["id"]))
    return emails


def read_sync_emails(service, limit: int = 20) -> list[dict]:
    """读取本次 Demo 同步使用的最近收件和发件邮件，最多二十封。"""
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

    emails: list[dict] = []
    for item in items[:max_results]:
        message_id = item.get("id") if isinstance(item, dict) else None
        if not isinstance(message_id, str) or not message_id.strip():
            raise RuntimeError("Gmail 同步邮件列表响应无效。")
        emails.append(read_email(service, message_id))
    return emails
