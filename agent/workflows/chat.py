"""Responsibility: Orchestrate the complete request-scoped business tool catalog, validating model answer citations.
Implementation: Read all published tools eagerly and derive write checkpoints from live execution modes; preserve arbitrary business receipts and specialized customer/experiment evidence. Constrain model calls with live catalogs and unchanged budgets; writes save a checkpoint and release the Worker while awaiting browser approval. Resumed claims consume canonical receipts without repeating the write or earlier model calls.
Relationships: DjangoBackendClient provides request-bound HTTP; the workspace-chat skill defines selection rules and the backend persists evidence.
Directory:
- ChatValidationError: Represent workspace contract validation failure.
- _nonblank: Read nonempty text.
- _keys: Validate fields of a closed object.
- _recognizable_request_id: Extract a recognizable request ID.
- _source: Validate a four-field source.
- parse_conversation_request: Parse a claimed workspace request.
- parse_answer_context: Parse frozen request context.
- trim_recent_history: Retain recent history within a character budget.
- trim_context_items: Trim initial knowledge input.
- _decode_json_object: Parse a JSON object without duplicate keys.
- _decode_json_object.unique: Reject duplicate JSON fields.
- parse_model_candidate: Validate answer citations and renumber them.
- parse_model_candidate.replace_marker: Replace citation numbers in the answer body.
- stable_failure_result: Build a stable failure report.
- _diagnostic_excerpt: Generate a bounded error summary.
- _lexical_units: Extract snippet-matching units.
- bailian_chat_provider: Invoke Bailian to generate one JSON answer.
- _workspace_failure: Generate a failure result for the current workspace version.
- _workspace_arguments: Validate model tool envelopes, specialized proposal arguments and existing pagination budgets.
- _workspace_uuid: Validate a UUID string.
- _workspace_catalog: Parse the data tool catalog actually published for the request.
- _workspace_schema_arguments: Validate argument keys against the live schema.
- _workspace_decision: Parse a model action.
- _workspace_tool_result: Validate a read receipt and generate a prompt summary.
- _workspace_append_evidence: Append read evidence with distinct identities.
- _workspace_excerpt: Extract original text relevant to the question.
- _workspace_prompt_evidence: Select sources within budget.
- answer_workspace_request: Process a workspace question and the read/confirmation proposal tool loop.
- _chat_report_is_saved: Check the authoritative terminal state after a lost response.
- process_chat_once: Claim and process at most one chat request.
Variable index:
- WORKSPACE_CHAT_PROMPT_VERSION: Prompt version actually loaded.
- _CITATION_FIELDS: Citation metadata contract.
- _CITATION_MARKER: Regular expression for numeric body citations.
- _CONTEXT_FIELDS: Context response field contract.
- _FAILURE_MESSAGES: Stable failure codes and messages.
- _HISTORY_CHARACTER_BUDGET: Recent history character budget.
- _NONSTANDARD_SOURCE_TAG: Regular expression for legacy nonstandard citation tags.
- _REQUEST_FIELDS: Five-field claimed-request contract.
- _SOURCE_FIELDS: Four-field source contract.
- _WORKSPACE_CHAT_SKILL: Workspace prompt and established model output budget.
- _WORKSPACE_DETAIL_EXCERPT_CHARACTERS: Excerpt budget for each customer detail.
- _WORKSPACE_MAX_EVIDENCE_ITEMS: Maximum of 12 sources displayed to the model.
- _WORKSPACE_MAX_PROMPT_CHARACTERS: Total source-body character budget.
- _WORKSPACE_MAX_SEARCH_PAGE_SIZE: Model pagination read limit of 20.
- _WORKSPACE_MAX_TOOL_READS: At most six tool selections per answer.
- _WORKSPACE_OTHER_EXCERPT_CHARACTERS: Per-source excerpt budget for other sources.
- __all__: Public workspace parsing and execution symbols.
- logger: Workflow stage and timing logs.
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
from integrations.salesmate_tools.read_contract import EXPERIMENT_WRITE_TOOLS, CUSTOMER_WRITE_TOOLS
from agent.clients.chat_actions import (
    ACTION_TOOLS, BUSINESS_READ_TOOLS, CONFIRMATION_CONTRACT, PREPARE_TOOLS,
    action_answer, require_observed_targets,
    validate_action_arguments, validate_action_receipt,
)


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
    "invalid_request": "Invalid workspace chat request.",
    "context_unavailable": "Chat context is temporarily unavailable.",
    "model_unavailable": "The answer model is temporarily unavailable. Try again later.",
    "invalid_model_output": "The answer model returned an invalid result.",
    "report_failed": "The answer could not be saved right now.",
}
_CITATION_MARKER = re.compile(r"\[(\d+)\]")
_NONSTANDARD_SOURCE_TAG = re.compile(
    r"[ \t]*\[(?:分析|邮件|画像|知识|来源|证据)[ \t]*[:：][ \t]*\d+\]"
)
logger = logging.getLogger("salesmate.chat")


# Function: Represent workspace contract validation failure.
# Logic: Callers convert this to a stage failure without hiding parsing errors.
# Constraints: Marks validation failure only; performs no I/O.
class ChatValidationError(ValueError):
    """Request, source, or model output violates the workspace chat contract."""


# Function: Read nonempty text.
# Inputs: `value`: text to validate; `path`: field path in error messages.
# Outputs: Validate string type and nonempty stripped content, returning the original string.
# Logic: Validate string type and nonempty stripped content, returning the original string.
# Constraints: Invalid values raise ChatValidationError.
def _nonblank(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ChatValidationError(f"{path} must be a nonempty string.")
    return value.strip()


# Function: Validate fields of a closed object.
# Inputs: `value`: object to validate; `expected`: allowed and required keys; `path`: error field path.
# Outputs: Require a Mapping whose keys exactly match expected; return the original object.
# Logic: Require a Mapping whose keys exactly match expected; return the original object.
# Constraints: Reject missing and extra fields.
def _keys(value: object, expected: set[str], path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ChatValidationError(f"{path} fields must match the contract exactly.")
    return value


# Function: Extract a recognizable request ID.
# Inputs: `value`: potentially invalid claimed request object.
# Outputs: Return its ID if it contains a nonempty string; otherwise return None.
# Logic: Return its ID if it contains a nonempty string; otherwise return None.
# Constraints: Used only for failure attribution, not proof of request authorization.
def _recognizable_request_id(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    request_id = value.get("request_id")
    return request_id.strip() if isinstance(request_id, str) and request_id.strip() else None


# Function: Validate a four-field source.
# Inputs: `value`: source object to validate; `path`: error field path.
# Outputs: Validate nonempty metadata and a string body, returning a source dictionary.
# Logic: Validate nonempty metadata and a string body, returning a source dictionary.
# Constraints: Preserve the original body without generating new evidence.
def _source(value: object, path: str) -> dict[str, str]:
    item = _keys(value, _SOURCE_FIELDS, path)
    content = item["content"]
    if not isinstance(content, str):
        raise ChatValidationError(f"{path}.content must be a string.")
    return {
        key: content if key == "content" else _nonblank(item[key], f"{path}.{key}")
        for key in ("source_id", "source_type", "title_or_label", "content")
    }


# Function: Parse a claimed workspace request.
# Inputs: `value`: five-field claim object with optional backend-owned resume data.
# Outputs: Normalized request and optional resume object.
# Logic: Validate identifiers and history; the workflow validates checkpoint contents before continuation.
# Constraints: Reject preselected companies and all extra fields except resume.
def parse_conversation_request(value: object) -> dict[str, Any]:
    """Parse a workspace request claimed from the backend."""
    fields = _REQUEST_FIELDS | ({"resume"} if isinstance(value, Mapping) and "resume" in value else set())
    request = _keys(value, fields, "request")
    history = request["recent_history"]
    if not isinstance(history, list):
        raise ChatValidationError("recent_history must be an array.")
    parsed_history = []
    for index, raw in enumerate(history):
        row = _keys(raw, {"role", "content"}, f"recent_history[{index}]")
        if row["role"] not in {"user", "assistant"}:
            raise ChatValidationError("Invalid history message role.")
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
        **({"resume": request["resume"]} if "resume" in request else {}),
    }


# Function: Parse frozen request context.
# Inputs: `value`: backend-frozen context; `expected_request_id` and `expected_scope` default to None, disabling the corresponding extra expected-value check.
# Outputs: Validate request, scope, source consistency, and status, returning a context dictionary.
# Logic: Validate request, scope, source consistency, and status, returning a context dictionary.
# Constraints: Identical source metadata cannot identify different bodies.
def parse_answer_context(
    value: object, *, expected_request_id: str | None = None,
    expected_scope: str | None = None,
) -> dict[str, Any]:
    """Parse backend-frozen context; sources must remain unique within this request."""
    context = _keys(value, _CONTEXT_FIELDS, "answer_context")
    request_id = _nonblank(context["request_id"], "answer_context.request_id")
    if expected_request_id is not None and request_id != expected_request_id:
        raise ChatValidationError("answer_context.request_id does not match the current request.")
    scope = context["scope"]
    if scope not in {"internal", "external"} or (
        expected_scope is not None and scope != expected_scope
    ):
        raise ChatValidationError("answer_context.scope does not match the request scope.")
    for field in ("customer_context", "context_items", "retrieval_gaps"):
        if not isinstance(context[field], list):
            raise ChatValidationError(f"answer_context.{field} must be an array.")
    customer = [_source(item, "customer_context") for item in context["customer_context"]]
    knowledge = [_source(item, "context_items") for item in context["context_items"]]
    identities: dict[tuple[str, str, str], str] = {}
    for item in [*customer, *knowledge]:
        key = tuple(item[field] for field in ("source_id", "source_type", "title_or_label"))
        if key in identities and identities[key] != item["content"]:
            raise ChatValidationError("The same source ID refers to different evidence content.")
        identities[key] = item["content"]
    customer_status = context["customer_context_status"]
    if customer_status not in ({"completed", "failed"} if scope == "internal" else {"not_applicable"}):
        raise ChatValidationError("Invalid answer_context.customer_context_status.")
    if context["knowledge_status"] not in {"completed", "failed"}:
        raise ChatValidationError("Invalid answer_context.knowledge_status.")
    if type(context["external_available"]) is not bool:
        raise ChatValidationError("answer_context.external_available must be a boolean.")
    if scope == "external" and (customer or not context["external_available"]):
        raise ChatValidationError("Invalid external context state.")
    return {
        **context,
        "request_id": request_id,
        "scope": scope,
        "customer_context": customer,
        "context_items": knowledge,
    }


# Function: Retain recent history within a character budget.
# Inputs: `value`: recent-message array with roles and bodies already validated.
# Outputs: Remove earliest messages first and return the remaining list.
# Logic: Remove earliest messages first and return the remaining list.
# Constraints: Do not change the original list or established history budget.
def trim_recent_history(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ChatValidationError("recent_history must be an array.")
    retained = list(value)
    while retained and sum(len(row["content"]) for row in retained) > _HISTORY_CHARACTER_BUDGET:
        retained.pop(0)
    return retained


# Function: Trim initial knowledge input.
# Inputs: `customer_context`: customer sources; `internal_knowledge`: internal knowledge; `external_knowledge`: external knowledge arrays.
# Outputs: Select at most 12 sources in order, add excerpt markers beyond 2000 characters, and return three source groups.
# Logic: Select at most 12 sources in order, add excerpt markers beyond 2000 characters, and return three source groups.
# Constraints: Do not mutate original backend evidence.
def trim_context_items(customer_context: object, internal_knowledge: object, external_knowledge: object) -> dict[str, list[dict[str, str]]]:
    """Display at most 12 knowledge sources, each up to 2000 characters, without changing original backend evidence."""
    result = {"customer_context": [], "internal_knowledge": [], "external_knowledge": []}
    for field, items in (
        ("customer_context", customer_context),
        ("internal_knowledge", internal_knowledge),
        ("external_knowledge", external_knowledge),
    ):
        if not isinstance(items, list):
            raise ChatValidationError(f"{field} must be an array.")
        for raw in items:
            if sum(map(len, result.values())) >= 12:
                break
            item = _source(raw, field)
            if item["content"].strip():
                result[field].append({
                    **item,
                    "content": item["content"][:1985] + "\n[Excerpt; full source not provided]"
                    if len(item["content"]) > 2000 else item["content"],
                })
    return result


# Function: Parse a JSON object without duplicate keys.
# Inputs: `value`: JSON string returned by the model.
# Outputs: Reject duplicate keys through an object hook and return a dict.
# Logic: Reject duplicate keys through an object hook; report only the decoded root type when it is not an object, without logging model content.
# Constraints: Invalid JSON or non-object values raise ChatValidationError.
def _decode_json_object(value: str) -> dict[str, Any]:
    # Function: Reject duplicate JSON fields.
    # Inputs: `pairs`: ordered key-value pairs supplied by the JSON decoder.
    # Outputs: Build a dictionary from pairs, failing immediately on duplicate keys.
    # Logic: Build a dictionary from pairs, failing immediately on duplicate keys.
    # Constraints: Used as a JSON decoding hook; never merge duplicate fields.
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ChatValidationError("Model output contains duplicate JSON fields.")
            result[key] = item
        return result
    try:
        parsed = json.loads(value, object_pairs_hook=unique)
    except (ValueError, TypeError) as error:
        raise ChatValidationError("Model output is not a valid JSON object.") from error
    if not isinstance(parsed, dict):
        raise ChatValidationError(f"Model output must be a JSON object; received {type(parsed).__name__}.")
    return parsed


# Function: Validate answer citations and renumber them.
# Inputs: `value`: model object containing answer and citations; `allowed_context_items`: sources displayed this round; `request_id`: optional request identifier.
# Outputs: Match allowed sources by their three-field identity, compact citations in order of first body appearance, and return the answer object.
# Logic: Match allowed sources by their three-field identity, compact citations in order of first body appearance, and return the answer object.
# Constraints: Do not cite undisplayed sources; request_id remains a call-contract parameter and is currently unused for validation.
def parse_model_candidate(
    value: object, *, allowed_context_items: object, request_id: str | None = None
) -> dict[str, Any]:
    """Allow citations only to evidence visible in this round and renumber used citations."""
    candidate = _keys(value, {"assistant_text", "citations"}, "model_candidate")
    text = _NONSTANDARD_SOURCE_TAG.sub("", _nonblank(candidate["assistant_text"], "assistant_text"))
    raw_citations = candidate["citations"]
    if not isinstance(raw_citations, list) or not isinstance(allowed_context_items, list):
        raise ChatValidationError("citations must be an array.")
    allowlist = {
        tuple(item[field] for field in ("source_id", "source_type", "title_or_label"))
        for item in allowed_context_items if item["content"].strip()
    }
    citations = []
    for index, raw in enumerate(raw_citations):
        citation = _keys(raw, _CITATION_FIELDS, f"citations[{index}]")
        parsed = {field: _nonblank(citation[field], field) for field in _CITATION_FIELDS}
        if tuple(parsed[field] for field in ("source_id", "source_type", "title_or_label")) not in allowlist:
            raise ChatValidationError("Citation is not in the authorized sources visible this turn.")
        citations.append(parsed)
    markers = [int(value) for value in _CITATION_MARKER.findall(text)]
    if any(marker < 1 or marker > len(citations) for marker in markers):
        raise ChatValidationError("Answer citation numbers do not match citations.")
    if not markers and citations:
        logger.info("workspace_chat_unused_citations_removed request_id=%s count=%s", request_id, len(citations))
    used: dict[tuple[str, str, str], int] = {}
    compact = []
    for marker in markers:
        row = citations[marker - 1]
        key = tuple(row[field] for field in ("source_id", "source_type", "title_or_label"))
        if key not in used:
            used[key] = len(compact) + 1
            compact.append(row)
    cursor = iter(markers)

    # Function: Replace citation numbers in the answer body.
    # Inputs: `_`: regex match object; implicitly read the validated enclosing marker cursor and citation mapping.
    # Outputs: Read enclosing cursor, citations, and used state to return a compacted number string.
    # Logic: Read enclosing cursor, citations, and used state to return a compacted number string.
    # Constraints: The parameter is a regex match; numbers come from validated markers.
    def replace_marker(_: re.Match[str]) -> str:
        row = citations[next(cursor) - 1]
        key = tuple(row[field] for field in ("source_id", "source_type", "title_or_label"))
        return f"[{used[key]}]"

    text = _CITATION_MARKER.sub(replace_marker, text)
    return {"assistant_text": text, "citations": compact}


# Function: Build a stable failure report.
# Inputs: `request_id`: recognizable request identifier; `code`: error code in the fixed failure-message table.
# Outputs: Validate request ID and failure code, returning a six-field failed object.
# Logic: Validate request ID and failure code, returning a six-field failed object.
# Constraints: Accept only fixed failure codes; do not persist backend state.
def stable_failure_result(request_id: object, code: object) -> dict[str, Any]:
    request_id = _nonblank(request_id, "request_id")
    if code not in _FAILURE_MESSAGES:
        raise ChatValidationError("Invalid failure code.")
    return {
        "request_id": request_id,
        "chat_prompt_version": WORKSPACE_CHAT_PROMPT_VERSION,
        "assistant_text": "", "citations": [], "status": "failed",
        "error": {"code": code, "message": _FAILURE_MESSAGES[code]},
    }


# Function: Generate a bounded error summary.
# Inputs: `value`: safe error details; `limit`: truncation length, default 160 characters.
# Outputs: Collapse whitespace, mask email addresses, and truncate to limit, returning a string.
# Logic: Collapse whitespace, mask email addresses, and truncate to limit, returning a string.
# Constraints: Not a general sensitive-data filter; intended only for safe error details.
def _diagnostic_excerpt(value: str, limit: int = 160) -> str:
    excerpt = re.sub(r"\s+", " ", value).strip()
    excerpt = re.sub(r"[\w.+-]+@[\w.-]+", "[email]", excerpt)
    return excerpt[:limit]


# Function: Extract snippet-matching units.
# Inputs: `value`: current user question or another string used for snippet matching.
# Outputs: Extract English words and Chinese bigrams into a set.
# Logic: Extract English words and Chinese bigrams into a set.
# Constraints: Used only to locate excerpts, not as a semantic retrieval model.
def _lexical_units(value: str) -> set[str]:
    units = set(re.findall(r"[a-z0-9][a-z0-9_-]+", value.lower()))
    for sequence in re.findall(r"[\u4e00-\u9fff]+", value):
        units.update(sequence[index:index + 2] for index in range(len(sequence) - 1))
    return units


# Function: Invoke Bailian to generate one JSON answer.
# Inputs: `messages`: model message array; `max_tokens`: maximum output budget, defaulting to the workspace skill setting.
# Outputs: Delegate to generate_chat_json and return model text.
# Logic: Delegate to generate_chat_json and return model text.
# Constraints: max_tokens defaults to the skill value; network and model errors propagate.
def bailian_chat_provider(messages: list[dict[str, str]], *, max_tokens: int = _WORKSPACE_CHAT_SKILL.max_tokens) -> str:
    from agent.llm.bailian import generate_chat_json
    return generate_chat_json(messages, max_tokens=max_tokens)


# Function: Generate a failure result for the current workspace version.
# Inputs: `request_id`: current request identifier; `code`: fixed failure code.
# Outputs: Delegate to stable failure construction, attach the current version, and return a dictionary.
# Logic: Delegate to stable failure construction, attach the current version, and return a dictionary.
# Constraints: Do not change error codes or their messages.
def _workspace_failure(request_id: str, code: str) -> dict[str, Any]:
    return {**stable_failure_result(request_id, code), "chat_prompt_version": WORKSPACE_CHAT_PROMPT_VERSION}


# Function: Validate model tool envelopes, specialized proposal arguments and existing pagination budgets.
# Inputs: `name`: model-selected tool name; `value`: model-generated argument object.
# Outputs: Return the nonblank name and copied JSON object with existing specialized pagination defaults.
# Logic: Check independent proposal contracts and existing pagination; the live catalog supplies generic argument requirements.
# Constraints: The live backend schema validates every tool; this function grants no name-based authorization.
def _workspace_arguments(name: object, value: object) -> tuple[str, dict[str, Any]]:
    if not isinstance(name, str) or not name.strip() or not isinstance(value, dict):
        raise ChatValidationError("Tool name and arguments must be a nonblank string and object.")
    arguments = dict(value)
    if name in ACTION_TOOLS:
        try:
            validate_action_arguments(name, arguments)
        except ValueError as error:
            raise ChatValidationError(str(error)) from error
    if name in {"customers.search", "experiments.rows", "orders.list", "connections.list"}:
        size = arguments.get("page_size", _WORKSPACE_MAX_SEARCH_PAGE_SIZE)
        if type(size) is not int or not 1 <= size <= _WORKSPACE_MAX_SEARCH_PAGE_SIZE:
            raise ChatValidationError("Invalid search page size.")
        arguments.setdefault("page_size", _WORKSPACE_MAX_SEARCH_PAGE_SIZE)
        if name != "experiments.rows":
            arguments.setdefault("page", 1)
    return name, arguments


# Function: Validate a UUID string.
# Inputs: `value`: company or tool-read UUID string to validate.
# Outputs: Parse the UUID; no return value on success.
# Logic: Parse the UUID; no return value on success.
# Constraints: Do not validate access permissions for the record identified by this UUID.
def _workspace_uuid(value: object) -> None:
    if not isinstance(value, str):
        raise ChatValidationError("Company ID must be a UUID.")
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError):
        raise ChatValidationError("Company ID must be a UUID.") from None


# Function: Parse the data tool catalog actually published for the request.
# Inputs: `raw`: backend catalog response; `request_id`: currently claimed request identifier.
# Outputs: Validate protocol, request, unique names and closed schemas, returning all published tools indexed by name.
# Logic: Accept read/write/confirm modes from the live registry; independent proposals additionally require their exact mode and confirmation contract.
# Constraints: No static name filter; unpublished or malformed tools cannot execute, and generic write/confirm modes use checkpoints.
def _workspace_catalog(raw: object, request_id: str) -> dict[str, dict[str, Any]]:
    """Use every valid tool published for this processing request."""
    if not isinstance(raw, Mapping) or (
        raw.get("contract_version") != "chat-tools-v1"
        or raw.get("request_id") != request_id
        or not isinstance(raw.get("tools"), list)
    ):
        raise ChatValidationError("Workspace tool catalog does not match the current request.")
    catalog: dict[str, dict[str, Any]] = {}
    for entry in raw["tools"]:
        if not isinstance(entry, Mapping):
            raise ChatValidationError("Invalid workspace tool catalog entry.")
        name = entry.get("name")
        schema = entry.get("inputSchema")
        if (
            not isinstance(name, str) or not name.strip() or name in catalog
            or entry.get("executionMode") not in {"read", "write", "confirm"}
            or (name in ACTION_TOOLS and entry.get("executionMode") != ("confirm" if name in PREPARE_TOOLS else "read"))
            or (name in ACTION_TOOLS and entry.get("confirmationContract") != CONFIRMATION_CONTRACT)
            or not isinstance(schema, Mapping)
            or schema.get("type") != "object"
            or not isinstance(schema.get("properties"), Mapping)
            or not isinstance(schema.get("required"), list)
            or schema.get("additionalProperties") is not False
        ):
            raise ChatValidationError("Workspace tool declaration does not match the execution-mode contract.")
        catalog[name] = {
            "name": name,
            "description": entry.get("description", ""),
            "inputSchema": dict(schema),
            "executionMode": entry["executionMode"],
        }
    return catalog


# Function: Validate argument keys against the live schema.
# Inputs: `arguments`: tool arguments; `schema`: closed-object schema published in the current backend catalog.
# Outputs: Reject undeclared fields and missing required fields; no return value on success.
# Logic: Reject undeclared fields and missing required fields; no return value on success.
# Constraints: The complete backend schema still validates individual field values.
def _workspace_schema_arguments(arguments: Mapping[str, Any], schema: Mapping[str, Any]) -> None:
    """Model arguments must first satisfy the current catalog; the backend still validates business constraints."""
    properties = schema["properties"]
    if set(arguments) - set(properties) or set(schema["required"]) - set(arguments):
        raise ChatValidationError("Tool arguments do not match the backend catalog schema.")


# Function: Parse a model action.
# Inputs: `raw`: model JSON text; `evidence`: currently displayed source allowlist; `request_id`: current request identifier.
# Outputs: Strictly distinguish tool and answer actions, returning the action and validated payload.
# Logic: Strictly distinguish tool and answer actions, returning the action and validated payload.
# Constraints: Answers may cite only currently displayed evidence; reject other actions.
def _workspace_decision(raw: object, evidence: list[dict[str, str]], request_id: str):
    if not isinstance(raw, str):
        raise ChatValidationError("Model output must be JSON text.")
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
    raise ChatValidationError("Model action must be an authorized tool query or final answer.")


# Function: Validate a read receipt and generate a prompt summary.
# Inputs: `raw`: backend read receipt; `request_id`: current request identifier; `name`: expected executed tool name.
# Outputs: Validate request, tool, and source UUIDs; project customer, experiment catalog, row, or file results into summaries and complete evidence.
# Logic: Validate request/tool/source bindings and 2xx receipt status; specialize known evidence and preserve arbitrary JSON results and queued/pending states for generic tools.
# Constraints: Summaries do not expand attachment bodies; apply shared budgets to source bodies.
def _workspace_tool_result(raw: object, request_id: str, name: str):
    if not isinstance(raw, Mapping):
        raise ChatValidationError("Tool response must be an object.")
    if raw.get("request_id") != request_id or raw.get("tool") != name:
        raise ChatValidationError("Tool response does not belong to this request.")
    if (
        raw.get("status") not in {"completed", "accepted", "confirmation_required"}
        or type(raw.get("http_status")) is not int or not 200 <= raw["http_status"] < 300
        or "data" not in raw
    ):
        raise ChatValidationError("Tool did not return a valid business receipt.")
    _workspace_uuid(raw.get("read_id"))
    items = raw.get("evidence_items")
    if not isinstance(items, list) or not items:
        raise ChatValidationError("Tool evidence must be an array.")
    evidence = [
        _source(item, path=f"tool.evidence_items[{index}]")
        for index, item in enumerate(items)
    ]
    if any(not item["source_id"].startswith(f"chat-tool:{raw['read_id']}:") for item in evidence):
        raise ChatValidationError("Tool evidence does not match this read ID.")
    data = raw["data"]
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
            raise ChatValidationError("Customer search response is missing pagination.")
        rows = []
        for result in data["results"][:_WORKSPACE_MAX_SEARCH_PAGE_SIZE]:
            if not isinstance(result, Mapping):
                raise ChatValidationError("Customer search result must be an object.")
            rows.append({key: result[key] for key in ("id", "name", "domains") if key in result})
        summary = {
            "count": data["count"], "page": data["page"],
            "page_size": data["page_size"], "results": rows,
        }
    elif name == "experiments.catalog":
        if not isinstance(data.get("batches"), list):
            raise ChatValidationError("Experiment catalog is missing the batches array.")
        summary = {"batches": [{key: batch[key] for key in ("batch", "owner", "synthetic", "read_only", "notice", "total")}
                    | {"tables": [{key: table[key] for key in ("model", "name", "count")} | {"write": {key: table.get("write", {}).get(key, False) for key in ("create", "update", "delete")}} for table in batch["tables"]]}
                    for batch in data["batches"]]}
    elif name == "experiments.rows":
        if not isinstance(data.get("results"), list):
            raise ChatValidationError("Experiment page is missing the records array.")
        summary = {key: data[key] for key in ("batch", "model", "count", "page", "page_size")}
        summary["write"] = data.get("write", {})
        summary["results"] = [{key: row[key] for key in ("pk", "owner", "synthetic", "read_only", "fingerprint") if key in row}
                              for row in data["results"][:_WORKSPACE_MAX_SEARCH_PAGE_SIZE]]
    elif name in EXPERIMENT_WRITE_TOOLS:
        summary = {key: data[key] for key in ("batch", "model", "pk", "operation", "synthetic", "audit")}
    elif name in CUSTOMER_WRITE_TOOLS:
        _workspace_uuid(data.get("id"))
        _nonblank(data.get("name"), "created customer name")
        summary = {"id": data["id"], "name": data["name"]}
    elif name in ACTION_TOOLS | BUSINESS_READ_TOOLS:
        summary = data
    elif name == "experiments.file_read":
        summary = {key: value for key, value in data.items() if key != "content"}
    elif name == "customers.context":
        summary = {key: data[key] for key in ("company_id", "company_name", "id", "name") if key in data}
    else:
        summary = {"status": raw["status"], "http_status": raw["http_status"], "data": data}
    return summary, evidence


# Function: Append read evidence with distinct identities.
# Inputs: `allowed`: accumulated source array; `additions`: sources newly registered by the backend.
# Outputs: Deduplicate by three-field source identity and extend allowed in place; no return value.
# Logic: Deduplicate by three-field source identity and extend allowed in place; no return value.
# Constraints: Fail immediately on conflicting content for one identity; do not overwrite historical sources.
def _workspace_append_evidence(allowed: list[dict[str, str]], additions: list[dict[str, str]]):
    identities = {
        (item["source_id"], item["source_type"], item["title_or_label"]): item["content"]
        for item in allowed
    }
    for item in additions:
        key = (item["source_id"], item["source_type"], item["title_or_label"])
        if key in identities:
            if identities[key] != item["content"]:
                raise ChatValidationError("The same evidence ID refers to different content.")
            continue
        allowed.append(item)
        identities[key] = item["content"]


# Function: Extract original text relevant to the question.
# Inputs: `content`: original source body; `question`: current question; `limit`: available characters for this source.
# Outputs: Preserve the beginning and locate original text by word units, returning excerpt-marked text within limit.
# Logic: Preserve the beginning and locate original text by word units, returning excerpt-marked text within limit.
# Constraints: Do not change the original meaning or imply that the full text was supplied.
def _workspace_excerpt(content: str, question: str, limit: int) -> str:
    """Show explicitly marked excerpts to the model, prioritizing original passages matching the question."""
    if len(content) <= limit:
        return content
    marker = "\n[Excerpt; full source not provided]"
    budget = limit - len(marker)
    head = min(700, budget // 3)
    tail = min(1200, budget // 3)
    used = [(0, head), (len(content) - tail, len(content))]
    for term in sorted(_lexical_units(question), key=len, reverse=True):
        # Preserve both ends and prefer later matches too: an early mention of
        # a budget must not crowd out a later approval, revision, or pause.
        for position in (content.lower().rfind(term), content.lower().find(term)):
            start, end = max(0, position - 160), min(len(content), position + 320)
            if position < 0 or any(start < b and end > a for a, b in used):
                continue
            if sum(b - a for a, b in used) + end - start + 3 * len(used) > budget:
                continue
            used.append((start, end))
    return "\n…\n".join(content[a:b] for a, b in sorted(used)) + marker


# Function: Select sources within budget.
# Inputs: `evidence`: accumulated source array; `question`: current question; budgets come from module constants.
# Outputs: Trim by established priorities and count/character budgets, returning the complete source allowlist and displayed excerpts.
# Logic: Trim by established priorities and count/character budgets, returning the complete source allowlist and displayed excerpts.
# Constraints: Preserve source priorities and budgets: new experiment files first, pagination/catalog next, rows last; undisplayed sources cannot be cited.
def _workspace_prompt_evidence(
    evidence: list[dict[str, str]], question: str
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Select sources that fit model input; the citation allowlist retains the complete bodies of selected sources."""
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


