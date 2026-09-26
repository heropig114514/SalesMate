"""Responsibility: Insert synthetic SalesMate test mail into the developer's own Gmail inbox for local end-to-end validation.

Implementation: Validates a JSON test plan, constructs RFC 2822 messages, obtains a dedicated Gmail insert OAuth token, inserts only into the authenticated inbox, and emits structured command results. It neither calls the backend nor sends mail to external addresses; the existing read-only synchronization flow consumes inserted mail.

Relationships: Uses Google OAuth and Gmail APIs; consumes `gmail_test_messages.template.json`; is exercised manually by developers and is separate from backend synchronization.

Directory:
- main: Parse command arguments, build test messages, and optionally insert them.
- connect_injector: Load or obtain the dedicated Gmail OAuth credentials and build a service.
- load_test_plan: Read and validate the mailbox and message scenarios from JSON.
- build_test_messages: Construct RFC 2822 messages for a test run.
- _sender: Validate a scenario sender representation.
- _required_text: Validate required nonempty template text.
- insert_message: Insert one encoded RFC 2822 message through Gmail.
- _mailbox: Validate and normalize a complete mailbox address.
- _summary: Build a body-free dry-run message summary.
- _print_json: Emit UTF-8-friendly JSON command output.

Variable index:
- INJECT_SCOPES: Gmail OAuth scopes required for insert and profile reads.
- TOOL_DIR: Directory containing this developer tool and its default files.
- DEFAULT_CREDENTIALS_PATH: Default Desktop OAuth client configuration path.
- DEFAULT_TOKEN_PATH: Default dedicated local injector token path.
- DEFAULT_MESSAGES_PATH: Default synthetic-message template path.
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


# Function: Parse command arguments, validate a synthetic mail plan, and either preview or insert the generated messages.
# Inputs: `argv` optionally supplies command-line arguments; otherwise argparse reads the process arguments.
# Outputs: Returns 0 for a completed or dry-run operation and 1 after a validation, OAuth, profile, or insertion failure; writes a JSON status object to stdout.
# Logic: Loads the plan, generates a run identifier and RFC 2822 messages, returns a body-free preview for `--dry-run`, or authorizes Gmail, verifies the selected mailbox, and inserts every message.
# Constraints: Uses a dedicated local token, never calls the backend, and reports failures without continuing to insertion.
def main(argv: list[str] | None = None) -> int:
    """Parse arguments, generate test mail, and insert it into the current developer Gmail."""
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


# Function: Load or obtain dedicated Gmail OAuth credentials and create the local test-injection service.
# Inputs: `credentials_path` identifies Desktop OAuth client JSON; `token_path` stores the dedicated authorized-user token.
# Outputs: Returns a configured Gmail v1 service or raises `RuntimeError` for invalid configuration or expired authorization.
# Logic: Validates a Desktop client document, reuses or refreshes its token when valid, otherwise runs a local OAuth callback flow, persists the token, and constructs the Gmail client.
# Constraints: Requires `INJECT_SCOPES`; rejects web-client configuration and never substitutes backend web OAuth credentials.
def connect_injector(credentials_path: Path, token_path: Path):
    """Create the Gmail Service used only for test injection and save its separate token."""
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


# Function: Read and strictly validate the mailbox and synthetic message scenarios from a JSON plan.
# Inputs: `path` is the JSON file containing mailbox_address and a nonempty messages array.
# Outputs: Returns the normalized mailbox address and validated sender, subject, and body scenarios; raises `RuntimeError` or `ValueError` for invalid input.
# Logic: Parses JSON, validates the top-level schema and exact message fields, rejects newline-bearing subjects, and normalizes each sender and required text value.
# Constraints: Does not read Gmail or construct OAuth credentials; every scenario must contain only the declared fields.
def load_test_plan(path: Path) -> tuple[str, list[dict[str, str]]]:
    """Read and validate the mailbox and mail-scenario JSON written for testing."""
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


# Function: Construct RFC 2822 messages from validated synthetic scenarios for one run.
# Inputs: `mailbox_address` is the destination inbox; `run_id` identifies the test run; `scenarios` supplies validated sender, subject, and body values.
# Outputs: Returns one `EmailMessage` per scenario with deterministic run headers and incrementing date offsets.
# Logic: Assigns From, To, run-tagged Subject, Date, Message-ID, X-SalesMate-Test-Run, and plaintext body for each scenario.
# Constraints: Uses the developer mailbox as the only recipient and leaves insertion to `insert_message`.
def build_test_messages(
    mailbox_address: str,
    *,
    run_id: str,
    scenarios: list[dict[str, str]],
) -> list[EmailMessage]:
    """Build RFC 2822 test mail from validated JSON scenarios."""
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


# Function: Validate a template sender expressed as a bare address or `Name <address>` form.
# Inputs: `value` is the candidate sender; `location` identifies its JSON location in validation errors.
# Outputs: Returns trimmed sender text or raises `ValueError`.
# Logic: Requires nonempty text, parses the address, and checks exactly one at-sign plus nonempty, whitespace-free local and domain components.
# Constraints: Preserves a valid display-name representation but does not normalize or verify external mailbox ownership.
def _sender(value: object, *, location: str) -> str:
    """Validate a template sender, allowing a bare address or a Name <address> form."""
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


# Function: Validate and normalize a required nonempty text field in the test plan.
# Inputs: `value` is the candidate field value; `location` identifies the field in validation errors.
# Outputs: Returns trimmed text or raises `ValueError`.
# Logic: Accepts only strings containing non-whitespace characters and strips surrounding whitespace.
# Constraints: Performs no schema inference, escaping, or external I/O.
def _required_text(value: object, location: str) -> str:
    """Read required text from a template."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location} 必须是非空字符串。")
    return value.strip()


