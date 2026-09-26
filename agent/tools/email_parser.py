"""Gmail raw MIME 邮件解析工具，不调用 LLM。"""

import base64
import binascii
import re
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime

from bs4 import BeautifulSoup


_REQUIRED_HEADERS = ("list-unsubscribe", "precedence", "auto-submitted")
_HISTORY_BOUNDARY_PATTERNS = (
    re.compile(r"^\s*On\s+.+\s+wrote:\s*$", re.IGNORECASE),
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$", re.IGNORECASE),
    re.compile(r"^\s*-{2,}\s*Forwarded message\s*-{2,}\s*$", re.IGNORECASE),
    re.compile(r"^\s*Begin forwarded message:\s*$", re.IGNORECASE),
)
_QUOTED_LINE_PATTERN = re.compile(r"^\s*>")


def parse_raw_email(
    raw: str,
    *,
    message_id: str,
    thread_id: str | None,
    received_at: str | None = None,
) -> dict:
    """完整解码 Gmail Base64URL raw MIME，并返回内部标准化邮件。"""
    message = _decode_raw_message(raw)
    body_text = _extract_body_text(message)

    from_addresses = _header_addresses(message, "From")
    return {
        "gmail_message_id": message_id,
        "thread_id": thread_id,
        "received_at": received_at,
        "from": from_addresses[0] if from_addresses else None,
        "to": _header_addresses(message, "To"),
        "cc": _header_addresses(message, "Cc"),
        "sent_at": _sent_at(message),
        "subject": _header_text(message, "Subject"),
        "body_text": body_text,
        "eligible_body_text": _eligible_body_text(body_text),
        "headers": {
            name: _header_text(message, name)
            for name in _REQUIRED_HEADERS
        },
    }


def _decode_raw_message(raw: str) -> Message:
    """严格解码 Base64URL，并隐藏底层解码或 MIME 解析细节。"""
    try:
        encoded = raw.encode("ascii")
        encoded += b"=" * (-len(encoded) % 4)
        data = base64.b64decode(encoded, altchars=b"-_", validate=True)
    except (AttributeError, UnicodeEncodeError, ValueError, binascii.Error):
        raise RuntimeError("Email content decoding failed.") from None

    try:
        return BytesParser(policy=policy.default).parsebytes(data)
    except Exception:
        raise RuntimeError("Email format parsing failed.") from None


def _header_text(message: Message, name: str) -> str:
    """使用 email header registry 解码 RFC 编码头字段。"""
    value = message.get(name)
    return str(value) if value is not None else ""


def _header_addresses(message: Message, name: str) -> list[str]:
    """按头字段原始顺序提取基本合法的 bare mailbox。"""
    values = message.get_all(name, [])
    decoded_values = [str(value) for value in values]
    return [
        address
        for _, address in getaddresses(decoded_values)
        if _is_valid_mailbox(address)
    ]


def _is_valid_mailbox(value: str) -> bool:
    """接受含单个 @、非空 local/domain 且无空白的 mailbox。"""
    address = value.strip()
    if address.count("@") != 1:
        return False
    local_part, domain = address.rsplit("@", 1)
    return bool(
        local_part
        and domain
        and not any(character.isspace() for character in address)
    )


def _sent_at(message: Message) -> str | None:
    """仅将可可靠解析且明确带时区的 MIME Date 规范化为 ISO8601。"""
    value = message.get("Date")
    if value is None:
        return None
    try:
        parsed = parsedate_to_datetime(str(value))
        offset = parsed.utcoffset() if parsed.tzinfo is not None else None
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None or offset is None:
        return None
    return parsed.isoformat()


def _extract_body_text(message: Message) -> str:
    """排除附件后优先首个非空纯文本，否则回退到首个可读 HTML。"""
    html_candidates: list[str] = []

    for part in message.walk():
        if part.is_multipart() or _is_attachment(part):
            continue

        content_type = part.get_content_type().lower()
        if content_type not in {"text/plain", "text/html"}:
            continue

        try:
            content = part.get_content()
        except (LookupError, UnicodeError, ValueError):
            continue
        if not isinstance(content, str) or not content.strip():
            continue

        if content_type == "text/plain":
            return content.strip()
        html_candidates.append(content)

    for html_content in html_candidates:
        visible_text = _html_to_visible_text(html_content)
        if visible_text:
            return visible_text

    return ""


def _eligible_body_text(body_text: str) -> str:
    """保守排除明确引用行及可识别历史块，不改变对外完整正文。"""
    eligible_lines: list[str] = []
    changed = False

    for line in body_text.splitlines():
        if any(pattern.fullmatch(line) for pattern in _HISTORY_BOUNDARY_PATTERNS):
            changed = True
            break
        if _QUOTED_LINE_PATTERN.match(line):
            changed = True
            continue
        eligible_lines.append(line)

    if not changed:
        return body_text
    return "\n".join(eligible_lines).strip()


def _is_attachment(part: Message) -> bool:
    """带 attachment disposition 或文件名的 MIME part 均不属于正文。"""
    return part.get_content_disposition() == "attachment" or bool(part.get_filename())


def _html_to_visible_text(content: str) -> str:
    """删除 HTML 非可见区域并转换为适合抽取的纯文本。"""
    html = BeautifulSoup(content, "html.parser")
    for element in html.find_all(("script", "style", "head")):
        element.decompose()
    return html.get_text(separator="\n", strip=True)
