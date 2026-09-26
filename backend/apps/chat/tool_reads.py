"""Responsibility: Provide chat requests with read and experiment-maintenance tool discovery, execution, and stable evidence registration.
Implementation: Employee and request locks protect workspace processing; read handlers register evidence, while every allowed write creates a durable approval before execution, including in laboratory mode.
Relationships: ``tool_views`` exposes Agent HTTP; ``services.save_answer`` attaches citation content only from this request context and ``ToolRead``.
Directory:
- processing_request: Authorize and lock a request being processed.
- catalog_for: Return the request's available read and experiment-maintenance tool catalog.
- evidence_for: Project actual business response into complete four-field sources.
- read_tool: Execute one data operation and register returned evidence.
Variable index:
- ALLOWED_TOOLS: Fixed tool set for customer reads and shared experiment reads and maintenance.
- CONTRACT_VERSION: Tool-integration contract identifier that does not limit answer prompt version.
- CALL_SCHEMA: Closed JSON Schema for request-bound tool invocation.
- CATALOG_SCHEMA: UUID and pagination Schema for catalog query.
- logger: Emits only request, employee, tool, status, duration, and exception type.
"""

import json
import logging
import uuid
from time import perf_counter

from django.db import transaction
from rest_framework.exceptions import APIException, PermissionDenied

from apps.agent_tools import services as tool_services
from apps.agent_tools.dispatch import execute
from apps.agent_tools.registry import build_registry
from apps.agent_tools.schemas import PAGE, UUID, object_schema, validate
from apps.crm.access import InvalidState, plain

from integrations.salesmate_tools.read_contract import WORKSPACE_TOOLS, EXPERIMENT_WRITE_TOOLS

from .models import ToolRead
from .services import lock_owner, request_for, require_workspace
from .approvals import CONTINUATION_SCHEMA, propose

ALLOWED_TOOLS = WORKSPACE_TOOLS
CONTRACT_VERSION = "chat-tools-v1"
CALL_SCHEMA = object_schema(
    {
        "request_id": UUID,
        "name": {"type": "string", "minLength": 1, "maxLength": 120},
        "arguments": {"type": "object"},
        "continuation": CONTINUATION_SCHEMA,
    },
    ["request_id", "name", "arguments"],
)
CATALOG_SCHEMA = object_schema({"request_id": UUID, **PAGE}, ["request_id"])
logger = logging.getLogger("salesmate.chat.tools")


# Function: Obtain chat request the current employee is processing.
# Inputs: Authenticated employee ``owner`` and validated UUID ``request_id``.
# Outputs: Row-locked ``AnswerRequest``; unauthorized is 404 and non-processing is 409.
# Logic: Reuse chat employee lock and end-to-end ownership checks; only processing requests without preselected company may invoke read or experiment-maintenance tools.
# Constraints: Caller must be in a transaction and lock ordering matches answer saving.
def processing_request(owner, request_id):
    lock_owner(owner)
    request = request_for(owner, request_id, lock=True)
    require_workspace(request.conversation)
    if request.status != "processing":
        raise InvalidState("只能为处理中的聊天请求调用工具。")
    return request


# Function: Discover tools allowed for this request and their exact parameter Schema.
# Inputs: Authenticated employee ``owner`` and ``query`` containing request_id and optional integer page and page_size.
# Outputs: Version, request ID, tools, count, page, and page_size without business data.
# Logic: Validate request, reuse original catalog, filter twice by fixed allowlist and per-tool expected execution mode, then paginate.
# Constraints: Publishes only experiment-maintenance writes and does not publish confirm or otherwise unauthorized read or maintenance tools or implicitly expand permissions.
@transaction.atomic
def catalog_for(owner, query):
    validate(query, CATALOG_SCHEMA)
    request = processing_request(owner, query["request_id"])
    entries = [
        entry
        for entry in tool_services.catalog(owner)
        if entry["name"] in ALLOWED_TOOLS and entry["executionMode"] == ("write" if entry["name"] in EXPERIMENT_WRITE_TOOLS else "read")
    ]
    entries.sort(key=lambda entry: entry["name"])
    page, size = query.get("page", 1), query.get("page_size", 30)
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": str(request.pk),
        "tools": entries[(page - 1) * size : page * size],
        "count": len(entries),
        "page": page,
        "page_size": size,
    }


