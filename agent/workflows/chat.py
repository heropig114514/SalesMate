"""职责：按请求编排客户与共享实验数据工具调用，核验模型回答引用。
实现：实时工具目录、固定候选集合、原有读取与上下文预算共同约束模型调用。
关联：DjangoBackendClient 提供请求绑定 HTTP；workspace-chat Skill 定义选择规则，后端持久化证据。
目录：
- ChatValidationError：表示工作空间契约校验失败。
- _nonblank：读取非空文本。
- _keys：核对封闭对象字段。
- _recognizable_request_id：提取可识别的请求 ID。
- _source：校验四字段来源。
- parse_conversation_request：解析领取的工作空间请求。
- parse_answer_context：解析冻结的请求上下文。
- trim_recent_history：按字符预算保留近期历史。
- trim_context_items：裁剪初始知识输入。
- _decode_json_object：解析无重复键的 JSON 对象。
- _decode_json_object.unique：拒绝重复 JSON 字段。
- parse_model_candidate：验证回答引用并重排编号。
- parse_model_candidate.replace_marker：替换正文引用编号。
- stable_failure_result：建立稳定失败回报。
- _diagnostic_excerpt：生成有限错误摘要。
- _lexical_units：提取片段匹配单元。
- _is_direct_tool_action：识别直接业务操作请求。
- bailian_chat_provider：调用百炼生成一次 JSON 回答。
- _workspace_failure：生成当前工作空间版本失败结果。
- _workspace_arguments：验证固定读取及实验维护工具及模型参数。
- _workspace_uuid：校验 UUID 字符串。
- _workspace_catalog：解析请求实际发布的数据工具目录。
- _workspace_schema_arguments：核对参数键与实时 Schema。
- _workspace_decision：解析模型动作。
- _workspace_tool_result：校验读取回执并生成提示摘要。
- _workspace_append_evidence：追加不同身份的读取证据。
- _workspace_excerpt：截取与问题相关的原文片段。
- _workspace_prompt_evidence：选择预算内的来源。
- answer_workspace_request：处理工作空间问题和读取及实验维护工具循环。
- _chat_report_is_saved：核对响应丢失后的权威终态。
- process_chat_once：领取并处理至多一个聊天请求。
变量索引：
- WORKSPACE_CHAT_PROMPT_VERSION：实际加载的提示词版本。
- _CITATION_FIELDS：引用元数据契约。
- _CITATION_MARKER：正文数字引用正则。
- _CONTEXT_FIELDS：上下文响应字段契约。
- _FAILURE_MESSAGES：稳定失败码和文案。
- _HISTORY_CHARACTER_BUDGET：近期历史字符预算。
- _NONSTANDARD_SOURCE_TAG：旧式非标准引用标签正则。
- _REQUEST_FIELDS：领取请求五字段契约。
- _SOURCE_FIELDS：来源四字段契约。
- _WORKSPACE_CHAT_SKILL：工作空间提示及既定模型输出预算。
- _WORKSPACE_DETAIL_EXCERPT_CHARACTERS：单条客户详情节选预算。
- _WORKSPACE_MAX_EVIDENCE_ITEMS：模型展示来源最多 12 条。
- _WORKSPACE_MAX_PROMPT_CHARACTERS：来源正文总字符预算。
- _WORKSPACE_MAX_SEARCH_PAGE_SIZE：模型分页读取上限 20。
- _WORKSPACE_MAX_TOOL_READS：每次回答最多六次读取。
- _WORKSPACE_OTHER_EXCERPT_CHARACTERS：其他来源单条节选预算。
- __all__：公开的工作空间解析与执行符号。
- logger：工作流阶段及耗时日志。
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from time import perf_counter
from typing import Any, Mapping

from agent.clients.backend_api import BackendContractError, BackendRequestError
from agent.skills import load_skill
from integrations.salesmate_tools.read_contract import EXPERIMENT_TOOLS, EXPERIMENT_WRITE_TOOLS, WORKSPACE_TOOLS


_WORKSPACE_CHAT_SKILL = load_skill("workspace-chat")
WORKSPACE_CHAT_PROMPT_VERSION = _WORKSPACE_CHAT_SKILL.version
_WORKSPACE_MAX_TOOL_READS = 6
_WORKSPACE_MAX_SEARCH_PAGE_SIZE = 20
_WORKSPACE_MAX_EVIDENCE_ITEMS = 12
_WORKSPACE_MAX_PROMPT_CHARACTERS = 18_000
_WORKSPACE_DETAIL_EXCERPT_CHARACTERS = 5_000
_WORKSPACE_OTHER_EXCERPT_CHARACTERS = 1_200
_HISTORY_CHARACTER_BUDGET = 6_000
_SOURCE_FIELDS = {"source_id", "source_type", "title_or_label", "content"}
_CITATION_FIELDS = {"source_id", "source_type", "title_or_label"}
_REQUEST_FIELDS = {
    "request_id", "conversation_id", "user_message_id",
    "question", "recent_history",
}
_CONTEXT_FIELDS = {
    "request_id", "scope", "customer_context", "context_items",
    "customer_context_status", "knowledge_status", "retrieval_gaps",
    "external_available",
}
_FAILURE_MESSAGES = {
    "invalid_request": "工作空间聊天请求无效。",
    "context_unavailable": "聊天资料暂时不可用。",
    "model_unavailable": "回答模型暂时不可用，请稍后重试。",
    "invalid_model_output": "回答模型返回了无效结果。",
    "report_failed": "回答结果暂时无法保存。",
}
_CITATION_MARKER = re.compile(r"\[(\d+)\]")
_NONSTANDARD_SOURCE_TAG = re.compile(
    r"[ \t]*\[(?:分析|邮件|画像|知识|来源|证据)[ \t]*[:：][ \t]*\d+\]"
)
logger = logging.getLogger("salesmate.chat")


# 功能：表示工作空间契约校验失败。
# 逻辑：由调用方转成阶段失败，不隐藏解析错误。
# 约束：只标记验证失败，不执行 I/O。
class ChatValidationError(ValueError):
    """请求、来源或模型输出不符合工作空间聊天契约。"""


# 功能：读取非空文本。
# 输入：`value` 待校验文本、`path` 错误消息中的字段路径。
# 输出：校验字符串类型与去空白后非空，返回原字符串。
# 逻辑：校验字符串类型与去空白后非空，返回原字符串。
# 约束：非法值抛出 ChatValidationError。
def _nonblank(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ChatValidationError(f"{path} 必须是非空字符串。")
    return value.strip()


# 功能：核对封闭对象字段。
# 输入：`value` 待校验对象、`expected` 允许且必需的键集合、`path` 错误字段路径。
# 输出：要求 Mapping 且键集合与 expected 完全一致，返回原对象。
# 逻辑：要求 Mapping 且键集合与 expected 完全一致，返回原对象。
# 约束：拒绝缺失及多余字段。
def _keys(value: object, expected: set[str], path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ChatValidationError(f"{path} 字段必须与契约完全一致。")
    return value


# 功能：提取可识别的请求 ID。
# 输入：`value` 可能无效的领取请求对象。
# 输出：对象含非空字符串时返回 ID，否则返回 None。
# 逻辑：对象含非空字符串时返回 ID，否则返回 None。
# 约束：只用于失败归属，不证明请求已获授权。
def _recognizable_request_id(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    request_id = value.get("request_id")
    return request_id.strip() if isinstance(request_id, str) and request_id.strip() else None


# 功能：校验四字段来源。
# 输入：`value` 待校验来源对象、`path` 错误字段路径。
# 输出：校验元数据非空及正文为字符串，返回来源字典。
# 逻辑：校验元数据非空及正文为字符串，返回来源字典。
# 约束：保留正文原值，不生成新证据。
def _source(value: object, path: str) -> dict[str, str]:
    item = _keys(value, _SOURCE_FIELDS, path)
    content = item["content"]
    if not isinstance(content, str):
        raise ChatValidationError(f"{path}.content 必须是字符串。")
    return {
        key: content if key == "content" else _nonblank(item[key], f"{path}.{key}")
        for key in ("source_id", "source_type", "title_or_label", "content")
    }


# 功能：解析领取的工作空间请求。
# 输入：`value` 后端领取接口返回的五字段对象。
# 输出：核对五字段及历史角色，返回规范化请求字典。
# 逻辑：核对五字段及历史角色，返回规范化请求字典。
# 约束：拒绝预选公司及多余字段。
def parse_conversation_request(value: object) -> dict[str, Any]:
    """解析后端领取的工作空间请求。"""
    request = _keys(value, _REQUEST_FIELDS, "request")
    history = request["recent_history"]
    if not isinstance(history, list):
        raise ChatValidationError("recent_history 必须是数组。")
    parsed_history = []
    for index, raw in enumerate(history):
        row = _keys(raw, {"role", "content"}, f"recent_history[{index}]")
        if row["role"] not in {"user", "assistant"}:
            raise ChatValidationError("历史消息角色无效。")
        parsed_history.append({
            "role": row["role"],
            "content": _nonblank(row["content"], f"recent_history[{index}].content"),
        })
    return {
        "request_id": _nonblank(request["request_id"], "request.request_id"),
        "conversation_id": _nonblank(request["conversation_id"], "request.conversation_id"),
        "user_message_id": _nonblank(request["user_message_id"], "request.user_message_id"),
        "question": _nonblank(request["question"], "request.question"),
        "recent_history": parsed_history,
    }


# 功能：解析冻结的请求上下文。
# 输入：`value` 后端冻结上下文；`expected_request_id`、`expected_scope` 默认 None 表示不额外核对对应预期值。
# 输出：核对请求和范围、来源一致性与状态，返回上下文字典。
# 逻辑：核对请求和范围、来源一致性与状态，返回上下文字典。
# 约束：同一来源元数据不能对应不同正文。
def parse_answer_context(
    value: object, *, expected_request_id: str | None = None,
    expected_scope: str | None = None,
) -> dict[str, Any]:
    """解析后端冻结的上下文；资料来源必须保持本次请求内唯一。"""
    context = _keys(value, _CONTEXT_FIELDS, "answer_context")
    request_id = _nonblank(context["request_id"], "answer_context.request_id")
    if expected_request_id is not None and request_id != expected_request_id:
        raise ChatValidationError("answer_context.request_id 与当前请求不一致。")
    scope = context["scope"]
    if scope not in {"internal", "external"} or (
        expected_scope is not None and scope != expected_scope
    ):
        raise ChatValidationError("answer_context.scope 与请求范围不一致。")
    for field in ("customer_context", "context_items", "retrieval_gaps"):
        if not isinstance(context[field], list):
            raise ChatValidationError(f"answer_context.{field} 必须是数组。")
    customer = [_source(item, "customer_context") for item in context["customer_context"]]
    knowledge = [_source(item, "context_items") for item in context["context_items"]]
    identities: dict[tuple[str, str, str], str] = {}
    for item in [*customer, *knowledge]:
        key = tuple(item[field] for field in ("source_id", "source_type", "title_or_label"))
        if key in identities and identities[key] != item["content"]:
            raise ChatValidationError("同一来源标识对应不同证据正文。")
        identities[key] = item["content"]
    customer_status = context["customer_context_status"]
    if customer_status not in ({"completed", "failed"} if scope == "internal" else {"not_applicable"}):
        raise ChatValidationError("answer_context.customer_context_status 无效。")
    if context["knowledge_status"] not in {"completed", "failed"}:
        raise ChatValidationError("answer_context.knowledge_status 无效。")
    if type(context["external_available"]) is not bool:
        raise ChatValidationError("answer_context.external_available 必须是布尔值。")
    if scope == "external" and (customer or not context["external_available"]):
        raise ChatValidationError("外部上下文状态无效。")
    return {
        **context,
        "request_id": request_id,
        "scope": scope,
        "customer_context": customer,
        "context_items": knowledge,
    }


# 功能：按字符预算保留近期历史。
# 输入：`value` 已经过角色与正文验证的近期消息数组。
# 输出：从最早消息开始剔除，返回剩余消息列表。
# 逻辑：从最早消息开始剔除，返回剩余消息列表。
# 约束：不修改原列表或既定历史预算。
def trim_recent_history(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ChatValidationError("recent_history 必须是数组。")
    retained = list(value)
    while retained and sum(len(row["content"]) for row in retained) > _HISTORY_CHARACTER_BUDGET:
        retained.pop(0)
    return retained


# 功能：裁剪初始知识输入。
# 输入：`customer_context` 客户来源、`internal_knowledge` 内部知识、`external_knowledge` 外部知识数组。
# 输出：按顺序选最多 12 条，超过 2000 字时加节选标记，返回三组来源。
# 逻辑：按顺序选最多 12 条，超过 2000 字时加节选标记，返回三组来源。
# 约束：不修改后端原始证据。
def trim_context_items(customer_context: object, internal_knowledge: object, external_knowledge: object) -> dict[str, list[dict[str, str]]]:
    """本流程只展示最多 12 条知识，单条至多 2000 字；不改后端原始证据。"""
    result = {"customer_context": [], "internal_knowledge": [], "external_knowledge": []}
    for field, items in (
        ("customer_context", customer_context),
        ("internal_knowledge", internal_knowledge),
        ("external_knowledge", external_knowledge),
    ):
        if not isinstance(items, list):
            raise ChatValidationError(f"{field} 必须是数组。")
        for raw in items:
            if sum(map(len, result.values())) >= 12:
                break
            item = _source(raw, field)
            if item["content"].strip():
                result[field].append({
                    **item,
                    "content": item["content"][:1985] + "\n[节选，原文未完整提供]"
                    if len(item["content"]) > 2000 else item["content"],
                })
    return result


# 功能：解析无重复键的 JSON 对象。
# 输入：`value` 模型返回的 JSON 字符串。
# 输出：通过对象钩子拒绝重复键，返回 dict。
# 逻辑：通过对象钩子拒绝重复键，返回 dict。
# 约束：非法 JSON 或非对象抛出 ChatValidationError。
def _decode_json_object(value: str) -> dict[str, Any]:
    # 功能：拒绝重复 JSON 字段。
    # 输入：`pairs` JSON 解码器提供的顺序键值对。
    # 输出：遍历 pairs 建立字典，重复键即失败。
    # 逻辑：遍历 pairs 建立字典，重复键即失败。
    # 约束：用于 JSON 解码钩子，不合并重复字段。
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ChatValidationError("模型结果含重复 JSON 字段。")
            result[key] = item
        return result
    try:
        parsed = json.loads(value, object_pairs_hook=unique)
    except (ValueError, TypeError) as error:
        raise ChatValidationError("模型结果不是有效 JSON Object。") from error
    if not isinstance(parsed, dict):
        raise ChatValidationError("模型结果必须是 JSON Object。")
    return parsed


# 功能：验证回答引用并重排编号。
# 输入：`value` 含回答与引用的模型对象、`allowed_context_items` 本轮展示来源、`request_id` 可选请求标识。
# 输出：对照允许来源三字段身份，按正文首次出现顺序压缩引用，返回回答对象。
# 逻辑：对照允许来源三字段身份，按正文首次出现顺序压缩引用，返回回答对象。
# 约束：不得引用未展示来源；request_id 保留为调用契约参数，当前不用于校验。
def parse_model_candidate(
    value: object, *, allowed_context_items: object, request_id: str | None = None
) -> dict[str, Any]:
    """只允许本轮可见证据的引用，并重排已使用的编号。"""
    candidate = _keys(value, {"assistant_text", "citations"}, "model_candidate")
    text = _NONSTANDARD_SOURCE_TAG.sub("", _nonblank(candidate["assistant_text"], "assistant_text"))
    raw_citations = candidate["citations"]
    if not isinstance(raw_citations, list) or not isinstance(allowed_context_items, list):
        raise ChatValidationError("citations 必须是数组。")
    allowlist = {
        tuple(item[field] for field in ("source_id", "source_type", "title_or_label"))
        for item in allowed_context_items if item["content"].strip()
    }
    citations = []
    for index, raw in enumerate(raw_citations):
        citation = _keys(raw, _CITATION_FIELDS, f"citations[{index}]")
        parsed = {field: _nonblank(citation[field], field) for field in _CITATION_FIELDS}
        if tuple(parsed[field] for field in ("source_id", "source_type", "title_or_label")) not in allowlist:
            raise ChatValidationError("引用不属于本轮可见的授权来源。")
        citations.append(parsed)
    markers = [int(value) for value in _CITATION_MARKER.findall(text)]
    if (not markers and citations) or any(marker < 1 or marker > len(citations) for marker in markers):
        raise ChatValidationError("回答引用编号与 citations 不一致。")
    used: dict[tuple[str, str, str], int] = {}
    compact = []
    for marker in markers:
        row = citations[marker - 1]
        key = tuple(row[field] for field in ("source_id", "source_type", "title_or_label"))
        if key not in used:
            used[key] = len(compact) + 1
            compact.append(row)
    cursor = iter(markers)

    # 功能：替换正文引用编号。
    # 输入：`_` 正则匹配对象；隐式读取外围已验证的 markers 游标及引用映射。
    # 输出：读取外围 cursor、citations 与 used，返回压缩后的编号字符串。
    # 逻辑：读取外围 cursor、citations 与 used，返回压缩后的编号字符串。
    # 约束：参数为正则匹配对象；序号来自已验证的 markers。
    def replace_marker(_: re.Match[str]) -> str:
        row = citations[next(cursor) - 1]
        key = tuple(row[field] for field in ("source_id", "source_type", "title_or_label"))
        return f"[{used[key]}]"

    text = _CITATION_MARKER.sub(replace_marker, text)
    return {"assistant_text": text, "citations": compact}


# 功能：建立稳定失败回报。
# 输入：`request_id` 可识别请求标识、`code` 固定失败消息表中的错误码。
# 输出：验证请求 ID 和失败码，返回六字段 failed 对象。
# 逻辑：验证请求 ID 和失败码，返回六字段 failed 对象。
# 约束：只接受固定失败码，不保存后端状态。
def stable_failure_result(request_id: object, code: object) -> dict[str, Any]:
    request_id = _nonblank(request_id, "request_id")
    if code not in _FAILURE_MESSAGES:
        raise ChatValidationError("失败代码无效。")
    return {
        "request_id": request_id,
        "chat_prompt_version": WORKSPACE_CHAT_PROMPT_VERSION,
        "assistant_text": "", "citations": [], "status": "failed",
        "error": {"code": code, "message": _FAILURE_MESSAGES[code]},
    }


# 功能：生成有限错误摘要。
# 输入：`value` 安全错误说明、`limit` 截断字符数，默认 160。
# 输出：压缩空白并遮蔽邮箱后按 limit 截断，返回字符串。
# 逻辑：压缩空白并遮蔽邮箱后按 limit 截断，返回字符串。
# 约束：不是任意敏感信息过滤器，只供安全错误说明。
def _diagnostic_excerpt(value: str, limit: int = 160) -> str:
    excerpt = re.sub(r"\s+", " ", value).strip()
    excerpt = re.sub(r"[\w.+-]+@[\w.-]+", "[email]", excerpt)
    return excerpt[:limit]


# 功能：提取片段匹配单元。
# 输入：`value` 当前用户问题或其他用于片段匹配的字符串。
# 输出：提取英文词和中文二元组，返回集合。
# 逻辑：提取英文词和中文二元组，返回集合。
# 约束：仅用于节选定位，不是语义检索模型。
def _lexical_units(value: str) -> set[str]:
    units = set(re.findall(r"[a-z0-9][a-z0-9_-]+", value.lower()))
    for sequence in re.findall(r"[\u4e00-\u9fff]+", value):
        units.update(sequence[index:index + 2] for index in range(len(sequence) - 1))
    return units


# 功能：识别直接业务操作请求。
# 输入：`question` 当前用户问题。
# 输出：解释类问法排除后进行既定正则匹配，返回布尔值。
# 逻辑：解释类问法排除后进行既定正则匹配，返回布尔值。
# 约束：此启发式不替代后端权限检查。
def _is_direct_tool_action(question: str) -> bool:
    compact = re.sub(r"\s+", "", question).lower()
    if re.search(r"(?:如何|怎么|怎样).{0,8}(?:发送|安排|创建|更新|删除)", compact):
        return False
    return bool(re.search(
        r"(?:请|帮我|替我|直接|马上|现在).{0,30}(?:发送|发信|寄出|群发|安排会议|预约会议|创建日程|删除客户|修改客户|写入crm|保存报价)",
        compact,
    ))


# 功能：调用百炼生成一次 JSON 回答。
# 输入：`messages` 模型消息数组、`max_tokens` 最大输出预算，默认由工作空间 Skill 提供。
# 输出：委托 generate_chat_json，返回模型文本。
# 逻辑：委托 generate_chat_json，返回模型文本。
# 约束：max_tokens 默认来自 Skill；网络与模型错误向上传播。
def bailian_chat_provider(messages: list[dict[str, str]], *, max_tokens: int = _WORKSPACE_CHAT_SKILL.max_tokens) -> str:
    from agent.llm.bailian import generate_chat_json
    return generate_chat_json(messages, max_tokens=max_tokens)


# 功能：生成当前工作空间版本失败结果。
# 输入：`request_id` 当前请求标识、`code` 固定失败码。
# 输出：委托稳定失败构造并写入当前版本，返回字典。
# 逻辑：委托稳定失败构造并写入当前版本，返回字典。
# 约束：不改变错误码及其提示。
def _workspace_failure(request_id: str, code: str) -> dict[str, Any]:
    return {**stable_failure_result(request_id, code), "chat_prompt_version": WORKSPACE_CHAT_PROMPT_VERSION}


# 功能：验证固定读取及实验维护工具及模型参数。
# 输入：`name` 模型选择的工具名称、`value` 模型生成的参数对象。
# 输出：客户参数严格核验；实验分页保留 20 条上限，返回名称与参数副本。
# 逻辑：客户参数严格核验；实验分页保留 20 条上限，返回名称与参数副本。
# 约束：实验参数完整类型由实时后端 Schema 验证，不猜测模型或批次名。
def _workspace_arguments(name: object, value: object) -> tuple[str, dict[str, Any]]:
    """校验客户参数与实验分页预算；实验字段类型继续由后端实时 Schema 验证。"""
    if (
        not isinstance(name, str)
        or name not in WORKSPACE_TOOLS
        or not isinstance(value, dict)
    ):
        raise ChatValidationError("工作空间只允许已登记的客户及实验读取及实验维护工具。")
    arguments = dict(value)
    if name in EXPERIMENT_TOOLS | EXPERIMENT_WRITE_TOOLS:
        if name == "experiments.rows":
            size = arguments.get("page_size", _WORKSPACE_MAX_SEARCH_PAGE_SIZE)
            if type(size) is not int or not 1 <= size <= _WORKSPACE_MAX_SEARCH_PAGE_SIZE:
                raise ChatValidationError("实验读取每页数量无效。")
            arguments.setdefault("page_size", _WORKSPACE_MAX_SEARCH_PAGE_SIZE)
        return name, arguments
    if name == "customers.search":
        if set(arguments) - {"q", "company", "archived", "page", "page_size"}:
            raise ChatValidationError("客户搜索含不支持的参数。")
        if "q" in arguments and (
            not isinstance(arguments["q"], str) or len(arguments["q"]) > 500
        ):
            raise ChatValidationError("客户搜索关键词无效。")
        if "company" in arguments:
            _workspace_uuid(arguments["company"])
        if "archived" in arguments and (
            not isinstance(arguments["archived"], str)
            or arguments["archived"] not in {"false", "all"}
        ):
            raise ChatValidationError("客户搜索归档参数无效。")
        if type(arguments.get("page", 1)) is not int or arguments.get("page", 1) < 1:
            raise ChatValidationError("客户搜索页码无效。")
        size = arguments.get("page_size", _WORKSPACE_MAX_SEARCH_PAGE_SIZE)
        if type(size) is not int or not 1 <= size <= _WORKSPACE_MAX_SEARCH_PAGE_SIZE:
            raise ChatValidationError("客户搜索每页数量无效。")
        arguments.setdefault("page", 1)
        arguments.setdefault("page_size", _WORKSPACE_MAX_SEARCH_PAGE_SIZE)
    elif set(arguments) != {"company_id"}:
        raise ChatValidationError("客户详情只接受 company_id。")
    else:
        _workspace_uuid(arguments["company_id"])
    return name, arguments


# 功能：校验 UUID 字符串。
# 输入：`value` 待验证的公司或工具读取 UUID 字符串。
# 输出：解析 UUID，成功无返回值。
# 逻辑：解析 UUID，成功无返回值。
# 约束：不验证该 UUID 对应记录的访问权限。
def _workspace_uuid(value: object) -> None:
    if not isinstance(value, str):
        raise ChatValidationError("公司 ID 必须是 UUID。")
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError):
        raise ChatValidationError("公司 ID 必须是 UUID。") from None


# 功能：解析请求实际发布的数据工具目录。
# 输入：`raw` 后端目录响应、`request_id` 当前已领取的请求标识。
# 输出：核对协议与请求，筛选固定候选并检查封闭 Schema，返回名称索引。
# 逻辑：核对协议与请求，筛选固定候选并检查封闭 Schema，返回名称索引。
# 约束：未发布工具不可执行，仅实验维护接受 write，其余要求 read。
def _workspace_catalog(raw: object, request_id: str) -> dict[str, dict[str, Any]]:
    """仅采用固定集合中由本次 processing 请求实际发布且执行模式匹配的工具。"""
    if not isinstance(raw, Mapping) or (
        raw.get("contract_version") != "chat-tools-v1"
        or raw.get("request_id") != request_id
        or not isinstance(raw.get("tools"), list)
    ):
        raise ChatValidationError("工作空间工具目录与当前请求不一致。")
    catalog: dict[str, dict[str, Any]] = {}
    for entry in raw["tools"]:
        if not isinstance(entry, Mapping):
            raise ChatValidationError("工作空间工具目录项无效。")
        name = entry.get("name")
        if name not in WORKSPACE_TOOLS:
            continue
        schema = entry.get("inputSchema")
        if (
            name in catalog
            or entry.get("executionMode") != ("write" if name in EXPERIMENT_WRITE_TOOLS else "read")
            or not isinstance(schema, Mapping)
            or schema.get("type") != "object"
            or not isinstance(schema.get("properties"), Mapping)
            or not isinstance(schema.get("required"), list)
            or schema.get("additionalProperties") is not False
        ):
            raise ChatValidationError("工作空间工具声明与执行模式契约不一致。")
        catalog[name] = {
            "name": name,
            "description": entry.get("description", ""),
            "inputSchema": dict(schema),
        }
    return catalog


# 功能：核对参数键与实时 Schema。
# 输入：`arguments` 工具参数、`schema` 本次后端目录发布的封闭对象 Schema。
# 输出：拒绝未声明字段及缺失必填项，成功无返回值。
# 逻辑：拒绝未声明字段及缺失必填项，成功无返回值。
# 约束：具体字段值仍由后端完整 Schema 校验。
def _workspace_schema_arguments(arguments: Mapping[str, Any], schema: Mapping[str, Any]) -> None:
    """模型参数先满足当前目录声明；具体业务约束仍由后端验证。"""
    properties = schema["properties"]
    if set(arguments) - set(properties) or set(schema["required"]) - set(arguments):
        raise ChatValidationError("工具参数与后端目录 Schema 不一致。")


# 功能：解析模型动作。
# 输入：`raw` 模型 JSON 文本、`evidence` 当前展示的来源白名单、`request_id` 当前请求标识。
# 输出：严格区分 tool 与 answer，返回动作和已验证载荷。
# 逻辑：严格区分 tool 与 answer，返回动作和已验证载荷。
# 约束：回答只能引用当前展示的 evidence；不接受其他动作。
def _workspace_decision(raw: object, evidence: list[dict[str, str]], request_id: str):
    if not isinstance(raw, str):
        raise ChatValidationError("模型结果必须是 JSON 文本。")
    value = _decode_json_object(raw)
    if value.get("action") == "tool" and set(value) == {"action", "name", "arguments"}:
        name, arguments = _workspace_arguments(value["name"], value["arguments"])
        return "tool", {"name": name, "arguments": arguments}
    if value.get("action") == "answer" and set(value) == {
        "action", "assistant_text", "citations"
    }:
        candidate = parse_model_candidate(
            {"assistant_text": value["assistant_text"], "citations": value["citations"]},
            allowed_context_items=evidence,
            request_id=request_id,
        )
        return "answer", candidate
    raise ChatValidationError("模型动作必须是读取及实验维护工具查询或最终回答。")


# 功能：校验读取回执并生成提示摘要。
# 输入：`raw` 后端读取回执、`request_id` 当前请求标识、`name` 预期被执行的工具名。
# 输出：核对请求工具和来源 UUID，按客户、实验目录、行或文件投影，返回摘要与完整证据。
# 逻辑：核对请求工具和来源 UUID，按客户、实验目录、行或文件投影，返回摘要与完整证据。
# 约束：摘要不展开附件正文；来源正文交给统一预算裁剪。
def _workspace_tool_result(raw: object, request_id: str, name: str):
    if not isinstance(raw, Mapping):
        raise ChatValidationError("工具响应必须是对象。")
    if raw.get("request_id") != request_id or raw.get("tool") != name:
        raise ChatValidationError("工具响应不属于本次请求。")
    if (
        raw.get("status") != "completed"
        or raw.get("http_status") != 200
        or not isinstance(raw.get("data"), Mapping)
    ):
        raise ChatValidationError("工具没有返回完成的查询数据。")
    _workspace_uuid(raw.get("read_id"))
    items = raw.get("evidence_items")
    if not isinstance(items, list) or not items:
        raise ChatValidationError("工具证据必须是数组。")
    evidence = [
        _source(item, path=f"tool.evidence_items[{index}]")
        for index, item in enumerate(items)
    ]
    if any(not item["source_id"].startswith(f"chat-tool:{raw['read_id']}:") for item in evidence):
        raise ChatValidationError("工具证据与本次读取 ID 不一致。")
    data = dict(raw["data"])
    if name == "customers.search":
        if (
            not isinstance(data.get("results"), list)
            or type(data.get("count")) is not int
            or data["count"] < 0
            or type(data.get("page")) is not int
            or data["page"] < 1
            or type(data.get("page_size")) is not int
            or data["page_size"] < 1
        ):
            raise ChatValidationError("客户搜索响应缺少分页数据。")
        rows = []
        for result in data["results"][:_WORKSPACE_MAX_SEARCH_PAGE_SIZE]:
            if not isinstance(result, Mapping):
                raise ChatValidationError("客户搜索结果必须是对象。")
            rows.append({key: result[key] for key in ("id", "name", "domains") if key in result})
        summary = {
            "count": data["count"], "page": data["page"],
            "page_size": data["page_size"], "results": rows,
        }
    elif name == "experiments.catalog":
        if not isinstance(data.get("batches"), list):
            raise ChatValidationError("实验目录缺少批次数组。")
        summary = {"batches": [{key: batch[key] for key in ("batch", "owner", "synthetic", "read_only", "notice", "total")}
                    | {"tables": [{key: table[key] for key in ("model", "name", "count")} | {"write": {key: table.get("write", {}).get(key, False) for key in ("create", "update", "delete")}} for table in batch["tables"]]}
                    for batch in data["batches"]]}
    elif name == "experiments.rows":
        if not isinstance(data.get("results"), list):
            raise ChatValidationError("实验分页缺少记录数组。")
        summary = {key: data[key] for key in ("batch", "model", "count", "page", "page_size")}
        summary["write"] = data.get("write", {})
        summary["results"] = [{key: row[key] for key in ("pk", "owner", "synthetic", "read_only", "fingerprint") if key in row}
                              for row in data["results"][:_WORKSPACE_MAX_SEARCH_PAGE_SIZE]]
    elif name in EXPERIMENT_WRITE_TOOLS:
        summary = {key: data[key] for key in ("batch", "model", "pk", "operation", "synthetic", "audit")}
    elif name == "experiments.file_read":
        summary = {key: value for key, value in data.items() if key != "content"}
    else:
        summary = {
            key: data[key]
            for key in ("company_id", "company_name", "id", "name")
            if key in data
        }
    return summary, evidence


# 功能：追加不同身份的读取证据。
# 输入：`allowed` 累积来源数组、`additions` 本次后端新登记的来源数组。
# 输出：按来源三字段去重并原地扩展 allowed，无返回值。
# 逻辑：按来源三字段去重并原地扩展 allowed，无返回值。
# 约束：同一身份内容冲突立即失败，不覆盖历史来源。
def _workspace_append_evidence(allowed: list[dict[str, str]], additions: list[dict[str, str]]):
    identities = {
        (item["source_id"], item["source_type"], item["title_or_label"]): item["content"]
        for item in allowed
    }
    for item in additions:
        key = (item["source_id"], item["source_type"], item["title_or_label"])
        if key in identities:
            if identities[key] != item["content"]:
                raise ChatValidationError("同一证据标识对应不同内容。")
            continue
        allowed.append(item)
        identities[key] = item["content"]


# 功能：截取与问题相关的原文片段。
# 输入：`content` 原始来源正文、`question` 当前问题、`limit` 此来源可用字符数。
# 输出：保留头部并按词单元寻找原文，返回受 limit 限制且带节选标记的文本。
# 逻辑：保留头部并按词单元寻找原文，返回受 limit 限制且带节选标记的文本。
# 约束：不改写原文含义，不表示已经提供全文。
def _workspace_excerpt(content: str, question: str, limit: int) -> str:
    """对模型展示明确标记的节选，优先保留与问题匹配的原文片段。"""
    if len(content) <= limit:
        return content
    marker = "\n[节选，原文未完整提供]"
    budget = limit - len(marker)
    head = min(700, budget // 3)
    excerpts = [content[:head]]
    used = [(0, head)]
    for term in sorted(_lexical_units(question), key=len, reverse=True):
        position = content.lower().find(term)
        if position < 0 or any(start <= position < end for start, end in used):
            continue
        start, end = max(0, position - 160), min(len(content), position + 320)
        if sum(map(len, excerpts)) + end - start > budget:
            break
        excerpts.append(content[start:end])
        used.append((start, end))
    if len(excerpts) == 1 and budget - len(excerpts[0]) >= 300:
        excerpts.append(content[-min(600, budget - len(excerpts[0])):])
    return "\n…\n".join(excerpts)[:budget] + marker


# 功能：选择预算内的来源。
# 输入：`evidence` 累积来源数组、`question` 当前问题；预算读取模块常量。
# 输出：按既定优先级和数量字符预算裁剪，返回完整来源白名单与展示片段。
# 逻辑：按既定优先级和数量字符预算裁剪，返回完整来源白名单与展示片段。
# 约束：保留原有来源优先级和预算；新实验文件优先展示，分页与目录随后，行记录最后；未展示来源不可引用。
def _workspace_prompt_evidence(
    evidence: list[dict[str, str]], question: str
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """选择可放入模型输入的来源；引用白名单仍使用所选来源的完整正文。"""
    priorities = {
        "customer_context": 0,
        "customer_search_page": 1,
        "internal_knowledge": 2,
        "customer_search": 3,
        "experiment_mutation": 0,
        "experiment_file": 0,
        "experiment_page": 1,
        "experiment_catalog": 1,
        "experiment_row": 3,
    }
    ranked = sorted(
        enumerate(evidence),
        key=lambda pair: (priorities.get(pair[1]["source_type"], 2), pair[0]),
    )
    visible: list[dict[str, str]] = []
    prompt: list[dict[str, str]] = []
    remaining = _WORKSPACE_MAX_PROMPT_CHARACTERS
    for _, item in ranked[:_WORKSPACE_MAX_EVIDENCE_ITEMS]:
        if remaining < 250:
            break
        limit = min(
            remaining,
            _WORKSPACE_DETAIL_EXCERPT_CHARACTERS
            if item["source_type"] == "customer_context"
            else _WORKSPACE_OTHER_EXCERPT_CHARACTERS,
        )
        excerpt = _workspace_excerpt(item["content"], question, limit)
        visible.append(item)
        prompt.append({**item, "content": excerpt})
        remaining -= len(excerpt)
    return visible, prompt


# 功能：处理工作空间问题和读取及实验维护工具循环。
# 输入：`request` 后端领取对象、`backend` 请求绑定客户端、`chat_provider` 单次模型调用函数。
# 输出：加载上下文，按模型决策发现客户及实验工具，最多六次读取后返回回答或阶段失败。
# 逻辑：加载上下文，按模型决策发现客户及实验工具，最多六次读取后返回回答或阶段失败。
# 约束：backend 是真实边界，chat_provider 是模型边界；保存证据在后端执行，参数与预算保持不变。
def answer_workspace_request(request: Mapping[str, Any], *, backend: Any, chat_provider: Any) -> dict[str, Any]:
    """最多六次数据工具调用；每次由模型选择，最终回答仅引用后端登记的证据。"""
    request_id = _recognizable_request_id(request)
    code = "invalid_request"
    try:
        request = parse_conversation_request(request)
        history = trim_recent_history(request["recent_history"])
        request_id = request["request_id"]
        if _is_direct_tool_action(request["question"]) and not re.search(r"kgseed|实验|虚构|synthetic", request["question"], re.I):
            return {
                "request_id": request_id,
                "chat_prompt_version": WORKSPACE_CHAT_PROMPT_VERSION,
                "assistant_text": "该操作未执行，工作空间只允许维护明确指定的共享虚构数据，不执行真实业务外部动作。",
                "citations": [], "status": "completed", "error": None,
            }
        code = "context_unavailable"
        context = parse_answer_context(
            backend.get_answer_context(request_id, "internal"),
            expected_request_id=request_id,
            expected_scope="internal",
        )
        if context["customer_context"] or context["customer_context_status"] != "completed":
            raise ChatValidationError("工作空间初始上下文不得包含预选客户。")
        if context["knowledge_status"] != "completed":
            raise ChatValidationError("工作空间内部知识状态不可用。")
        knowledge = trim_context_items([], context["context_items"], [])["internal_knowledge"]
        evidence = list(knowledge)
        observations: list[dict[str, Any]] = []
        signatures: set[tuple[str, str]] = set()
        catalog: dict[str, dict[str, Any]] | None = None
        for turn in range(_WORKSPACE_MAX_TOOL_READS + 1):
            visible_evidence, prompt_evidence = _workspace_prompt_evidence(
                evidence, request["question"]
            )
            messages = [
                {"role": "system", "content": _WORKSPACE_CHAT_SKILL.instructions},
                *history,
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "question": request["question"],
                            "authorized_evidence": prompt_evidence,
                            "evidence_items_available": len(evidence),
                            "evidence_items_shown": len(prompt_evidence),
                            "available_tools": list(catalog.values()) if catalog is not None else [
                                {"name": name} for name in sorted(WORKSPACE_TOOLS)
                            ],
                            "tool_results": observations,
                            "remaining_reads": _WORKSPACE_MAX_TOOL_READS - turn,
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
            code = "model_unavailable"
            started = perf_counter()
            logger.info(
                "workspace_chat_model_started request_id=%s turn=%s reads=%s evidence=%s",
                request_id, turn + 1, turn, len(prompt_evidence),
            )
            raw = chat_provider(messages, max_tokens=_WORKSPACE_CHAT_SKILL.max_tokens)
            logger.info(
                "workspace_chat_model_completed request_id=%s turn=%s duration_ms=%s output_chars=%s",
                request_id, turn + 1, round((perf_counter() - started) * 1000),
                len(raw) if isinstance(raw, str) else None,
            )
            code = "invalid_model_output"
            action, payload = _workspace_decision(raw, visible_evidence, request_id)
            if action == "answer":
                return {
                    "request_id": request_id, "chat_prompt_version": WORKSPACE_CHAT_PROMPT_VERSION,
                    **payload, "status": "completed", "error": None,
                }
            if turn >= _WORKSPACE_MAX_TOOL_READS:
                raise ChatValidationError("数据工具调用次数已达上限，模型仍未回答。")
            name, arguments = payload["name"], payload["arguments"]
            if catalog is None:
                code = "context_unavailable"
                catalog = _workspace_catalog(backend.get_chat_tools(request_id), request_id)
                logger.info(
                    "workspace_chat_tools_loaded request_id=%s tools=%s",
                    request_id, sorted(catalog),
                )
            if name not in catalog:
                observations.append({
                    "tool": name, "status": "unavailable", "reason": "not_published",
                })
                continue
            _workspace_schema_arguments(arguments, catalog[name]["inputSchema"])
            signature = (name, json.dumps(arguments, sort_keys=True, ensure_ascii=False))
            if signature in signatures:
                raise ChatValidationError("模型重复请求同一数据工具调用。")
            signatures.add(signature)
            code = "context_unavailable"
            try:
                raw_result = backend.read_chat_tool(request_id, name, arguments)
            except BackendRequestError as error:
                if (
                    error.scope != "tool"
                    or error.status_code not in {400, 404}
                ):
                    raise
                observations.append({
                    "tool": name, "arguments": arguments,
                    "status": "invalid_arguments" if error.status_code == 400 else "unavailable",
                    "http_status": error.status_code,
                })
                logger.info(
                    "workspace_chat_tool_unavailable request_id=%s tool=%s status=%s",
                    request_id, name, error.status_code,
                )
                continue
            summary, items = _workspace_tool_result(raw_result, request_id, name)
            _workspace_append_evidence(evidence, items)
            observations.append({
                "tool": name, "arguments": arguments, "status": "completed",
                "data": summary,
            })
            logger.info(
                "workspace_chat_tool_completed request_id=%s tool=%s evidence=%s total_evidence=%s",
                request_id, name, len(items), len(evidence),
            )
        raise ChatValidationError("工作空间聊天未生成回答。")
    except Exception as error:
        logger.warning(
            "workspace_chat_failed request_id=%s stage=%s error_type=%s reason=%s",
            request_id, code, type(error).__name__,
            str(error) if isinstance(error, ChatValidationError) else "unavailable",
        )
        if request_id is None:
            raise
        return _workspace_failure(request_id, code)


# 功能：核对响应丢失后的权威终态。
# 输入：`result` 本地回报载荷、`state` 后端权威状态响应。
# 输出：比较请求版本状态及失败内容或引用身份，返回布尔值。
# 逻辑：比较请求版本状态及失败内容或引用身份，返回布尔值。
# 约束：只确认已保存结果，不重新回报或运行模型。
def _chat_report_is_saved(result: Mapping[str, Any], state: object) -> bool:
    """回报响应丢失时，仅在权威终态与本地结果相符才确认已保存。"""
    if not isinstance(state, Mapping) or (
        state.get("request_id") != result["request_id"]
        or state.get("status") != result["status"]
        or state.get("chat_prompt_version") != result["chat_prompt_version"]
    ):
        return False
    if result["status"] == "failed":
        return state.get("error") == result["error"]
    stored = state.get("citations")
    if not isinstance(stored, list) or not state.get("assistant_message_id"):
        return False
    identity = ("source_id", "source_type", "title_or_label")
    return [tuple(item.get(key) for key in identity) for item in stored if isinstance(item, Mapping)] == [
        tuple(item[key] for key in identity) for item in result["citations"]
    ] and len(stored) == len(result["citations"])


# 功能：领取一次工作空间聊天请求并尝试一次回报。
# 输入：`backend` 后端客户端、`chat_provider` 单次模型调用边界。
# 输出：dict[str, Any] | None。
# 逻辑：领取请求后只执行工作空间聊天；不根据客户绑定或配置切换旧流程。
# 约束：请求必须没有预选公司；后端负责员工可见范围和权威结果保存。
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
        raise ChatValidationError("领取的聊天请求缺少 request_id。")
    logger.info("chat_request_claimed request_id=%s mode=workspace", request_id)
    result = answer_workspace_request(
        claimed_request, backend=backend, chat_provider=chat_provider
    )
    try:
        backend.report_answer(result)
    except Exception as error:
        if (
            isinstance(error, BackendContractError)
            or isinstance(error, BackendRequestError) and error.status_code == 0
        ) and hasattr(backend, "get_chat_request_status"):
            try:
                state = backend.get_chat_request_status(request_id)
                if _chat_report_is_saved(result, state):
                    logger.info(
                        "chat_report_confirmed request_id=%s status=%s",
                        request_id, result["status"],
                    )
                    return result
            except Exception as status_error:
                logger.warning(
                    "chat_report_status_failed request_id=%s error_type=%s",
                    request_id, type(status_error).__name__,
                )
        backend_detail = (
            _diagnostic_excerpt(error.detail)
            if isinstance(error, BackendRequestError) and error.status_code == 400
            else "details_hidden"
        )
        logger.error(
            "chat_report_failed request_id=%s result_status=%s error_type=%s http_status=%s backend_code=%s detail=%r",
            request_id, result["status"], type(error).__name__,
            error.status_code if isinstance(error, BackendRequestError) else None,
            error.code if isinstance(error, BackendRequestError) else None,
            backend_detail,
        )
        # 回报失败只形成本地结果；不自动重试，也不伪装已保存。
        return {
            **stable_failure_result(request_id, "report_failed"),
            "chat_prompt_version": result["chat_prompt_version"],
        }
    logger.info(
        "chat_report_completed request_id=%s status=%s code=%s",
        request_id, result["status"], (result["error"] or {}).get("code"),
    )
    return result


__all__ = [
    "ChatValidationError",
    "WORKSPACE_CHAT_PROMPT_VERSION",
    "answer_workspace_request",
    "bailian_chat_provider",
    "parse_answer_context",
    "parse_conversation_request",
    "parse_model_candidate",
    "process_chat_once",
    "stable_failure_result",
]
