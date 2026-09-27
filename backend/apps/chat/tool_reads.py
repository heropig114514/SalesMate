"""Responsibility: Discover workspace reads and approved customer/experiment writes with canonical evidence.
Implementation: Employee and request locks protect processing; order/email tools prepare independent proposals, while customer creation and experiment writes use checkpointed browser approval, including in laboratory mode.
Relationships: ``tool_views`` exposes Agent HTTP; ``services.save_answer`` attaches citation content only from this request context and ``ToolRead``.
Directory:
- processing_request: Authorize and lock a request being processed.
- catalog_for: Publish private customer creation and business proposals only to their actual request owner.
- evidence_for: Project actual business response into complete four-field sources.
- read_tool: Execute one data operation and register returned evidence.
- read_business_tool: Register strict business reads and independent proposal receipts.
Variable index:
- ALLOWED_TOOLS: Fixed customer read/creation and shared experiment tool set; writes require approval.
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

from integrations.salesmate_tools.read_contract import WORKSPACE_TOOLS, EXPERIMENT_WRITE_TOOLS, CUSTOMER_WRITE_TOOLS, WORKSPACE_WRITE_TOOLS

from .models import ToolRead
from .services import lock_owner, request_for, require_workspace
from .approvals import CONTINUATION_SCHEMA, propose
from . import action_contract, action_services

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
# Logic: Filter the registry by fixed names/modes and remove private creation for borrowed laboratory requests; paginate after adding eligible independent proposals.
# Constraints: Only customer creation joins checkpointed writes; other ordinary business writes remain unpublished and experiment permissions stay unchanged.
@transaction.atomic
def catalog_for(owner, query):
    validate(query, CATALOG_SCHEMA)
    request = processing_request(owner, query["request_id"])
    entries = [
        entry
        for entry in tool_services.catalog(owner)
        if entry["name"] in ALLOWED_TOOLS and entry["executionMode"] == ("write" if entry["name"] in WORKSPACE_WRITE_TOOLS else "read")
    ]
    if (request.owner_id == owner.pk and request.requested_by_id in (None, owner.pk)
            and request.conversation.owner_id == owner.pk):
        action_services.require_request(owner, request)
        entries.extend(action_contract.catalog().values())
    else:
        entries = [entry for entry in entries if entry["name"] not in CUSTOMER_WRITE_TOOLS]
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
# Logic: Split search/table evidence; register credential-free business snapshots, frozen proposals, and separate real-customer or synthetic mutation receipts.
# Constraints: Sources differ by read UUID and record identifier; proposal evidence records preparation rather than execution, and later status reads never overwrite earlier evidence.
def evidence_for(read_id, name, data):
    prefix = f"chat-tool:{read_id}"
    if name in action_contract.ACTION_TOOLS:
        parts = [(f"{prefix}:proposal:{data['id']}", "chat_action_proposal", "Employee-confirmed action proposal", data)]
    elif name in action_contract.BUSINESS_READ_TOOLS:
        parts = [(f"{prefix}:business", "chat_business_read", name, data)]
    elif name == "customers.search":
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
    elif name in CUSTOMER_WRITE_TOOLS:
        parts = [(f"{prefix}:company:{data['id']}", "customer_creation", f"{data['name']} · 客户录入回执", data)]
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
# Outputs: Reads return evidence; independent proposals return 201; customer/experiment writes return 202 approval_required without mutation; errors retain HTTP status.
# Logic: Enforce private identity for customer creation and business tools; validate registry writes and suspend for a browser decision under the request lock.
# Constraints: Request errors propagate; business ``APIException`` returns scope=tool without ending chat; unknown exceptions roll back and propagate without retry or fabricated empty information.
def read_tool(owner, payload):
    validate(payload, CALL_SCHEMA)
    started = perf_counter()
    name, request_id = payload["name"], payload["request_id"]
    stage = "request"
    try:
        with transaction.atomic():
            if name in action_contract.ACTION_TOOLS | action_contract.BUSINESS_READ_TOOLS | CUSTOMER_WRITE_TOOLS:
                action_services.require_request(owner, request_for(owner, request_id))
                action_services.lock_operation_owners(owner, name, payload["arguments"])
            request = processing_request(owner, request_id)
            if name in CUSTOMER_WRITE_TOOLS:
                action_services.require_request(owner, request)
            if name in action_contract.ACTION_TOOLS | action_contract.BUSINESS_READ_TOOLS:
                action_services.require_request(owner, request)
                if "continuation" in payload:
                    raise PermissionDenied("业务提案不接受实验操作检查点。")
                return read_business_tool(owner, request, name, payload["arguments"])
            stage = "authorization"
            spec = build_registry().get(name)
            if (
                name not in ALLOWED_TOOLS
                or spec is None
                or spec["executionMode"] != ("write" if name in WORKSPACE_WRITE_TOOLS else "read")
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


# Function: Execute strict reads or persist independent proposals with canonical evidence.
# Inputs: Employee `owner`, locked processing `request`, authorized `name`, and submitted `arguments`.
# Outputs: Existing chat receipt envelope with 200/201, or a scoped business error.
# Logic: An inner savepoint makes proposal, audit and ToolRead atomic; preparation replay reuses its original read UUID instead of creating another operation.
# Constraints: This branch never invokes generic Tool confirmation or business execution; request authentication occurs before entering it.
def read_business_tool(owner, request, name, arguments):
    started = perf_counter()
    try:
        with transaction.atomic():
            validate(arguments, action_contract.catalog()[name]["inputSchema"])
            status = 200
            if name == action_contract.GET_ACTION:
                data = action_services.proposal_data(action_services.proposal_for(owner, arguments["proposal_id"], request.conversation_id))
            elif name in action_contract.ACTION_TOOLS:
                proposal, created = action_services.prepare(owner, request, name, arguments)
                if not created:
                    previous = ToolRead.objects.get(request=request, tool=name, arguments=arguments)
                    return {**previous.result, "request_id": str(request.pk), "read_id": str(previous.pk), "evidence_items": previous.evidence_items}
                data, status = action_services.proposal_data(proposal), 201
            else:
                data = plain(action_services.business_read(owner, name, arguments))
            result = {"tool": name, "status": "completed", "http_status": status, "data": data}
            read = ToolRead(request=request, tool=name, arguments=arguments, result=result)
            read.evidence_items = evidence_for(read.pk, name, data)
            read.save()
            result = {**result, "request_id": str(request.pk), "read_id": str(read.pk), "evidence_items": read.evidence_items}
    except APIException as error:
        result = {"request_id": str(request.pk), "tool": name, "status": "failed", "http_status": error.status_code,
            "error": {"scope": "tool", "code": error.default_code, "detail": plain(error.detail)}}
    logger.info("chat_business_tool_finished request_id=%s conversation_id=%s owner_id=%s tool=%s status=%s http_status=%s duration_ms=%s",
        request.pk, request.conversation_id, owner.pk, name, result["status"], result["http_status"], round((perf_counter() - started) * 1000))
    return result