# Function: Create sources from actual returned query data without conflicts with other reads.
# Inputs: New-read UUID ``read_id``, allowed tool name ``name``, and JSON business result ``data``.
# Outputs: Evidence array containing source_id, source_type, title_or_label, and content.
# Logic: Customer search and experiment tables split row and pagination evidence; experiment-maintenance receipts register an ``experiment_mutation`` source and other reads serialize completely.
# Constraints: Sources differ by read UUID and record identifier and content may be long; Agent selects evidence within budget and cannot call an excerpt complete.
def evidence_for(read_id, name, data):
    prefix = f"chat-tool:{read_id}"
    if name == "customers.search":
        parts = [
            (
                f"{prefix}:company:{row['id']}",
                "customer_search",
                f"{row['name']} · 客户目录",
                row,
            )
            for row in data["results"]
        ]
        parts.append(
            (
                f"{prefix}:page:{data['page']}",
                "customer_search_page",
                f"客户搜索 · 第 {data['page']} 页",
                {key: value for key, value in data.items() if key != "results"}
                | {"returned_company_ids": [row["id"] for row in data["results"]]},
            )
        )
    elif name == "experiments.rows":
        parts = [(f"{prefix}:row:{data['model']}:{row['pk']}", "experiment_row",
                  f"虚构实验 · {data['model']} · {row['pk']}", row) for row in data["results"]]
        parts.append((f"{prefix}:page:{data['page']}", "experiment_page", "虚构实验 · 分页范围",
                      {key: value for key, value in data.items() if key != "results"}
                      | {"returned_pks": [row["pk"] for row in data["results"]], "synthetic": True}))
    elif name in {"experiments.catalog", "experiments.file_read"}:
        parts = [(f"{prefix}:experiment", "experiment_catalog" if name.endswith("catalog") else "experiment_file",
                  "虚构实验 · 批次目录" if name.endswith("catalog") else "虚构实验 · 文件内容块", data)]
    elif name in EXPERIMENT_WRITE_TOOLS:
        parts = [(f"{prefix}:mutation", "experiment_mutation", "虚构实验 · 维护回执", data)]
    else:
        parts = [
            (
                f"{prefix}:company:{data['company_id']}",
                "customer_context",
                f"{data['company_name']} · 客户资料",
                data,
            )
        ]
    return [
        {
            "source_id": source,
            "source_type": kind,
            "title_or_label": title,
            "content": json.dumps(
                content, ensure_ascii=False, sort_keys=True, allow_nan=False
            ),
        }
        for source, kind, title, content in parts
    ]


# Function: Execute data-tool invocation authorized for this request and register stable evidence.
# Inputs: ``owner`` is the employee determined by the Agent credential and ``payload`` matches the call schema.
# Outputs: Reads return data/evidence; writes return 202 approval_required without mutation; tool errors retain HTTP status.
# Logic: Validate live registration and arguments under request lock; non-read operations freeze continuation for independent browser approval and reads execute with stable evidence.
# Constraints: Request errors propagate; business ``APIException`` returns scope=tool without ending chat; unknown exceptions roll back and propagate without retry or fabricated empty information.
def read_tool(owner, payload):
    validate(payload, CALL_SCHEMA)
    started = perf_counter()
    name, request_id = payload["name"], payload["request_id"]
    stage = "request"
    try:
        with transaction.atomic():
            request = processing_request(owner, request_id)
            stage = "authorization"
            spec = build_registry().get(name)
            if (
                name not in ALLOWED_TOOLS
                or spec is None
                or spec["executionMode"] != ("write" if name in EXPERIMENT_WRITE_TOOLS else "read")
            ):
                raise PermissionDenied("聊天入口不允许此工具。")
            tool_services.authorize(owner, None, name)
            try:
                stage = "arguments"
                validate(payload["arguments"], spec["inputSchema"])
                stage = "execute"
                if spec["executionMode"] != "read":
                    return propose(request, spec, payload["arguments"], payload.get("continuation"))
                else:
                    response = execute(owner, spec, payload["arguments"])
            except APIException as error:
                result = {
                    "request_id": str(request.pk),
                    "tool": name,
                    "status": "failed",
                    "http_status": error.status_code,
                    "error": {
                        "scope": "tool",
                        "code": error.default_code,
                        "detail": plain(error.detail),
                    },
                }
            else:
                stage = "response"
                if not 200 <= response.status_code < 300:
                    raise APIException("读取及实验维护工具返回了非预期响应。")
                business = {"tool": name, **tool_services.response_data(response)}
                if business["status"] != "completed":
                    raise APIException("读取及实验维护工具没有完成查询。")
                read_id = uuid.uuid4()
                stage = "evidence"
                evidence = evidence_for(read_id, name, business["data"])
                stage = "persist"
                ToolRead.objects.create(
                    id=read_id,
                    request=request,
                    tool=name,
                    arguments=payload["arguments"],
                    result=business,
                    evidence_items=evidence,
                )
                result = {
                    **business,
                    "request_id": str(request.pk),
                    "read_id": str(read_id),
                    "evidence_items": evidence,
                }
        logger.info(
            "chat_tool_finished request_id=%s owner_id=%s tool=%s status=%s http_status=%s stage=%s duration_ms=%s",
            request_id,
            owner.pk,
            name,
            result["status"],
            result["http_status"],
            stage,
            round((perf_counter() - started) * 1000),
        )
        return result
    except Exception as error:
        logger.warning(
            "chat_tool_failed request_id=%s owner_id=%s tool=%s stage=%s error_type=%s duration_ms=%s",
            request_id,
            owner.pk,
            name,
            stage,
            type(error).__name__,
            round((perf_counter() - started) * 1000),
        )
        raise