# Function: Process a workspace question and the read/confirmation proposal tool loop.
# Inputs: `request`: backend claim object; `backend`: request-bound client; `chat_provider`: one-call model function.
# Outputs: Final answer, stage failure, or awaiting_approval suspension; the complete request still has at most six tool turns.
# Logic: Load all catalog pages before the first model call; derive checkpoints from executionMode and resume canonical receipts without replay, preserving loop budgets and independent email prerequisites.
# Constraints: backend is the real service boundary and chat_provider the model boundary; the backend persists evidence, with parameters and budgets unchanged.
def answer_workspace_request(request: Mapping[str, Any], *, backend: Any, chat_provider: Any) -> dict[str, Any]:
    """At most six data tool calls, each selected by the model; the final answer cites only backend-registered evidence."""
    request_id = _recognizable_request_id(request)
    code = "invalid_request"
    try:
        request = parse_conversation_request(request)
        history = trim_recent_history(request["recent_history"])
        request_id = request["request_id"]
        code = "context_unavailable"
        context = parse_answer_context(
            backend.get_answer_context(request_id, "internal"),
            expected_request_id=request_id,
            expected_scope="internal",
        )
        if context["customer_context"] or context["customer_context_status"] != "completed":
            raise ChatValidationError("Initial workspace context must not contain a preselected customer.")
        if context["knowledge_status"] != "completed":
            raise ChatValidationError("Workspace internal knowledge is unavailable.")
        knowledge = trim_context_items([], context["context_items"], [])["internal_knowledge"]
        evidence = list(knowledge)
        observations: list[dict[str, Any]] = []
        records: dict[tuple[str, str], dict[str, Any]] = {}
        signatures: set[tuple[str, str]] = set()
        catalog = _workspace_catalog(backend.get_chat_tools(request_id), request_id)
        checkpointed = {name for name, spec in catalog.items() if spec["executionMode"] in {"write", "confirm"} and name not in ACTION_TOOLS}
        logger.info("workspace_chat_tools_loaded request_id=%s count=%s", request_id, len(catalog))
        start_turn = 0
        if "resume" in request:
            resume = _keys(request["resume"], {"continuation", "tool_result", "arguments", "evidence_items"}, "resume")
            checkpoint = _keys(resume["continuation"], {"next_turn", "observations", "signatures"}, "continuation")
            start_turn = checkpoint["next_turn"]
            if type(start_turn) is not int or not 1 <= start_turn <= _WORKSPACE_MAX_TOOL_READS:
                raise ChatValidationError("Invalid approval continuation turn.")
            if not isinstance(checkpoint["observations"], list) or not all(isinstance(item, dict) for item in checkpoint["observations"]):
                raise ChatValidationError("Invalid approval observations.")
            observations = list(checkpoint["observations"])
            signatures = {tuple(item) for item in checkpoint["signatures"]}
            if len(observations) != start_turn - 1 or any(len(item) != 2 or not all(isinstance(part, str) for part in item) for item in signatures):
                raise ChatValidationError("Approval continuation does not preserve the tool budget.")
            # An approval wait may outlive record versions. Permit explicit fresh
            # target reads before a later order/email proposal in this request.
            signatures = {item for item in signatures if item[0] not in {"orders.get", "connections.get", "customers.context"}}
            name = resume["tool_result"].get("tool")
            if name not in checkpointed:
                raise ChatValidationError("Unexpected approved tool.")
            summary, _ = _workspace_tool_result(resume["tool_result"], request_id, name)
            _workspace_append_evidence(evidence, [_source(item, "resume.evidence") for item in resume["evidence_items"]])
            observations.append({"tool": name, "arguments": resume["arguments"], "status": resume["tool_result"]["status"], "data": summary})
        for turn in range(start_turn, _WORKSPACE_MAX_TOOL_READS + 1):
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
                            "available_tools": list(catalog.values()),
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
                raise ChatValidationError("The data-tool call limit was reached without an answer.")
            name, arguments = payload["name"], payload["arguments"]
            if name not in catalog:
                if name in ACTION_TOOLS | BUSINESS_READ_TOOLS:
                    return {
                        "request_id": request_id, "chat_prompt_version": WORKSPACE_CHAT_PROMPT_VERSION,
                        "assistant_text": "This operation is not available through the current backend chat tools. This turn did not initiate any business changes or email sends. I can still help draft the proposed changes or email as text.",
                        "citations": [], "status": "completed", "error": None,
                    }
                raise ChatValidationError("The selected tool is not published for this request.")
            _workspace_schema_arguments(arguments, catalog[name]["inputSchema"])
            if name in PREPARE_TOOLS:
                try:
                    require_observed_targets(name, arguments, records)
                except ValueError as error:
                    observations.append({"tool": name, "status": "invalid_arguments", "reason": str(error)})
                    logger.info("workspace_chat_proposal_prerequisite request_id=%s tool=%s reason=%s", request_id, name, error)
                    continue
            signature = (name, json.dumps(arguments, sort_keys=True, ensure_ascii=False))
            if signature in signatures:
                raise ChatValidationError("Model repeated the same data-tool call.")
            signatures.add(signature)
            code = "context_unavailable"
            try:
                if name in checkpointed:
                    raw_result = backend.read_chat_tool(request_id, name, arguments, continuation={
                        "next_turn": turn + 1, "observations": observations,
                        "signatures": [list(item) for item in sorted(signatures)],
                    })
                else:
                    raw_result = backend.read_chat_tool(request_id, name, arguments)
            except BackendRequestError as error:
                if name in PREPARE_TOOLS and (error.status_code == 0 or error.status_code >= 500):
                    logger.warning("workspace_chat_proposal_unknown request_id=%s tool=%s status=%s", request_id, name, error.status_code)
                    return {
                        "request_id": request_id, "chat_prompt_version": WORKSPACE_CHAT_PROMPT_VERSION,
                        "assistant_text": "The proposal response could not be confirmed. Check this conversation's pending actions before preparing another proposal. No approval or execution was requested.",
                        "citations": [], "status": "completed", "error": None,
                    }
                if (
                    error.scope != "tool"
                    or error.status_code not in {400, 404, 409, 422}
                ):
                    raise
                if error.status_code == 409 and name in PREPARE_TOOLS and "order_id" in arguments:
                    target = arguments["order_id"]
                    records.pop(("orders.get", target), None)
                    signatures.discard(("orders.get", json.dumps({"id": target}, sort_keys=True, ensure_ascii=False)))
                observations.append({
                    "tool": name, "arguments": arguments,
                    "status": "invalid_arguments" if error.status_code in {400, 422} else "conflict" if error.status_code == 409 else "unavailable",
                    "http_status": error.status_code,
                })
                logger.info(
                    "workspace_chat_tool_unavailable request_id=%s tool=%s status=%s",
                    request_id, name, error.status_code,
                )
                continue
            if raw_result.get("status") == "approval_required":
                if name not in checkpointed or raw_result.get("request_id") != request_id or raw_result.get("tool") != name:
                    raise ChatValidationError("Approval response does not match this write.")
                logger.info("workspace_chat_suspended request_id=%s tool=%s next_turn=%s", request_id, name, turn + 1)
                return {"request_id": request_id, "status": "awaiting_approval", "error": None}
            if name in checkpointed:
                raise ChatValidationError("A proposed write must await browser approval; only a resumed claim may contain its execution receipt.")
            summary, items = _workspace_tool_result(raw_result, request_id, name)
            if name in ACTION_TOOLS:
                try:
                    validate_action_receipt(summary, name, arguments)
                    if name in PREPARE_TOOLS and "connection_id" in arguments:
                        connection = records[("connections.get", arguments["connection_id"])]
                        if summary["preview"]["from_address"] != connection.get("account"):
                            raise ValueError("The proposed sender does not match the selected Gmail account.")
                except ValueError as error:
                    raise ChatValidationError(str(error)) from error
                logger.info("workspace_chat_proposal_result request_id=%s proposal_id=%s kind=%s status=%s", request_id, summary["id"], summary["kind"], summary["status"])
                return {
                    "request_id": request_id, "chat_prompt_version": WORKSPACE_CHAT_PROMPT_VERSION,
                    "assistant_text": action_answer(summary), "citations": [],
                    "status": "completed", "error": None,
                }
            if name in {"orders.get", "connections.get", "customers.context"}:
                target = arguments.get("id", arguments.get("company_id"))
                returned = raw_result["data"].get("company_id" if name == "customers.context" else "id")
                if returned != target:
                    raise ChatValidationError("The returned record does not match the requested target.")
                records[(name, target)] = dict(raw_result["data"])
            _workspace_append_evidence(evidence, items)
            observations.append({
                "tool": name, "arguments": arguments, "status": raw_result["status"],
                "data": summary,
            })
            logger.info(
                "workspace_chat_tool_completed request_id=%s tool=%s evidence=%s total_evidence=%s",
                request_id, name, len(items), len(evidence),
            )
        raise ChatValidationError("Workspace chat did not produce an answer.")
    except Exception as error:
        logger.warning(
            "workspace_chat_failed request_id=%s stage=%s error_type=%s reason=%s",
            request_id, code, type(error).__name__,
            str(error) if isinstance(error, ChatValidationError) else "unavailable",
        )
        if request_id is None:
            raise
        return _workspace_failure(request_id, code)


