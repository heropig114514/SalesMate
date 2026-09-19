"""职责：校验聊天 HTTP 输入，隔离浏览器写入与 Agent 契约。
实现：精确字段、类型、枚举和引用编号校验；不依赖 Agent 内部校验器。
关联：chat.views/services 使用，输出普通 JSON 值以便幂等比较。
目录：
- fields：验证精确字段集合。
- text：验证非空字符串。
- identifier：解析 UUID。
- report：校验终态结果和引用结构。
变量索引：
- ERROR_MESSAGES：允许持久化的 Agent 错误及安全展示文案。
- REPORT_FIELDS：Agent 回报的精确字段集合。
"""

import re
import uuid

from rest_framework.exceptions import ValidationError

ERROR_MESSAGES = {
    "invalid_request": "回答请求无效。",
    "context_unavailable": "客户上下文暂时不可用。",
    "knowledge_unavailable": "回答所需资料暂时不可用。",
    "model_unavailable": "回答模型暂时不可用，请稍后重试。",
    "invalid_model_output": "回答模型返回了无效结果。",
}
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
# 输出：普通字典；非法字段、版本或引用抛 400。
# 逻辑：成功与失败互斥，正文引用必须覆盖全部有序引用；错误文案限安全集合。
# 约束：仅验证结构，来源授权和快照白名单由事务服务再次检查。
def report(value):
    fields(value, REPORT_FIELDS)
    identifier(value["request_id"])
    if value["chat_prompt_version"] != "chat-v2":
        raise ValidationError("不支持的 chat_prompt_version。")
    citations = value["citations"]
    if not isinstance(citations, list):
        raise ValidationError("citations 必须为数组。")
    identities = []
    for citation in citations:
        fields(citation, {"source_id", "source_type", "title_or_label"})
        identities.append(
            tuple(
                text(citation[key], key)
                for key in ("source_id", "source_type", "title_or_label")
            )
        )
    if len(set(identities)) != len(identities):
        raise ValidationError("引用身份不能重复。")
    if value["status"] == "completed":
        body = text(value["assistant_text"], "assistant_text")
        if value["error"] is not None:
            raise ValidationError("成功回答不能含错误。")
        markers = re.findall(r"\[(\d+)\]", body)
        # 集合比较同时拒绝 0、越界编号、前导零和未在正文使用的引用。
        if set(markers) != {str(index) for index in range(1, len(citations) + 1)}:
            raise ValidationError("正文引用编号与 citations 不一致。")
    elif value["status"] == "failed":
        if value["assistant_text"] != "" or citations:
            raise ValidationError("失败结果不得包含回答或引用。")
        fields(value["error"], {"code", "message"})
        code = text(value["error"]["code"], "error.code")
        if (
            code not in ERROR_MESSAGES
            or value["error"]["message"] != ERROR_MESSAGES[code]
        ):
            raise ValidationError("失败码或提示不符合安全错误契约。")
    else:
        raise ValidationError("回报状态必须为 completed 或 failed。")
    return dict(value)
