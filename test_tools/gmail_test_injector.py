"""向开发者自己的 Gmail 插入 SalesMate 合成测试邮件。

该工具使用独立的 Gmail insert OAuth token，只用于本地全链路测试。它不会
调用后端，也不会把邮件发送给外部地址；插入后的邮件由现有只读同步流程读取。
"""

from __future__ import annotations

import argparse
import base64
import json
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid, parseaddr
from pathlib import Path
from typing import Any
from uuid import uuid4

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

INJECT_SCOPES = [
    "https://www.googleapis.com/auth/gmail.insert",
    "https://www.googleapis.com/auth/gmail.readonly",
]
TOOL_DIR = Path(__file__).resolve().parent
DEFAULT_CREDENTIALS_PATH = TOOL_DIR / "gmail_inject_credentials.json"
DEFAULT_TOKEN_PATH = TOOL_DIR / "gmail_inject_token.json"
DEFAULT_MESSAGES_PATH = TOOL_DIR / "gmail_test_messages.template.json"


def main(argv: list[str] | None = None) -> int:
    """解析参数，生成测试邮件并插入当前开发者 Gmail。"""
    parser = argparse.ArgumentParser(
        description="向自己的 Gmail 插入 SalesMate 全链路合成测试邮件"
    )
    parser.add_argument(
        "--credentials",
        type=Path,
        default=DEFAULT_CREDENTIALS_PATH,
        help="Desktop OAuth credentials.json 路径",
    )
    parser.add_argument(
        "--token",
        type=Path,
        default=DEFAULT_TOKEN_PATH,
        help="独立测试注入 token 保存路径",
    )
    parser.add_argument(
        "--messages-file",
        type=Path,
        default=DEFAULT_MESSAGES_PATH,
        help="测试邮件 JSON 文件路径",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只显示将要插入的邮件，不连接 Gmail",
    )
    args = parser.parse_args(argv)

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
    try:
        mailbox_address, scenarios = load_test_plan(args.messages_file)
        messages = build_test_messages(
            mailbox_address,
            run_id=run_id,
            scenarios=scenarios,
        )
    except Exception as error:
        _print_json(
            {
                "status": "failed",
                "run_id": run_id,
                "error": f"{type(error).__name__}: {error}",
            }
        )
        return 1

    if args.dry_run:
        _print_json(
            {
                "status": "dry_run",
                "run_id": run_id,
                "mailbox_address": mailbox_address,
                "messages_file": str(args.messages_file.resolve()),
                "message_count": len(messages),
                "messages": [_summary(item) for item in messages],
            }
        )
        return 0

    try:
        service = connect_injector(args.credentials, args.token)
        profile = service.users().getProfile(userId="me").execute()
        authorized_address = _mailbox(profile.get("emailAddress"))
        if authorized_address.casefold() != mailbox_address.casefold():
            raise RuntimeError(
                "注入授权账号与测试邮件 JSON 中的 mailbox_address 不一致："
                f"当前授权为 {authorized_address}。请删除 {args.token} 后重新选择账号。"
            )
        inserted = [insert_message(service, item) for item in messages]
    except Exception as error:
        _print_json(
            {
                "status": "failed",
                "run_id": run_id,
                "error": f"{type(error).__name__}: {error}",
            }
        )
        return 1

    _print_json(
        {
            "status": "completed",
            "run_id": run_id,
            "mailbox_address": mailbox_address,
            "messages_file": str(args.messages_file.resolve()),
            "inserted_count": len(inserted),
            "gmail_message_ids": [item["id"] for item in inserted],
            "gmail_search": f'"[SalesMate测试:{run_id}]"',
            "next_step": "打开 SalesMate 工作台，点击“同步 Gmail”。",
        }
    )
    return 0


