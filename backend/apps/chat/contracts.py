"""Responsibility: Validate chat HTTP input and isolate browser writes from Agent contract.
Implementation: Validate fields, types, storage lengths, and terminal-state structure without deciding business meaning of content, sources, or prompt version.
Relationships: Used by ``chat.views`` and ``services`` and returns ordinary JSON values for idempotency comparison.
Directory:
- fields: Validate exact field set.
- text: Validate nonempty string.
- identifier: Parse UUID.
- report: Validate terminal result and citation structure.
Variable index:
- REPORT_FIELDS: Exact field set for Agent reports.
"""

import uuid

from rest_framework.exceptions import ValidationError

REPORT_FIELDS = {
    "request_id",
    "chat_prompt_version",
    "assistant_text",
    "citations",
    "status",
    "error",
}


# Function: Reject unknown or missing fields.
# Inputs: Request object ``value`` and required field set ``names``.
# Outputs: Original object; invalid input raises ``ValidationError``.
# Logic: Allows only a dictionary with an exactly matching field set.
# Constraints: Does not silently ignore extra identity, state, or override parameters.
def fields(value, names):
    if not isinstance(value, dict) or set(value) != set(names):
        raise ValidationError("请求字段必须与聊天契约完全一致。")
    return value


# Function: Validate a required string.
# Inputs: Field value ``value`` and safe field name ``name``.
# Outputs: Nonempty text retaining original whitespace.
# Logic: Rejects numbers, containers, and whitespace-only values without changing evidence or idempotency content.
# Constraints: Exceptions contain field name only, never user content.
def text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} 必须为非空字符串。")
    return value


# Function: Parse a client identifier.
# Inputs: UUID text ``value``.
# Outputs: UUID; invalid input consistently produces 400.
# Logic: Restricts string before parsing to avoid exposing ORM conversion errors.
# Constraints: Does not query or infer record ownership.
def identifier(value):
    try:
        return uuid.UUID(text(value, "id"))
    except (ValueError, AttributeError):
        raise ValidationError("id 必须为有效 UUID。") from None


# Function: Normalize and validate an Agent result.
# Inputs: JSON report ``value``.
# Outputs: Ordinary dictionary; invalid field, type, length, or terminal-state structure raises 400.
# Logic: Version and error are nonempty text, citations are three-field arrays, and completed or failed payload structures remain mutually exclusive.
# Constraints: Does not scan content numbering, deduplicate, or verify source truth; transaction service checks permission and request state while Agent owns content and error redaction.
def report(value):
    fields(value, REPORT_FIELDS)
    identifier(value["request_id"])
    if len(text(value["chat_prompt_version"], "chat_prompt_version")) > 100:
        raise ValidationError("chat_prompt_version 最多 100 个字符。")
    citations = value["citations"]
    if not isinstance(citations, list):
        raise ValidationError("citations 必须为数组。")
    for citation in citations:
        fields(citation, {"source_id", "source_type", "title_or_label"})
        for key in ("source_id", "source_type", "title_or_label"):
            text(citation[key], key)
        if len(citation["source_type"]) > 80:
            raise ValidationError("source_type 最多 80 个字符。")
    if value["status"] == "completed":
        text(value["assistant_text"], "assistant_text")
        if value["error"] is not None:
            raise ValidationError("成功回答不能含错误。")
    elif value["status"] == "failed":
        if value["assistant_text"] != "" or citations:
            raise ValidationError("失败结果不得包含回答或引用。")
        fields(value["error"], {"code", "message"})
        text(value["error"]["code"], "error.code")
        text(value["error"]["message"], "error.message")
    else:
        raise ValidationError("回报状态必须为 completed 或 failed。")
    return dict(value)
