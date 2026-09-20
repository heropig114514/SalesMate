"""工作空间聊天：按员工请求发现只读工具、读取后端证据并回报回答。

本模块只处理没有预选公司的聊天请求。后端决定员工可见范围并保存会话、
工具读取及引用；Agent 只选择查询、控制模型输入和核对输出。
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


class ChatValidationError(ValueError):
    """请求、来源或模型输出不符合工作空间聊天契约。"""


def _nonblank(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ChatValidationError(f"{path} 必须是非空字符串。")
    return value.strip()


def _keys(value: object, expected: set[str], path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ChatValidationError(f"{path} 字段必须与契约完全一致。")
    return value


def _recognizable_request_id(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    request_id = value.get("request_id")
    return request_id.strip() if isinstance(request_id, str) and request_id.strip() else None


def _source(value: object, path: str) -> dict[str, str]:
    item = _keys(value, _SOURCE_FIELDS, path)
    content = item["content"]
    if not isinstance(content, str):
        raise ChatValidationError(f"{path}.content 必须是字符串。")
    return {
        key: content if key == "content" else _nonblank(item[key], f"{path}.{key}")
        for key in ("source_id", "source_type", "title_or_label", "content")
    }


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


def trim_recent_history(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ChatValidationError("recent_history 必须是数组。")
    retained = list(value)
    while retained and sum(len(row["content"]) for row in retained) > _HISTORY_CHARACTER_BUDGET:
        retained.pop(0)
    return retained


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


def _decode_json_object(value: str) -> dict[str, Any]:
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

    def replace_marker(_: re.Match[str]) -> str:
        row = citations[next(cursor) - 1]
        key = tuple(row[field] for field in ("source_id", "source_type", "title_or_label"))
        return f"[{used[key]}]"

    text = _CITATION_MARKER.sub(replace_marker, text)
    return {"assistant_text": text, "citations": compact}


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


def _diagnostic_excerpt(value: str, limit: int = 160) -> str:
    excerpt = re.sub(r"\s+", " ", value).strip()
    excerpt = re.sub(r"[\w.+-]+@[\w.-]+", "[email]", excerpt)
    return excerpt[:limit]


def _lexical_units(value: str) -> set[str]:
    units = set(re.findall(r"[a-z0-9][a-z0-9_-]+", value.lower()))
    for sequence in re.findall(r"[\u4e00-\u9fff]+", value):
        units.update(sequence[index:index + 2] for index in range(len(sequence) - 1))
    return units


def _is_direct_tool_action(question: str) -> bool:
    compact = re.sub(r"\s+", "", question).lower()
    if re.search(r"(?:如何|怎么|怎样).{0,8}(?:发送|安排|创建|更新|删除)", compact):
        return False
    return bool(re.search(
        r"(?:请|帮我|替我|直接|马上|现在).{0,30}(?:发送|发信|寄出|群发|安排会议|预约会议|创建日程|删除客户|修改客户|写入crm|保存报价)",
        compact,
    ))


def bailian_chat_provider(messages: list[dict[str, str]], *, max_tokens: int = _WORKSPACE_CHAT_SKILL.max_tokens) -> str:
    from agent.llm.bailian import generate_chat_json
    return generate_chat_json(messages, max_tokens=max_tokens)


def _workspace_failure(request_id: str, code: str) -> dict[str, Any]:
    return {**stable_failure_result(request_id, code), "chat_prompt_version": WORKSPACE_CHAT_PROMPT_VERSION}


def _workspace_arguments(name: object, value: object) -> tuple[str, dict[str, Any]]:
    """只接受当前后端协定的两个只读客户工具及其参数。"""
    if (
        not isinstance(name, str)
        or name not in {"customers.search", "customers.context"}
        or not isinstance(value, dict)
    ):
        raise ChatValidationError("工作空间只允许客户搜索和客户详情只读工具。")
    arguments = dict(value)
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


def _workspace_uuid(value: object) -> None:
    if not isinstance(value, str):
        raise ChatValidationError("公司 ID 必须是 UUID。")
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError):
        raise ChatValidationError("公司 ID 必须是 UUID。") from None


def _workspace_catalog(raw: object, request_id: str) -> dict[str, dict[str, Any]]:
    """只采用后端在本次 processing 请求中发布的两个 read 工具。"""
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
        if name not in {"customers.search", "customers.context"}:
            continue
        schema = entry.get("inputSchema")
        if (
            name in catalog
            or entry.get("executionMode") != "read"
            or not isinstance(schema, Mapping)
            or schema.get("type") != "object"
            or not isinstance(schema.get("properties"), Mapping)
            or not isinstance(schema.get("required"), list)
            or schema.get("additionalProperties") is not False
        ):
            raise ChatValidationError("工作空间工具声明与只读契约不一致。")
        catalog[name] = {
            "name": name,
            "description": entry.get("description", ""),
            "inputSchema": dict(schema),
        }
    return catalog


def _workspace_schema_arguments(arguments: Mapping[str, Any], schema: Mapping[str, Any]) -> None:
    """模型参数先满足当前目录声明；具体业务约束仍由后端验证。"""
    properties = schema["properties"]
    if set(arguments) - set(properties) or set(schema["required"]) - set(arguments):
        raise ChatValidationError("工具参数与后端目录 Schema 不一致。")


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
    raise ChatValidationError("模型动作必须是只读工具查询或最终回答。")


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
    else:
        summary = {
            key: data[key]
            for key in ("company_id", "company_name", "id", "name")
            if key in data
        }
    return summary, evidence


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


def _workspace_prompt_evidence(
    evidence: list[dict[str, str]], question: str
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """选择可放入模型输入的来源；引用白名单仍使用所选来源的完整正文。"""
    priorities = {
        "customer_context": 0,
        "customer_search_page": 1,
        "internal_knowledge": 2,
        "customer_search": 3,
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


def answer_workspace_request(request: Mapping[str, Any], *, backend: Any, chat_provider: Any) -> dict[str, Any]:
    """最多六次只读查询；每次由模型选择，最终回答仅引用后端登记的证据。"""
    request_id = _recognizable_request_id(request)
    code = "invalid_request"
    try:
        request = parse_conversation_request(request)
        history = trim_recent_history(request["recent_history"])
        request_id = request["request_id"]
        if _is_direct_tool_action(request["question"]):
            return {
                "request_id": request_id,
                "chat_prompt_version": WORKSPACE_CHAT_PROMPT_VERSION,
                "assistant_text": "该操作未执行，当前工作空间聊天只支持只读查询和文字建议。",
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
                                {"name": "customers.search"}, {"name": "customers.context"}
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
                raise ChatValidationError("只读查询次数已达上限，模型仍未回答。")
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
                raise ChatValidationError("模型重复请求同一只读查询。")
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
