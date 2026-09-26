"""Responsibility: Append synthetic messages to a developer's own QQ inbox to verify synchronization and analysis.
Implementation: Validate JSON and generate MIME with the standard library; only explicit `--apply` uses fixed TLS IMAP APPEND, with no retry after failure.
Relationships: `qq_test_messages.template.json` provides scenarios; the packaging pipeline runs only `--dry-run` and has no main-project dependency.
Directory:
- load_test_plan: Validate the QQ receiving account and synthetic scenarios.
- build_test_messages: Generate MIME messages with a batch marker.
- read_authorization_code: Read an authorization code from the environment or a non-echoing prompt.
- inject_messages: Log in to the same account and append messages to INBOX one by one.
- main: Parse explicit modes and emit a safe JSON result.
Variable index:
- TOOL_DIR: Directory containing this independent script.
- DEFAULT_MESSAGES_PATH: Default QQ sample file.
- IMAP_HOST: Fixed `imap.qq.com` host.
- IMAP_PORT: Fixed TLS port 993.
- TIMEOUT: Network wait limit of 30 seconds.
- logger: Records only failed stage, batch, and error type.
"""
from __future__ import annotations

import argparse
import getpass
import imaplib
import json
import logging
import os
import re
import ssl
import warnings
from datetime import datetime, timedelta
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime, parseaddr, parsedate_to_datetime
from pathlib import Path
from uuid import uuid4

TOOL_DIR = Path(__file__).resolve().parent
DEFAULT_MESSAGES_PATH = TOOL_DIR / "qq_test_messages.template.json"
IMAP_HOST = "imap.qq.com"
IMAP_PORT = 993
TIMEOUT = 30
logger = logging.getLogger("salesmate.qq_test_injector")


