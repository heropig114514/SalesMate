"""职责：将合成邮件追加到开发者自己的 QQ 收件箱以验证同步与分析。
实现：标准库校验 JSON 并生成 MIME；仅显式 --apply 使用固定 TLS IMAP APPEND，失败不重试。
关联：qq_test_messages.template.json 提供场景；打包流水线仅运行 --dry-run，不依赖主项目。
目录：
- load_test_plan：校验 QQ 收件账号和合成场景。
- build_test_messages：生成带批次标记的 MIME 邮件。
- read_authorization_code：从环境或无回显提示读取授权码。
- inject_messages：登录同一账号并逐封追加到 INBOX。
- main：解析显式模式并输出安全 JSON 结果。
变量索引：
- TOOL_DIR：独立脚本所在目录。
- DEFAULT_MESSAGES_PATH：默认 QQ 示例文件。
- IMAP_HOST：固定 imap.qq.com。
- IMAP_PORT：固定 TLS 端口 993。
- TIMEOUT：网络等待上限 30 秒。
- logger：仅记录失败阶段、批次与错误类型。
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


# 功能：完整验证模板后才允许任何外部操作。
# 输入：`path` 为 JSON 文件路径。
# 输出：规范化 QQ 地址与场景数组；非法字段抛 ValueError。
# 逻辑：字段白名单、非空正文和单行头部校验；每封邮件收件账号由顶层唯一指定。
# 约束：不读取授权码，不允许覆盖服务器、收件人或文件夹，不推断邮箱别名。
def load_test_plan(path: Path) -> tuple[str, list[dict[str, str]]]:
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict) or set(document) != {"mailbox_address", "messages"}:
        raise ValueError("JSON 仅接受 mailbox_address 和 messages。")
    address = document["mailbox_address"]
    if not isinstance(address, str) or not re.fullmatch(r"[A-Za-z0-9_.+-]+@(qq|foxmail)\.com", address, re.IGNORECASE):
        raise ValueError("mailbox_address 必须是完整 QQ 或 foxmail 地址。")
    scenarios = document["messages"]
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("messages 必须是非空数组。")
    for index, item in enumerate(scenarios):
        if not isinstance(item, dict) or set(item) != {"from", "subject", "body"}:
            raise ValueError(f"messages[{index}] 仅接受 from、subject、body。")
        for key, value in item.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"messages[{index}].{key} 必须是非空文本。")
            if key != "body" and any(char in value for char in ("\r", "\n", "\x00")):
                raise ValueError(f"messages[{index}].{key} 不能包含头部控制字符。")
        sender = parseaddr(item["from"])[1]
        if not re.fullmatch(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", sender):
            raise ValueError(f"messages[{index}].from 必须包含完整邮箱地址。")
    return address.casefold(), scenarios


# 功能：构造唯一批次的合成邮件。
# 输入：`address` 为已校验收件账号；`scenarios` 为已校验场景；`run_id` 为内部生成批次。
# 输出：SMTP CRLF 策略的 EmailMessage 数组。
# 逻辑：保留场景正文，附加主题标记、唯一 Message-ID 和过去的 Date，便于同步范围选择。
# 约束：不执行 SMTP；日期不超出当前时刻，避免刚生成的邮件落在同步上界之后。
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


# 功能：取得本次显式写入所需的客户端授权码。
# 输入：无参数；读取 QQ_TEST_AUTHORIZATION_CODE 或终端交互。
# 输出：16 位字母授权码；不可安全输入或格式错误则抛异常。
# 逻辑：环境变量存在时使用其值，否则要求无回显 getpass；拒绝 getpass 的回显降级。
# 约束：不接受命令行授权码，不落盘或输出授权码；仅由 --apply 路径调用。
def read_authorization_code() -> str:
    code = os.environ.get("QQ_TEST_AUTHORIZATION_CODE")
    if code is None:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            code = getpass.getpass("QQ 客户端授权码（不回显）：")
    if not re.fullmatch(r"[A-Za-z]{16}", code):
        raise ValueError("QQ 客户端授权码必须是 16 位字母。")
    return code


# 功能：向已登录账号的 INBOX 逐封追加合成邮件。
# 输入：`address` 为唯一目标兼登录账号；`messages` 为构造的 MIME；`run_id` 为批次标识。
# 输出：状态、已确认数量、Message-ID 和必要的未知结果提示；不返回凭证或服务端原文。
# 逻辑：验证 TLS、登录和 INBOX 后逐封 APPEND；明确 NO/BAD 或异常立即停止，保留先前成功结果。
# 约束：APPEND 中断可能已写入，返回 uncertain 且不重试；不删除、EXPUNGE、发送或回退到 SMTP。
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
            raise RuntimeError("登录未确认。")
        code = None
        stage = "inbox"
        if client.select("INBOX", readonly=True)[0] != "OK":
            raise RuntimeError("收件箱不可用。")
        for message in messages:
            stage = "serialize"
            raw = message.as_bytes()
            internal_date = imaplib.Time2Internaldate(parsedate_to_datetime(message["Date"]))
            pending_id = str(message["Message-ID"])
            stage, submitting = "append", True
            status, _ = client.append("INBOX", None, internal_date, raw)
            if status != "OK":
                # NO/BAD 是明确拒绝；未知响应仍按可能写入处理，不伪造可重试结果。
                submitting = status not in {"NO", "BAD"}
                raise RuntimeError("APPEND 未确认。")
            submitting = False
            result["message_ids"].append(pending_id)
            result["inserted_count"] += 1
        result["status"] = "completed"
    except Exception as error:
        result.update(status="uncertain" if submitting else "failed", stage=stage, error_type=type(error).__name__)
        result["message"] = "写入结果未知，请按批次主题及 Message-ID 核对邮箱，勿直接重跑。" if submitting else "操作失败；已确认的邮件保留，请核对授权、IMAP 状态及已写入数量。"
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


# 功能：运行独立 QQ 测试注入器。
# 输入：`argv` 为命令行列表，None 使用进程参数；默认模板位于脚本旁。
# 输出：stdout 安全 JSON；成功或预览返回 0，失败返回 1，参数错误退出 2。
# 逻辑：强制选择 --dry-run 或 --apply；预览仅校验并构造邮件，显式写入才读取授权码。
# 约束：不读取主项目 .env 或凭证；每次批次不同，重复 --apply 会创建新邮件，不去重或自动重试。
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="向自己的 QQ 收件箱追加 SalesMate 合成测试邮件；不向外部发信。")
    parser.add_argument("--messages-file", type=Path, default=DEFAULT_MESSAGES_PATH)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="仅校验和预览，不连接邮箱")
    mode.add_argument("--apply", action="store_true", help="明确允许将模板邮件追加到自己的 QQ 收件箱")
    args = parser.parse_args(argv)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
    try:
        address, scenarios = load_test_plan(args.messages_file)
        messages = build_test_messages(address, scenarios, run_id)
    except (OSError, UnicodeError, ValueError) as error:
        print(json.dumps({"status": "failed", "stage": "plan", "error_type": type(error).__name__, "message": "模板无法读取或字段无效，请按 README 检查文件和 QQ 地址。"}, ensure_ascii=False))
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