def connect_injector(credentials_path: Path, token_path: Path):
    """建立只供测试注入使用的 Gmail Service，并保存独立 token。"""
    if not credentials_path.is_file():
        raise RuntimeError(
            f"测试注入 OAuth 配置不存在：{credentials_path}。"
            "请在 Google Cloud 创建 Desktop app 客户端并将下载的 JSON 保存到该路径。"
        )
    try:
        client_document = json.loads(credentials_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise RuntimeError("测试注入 OAuth 配置不是有效 JSON。") from None
    if not isinstance(client_document, dict) or "installed" not in client_document:
        client_type = "Web application" if "web" in client_document else "未知"
        raise RuntimeError(
            f"测试注入器需要 Desktop app OAuth Client，当前文件类型是 {client_type}。"
            "后端网页 OAuth Client 不能用于随机本地回调端口。"
        )

    credentials = None
    try:
        if token_path.is_file():
            credentials = Credentials.from_authorized_user_file(
                str(token_path), INJECT_SCOPES
            )
        if not credentials or not credentials.valid:
            if credentials and credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(credentials_path), INJECT_SCOPES
                )
                credentials = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(credentials.to_json(), encoding="utf-8")
    except RefreshError:
        raise RuntimeError(
            f"测试注入授权已失效，请删除 {token_path} 后重新授权。"
        ) from None

    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def load_test_plan(path: Path) -> tuple[str, list[dict[str, str]]]:
    """读取并校验测试人员编写的邮箱和邮件场景 JSON。"""
    if not path.is_file():
        raise RuntimeError(f"测试邮件 JSON 文件不存在：{path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"测试邮件 JSON 读取失败：{type(error).__name__}: {error}"
        ) from None

    if not isinstance(document, dict):
        raise ValueError("测试邮件 JSON 顶层必须是对象。")
    try:
        mailbox_address = _mailbox(document.get("mailbox_address"))
    except ValueError as error:
        raise ValueError(
            f"测试邮件 JSON 的 mailbox_address 无效：{error}"
        ) from None
    raw_messages = document.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        raise ValueError("测试邮件 JSON 的 messages 必须是非空数组。")

    required_fields = {"from", "subject", "body"}
    scenarios: list[dict[str, str]] = []
    for index, item in enumerate(raw_messages):
        location = f"messages[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{location} 必须是对象。")
        if set(item) != required_fields:
            missing = sorted(required_fields - set(item))
            extra = sorted(set(item) - required_fields)
            details = []
            if missing:
                details.append(f"缺少 {missing}")
            if extra:
                details.append(f"存在多余字段 {extra}")
            raise ValueError(f"{location} 字段无效：{'；'.join(details)}。")

        sender = _sender(item["from"], location=location)
        subject = _required_text(item["subject"], f"{location}.subject")
        if "\r" in subject or "\n" in subject:
            raise ValueError(f"{location}.subject 不能包含换行。")
        body = _required_text(item["body"], f"{location}.body")
        scenarios.append({"from": sender, "subject": subject, "body": body})
    return mailbox_address, scenarios


def build_test_messages(
    mailbox_address: str,
    *,
    run_id: str,
    scenarios: list[dict[str, str]],
) -> list[EmailMessage]:
    """根据已校验的 JSON 场景构造 RFC 2822 测试邮件。"""
    now = datetime.now().astimezone()
    result: list[EmailMessage] = []
    for index, scenario in enumerate(scenarios):
        message = EmailMessage()
        message["From"] = scenario["from"]
        message["To"] = mailbox_address
        message["Subject"] = f"[SalesMate测试:{run_id}] {scenario['subject']}"
        message["Date"] = format_datetime(now + timedelta(seconds=index))
        sender_address = parseaddr(scenario["from"])[1]
        sender_domain = sender_address.rsplit("@", 1)[1]
        message["Message-ID"] = make_msgid(
            idstring=f"salesmate-{run_id}-{index}", domain=sender_domain
        )
        message["X-SalesMate-Test-Run"] = run_id
        message.set_content(scenario["body"])
        result.append(message)
    return result


def _sender(value: object, *, location: str) -> str:
    """校验模板中的发件人，允许纯邮箱或“名称 <邮箱>”格式。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location}.from 必须是非空字符串。")
    candidate = value.strip()
    _display_name, address = parseaddr(candidate)
    if address.count("@") != 1:
        raise ValueError(f"{location}.from 必须包含有效邮箱地址。")
    local, domain = address.rsplit("@", 1)
    if not local or not domain or any(character.isspace() for character in address):
        raise ValueError(f"{location}.from 必须包含有效邮箱地址。")
    return candidate


def _required_text(value: object, location: str) -> str:
    """读取模板中的必填文本。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location} 必须是非空字符串。")
    return value.strip()


def insert_message(service, message: EmailMessage) -> dict[str, Any]:
    """把一封 RFC 2822 测试邮件插入当前 Gmail 的收件箱。"""
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    response = (
        service.users()
        .messages()
        .insert(
            userId="me",
            internalDateSource="dateHeader",
            body={"raw": raw, "labelIds": ["INBOX", "UNREAD"]},
        )
        .execute()
    )
    message_id = response.get("id") if isinstance(response, dict) else None
    if not isinstance(message_id, str) or not message_id:
        raise RuntimeError("Gmail 没有返回插入后的 message ID。")
    return response


def _mailbox(value: object) -> str:
    """校验并规范单个完整邮箱地址。"""
    if not isinstance(value, str):
        raise ValueError("邮箱地址无效。")
    display_name, address = parseaddr(value.strip())
    if display_name or address != value.strip() or address.count("@") != 1:
        raise ValueError("邮箱地址必须是不带显示名称的完整地址。")
    local, domain = address.rsplit("@", 1)
    if not local or not domain or any(character.isspace() for character in address):
        raise ValueError("邮箱地址无效。")
    return address


def _summary(message: EmailMessage) -> dict[str, str]:
    """生成 dry-run 使用的无正文邮件摘要。"""
    return {
        "from": str(message["From"]),
        "to": str(message["To"]),
        "subject": str(message["Subject"]),
    }


def _print_json(value: object) -> None:
    """以 UTF-8 友好的格式输出命令结果。"""
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