# Function: Permit external operations only after fully validating the template.
# Inputs: `path` is a JSON-file path.
# Outputs: A normalized QQ address and scenario list; invalid fields raise `ValueError`.
# Logic: Validate field allowlists, nonempty bodies, and one-line headers; the top level uniquely specifies the receiving account for every message.
# Constraints: Do not read authorization codes, permit overriding server, recipient, or folder, or infer mailbox aliases.
def load_test_plan(path: Path) -> tuple[str, list[dict[str, str]]]:
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict) or set(document) != {"mailbox_address", "messages"}:
        raise ValueError("JSON accepts only mailbox_address and messages.")
    address = document["mailbox_address"]
    if not isinstance(address, str) or not re.fullmatch(r"[A-Za-z0-9_.+-]+@(qq|foxmail)\.com", address, re.IGNORECASE):
        raise ValueError("mailbox_address must be a complete QQ or foxmail address.")
    scenarios = document["messages"]
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("messages must be a nonempty array.")
    for index, item in enumerate(scenarios):
        if not isinstance(item, dict) or set(item) != {"from", "subject", "body"}:
            raise ValueError(f"messages[{index}] accepts only from, subject, and body.")
        for key, value in item.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"messages[{index}].{key} must be nonempty text.")
            if key != "body" and any(char in value for char in ("\r", "\n", "\x00")):
                raise ValueError(f"messages[{index}].{key} cannot contain header control characters.")
        sender = parseaddr(item["from"])[1]
        if not re.fullmatch(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", sender):
            raise ValueError(f"messages[{index}].from must contain a complete email address.")
    return address.casefold(), scenarios


# Function: Construct synthetic messages for a unique batch.
# Inputs: `address` is the validated receiving account; `scenarios` are validated scenarios; `run_id` is the internally generated batch.
# Outputs: An array of `EmailMessage` objects using SMTP CRLF policy.
# Logic: Preserve scenario bodies and add a subject marker, unique Message-ID, and past Date to support synchronization-range selection.
# Constraints: Do not use SMTP; dates do not exceed current time so newly generated messages do not fall after the synchronization upper bound.
def build_test_messages(address: str, scenarios: list[dict[str, str]], run_id: str) -> list[EmailMessage]:
    now = datetime.now().astimezone()
    messages = []
    for index, scenario in enumerate(scenarios):
        message = EmailMessage(policy=policy.SMTP)
        message["From"], message["To"] = scenario["from"], address
        message["Subject"] = f"[SalesMate测试:QQ:{run_id}] {scenario['subject']}"
        message["Date"] = format_datetime(now - timedelta(seconds=len(scenarios) - index))
        message["Message-ID"] = f"<salesmate-qq-{run_id}-{index}@test.salesmate.example>"
        message["X-SalesMate-Test-Run"] = run_id
        message.set_content(scenario["body"], charset="utf-8", cte="base64")
        messages.append(message)
    return messages


# Function: Obtain the client authorization code required for this explicit write.
# Inputs: No parameters; reads QQ_TEST_AUTHORIZATION_CODE or terminal interaction.
# Outputs: A 16-letter authorization code; unsafe input or invalid format raises an exception.
# Logic: Use the environment value when present, otherwise require non-echoing `getpass`; reject `getpass` echo fallback.
# Constraints: Do not accept command-line authorization codes or persist or output codes; called only from the `--apply` path.
def read_authorization_code() -> str:
    code = os.environ.get("QQ_TEST_AUTHORIZATION_CODE")
    if code is None:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            code = getpass.getpass("QQ client authorization code (hidden): ")
    if not re.fullmatch(r"[A-Za-z]{16}", code):
        raise ValueError("QQ client authorization code must contain 16 letters.")
    return code


# Function: Append synthetic messages one by one to the logged-in account's INBOX.
# Inputs: `address` is the sole target and logged-in account; `messages` are constructed MIME messages; `run_id` identifies the batch.
# Outputs: Status, confirmed count, Message-IDs, and required unknown-result guidance; never returns credentials or raw server text.
# Logic: Validate TLS, login, and INBOX, then APPEND each message; stop immediately for explicit NO/BAD or exceptions while retaining prior successes.
# Constraints: An interrupted APPEND may already have written, so return `uncertain` without retrying; do not delete, EXPUNGE, send, or fall back to SMTP.
def inject_messages(address: str, messages: list[EmailMessage], run_id: str) -> dict:
    result = {"status": "failed", "run_id": run_id, "inserted_count": 0, "message_ids": []}
    client = None
    stage, submitting, pending_id = "credentials", False, None
    try:
        code = read_authorization_code()
        stage = "connect"
        client = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=ssl.create_default_context(), timeout=TIMEOUT)
        stage = "login"
        if client.login(address, code)[0] != "OK":
            raise RuntimeError("Login was not confirmed.")
        code = None
        stage = "inbox"
        if client.select("INBOX", readonly=True)[0] != "OK":
            raise RuntimeError("Inbox is unavailable.")
        for message in messages:
            stage = "serialize"
            raw = message.as_bytes()
            internal_date = imaplib.Time2Internaldate(parsedate_to_datetime(message["Date"]))
            pending_id = str(message["Message-ID"])
            stage, submitting = "append", True
            status, _ = client.append("INBOX", None, internal_date, raw)
            if status != "OK":
                # NO/BAD is explicit rejection; treat an unknown response as potentially written and do not fabricate a retryable result.
                submitting = status not in {"NO", "BAD"}
                raise RuntimeError("APPEND was not confirmed.")
            submitting = False
            result["message_ids"].append(pending_id)
            result["inserted_count"] += 1
        result["status"] = "completed"
    except Exception as error:
        result.update(status="uncertain" if submitting else "failed", stage=stage, error_type=type(error).__name__)
        result["message"] = "Write result is unknown. Check the mailbox by batch subject and Message-ID; do not rerun directly." if submitting else "Operation failed. Confirmed messages remain; check authorization, IMAP state, and the inserted count."
        if submitting:
            result["uncertain_message_id"] = pending_id
        logger.error("qq_test_injection_failed run_id=%s stage=%s error_type=%s confirmed=%s", run_id, stage, type(error).__name__, result["inserted_count"])
    finally:
        if client is not None:
            try:
                client.logout()
            except (OSError, imaplib.IMAP4.error) as error:
                logger.warning("qq_test_logout_failed run_id=%s error_type=%s", run_id, type(error).__name__)
            finally:
                try:
                    client.shutdown()
                except OSError as error:
                    logger.warning("qq_test_shutdown_failed run_id=%s error_type=%s", run_id, type(error).__name__)
    return result


# Function: Run the independent QQ test injector.
# Inputs: `argv` is a command-line list; no value uses process arguments; the default template is beside this script.
# Outputs: Safe JSON on stdout; success or preview returns 0, failure returns 1, and argument errors exit 2.
# Logic: Require either `--dry-run` or `--apply`; preview validates and constructs messages only, while explicit writing reads authorization codes.
# Constraints: Do not read the main project's `.env` or credentials; every batch differs, and repeated `--apply` creates new messages without deduplication or automatic retry.
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Append synthetic SalesMate test messages to your own QQ inbox; does not send external mail.")
    parser.add_argument("--messages-file", type=Path, default=DEFAULT_MESSAGES_PATH)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Validate and preview only; do not connect to a mailbox")
    mode.add_argument("--apply", action="store_true", help="Explicitly allow appending template messages to your own QQ inbox")
    args = parser.parse_args(argv)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
    try:
        address, scenarios = load_test_plan(args.messages_file)
        messages = build_test_messages(address, scenarios, run_id)
    except (OSError, UnicodeError, ValueError) as error:
        print(json.dumps({"status": "failed", "stage": "plan", "error_type": type(error).__name__, "message": "The template could not be read or contains invalid fields. Check the file and QQ address against the README."}, ensure_ascii=False))
        return 1
    if args.dry_run:
        result = {"status": "dry_run", "run_id": run_id, "mailbox_address": address, "message_count": len(messages), "messages": [{"from": str(message["From"]), "subject": str(message["Subject"])} for message in messages]}
    else:
        result = inject_messages(address, messages, run_id)
        result["mailbox_address"] = address
    result["search_subject"] = f"[SalesMate测试:QQ:{run_id}]"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"completed", "dry_run"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
