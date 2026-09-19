"""职责：提供客户证据问答及统一聊天消费入口。
实现：保持客户严格证据流程；明确空客户请求分派 general_chat，不修改原模型参数与裁剪预算。
关联：后端 chat 服务、sales-chat Skill、general_chat 通用流程。
目录：
- ChatValidationError：聊天边界数据不符合当前 Demo 的严格契约。
- parse_recent_history：严格解析后端按原序返回的 user/assistant 最近历史。
- parse_conversation_request：解析领取请求；缺失或与已知绑定不符的 company_id 均为无效请求。
- parse_context_item：严格解析一个可引用来源；空 content 只能作为无证据元数据保留。
- deduplicate_context_items：按完整四字段去重并保留第一次出现的 Context Item。
- parse_retrieval_gap：解析不含原始提供商细节的稳定检索缺口对象。
- parse_answer_context：严格解析一次 request-bound 的 internal 或 external 上下文响应。
- trim_recent_history：删除完整的最旧消息，直到保留历史的 content 字符数不超预算。
- trim_context_items：按 customer → internal → external 优先级去重、限量并截断内容。
- build_chat_messages：构造 Skill、上下文、原序历史、当前问题组成的固定顺序消息。
- validate_citation_allowlist：要求每个 citation 三元组精确命中本次上下文中的同一来源。
- parse_model_candidate：解析模型 JSON，校验精确 Schema、marker 和 Citation 白名单闭包。
- parse_report_answer：严格解析 completed/failed Report Answer，并返回稳定字段顺序的副本。
- stable_failure_result：为已识别 request_id 生成不含事实正文的固定失败结果。
- answer_conversation_request：用当前请求绑定的授权资料生成一次只读回答，模型调用至多一次。
- _completed_result：组装客户模式 completed 结果并经回报解析器校验。
- _recognizable_request_id：仅从映射中提取非空字符串请求标识，无法识别时返回 None。
- _failed_external_context：构造外部检索失败的严格上下文与安全缺口。
- _add_missing_failure_gap：只在缺少对应 scope 缺口时追加检索失败描述。
- _is_direct_tool_action：按中英文动作模式识别直接执行请求，排除教程式问法。
- _needs_matter_clarification：结合问题代词、剩余词段及历史判断是否需要澄清事项。
- _history_has_specific_matter：通过移除泛化词后的词元判断历史是否提及具体事项。
- _lexical_units：提取英文词及中文片段并移除既定停用词。
- _support_units：将词元与既定概念同义词映射成证据比较单元。
- _validate_answer_policy：串联客户回答的动作、证据、判断和冲突措辞约束。
- _is_insufficiency_only：识别短文本中不含引用和数字的明确资料不足声明。
- _validate_factual_sentence_markers：规范句末引用位置后逐句要求事实性文字带编号。
- _is_nonfactual_gap_sentence：识别不含引用的资料缺口或未确认措辞。
- _validate_deterministic_citation_support：逐句核对引用证据覆盖数值且存在共同词元或概念。
- _number_tokens：提取数值和百分号并归一化整数及小数尾零。
- _validate_judgment_wording：要求包含推测建议线索的文字明确标注判断或可能。
- _validate_conflict_wording：要求冲突声明引用至少两个来源且标注待确认。
- _flatten_context：按客户、内部、外部的固定顺序展开证据数组。
- _parse_context_item_array：校验数组后按索引路径解析每个来源条目。
- _deduplicate_parsed_context_items：按四字段身份保留首次出现的来源副本。
- _truncate_context_item：仅截断超预算正文并追加节选标记，保留来源身份。
- _context_item_key：按身份三字段及正文构造完整去重键。
- _citation_key：按来源标识、类型和标题构造引用身份键。
- _record_unique_citation_identity：记录来源身份对应正文并拒绝同身份的内容冲突。
- _ensure_unique_citation_identities：遍历来源并验证同一引用身份只有一种正文。
- _parse_citations：严格解析有序的三字段引用数组并拒绝重复。
- _validate_citation_markers：检查正文编号集合与引用数组位置完整一致。
- _parse_report_error：要求错误代码及文案精确命中安全失败集合。
- _untrusted_block：将证据序列化为明确标注不可信数据的消息块。
- _decode_json_object：解析 JSON 对象并拒绝无效语法、重复键和非对象根值。
- _unique_json_object：从有序键值对构造对象并拒绝重复键。
- _object：要求输入为映射对象，否则抛带字段路径的校验异常。
- _array：要求输入为 list，否则抛带字段路径的校验异常。
- _keys：要求对象键集合与协议字段精确一致。
- _string：要求输入为字符串，不做隐式类型转换。
- _nonblank：要求字符串去空白后非空，返回原始字符串。
- _enum：要求字符串属于明确的枚举集合。
- _boolean：只接受 bool 类型，不把整数当作布尔值。
- _nonnegative_integer：只接受非负整数预算，排除 bool 类型。
- bailian_chat_provider：通过聊天专用百炼边界发送有序消息，不提供任何工具能力。
- process_chat_once：领取一次请求，按可空客户绑定路由客户或通用问答，并尝试一次回报。
变量索引：
- _SALES_CHAT_SKILL：客户聊天 Skill 元数据。
- CHAT_PROMPT：客户专用提示。
- CHAT_PROMPT_VERSION：客户提示版本。
- CHAT_MAX_TOKENS：原输出 token 上限。
- DEFAULT_HISTORY_CHARACTER_BUDGET：原历史字符预算。
- DEFAULT_CONTEXT_ITEM_LIMIT：原证据条数预算。
- DEFAULT_CONTEXT_CONTENT_CHARACTER_LIMIT：原单条证据字符预算。
- TRUNCATION_MARKER：裁剪标记。
- _REQUEST_FIELDS：对应协议字段或枚举白名单。
- _HISTORY_FIELDS：对应协议字段或枚举白名单。
- _CONTEXT_ITEM_FIELDS：对应协议字段或枚举白名单。
- _ANSWER_CONTEXT_FIELDS：对应协议字段或枚举白名单。
- _RETRIEVAL_GAP_FIELDS：对应协议字段或枚举白名单。
- _CANDIDATE_FIELDS：对应协议字段或枚举白名单。
- _CITATION_FIELDS：对应协议字段或枚举白名单。
- _REPORT_FIELDS：对应协议字段或枚举白名单。
- _ERROR_FIELDS：对应协议字段或枚举白名单。
- _HISTORY_ROLES：对应协议字段或枚举白名单。
- _CONTEXT_SCOPES：对应协议字段或枚举白名单。
- _KNOWLEDGE_STATUSES：对应协议字段或枚举白名单。
- _REPORT_STATUSES：对应协议字段或枚举白名单。
- _FAILURE_MESSAGES：安全错误文案。
- _CITATION_MARKER：引用编号匹配表达式。
- _LEXICAL_STOP_UNITS：相关性词元中的泛化停用词。
- _SUPPORT_CONCEPTS：确定性证据比较的同义概念词组。
- __all__：公开导出符号。
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from agent.skills import load_skill


_SALES_CHAT_SKILL = load_skill("sales-chat")
CHAT_PROMPT = _SALES_CHAT_SKILL.instructions
CHAT_PROMPT_VERSION = _SALES_CHAT_SKILL.version
CHAT_MAX_TOKENS = _SALES_CHAT_SKILL.max_tokens

DEFAULT_HISTORY_CHARACTER_BUDGET = 6_000
DEFAULT_CONTEXT_ITEM_LIMIT = 12
DEFAULT_CONTEXT_CONTENT_CHARACTER_LIMIT = 2_000
TRUNCATION_MARKER = "...[truncated]"

_REQUEST_FIELDS = {
    "request_id",
    "conversation_id",
    "company_id",
    "user_message_id",
    "question",
    "recent_history",
}
_HISTORY_FIELDS = {"role", "content"}
_CONTEXT_ITEM_FIELDS = {"source_id", "source_type", "title_or_label", "content"}
_ANSWER_CONTEXT_FIELDS = {
    "request_id",
    "scope",
    "customer_context",
    "context_items",
    "customer_context_status",
    "knowledge_status",
    "retrieval_gaps",
    "external_available",
}
_RETRIEVAL_GAP_FIELDS = {"scope", "code", "message"}
_CANDIDATE_FIELDS = {"assistant_text", "citations"}
_CITATION_FIELDS = {"source_id", "source_type", "title_or_label"}
_REPORT_FIELDS = {
    "request_id",
    "chat_prompt_version",
    "assistant_text",
    "citations",
    "status",
    "error",
}
_ERROR_FIELDS = {"code", "message"}
_HISTORY_ROLES = frozenset({"user", "assistant"})
_CONTEXT_SCOPES = frozenset({"internal", "external"})
_KNOWLEDGE_STATUSES = frozenset({"completed", "failed"})
_REPORT_STATUSES = frozenset({"completed", "failed"})
_FAILURE_MESSAGES = {
    "invalid_request": "回答请求无效。",
    "context_unavailable": "客户上下文暂时不可用。",
    "knowledge_unavailable": "回答所需资料暂时不可用。",
    "model_unavailable": "回答模型暂时不可用，请稍后重试。",
    "invalid_model_output": "回答模型返回了无效结果。",
    "report_failed": "回答结果暂时无法保存。",
}
_CITATION_MARKER = re.compile(r"\[(\d+)\]")


# 功能：聊天边界数据不符合当前 Demo 的严格契约。
# 逻辑：以 ValueError 子类标识可控聊天契约失败。
# 约束：错误不包含服务凭证。
class ChatValidationError(ValueError):
    """聊天边界数据不符合当前 Demo 的严格契约。"""


# 功能：严格解析后端按原序返回的 user/assistant 最近历史。
# 输入：`value` 待验证数据。
# 输出：list[dict[str, str]]。
# 逻辑：严格解析后端按原序返回的 user/assistant 最近历史，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def parse_recent_history(value: object) -> list[dict[str, str]]:
    """严格解析后端按原序返回的 user/assistant 最近历史。"""
    history = _array(value, "recent_history")
    parsed: list[dict[str, str]] = []
    for index, raw_message in enumerate(history):
        path = f"recent_history[{index}]"
        message = _object(raw_message, path)
        _keys(message, _HISTORY_FIELDS, path)
        parsed.append(
            {
                "role": _enum(message["role"], _HISTORY_ROLES, f"{path}.role"),
                "content": _nonblank(message["content"], f"{path}.content"),
            }
        )
    return parsed


# 功能：解析领取请求；缺失或与已知绑定不符的 company_id 均为无效请求。
# 输入：`value` 待验证数据、`expected_company_id` 预期客户绑定。
# 输出：dict[str, Any]。
# 逻辑：解析领取请求；缺失或与已知绑定不符的 company_id 均为无效请求，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def parse_conversation_request(
    value: object,
    *,
    expected_company_id: str | None = None,
) -> dict[str, Any]:
    """解析领取请求；缺失或与已知绑定不符的 company_id 均为无效请求。"""
    request = _object(value, "request")
    _keys(request, _REQUEST_FIELDS, "request")
    company_id = _nonblank(request["company_id"], "request.company_id")
    if expected_company_id is not None:
        expected = _nonblank(expected_company_id, "expected_company_id")
        if company_id != expected:
            raise ChatValidationError("request.company_id 与当前客户绑定不一致。")

    return {
        "request_id": _nonblank(request["request_id"], "request.request_id"),
        "conversation_id": _nonblank(
            request["conversation_id"], "request.conversation_id"
        ),
        "company_id": company_id,
        "user_message_id": _nonblank(
            request["user_message_id"], "request.user_message_id"
        ),
        "question": _nonblank(request["question"], "request.question"),
        "recent_history": parse_recent_history(request["recent_history"]),
    }


# 功能：严格解析一个可引用来源；空 content 只能作为无证据元数据保留。
# 输入：`value` 待验证数据、`path` 错误定位路径。
# 输出：dict[str, str]。
# 逻辑：严格解析一个可引用来源；空 content 只能作为无证据元数据保留，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def parse_context_item(value: object, *, path: str = "context_item") -> dict[str, str]:
    """严格解析一个可引用来源；空 content 只能作为无证据元数据保留。"""
    item = _object(value, path)
    _keys(item, _CONTEXT_ITEM_FIELDS, path)
    return {
        "source_id": _nonblank(item["source_id"], f"{path}.source_id"),
        "source_type": _nonblank(item["source_type"], f"{path}.source_type"),
        "title_or_label": _nonblank(item["title_or_label"], f"{path}.title_or_label"),
        "content": _string(item["content"], f"{path}.content"),
    }


# 功能：按完整四字段去重并保留第一次出现的 Context Item。
# 输入：`value` 待验证数据。
# 输出：list[dict[str, str]]。
# 逻辑：按完整四字段去重并保留第一次出现的 Context Item，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def deduplicate_context_items(value: object) -> list[dict[str, str]]:
    """按完整四字段去重并保留第一次出现的 Context Item。"""
    items = _array(value, "context_items")
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for index, raw_item in enumerate(items):
        item = parse_context_item(raw_item, path=f"context_items[{index}]")
        key = _context_item_key(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


# 功能：解析不含原始提供商细节的稳定检索缺口对象。
# 输入：`value` 待验证数据、`path` 错误定位路径。
# 输出：dict[str, str]。
# 逻辑：解析不含原始提供商细节的稳定检索缺口对象，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def parse_retrieval_gap(
    value: object, *, path: str = "retrieval_gap"
) -> dict[str, str]:
    """解析不含原始提供商细节的稳定检索缺口对象。"""
    gap = _object(value, path)
    _keys(gap, _RETRIEVAL_GAP_FIELDS, path)
    return {
        "scope": _nonblank(gap["scope"], f"{path}.scope"),
        "code": _nonblank(gap["code"], f"{path}.code"),
        "message": _nonblank(gap["message"], f"{path}.message"),
    }


# 功能：严格解析一次 request-bound 的 internal 或 external 上下文响应。
# 输入：`value` 待验证数据、`expected_request_id` 预期请求标识、`expected_scope` 预期知识范围。
# 输出：dict[str, Any]。
# 逻辑：严格解析一次 request-bound 的 internal 或 external 上下文响应，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def parse_answer_context(
    value: object,
    *,
    expected_request_id: str | None = None,
    expected_scope: str | None = None,
) -> dict[str, Any]:
    """严格解析一次 request-bound 的 internal 或 external 上下文响应。"""
    context = _object(value, "answer_context")
    _keys(context, _ANSWER_CONTEXT_FIELDS, "answer_context")

    request_id = _nonblank(context["request_id"], "answer_context.request_id")
    if expected_request_id is not None:
        expected_id = _nonblank(expected_request_id, "expected_request_id")
        if request_id != expected_id:
            raise ChatValidationError("answer_context.request_id 与当前请求不一致。")

    scope = _enum(context["scope"], _CONTEXT_SCOPES, "answer_context.scope")
    if expected_scope is not None:
        expected = _enum(expected_scope, _CONTEXT_SCOPES, "expected_scope")
        if scope != expected:
            raise ChatValidationError("answer_context.scope 与请求范围不一致。")

    customer_status = _nonblank(
        context["customer_context_status"],
        "answer_context.customer_context_status",
    )
    if scope == "internal":
        if customer_status not in {"completed", "failed"}:
            raise ChatValidationError(
                "internal answer_context.customer_context_status 枚举值无效。"
            )
    elif customer_status != "not_applicable":
        raise ChatValidationError(
            "external answer_context.customer_context_status 必须为 not_applicable。"
        )

    external_available = _boolean(
        context["external_available"], "answer_context.external_available"
    )
    if scope == "external" and not external_available:
        raise ChatValidationError(
            "external answer_context.external_available 必须为 true。"
        )

    customer_context = _parse_context_item_array(
        context["customer_context"], "answer_context.customer_context"
    )
    if scope == "external" and customer_context:
        raise ChatValidationError("external answer_context 不得包含 customer_context。")

    context_items = _parse_context_item_array(
        context["context_items"], "answer_context.context_items"
    )
    _ensure_unique_citation_identities(
        [*customer_context, *context_items],
        "answer_context",
    )
    raw_gaps = _array(context["retrieval_gaps"], "answer_context.retrieval_gaps")
    retrieval_gaps = [
        parse_retrieval_gap(item, path=f"answer_context.retrieval_gaps[{index}]")
        for index, item in enumerate(raw_gaps)
    ]

    return {
        "request_id": request_id,
        "scope": scope,
        "customer_context": _deduplicate_parsed_context_items(customer_context),
        "context_items": _deduplicate_parsed_context_items(context_items),
        "customer_context_status": customer_status,
        "knowledge_status": _enum(
            context["knowledge_status"],
            _KNOWLEDGE_STATUSES,
            "answer_context.knowledge_status",
        ),
        "retrieval_gaps": retrieval_gaps,
        "external_available": external_available,
    }


# 功能：删除完整的最旧消息，直到保留历史的 content 字符数不超预算。
# 输入：`value` 待验证数据、`character_budget` 既定字符预算。
# 输出：list[dict[str, str]]。
# 逻辑：删除完整的最旧消息，直到保留历史的 content 字符数不超预算，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def trim_recent_history(
    value: object,
    *,
    character_budget: int = DEFAULT_HISTORY_CHARACTER_BUDGET,
) -> list[dict[str, str]]:
    """删除完整的最旧消息，直到保留历史的 content 字符数不超预算。"""
    budget = _nonnegative_integer(character_budget, "character_budget")
    history = parse_recent_history(value)
    total = sum(len(message["content"]) for message in history)
    first_retained = 0
    while first_retained < len(history) and total > budget:
        total -= len(history[first_retained]["content"])
        first_retained += 1
    return [dict(message) for message in history[first_retained:]]


# 功能：按 customer → internal → external 优先级去重、限量并截断内容。
# 输入：`customer_context` 客户证据、`internal_knowledge` 内部知识、`external_knowledge` 外部知识、`item_limit` 最大来源条数、`content_character_limit` 既定单条字符预算。
# 输出：dict[str, list[dict[str, str]]]。
# 逻辑：按 customer → internal → external 优先级去重、限量并截断内容，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def trim_context_items(
    customer_context: object,
    internal_knowledge: object,
    external_knowledge: object,
    *,
    item_limit: int = DEFAULT_CONTEXT_ITEM_LIMIT,
    content_character_limit: int = DEFAULT_CONTEXT_CONTENT_CHARACTER_LIMIT,
) -> dict[str, list[dict[str, str]]]:
    """按 customer → internal → external 优先级去重、限量并截断内容。"""
    maximum_items = _nonnegative_integer(item_limit, "item_limit")
    maximum_content = _nonnegative_integer(
        content_character_limit, "content_character_limit"
    )
    groups = (
        (
            "customer_context",
            _parse_context_item_array(customer_context, "customer_context"),
        ),
        (
            "internal_knowledge",
            _parse_context_item_array(internal_knowledge, "internal_knowledge"),
        ),
        (
            "external_knowledge",
            _parse_context_item_array(external_knowledge, "external_knowledge"),
        ),
    )
    result: dict[str, list[dict[str, str]]] = {
        "customer_context": [],
        "internal_knowledge": [],
        "external_knowledge": [],
    }
    seen: set[tuple[str, str, str, str]] = set()
    source_contents: dict[tuple[str, str, str], str] = {}
    retained_count = 0
    for group_name, items in groups:
        for item in items:
            _record_unique_citation_identity(item, source_contents, group_name)
            if not item["content"].strip():
                continue
            key = _context_item_key(item)
            if key in seen:
                continue
            seen.add(key)
            if retained_count >= maximum_items:
                continue
            result[group_name].append(
                _truncate_context_item(item, content_character_limit=maximum_content)
            )
            retained_count += 1
    return result


# 功能：构造 Skill、上下文、原序历史、当前问题组成的固定顺序消息。
# 输入：`request` 后端领取的请求、`internal_context` 内部上下文、`external_context` 外部上下文、`expected_company_id` 预期客户绑定、`history_character_budget` 历史字符预算、`context_item_limit` 既定条目预算、`context_content_character_limit` 单条来源字符预算。
# 输出：list[dict[str, str]]。
# 逻辑：构造 Skill、上下文、原序历史、当前问题组成的固定顺序消息，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def build_chat_messages(
    request: object,
    *,
    internal_context: object,
    external_context: object | None = None,
    expected_company_id: str | None = None,
    history_character_budget: int = DEFAULT_HISTORY_CHARACTER_BUDGET,
    context_item_limit: int = DEFAULT_CONTEXT_ITEM_LIMIT,
    context_content_character_limit: int = DEFAULT_CONTEXT_CONTENT_CHARACTER_LIMIT,
) -> list[dict[str, str]]:
    """构造 Skill、上下文、原序历史、当前问题组成的固定顺序消息。"""
    parsed_request = parse_conversation_request(
        request, expected_company_id=expected_company_id
    )
    parsed_internal = parse_answer_context(
        internal_context,
        expected_request_id=parsed_request["request_id"],
        expected_scope="internal",
    )
    parsed_external = None
    if external_context is not None:
        parsed_external = parse_answer_context(
            external_context,
            expected_request_id=parsed_request["request_id"],
            expected_scope="external",
        )

    trimmed_context = trim_context_items(
        parsed_internal["customer_context"],
        parsed_internal["context_items"],
        parsed_external["context_items"] if parsed_external is not None else [],
        item_limit=context_item_limit,
        content_character_limit=context_content_character_limit,
    )
    retrieval_gaps = list(parsed_internal["retrieval_gaps"])
    if parsed_external is not None:
        retrieval_gaps.extend(parsed_external["retrieval_gaps"])
    context_payload = {
        **trimmed_context,
        "retrieval_gaps": retrieval_gaps,
    }

    messages = [
        {"role": "system", "content": CHAT_PROMPT},
        {
            "role": "user",
            "content": _untrusted_block("UNTRUSTED_CONTEXT_DATA", context_payload),
        },
    ]
    retained_history = trim_recent_history(
        parsed_request["recent_history"],
        character_budget=history_character_budget,
    )
    messages.extend(
        {
            "role": message["role"],
            "content": _untrusted_block(
                "UNTRUSTED_HISTORY_DATA", {"content": message["content"]}
            ),
        }
        for message in retained_history
    )
    messages.append(
        {
            "role": "user",
            "content": _untrusted_block(
                "CURRENT_USER_QUESTION_UNTRUSTED_DATA",
                {"question": parsed_request["question"]},
            ),
        }
    )
    return messages


# 功能：要求每个 citation 三元组精确命中本次上下文中的同一来源。
# 输入：`citations` 有序引用、`allowed_context_items` 当前授权证据。
# 输出：list[dict[str, str]]。
# 逻辑：要求每个 citation 三元组精确命中本次上下文中的同一来源，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def validate_citation_allowlist(
    citations: object,
    allowed_context_items: object,
) -> list[dict[str, str]]:
    """要求每个 citation 三元组精确命中本次上下文中的同一来源。"""
    parsed_citations = _parse_citations(citations)
    allowed_items = _parse_context_item_array(
        allowed_context_items, "allowed_context_items"
    )
    _ensure_unique_citation_identities(allowed_items, "allowed_context_items")
    allowed_triples = {_citation_key(item) for item in allowed_items}
    for index, citation in enumerate(parsed_citations):
        if _citation_key(citation) not in allowed_triples:
            raise ChatValidationError(
                f"citations[{index}] 未精确命中当前请求的来源白名单。"
            )
    return parsed_citations


# 功能：解析模型 JSON，校验精确 Schema、marker 和 Citation 白名单闭包。
# 输入：`value` 待验证数据、`allowed_context_items` 当前授权证据。
# 输出：dict[str, Any]。
# 逻辑：解析模型 JSON，校验精确 Schema、marker 和 Citation 白名单闭包，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def parse_model_candidate(
    value: object,
    *,
    allowed_context_items: object,
) -> dict[str, Any]:
    """解析模型 JSON，校验精确 Schema、marker 和 Citation 白名单闭包。"""
    candidate_value = _decode_json_object(value) if isinstance(value, str) else value
    candidate = _object(candidate_value, "model_candidate")
    _keys(candidate, _CANDIDATE_FIELDS, "model_candidate")
    assistant_text = _nonblank(
        candidate["assistant_text"], "model_candidate.assistant_text"
    )
    citations = validate_citation_allowlist(
        candidate["citations"], allowed_context_items
    )
    _validate_citation_markers(assistant_text, citations)
    return {"assistant_text": assistant_text, "citations": citations}


# 功能：严格解析 completed/failed Report Answer，并返回稳定字段顺序的副本。
# 输入：`value` 待验证数据、`expected_request_id` 预期请求标识、`allowed_context_items` 当前授权证据。
# 输出：dict[str, Any]。
# 逻辑：严格解析 completed/failed Report Answer，并返回稳定字段顺序的副本，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def parse_report_answer(
    value: object,
    *,
    expected_request_id: str | None = None,
    allowed_context_items: object | None = None,
) -> dict[str, Any]:
    """严格解析 completed/failed Report Answer，并返回稳定字段顺序的副本。"""
    report = _object(value, "report_answer")
    _keys(report, _REPORT_FIELDS, "report_answer")
    request_id = _nonblank(report["request_id"], "report_answer.request_id")
    if expected_request_id is not None:
        expected = _nonblank(expected_request_id, "expected_request_id")
        if request_id != expected:
            raise ChatValidationError("report_answer.request_id 与当前请求不一致。")

    status = _enum(report["status"], _REPORT_STATUSES, "report_answer.status")
    prompt_version = _nonblank(
        report["chat_prompt_version"], "report_answer.chat_prompt_version"
    )
    if prompt_version != CHAT_PROMPT_VERSION:
        raise ChatValidationError("report_answer.chat_prompt_version 不受支持。")
    if status == "completed":
        assistant_text = _nonblank(
            report["assistant_text"], "report_answer.assistant_text"
        )
        citations = (
            validate_citation_allowlist(report["citations"], allowed_context_items)
            if allowed_context_items is not None
            else _parse_citations(report["citations"])
        )
        _validate_citation_markers(assistant_text, citations)
        if report["error"] is not None:
            raise ChatValidationError("completed report_answer.error 必须为 null。")
        error = None
    else:
        if report["assistant_text"] != "":
            raise ChatValidationError("failed report_answer.assistant_text 必须为空。")
        if _array(report["citations"], "report_answer.citations"):
            raise ChatValidationError("failed report_answer.citations 必须为空。")
        error = _parse_report_error(report["error"])
        assistant_text = ""
        citations = []

    return {
        "request_id": request_id,
        "chat_prompt_version": prompt_version,
        "assistant_text": assistant_text,
        "citations": citations,
        "status": status,
        "error": error,
    }


# 功能：为已识别 request_id 生成不含事实正文的固定失败结果。
# 输入：`request_id` 当前请求标识、`code` 稳定错误码。
# 输出：dict[str, Any]。
# 逻辑：为已识别 request_id 生成不含事实正文的固定失败结果，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def stable_failure_result(request_id: object, code: object) -> dict[str, Any]:
    """为已识别 request_id 生成不含事实正文的固定失败结果。"""
    parsed_request_id = _nonblank(request_id, "request_id")
    parsed_code = _nonblank(code, "code")
    if parsed_code not in _FAILURE_MESSAGES:
        raise ChatValidationError("code 不是受支持的稳定失败代码。")
    return {
        "request_id": parsed_request_id,
        "chat_prompt_version": CHAT_PROMPT_VERSION,
        "assistant_text": "",
        "citations": [],
        "status": "failed",
        "error": {
            "code": parsed_code,
            "message": _FAILURE_MESSAGES[parsed_code],
        },
    }


# 功能：用当前请求绑定的授权资料生成一次只读回答，模型调用至多一次。
# 输入：`request` 后端领取的请求、`backend` 后端客户端、`chat_provider` 单次模型调用边界。
# 输出：dict[str, Any]。
# 逻辑：用当前请求绑定的授权资料生成一次只读回答，模型调用至多一次，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def answer_conversation_request(
    request: Mapping[str, Any],
    *,
    backend: Any,
    chat_provider: Any,
) -> dict[str, Any]:
    """用当前请求绑定的授权资料生成一次只读回答，模型调用至多一次。"""
    recognizable_request_id = _recognizable_request_id(request)
    try:
        parsed_request = parse_conversation_request(request)
    except ChatValidationError:
        if recognizable_request_id is None:
            raise
        return stable_failure_result(recognizable_request_id, "invalid_request")

    request_id = parsed_request["request_id"]
    if _is_direct_tool_action(parsed_request["question"]):
        return _completed_result(
            request_id,
            "该操作未执行，当前仅支持问答，不会执行邮件、日历、CRM、文件或其他业务写操作。",
            [],
        )
    if _needs_matter_clarification(
        parsed_request["question"], parsed_request["recent_history"]
    ):
        return _completed_result(
            request_id,
            "请问您指的是该客户的哪一项具体事项？",
            [],
        )

    try:
        internal = parse_answer_context(
            backend.get_answer_context(request_id, "internal"),
            expected_request_id=request_id,
            expected_scope="internal",
        )
    except Exception:
        return stable_failure_result(request_id, "context_unavailable")

    if internal["customer_context_status"] == "failed":
        return stable_failure_result(request_id, "context_unavailable")
    _add_missing_failure_gap(
        internal,
        scope="internal_knowledge",
        message="内部知识暂时不可用。",
    )

    external = None
    if internal["external_available"]:
        try:
            external = parse_answer_context(
                backend.get_answer_context(request_id, "external"),
                expected_request_id=request_id,
                expected_scope="external",
            )
        except Exception:
            external = _failed_external_context(request_id)
        else:
            _add_missing_failure_gap(
                external,
                scope="external_knowledge",
                message="外部知识暂时不可用。",
            )

    try:
        trimmed = trim_context_items(
            internal["customer_context"],
            internal["context_items"],
            external["context_items"] if external is not None else [],
        )
    except ChatValidationError:
        return stable_failure_result(request_id, "context_unavailable")
    usable = {
        group: [item for item in items if item["content"].strip()]
        for group, items in trimmed.items()
    }
    allowed_items = [
        *usable["customer_context"],
        *usable["internal_knowledge"],
        *usable["external_knowledge"],
    ]
    knowledge_failed = internal["knowledge_status"] == "failed" or (
        external is not None and external["knowledge_status"] == "failed"
    )
    if not allowed_items:
        if knowledge_failed:
            return stable_failure_result(request_id, "knowledge_unavailable")
        return _completed_result(
            request_id,
            "现有资料不足，无法回答该问题。",
            [],
        )

    model_internal = {
        **internal,
        "customer_context": usable["customer_context"],
        "context_items": usable["internal_knowledge"],
    }
    model_external = None
    if external is not None:
        model_external = {
            **external,
            "customer_context": [],
            "context_items": usable["external_knowledge"],
        }
    messages = build_chat_messages(
        parsed_request,
        internal_context=model_internal,
        external_context=model_external,
    )

    try:
        raw_candidate = chat_provider(messages, max_tokens=CHAT_MAX_TOKENS)
    except Exception:
        return stable_failure_result(request_id, "model_unavailable")
    if not isinstance(raw_candidate, str):
        return stable_failure_result(request_id, "invalid_model_output")

    try:
        candidate = parse_model_candidate(
            raw_candidate,
            allowed_context_items=allowed_items,
        )
        _validate_answer_policy(
            candidate,
            relevant_context=usable,
        )
    except ChatValidationError:
        return stable_failure_result(request_id, "invalid_model_output")

    return _completed_result(
        request_id,
        candidate["assistant_text"],
        candidate["citations"],
    )


# 功能：组装客户模式 completed 结果并经回报解析器校验。
# 输入：`request_id` 当前请求标识、`assistant_text` 助手正文、`citations` 有序引用。
# 输出：dict[str, Any]。
# 逻辑：组装客户模式 completed 结果并经回报解析器校验，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _completed_result(
    request_id: str,
    assistant_text: str,
    citations: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "chat_prompt_version": CHAT_PROMPT_VERSION,
        "assistant_text": assistant_text,
        "citations": [dict(citation) for citation in citations],
        "status": "completed",
        "error": None,
    }


# 功能：仅从映射中提取非空字符串请求标识，无法识别时返回 None。
# 输入：`value` 待验证数据。
# 输出：str | None。
# 逻辑：仅从映射中提取非空字符串请求标识，无法识别时返回 None，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _recognizable_request_id(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    request_id = value.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        return None
    return request_id


# 功能：构造外部检索失败的严格上下文与安全缺口。
# 输入：`request_id` 当前请求标识。
# 输出：dict[str, Any]。
# 逻辑：构造外部检索失败的严格上下文与安全缺口，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _failed_external_context(request_id: str) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "scope": "external",
        "customer_context": [],
        "context_items": [],
        "customer_context_status": "not_applicable",
        "knowledge_status": "failed",
        "retrieval_gaps": [
            {
                "scope": "external_knowledge",
                "code": "temporarily_unavailable",
                "message": "外部知识暂时不可用。",
            }
        ],
        "external_available": True,
    }


# 功能：只在缺少对应 scope 缺口时追加检索失败描述。
# 输入：`context` 当前校验或裁剪参数、`scope` 知识范围、`message` 安全错误说明。
# 输出：None。
# 逻辑：只在缺少对应 scope 缺口时追加检索失败描述，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _add_missing_failure_gap(
    context: dict[str, Any], *, scope: str, message: str
) -> None:
    if context["knowledge_status"] != "failed" or context["retrieval_gaps"]:
        return
    context["retrieval_gaps"].append(
        {
            "scope": scope,
            "code": "temporarily_unavailable",
            "message": message,
        }
    )


# 功能：按中英文动作模式识别直接执行请求，排除教程式问法。
# 输入：`question` 用户问题。
# 输出：bool。
# 逻辑：按中英文动作模式识别直接执行请求，排除教程式问法，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _is_direct_tool_action(question: str) -> bool:
    normalized = re.sub(r"\s+", "", question).lower()
    english = question.lower()
    if re.search(
        r"(?:如何|怎么|怎样|说明|解释|教程).{0,8}(?:发送|安排|创建|更新|删除|写入)",
        normalized,
    ):
        return False

    side_effect_patterns = (
        r"(?:请|帮我|替我|直接|马上|现在|务必|给[^，。！？!?]{0,20})*(?:发送|发出|寄出|群发|转发|发)(?:一封|这封|该封)?(?:邮件|消息|短信|通知|信)",
        r"(?:请|帮我|替我|直接|马上|现在)*(?:回复)(?:客户|邮件|消息|这封信)",
        r"(?:请|帮我|替我|直接|马上|现在)*(?:创建|新增|安排|预约|取消|修改|删除).{0,20}(?:会议|日程|日历|预约)",
        r"(?:请|帮我|替我|直接|马上|现在)*(?:创建|新增|更新|修改|删除|写入|保存).{0,20}(?:crm|客户记录|商机|机会|工单|报价|订单|联系人)",
        r"(?:请|帮我|替我|直接|马上|现在)*(?:创建|新建|写入|保存|上传|移动|修改|删除).{0,20}(?:文件|文档|表格|附件)",
    )
    if any(
        re.search(pattern, normalized, re.IGNORECASE)
        for pattern in side_effect_patterns
    ):
        return True
    return bool(
        re.search(
            r"\b(?:send|schedule|create|update|delete|write|upload)\b.{0,30}"
            r"\b(?:email|message|calendar|meeting|crm|file|record)\b",
            english,
        )
    )


# 功能：结合问题代词、剩余词段及历史判断是否需要澄清事项。
# 输入：`question` 用户问题、`recent_history` 会话历史。
# 输出：bool。
# 逻辑：结合问题代词、剩余词段及历史判断是否需要澄清事项，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _needs_matter_clarification(
    question: str, recent_history: list[dict[str, str]]
) -> bool:
    normalized = re.sub(r"[\s，。！？!?、；;：:]", "", question)
    # company_id 已绑定，因此“这个客户”本身不是不明确事项。
    normalized = normalized.replace("这个客户", "客户").replace("该客户", "客户")
    ambiguous_reference = re.search(
        r"(?:这个|那个|它|这件事|那件事|之前那个|上次那个|上述事项|该事项)",
        normalized,
    )
    if ambiguous_reference is None:
        return False

    residue = re.sub(
        r"(?:这个|那个|它|这件事|那件事|之前那个|上次那个|上述事项|该事项|"
        r"关于|请问|帮我|看看|一下|下一步|目前|现在|后来|怎么|怎样|如何|"
        r"处理|办理|情况|进展|结果|怎么样|怎么办|呢|吗|可以|是否|客户)",
        "",
        normalized,
    )
    if len(residue) >= 2:
        return False
    return not any(
        _history_has_specific_matter(item["content"]) for item in recent_history
    )


# 功能：通过移除泛化词后的词元判断历史是否提及具体事项。
# 输入：`content` 来源或历史文本。
# 输出：bool。
# 逻辑：通过移除泛化词后的词元判断历史是否提及具体事项，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _history_has_specific_matter(content: str) -> bool:
    units = _lexical_units(content)
    return bool(units - {"可以", "请问", "客户", "需求", "风险", "情况"})


_LEXICAL_STOP_UNITS = {
    "这个",
    "那个",
    "什么",
    "怎么",
    "如何",
    "是否",
    "可以",
    "请问",
    "帮我",
    "客户",
    "公司",
    "目前",
    "现在",
    "相关",
    "资料",
    "信息",
    "情况",
    "一下",
    "the",
    "and",
    "for",
    "with",
    "what",
    "how",
    "this",
    "that",
    "customer",
}

_SUPPORT_CONCEPTS = {
    "company_size": ("规模", "员工", "人数", "人力", "headcount", "employee"),
    "budget": ("预算", "资金", "金额", "价格", "报价", "费用", "price", "budget"),
    "decision": ("决策", "审批", "负责人", "联系人", "采购", "decision", "approver"),
    "delivery": ("交期", "交付", "到货", "发货", "周期", "delivery", "lead time"),
    "demand": ("需求", "采购", "购买", "意向", "询价", "demand"),
    "product": ("产品", "设备", "系统", "型号", "功能", "方案", "product", "equipment"),
    "risk": ("风险", "异议", "问题", "阻碍", "担忧", "risk", "issue"),
    "contract": ("订单", "合同", "签约", "成交", "续购", "order", "contract"),
    "meeting": ("会议", "会面", "日程", "预约", "meeting", "schedule"),
    "status": ("状态", "进展", "阶段", "跟进", "目前", "status", "progress"),
}


# 功能：提取英文词及中文片段并移除既定停用词。
# 输入：`value` 待验证数据。
# 输出：set[str]。
# 逻辑：提取英文词及中文片段并移除既定停用词，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _lexical_units(value: str) -> set[str]:
    text = value.lower()
    units = {
        word
        for word in re.findall(r"[a-z0-9][a-z0-9_-]+", text)
        if word not in _LEXICAL_STOP_UNITS
    }
    for sequence in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(sequence) == 1:
            continue
        if len(sequence) == 2:
            candidates = (sequence,)
        else:
            candidates = tuple(
                sequence[index : index + 2] for index in range(len(sequence) - 1)
            )
        units.update(
            candidate
            for candidate in candidates
            if candidate not in _LEXICAL_STOP_UNITS
        )
    return units


# 功能：将词元与既定概念同义词映射成证据比较单元。
# 输入：`value` 待验证数据。
# 输出：set[str]。
# 逻辑：将词元与既定概念同义词映射成证据比较单元，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _support_units(value: str) -> set[str]:
    units = _lexical_units(value)
    lowered = value.lower()
    units.update(
        f"concept:{concept}"
        for concept, terms in _SUPPORT_CONCEPTS.items()
        if any(term in lowered for term in terms)
    )
    return units


# 功能：串联客户回答的动作、证据、判断和冲突措辞约束。
# 输入：`candidate` 模型候选结果、`relevant_context` 本请求裁剪后的证据。
# 输出：None。
# 逻辑：串联客户回答的动作、证据、判断和冲突措辞约束，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _validate_answer_policy(
    candidate: Mapping[str, Any],
    *,
    relevant_context: Mapping[str, list[dict[str, str]]],
) -> None:
    text = candidate["assistant_text"]
    citations = candidate["citations"]
    if not citations:
        if not _is_insufficiency_only(text):
            raise ChatValidationError("有可用证据时，无引用回答只能是资料不足说明。")
        return
    if _is_insufficiency_only(text):
        raise ChatValidationError("资料不足回答不得附带 citation。")

    _validate_factual_sentence_markers(text)
    _validate_deterministic_citation_support(text, citations, relevant_context)
    _validate_judgment_wording(text)
    _validate_conflict_wording(text, citations, relevant_context)


# 功能：识别短文本中不含引用和数字的明确资料不足声明。
# 输入：`text` 待检查文本。
# 输出：bool。
# 逻辑：识别短文本中不含引用和数字的明确资料不足声明，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _is_insufficiency_only(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if len(compact) > 120 or _CITATION_MARKER.search(compact):
        return False
    gap_cue = bool(
        re.search(
            r"(?:资料不足|证据不足|信息不足|上下文不足|未提供|未找到|缺少|缺失|"
            r"无相关(?:资料|证据|信息)|没有.{0,8}(?:资料|证据|信息))",
            compact,
        )
    )
    uncertainty_cue = bool(
        re.search(
            r"(?:无法|不能|不足以|暂时无法).{0,12}(?:回答|确认|判断|确定)", compact
        )
    )
    return gap_cue and uncertainty_cue and not _number_tokens(compact)


# 功能：规范句末引用位置后逐句要求事实性文字带编号。
# 输入：`text` 待检查文本。
# 输出：None。
# 逻辑：规范句末引用位置后逐句要求事实性文字带编号，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _validate_factual_sentence_markers(text: str) -> None:
    normalized = re.sub(
        r"([。！？!?])\s*((?:\[\d+\]\s*)+)",
        lambda match: match.group(2) + match.group(1),
        text,
    )
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[。！？!?])|[\r\n]+", normalized)
        if sentence.strip()
    ]
    for sentence in sentences:
        if _is_nonfactual_gap_sentence(sentence):
            continue
        if not _CITATION_MARKER.search(sentence):
            raise ChatValidationError("每个事实性句子都必须含 Citation marker。")


# 功能：识别不含引用的资料缺口或未确认措辞。
# 输入：`sentence` 当前语句。
# 输出：bool。
# 逻辑：识别不含引用的资料缺口或未确认措辞，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _is_nonfactual_gap_sentence(sentence: str) -> bool:
    compact = re.sub(r"\s+", "", sentence)
    return bool(
        re.search(
            r"(?:无法确认|不能确认|尚未确认|待确认|资料不足|证据不足|信息不足|"
            r"暂时不可用|未获取|检索失败|缺少|缺失|存在冲突|信息冲突|相互矛盾)",
            compact,
        )
    ) and not _CITATION_MARKER.search(compact)


# 功能：逐句核对引用证据覆盖数值且存在共同词元或概念。
# 输入：`text` 待检查文本、`citations` 有序引用、`relevant_context` 本请求裁剪后的证据。
# 输出：None。
# 逻辑：逐句核对引用证据覆盖数值且存在共同词元或概念，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _validate_deterministic_citation_support(
    text: str,
    citations: list[dict[str, str]],
    relevant_context: Mapping[str, list[dict[str, str]]],
) -> None:
    item_lookup: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for item in _flatten_context(relevant_context):
        item_lookup.setdefault(_citation_key(item), []).append(item)

    normalized = re.sub(
        r"([。！？!?])\s*((?:\[\d+\]\s*)+)",
        lambda match: match.group(2) + match.group(1),
        text,
    )
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[。！？!?])|[\r\n]+", normalized)
        if sentence.strip() and _CITATION_MARKER.search(sentence)
    ]
    for sentence in sentences:
        marker_indexes = {int(value) for value in _CITATION_MARKER.findall(sentence)}
        cited_items = [
            item
            for marker in marker_indexes
            for item in item_lookup.get(_citation_key(citations[marker - 1]), [])
        ]
        evidence_text = "\n".join(
            f'{item["title_or_label"]} {item["content"]}' for item in cited_items
        )
        claim = _CITATION_MARKER.sub("", sentence)
        claim_numbers = _number_tokens(claim)
        evidence_numbers = _number_tokens(evidence_text)
        if claim_numbers - evidence_numbers:
            raise ChatValidationError("回答中的数值未被所引用来源支持。")

        if not (_support_units(claim) & _support_units(evidence_text)):
            raise ChatValidationError("事实性句子与所引用来源缺少可验证的语义关联。")


# 功能：提取数值和百分号并归一化整数及小数尾零。
# 输入：`value` 待验证数据。
# 输出：set[str]。
# 逻辑：提取数值和百分号并归一化整数及小数尾零，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _number_tokens(value: str) -> set[str]:
    tokens: set[str] = set()
    for raw_number, percent in re.findall(r"(\d+(?:\.\d+)?)(%?)", value):
        if "." in raw_number:
            normalized = raw_number.rstrip("0").rstrip(".")
        else:
            normalized = str(int(raw_number))
        tokens.add(normalized + percent)
    return tokens


# 功能：要求包含推测建议线索的文字明确标注判断或可能。
# 输入：`text` 待检查文本。
# 输出：None。
# 逻辑：要求包含推测建议线索的文字明确标注判断或可能，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _validate_judgment_wording(text: str) -> None:
    has_judgment_cue = bool(
        re.search(r"(?:预计|推测|倾向|大概|或许|看起来|看来|意味着|建议|认为)", text)
    )
    if has_judgment_cue and not re.search(r"(?:判断|可能)", text):
        raise ChatValidationError("证据判断或建议必须使用‘判断’或‘可能’。")


# 功能：要求冲突声明引用至少两个来源且标注待确认。
# 输入：`text` 待检查文本、`citations` 有序引用、`relevant_context` 本请求裁剪后的证据。
# 输出：None。
# 逻辑：要求冲突声明引用至少两个来源且标注待确认，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _validate_conflict_wording(
    text: str,
    citations: list[dict[str, str]],
    relevant_context: Mapping[str, list[dict[str, str]]],
) -> None:
    del relevant_context
    citation_keys = {_citation_key(citation) for citation in citations}
    conflict_claimed = bool(
        re.search(r"(?:\u51b2\u7a81|\u4e0d\u4e00\u81f4|\u77db\u76fe)", text)
    )
    unconfirmed = bool(
        re.search(
            r"(?:\u672a\u786e\u8ba4|\u65e0\u6cd5\u786e\u8ba4|"
            r"\u6709\u5f85\u786e\u8ba4|\u5f85\u786e\u8ba4)",
            text,
        )
    )
    if conflict_claimed and (len(citation_keys) < 2 or not unconfirmed):
        raise ChatValidationError(
            "Conflict answers must cite both sources and mark the fact unconfirmed."
        )


# 功能：按客户、内部、外部的固定顺序展开证据数组。
# 输入：`relevant_context` 本请求裁剪后的证据。
# 输出：list[dict[str, str]]。
# 逻辑：按客户、内部、外部的固定顺序展开证据数组，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _flatten_context(
    relevant_context: Mapping[str, list[dict[str, str]]]
) -> list[dict[str, str]]:
    return [
        *relevant_context["customer_context"],
        *relevant_context["internal_knowledge"],
        *relevant_context["external_knowledge"],
    ]


# 功能：校验数组后按索引路径解析每个来源条目。
# 输入：`value` 待验证数据、`path` 错误定位路径。
# 输出：list[dict[str, str]]。
# 逻辑：校验数组后按索引路径解析每个来源条目，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _parse_context_item_array(value: object, path: str) -> list[dict[str, str]]:
    items = _array(value, path)
    return [
        parse_context_item(item, path=f"{path}[{index}]")
        for index, item in enumerate(items)
    ]


# 功能：按四字段身份保留首次出现的来源副本。
# 输入：`items` 已解析来源数组。
# 输出：list[dict[str, str]]。
# 逻辑：按四字段身份保留首次出现的来源副本，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _deduplicate_parsed_context_items(
    items: list[dict[str, str]],
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in items:
        key = _context_item_key(item)
        if key not in seen:
            seen.add(key)
            result.append(dict(item))
    return result


# 功能：仅截断超预算正文并追加节选标记，保留来源身份。
# 输入：`item` 来源条目、`content_character_limit` 既定单条字符预算。
# 输出：dict[str, str]。
# 逻辑：仅截断超预算正文并追加节选标记，保留来源身份，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _truncate_context_item(
    item: Mapping[str, str], *, content_character_limit: int
) -> dict[str, str]:
    content = item["content"]
    if len(content) > content_character_limit:
        content = content[:content_character_limit] + TRUNCATION_MARKER
    return {
        "source_id": item["source_id"],
        "source_type": item["source_type"],
        "title_or_label": item["title_or_label"],
        "content": content,
    }


# 功能：按身份三字段及正文构造完整去重键。
# 输入：`item` 来源条目。
# 输出：tuple[str, str, str, str]。
# 逻辑：按身份三字段及正文构造完整去重键，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _context_item_key(item: Mapping[str, str]) -> tuple[str, str, str, str]:
    return (
        item["source_id"],
        item["source_type"],
        item["title_or_label"],
        item["content"],
    )


# 功能：按来源标识、类型和标题构造引用身份键。
# 输入：`item` 来源条目。
# 输出：tuple[str, str, str]。
# 逻辑：按来源标识、类型和标题构造引用身份键，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _citation_key(item: Mapping[str, str]) -> tuple[str, str, str]:
    return item["source_id"], item["source_type"], item["title_or_label"]


# 功能：记录来源身份对应正文并拒绝同身份的内容冲突。
# 输入：`item` 来源条目、`source_contents` 已见引用身份到正文的映射、`path` 错误定位路径。
# 输出：None。
# 逻辑：记录来源身份对应正文并拒绝同身份的内容冲突，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _record_unique_citation_identity(
    item: Mapping[str, str],
    source_contents: dict[tuple[str, str, str], str],
    path: str,
) -> None:
    identity = _citation_key(item)
    content = item["content"]
    if identity in source_contents and source_contents[identity] != content:
        raise ChatValidationError(f"{path} 中同一引用标识对应了不同内容。")
    source_contents[identity] = content


# 功能：遍历来源并验证同一引用身份只有一种正文。
# 输入：`items` 已解析来源数组、`path` 错误定位路径。
# 输出：None。
# 逻辑：遍历来源并验证同一引用身份只有一种正文，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _ensure_unique_citation_identities(items: list[dict[str, str]], path: str) -> None:
    source_contents: dict[tuple[str, str, str], str] = {}
    for item in items:
        _record_unique_citation_identity(item, source_contents, path)


# 功能：严格解析有序的三字段引用数组并拒绝重复。
# 输入：`value` 待验证数据。
# 输出：list[dict[str, str]]。
# 逻辑：严格解析有序的三字段引用数组并拒绝重复，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _parse_citations(value: object) -> list[dict[str, str]]:
    raw_citations = _array(value, "citations")
    citations: list[dict[str, str]] = []
    for index, raw_citation in enumerate(raw_citations):
        path = f"citations[{index}]"
        citation = _object(raw_citation, path)
        _keys(citation, _CITATION_FIELDS, path)
        citations.append(
            {
                "source_id": _nonblank(citation["source_id"], f"{path}.source_id"),
                "source_type": _nonblank(
                    citation["source_type"], f"{path}.source_type"
                ),
                "title_or_label": _nonblank(
                    citation["title_or_label"], f"{path}.title_or_label"
                ),
            }
        )
    return citations


# 功能：检查正文编号集合与引用数组位置完整一致。
# 输入：`assistant_text` 助手正文、`citations` 有序引用。
# 输出：None。
# 逻辑：检查正文编号集合与引用数组位置完整一致，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _validate_citation_markers(
    assistant_text: str, citations: list[dict[str, str]]
) -> None:
    raw_markers = _CITATION_MARKER.findall(assistant_text)
    marker_indexes: set[int] = set()
    for raw_marker in raw_markers:
        marker = int(raw_marker)
        if raw_marker != str(marker) or marker < 1 or marker > len(citations):
            raise ChatValidationError("assistant_text 含越界或非规范 Citation marker。")
        marker_indexes.add(marker)
    expected_indexes = set(range(1, len(citations) + 1))
    if marker_indexes != expected_indexes:
        raise ChatValidationError("每个 citation 都必须由正文中的对应 marker 使用。")


# 功能：要求错误代码及文案精确命中安全失败集合。
# 输入：`value` 待验证数据。
# 输出：dict[str, str]。
# 逻辑：要求错误代码及文案精确命中安全失败集合，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _parse_report_error(value: object) -> dict[str, str]:
    error = _object(value, "report_answer.error")
    _keys(error, _ERROR_FIELDS, "report_answer.error")
    code = _nonblank(error["code"], "report_answer.error.code")
    if code not in _FAILURE_MESSAGES:
        raise ChatValidationError("report_answer.error.code 不是稳定失败代码。")
    return {
        "code": code,
        "message": _nonblank(error["message"], "report_answer.error.message"),
    }


# 功能：将证据序列化为明确标注不可信数据的消息块。
# 输入：`label` 数据块标签、`payload` 不可信输入载荷。
# 输出：str。
# 逻辑：将证据序列化为明确标注不可信数据的消息块，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _untrusted_block(label: str, payload: object) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"BEGIN_{label}\n{serialized}\nEND_{label}"


# 功能：解析 JSON 对象并拒绝无效语法、重复键和非对象根值。
# 输入：`value` 待验证数据。
# 输出：object。
# 逻辑：解析 JSON 对象并拒绝无效语法、重复键和非对象根值，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _decode_json_object(value: str) -> object:
    if not value.strip():
        raise ChatValidationError("模型候选必须是非空 JSON 文本。")
    try:
        return json.loads(value, object_pairs_hook=_unique_json_object)
    except ChatValidationError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError):
        raise ChatValidationError("模型候选不是有效 JSON Object。") from None


# 功能：从有序键值对构造对象并拒绝重复键。
# 输入：`pairs` 解码键值对。
# 输出：dict[str, Any]。
# 逻辑：从有序键值对构造对象并拒绝重复键，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ChatValidationError("模型候选 JSON 不得包含重复字段。")
        result[key] = value
    return result


# 功能：要求输入为映射对象，否则抛带字段路径的校验异常。
# 输入：`value` 待验证数据、`path` 错误定位路径。
# 输出：Mapping[str, Any]。
# 逻辑：要求输入为映射对象，否则抛带字段路径的校验异常，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _object(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ChatValidationError(f"{path} 必须是对象。")
    return value


# 功能：要求输入为 list，否则抛带字段路径的校验异常。
# 输入：`value` 待验证数据、`path` 错误定位路径。
# 输出：list[Any]。
# 逻辑：要求输入为 list，否则抛带字段路径的校验异常，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _array(value: object, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ChatValidationError(f"{path} 必须是数组。")
    return value


# 功能：要求对象键集合与协议字段精确一致。
# 输入：`value` 待验证数据、`expected` 允许字段集合、`path` 错误定位路径。
# 输出：None。
# 逻辑：要求对象键集合与协议字段精确一致，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _keys(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    if set(value) != expected:
        raise ChatValidationError(f"{path} 字段必须与契约完全一致。")


# 功能：要求输入为字符串，不做隐式类型转换。
# 输入：`value` 待验证数据、`path` 错误定位路径。
# 输出：str。
# 逻辑：要求输入为字符串，不做隐式类型转换，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _string(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise ChatValidationError(f"{path} 必须是字符串。")
    return value


# 功能：要求字符串去空白后非空，返回原始字符串。
# 输入：`value` 待验证数据、`path` 错误定位路径。
# 输出：str。
# 逻辑：要求字符串去空白后非空，返回原始字符串，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _nonblank(value: object, path: str) -> str:
    text = _string(value, path)
    if not text.strip():
        raise ChatValidationError(f"{path} 必须是非空字符串。")
    return text


# 功能：要求字符串属于明确的枚举集合。
# 输入：`value` 待验证数据、`allowed` 允许枚举集合、`path` 错误定位路径。
# 输出：str。
# 逻辑：要求字符串属于明确的枚举集合，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _enum(value: object, allowed: frozenset[str], path: str) -> str:
    text = _nonblank(value, path)
    if text not in allowed:
        raise ChatValidationError(f"{path} 枚举值无效。")
    return text


# 功能：只接受 bool 类型，不把整数当作布尔值。
# 输入：`value` 待验证数据、`path` 错误定位路径。
# 输出：bool。
# 逻辑：只接受 bool 类型，不把整数当作布尔值，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _boolean(value: object, path: str) -> bool:
    if type(value) is not bool:
        raise ChatValidationError(f"{path} 必须是布尔值。")
    return value


# 功能：只接受非负整数预算，排除 bool 类型。
# 输入：`value` 待验证数据、`path` 错误定位路径。
# 输出：int。
# 逻辑：只接受非负整数预算，排除 bool 类型，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def _nonnegative_integer(value: object, path: str) -> int:
    if type(value) is not int or value < 0:
        raise ChatValidationError(f"{path} 必须是非负整数。")
    return value


# 功能：通过聊天专用百炼边界发送有序消息，不提供任何工具能力。
# 输入：`messages` 有序模型消息、`max_tokens` 既定输出上限。
# 输出：str。
# 逻辑：通过聊天专用百炼边界发送有序消息，不提供任何工具能力，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def bailian_chat_provider(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = CHAT_MAX_TOKENS,
) -> str:
    """通过聊天专用百炼边界发送有序消息，不提供任何工具能力。"""
    from agent.llm.bailian import generate_chat_json

    return generate_chat_json(messages, max_tokens=max_tokens)


# 功能：领取一次请求，按可空客户绑定路由客户或通用问答，并尝试一次回报。
# 输入：`backend` 后端客户端、`chat_provider` 单次模型调用边界。
# 输出：dict[str, Any] | None。
# 逻辑：领取一次请求，按可空客户绑定路由客户或通用问答，并尝试一次回报，保持现有字段规则与处理顺序。
# 约束：复用既定预算与错误语义；通用模式仅在入口明确选择，不作为客户问答失败后的回退。
def process_chat_once(
    *,
    backend: Any,
    chat_provider: Any = bailian_chat_provider,
) -> dict[str, Any] | None:
    """领取并处理至多一个聊天请求，然后恰好尝试一次结果回报。"""
    claimed_request = backend.claim_answer_request()
    if claimed_request is None:
        return None

    request_id = _recognizable_request_id(claimed_request)
    if request_id is None:
        # 无安全可识别的 request_id 时无法回报；严格请求解析会在此失败，
        # 而不会调用上下文、模型或 report 接口。
        return answer_conversation_request(
            claimed_request,
            backend=backend,
            chat_provider=chat_provider,
        )

    if claimed_request.get("company_id", "missing") is None:
        from .general_chat import answer

        result = answer(claimed_request, backend=backend, chat_provider=chat_provider)
    else:
        result = answer_conversation_request(
            claimed_request, backend=backend, chat_provider=chat_provider
        )
    try:
        backend.report_answer(result)
    except Exception:
        # Report Answer 失败只形成本地结果。Demo 不自动重试，也不伪装已保存。
        return {
            **stable_failure_result(request_id, "report_failed"),
            "chat_prompt_version": result["chat_prompt_version"],
        }
    return result


__all__ = [
    "CHAT_MAX_TOKENS",
    "CHAT_PROMPT",
    "CHAT_PROMPT_VERSION",
    "DEFAULT_CONTEXT_CONTENT_CHARACTER_LIMIT",
    "DEFAULT_CONTEXT_ITEM_LIMIT",
    "DEFAULT_HISTORY_CHARACTER_BUDGET",
    "TRUNCATION_MARKER",
    "ChatValidationError",
    "answer_conversation_request",
    "bailian_chat_provider",
    "build_chat_messages",
    "deduplicate_context_items",
    "parse_answer_context",
    "parse_context_item",
    "parse_conversation_request",
    "parse_model_candidate",
    "parse_recent_history",
    "parse_report_answer",
    "parse_retrieval_gap",
    "process_chat_once",
    "stable_failure_result",
    "trim_context_items",
    "trim_recent_history",
    "validate_citation_allowlist",
]