# Function: Insert one RFC 2822 test message into the authenticated Gmail inbox.
# Inputs: `service` is the configured Gmail client; `message` is the generated EmailMessage.
# Outputs: Returns Gmail's response dictionary with a nonempty message ID or raises `RuntimeError`.
# Logic: URL-safe-base64 encodes message bytes, requests Gmail insertion into INBOX and UNREAD using the Date header, and validates the response ID.
# Constraints: Always targets the authenticated `me` mailbox and never sends through SMTP.
def insert_message(service, message: EmailMessage) -> dict[str, Any]:
    """Insert one RFC 2822 test message into the current Gmail inbox."""
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


# Function: Validate and normalize one complete mailbox address without a display name.
# Inputs: `value` is the candidate mailbox value.
# Outputs: Returns the normalized address or raises `ValueError`.
# Logic: Requires a string, parses it, rejects display names or malformed addresses, and checks local/domain components for whitespace.
# Constraints: Validates syntax only and does not query Gmail or external DNS.
def _mailbox(value: object) -> str:
    """Validate and normalize one complete mailbox address."""
    if not isinstance(value, str):
        raise ValueError("邮箱地址无效。")
    display_name, address = parseaddr(value.strip())
    if display_name or address != value.strip() or address.count("@") != 1:
        raise ValueError("邮箱地址必须是不带显示名称的完整地址。")
    local, domain = address.rsplit("@", 1)
    if not local or not domain or any(character.isspace() for character in address):
        raise ValueError("邮箱地址无效。")
    return address


# Function: Build a body-free summary for dry-run output.
# Inputs: `message` is a generated EmailMessage.
# Outputs: Returns its From, To, and Subject headers as strings.
# Logic: Reads only display headers needed to preview planned insertion.
# Constraints: Deliberately excludes the body and does not mutate the message.
def _summary(message: EmailMessage) -> dict[str, str]:
    """Generate a body-free mail summary for dry-run output."""
    return {
        "from": str(message["From"]),
        "to": str(message["To"]),
        "subject": str(message["Subject"]),
    }


# Function: Emit one UTF-8-friendly formatted JSON command result.
# Inputs: `value` is a JSON-serializable command result.
# Outputs: Writes indented JSON to standard output and returns `None`.
# Logic: Serializes with non-ASCII characters preserved for developer-readable status output.
# Constraints: Does not write files, log secrets independently, or handle serialization errors.
def _print_json(value: object) -> None:
    """Print command output in a UTF-8-friendly format."""
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
