"""职责：校验聊天 HTTP 输入，隔离浏览器写入与 Agent 契约。
实现：校验字段、类型、存储长度和终态结构，不判定正文、来源或提示词版本的业务含义。
关联：chat.views/services 使用，输出普通 JSON 值以便幂等比较。
目录：
- fields：验证精确字段集合。
- text：验证非空字符串。
- identifier：解析 UUID。
- report：校验终态结果和引用结构。
变量索引：
- REPORT_FIELDS：Agent 回报的精确字段集合。
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


# 功能：拒绝未知或缺失字段。
# 输入：`value` 请求对象，`names` 必需字段集合。
# 输出：原对象，非法时抛 ValidationError。
# 逻辑：只允许字典且字段集合完全一致。
# 约束：不静默忽略额外身份、状态或覆盖参数。
def fields(value, names):
    if not isinstance(value, dict) or set(value) != set(names):
        raise ValidationError("请求字段必须与聊天契约完全一致。")
    return value


# 功能：验证必填字符串。
# 输入：`value` 字段值，`name` 安全字段名。
# 输出：保留原空格的非空文本。
# 逻辑：拒绝数字、容器及纯空白，不改变证据或幂等内容。
# 约束：异常仅包含字段名，不包含用户正文。
def text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} 必须为非空字符串。")
    return value


# 功能：解析客户端标识。
# 输入：`value` UUID 文本。
# 输出：UUID，非法时统一 400。
# 逻辑：先限制字符串再解析，避免 ORM 转换错误泄露。
# 约束：不查询或推断记录归属。
def identifier(value):
    try:
        return uuid.UUID(text(value, "id"))
    except (ValueError, AttributeError):
        raise ValidationError("id 必须为有效 UUID。") from None


# 功能：规范并校验 Agent 结果。
# 输入：`value` JSON 回报。
# 输出：普通字典；字段、类型、长度或终态结构不合法时抛 400。
# 逻辑：版本与错误为非空文本，引用为三字段数组；保持 completed/failed 的互斥载荷结构。
# 约束：不扫描正文编号、不去重或验证来源真实性；权限与请求状态由事务服务检查，Agent 负责内容与错误脱敏。
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