# Function: Check the authoritative terminal state after a lost response.
# Inputs: `result`: local report payload; `state`: authoritative backend state response.
# Outputs: Compare request version/status and failure content or citation identity, returning a boolean.
# Logic: Compare request version/status and failure content or citation identity, returning a boolean.
# Constraints: Confirm only already-saved results; do not report again or rerun the model.
def _chat_report_is_saved(result: Mapping[str, Any], state: object) -> bool:
    """After a lost report response, confirm persistence only when the authoritative terminal state matches the local result."""
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


# Function: Claim one workspace chat request and attempt one report.
# Inputs: `backend`: backend client; `chat_provider`: single model-call boundary.
# Outputs: Final/local failure result, suspended request status, or None when no request is available.
# Logic: Run workspace chat and report final results; approval suspension releases this worker without writing a terminal answer.
# Constraints: Requests must have no preselected company; the backend controls employee visibility and authoritative result persistence.
def process_chat_once(
    *,
    backend: Any,
    chat_provider: Any = bailian_chat_provider,
) -> dict[str, Any] | None:
    """Claim one chat request; release approval waits without a terminal report and report final answers once."""
    claimed_request = backend.claim_answer_request()
    if claimed_request is None:
        return None

    request_id = _recognizable_request_id(claimed_request)
    if request_id is None:
        raise ChatValidationError("Claimed chat request is missing request_id.")
    logger.info("chat_request_claimed request_id=%s mode=workspace", request_id)
    result = answer_workspace_request(
        claimed_request, backend=backend, chat_provider=chat_provider
    )
    if result["status"] == "awaiting_approval":
        return result
    if result["status"] == "failed" and hasattr(backend, "get_chat_request_status"):
        # A lost proposal response may have committed the suspension. Observe state once instead of reporting over it.
        try:
            state = backend.get_chat_request_status(request_id)
            if state.get("status") in {"awaiting_approval", "pending", "cancelled"}:
                logger.info("chat_suspension_confirmed request_id=%s status=%s", request_id, state["status"])
                return {"request_id": request_id, "status": state["status"], "error": None}
        except Exception as status_error:
            logger.warning("chat_suspension_status_failed request_id=%s error_type=%s", request_id, type(status_error).__name__)
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
        # Report failure produces only a local result; do not retry automatically or pretend it was saved.
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
