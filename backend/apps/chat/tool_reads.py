"""职责：为聊天请求提供读取及实验维护工具发现、执行及稳定证据登记。
实现：员工与请求锁保护工作空间 processing 边界，拒绝继续执行旧公司请求；复用原工具 Schema、处理器和权限，独立保存每次成功结果。
关联：tool_views 暴露 Agent HTTP；services.save_answer 仅从本请求上下文和 ToolRead 附加引用正文。
目录：
- processing_request：授权并锁定处理中的请求。
- catalog_for：返回请求可用的读取及实验维护工具目录。
- evidence_for：把实际业务响应投影为完整四字段来源。
- read_tool：执行一次数据操作并登记返回证据。
变量索引：
- ALLOWED_TOOLS：客户读取及共享实验读取、维护的固定工具集合。
- CONTRACT_VERSION：工具对接协议标识，不限制回答提示词版本。
- CALL_SCHEMA：请求绑定工具调用的封闭 JSON Schema。
- CATALOG_SCHEMA：目录查询的 UUID 与分页 Schema。
- logger：仅输出请求、员工、工具、状态、耗时和异常类型。
"""

import hashlib
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

ALLOWED_TOOLS = WORKSPACE_TOOLS
CONTRACT_VERSION = "chat-tools-v1"
CALL_SCHEMA = object_schema(
    {
        "request_id": UUID,
        "name": {"type": "string", "minLength": 1, "maxLength": 120},
        "arguments": {"type": "object"},
    },
    ["request_id", "name", "arguments"],
)
CATALOG_SCHEMA = object_schema({"request_id": UUID, **PAGE}, ["request_id"])
logger = logging.getLogger("salesmate.chat.tools")


# 功能：取得当前员工正在处理的聊天请求。
# 输入：`owner` 为已认证员工，`request_id` 为已验证 UUID。
# 输出：带行锁的 AnswerRequest；越权为 404，非 processing 为 409。
# 逻辑：沿用聊天员工锁与全链路归属校验，仅允许无预选公司且 processing 的请求调用读取或实验维护工具。
# 约束：调用方必须处于事务中，锁顺序与保存回答一致。
def processing_request(owner, request_id):
    lock_owner(owner)
    request = request_for(owner, request_id, lock=True)
    require_workspace(request.conversation)
    if request.status != "processing":
        raise InvalidState("只能为处理中的聊天请求调用工具。")
    return request


# 功能：发现本次请求允许执行的工具及准确参数 Schema。
# 输入：`owner` 为认证员工，`query` 含 request_id 及可选整数 page/page_size。
# 输出：版本、请求 ID、tools/count/page/page_size，不包含业务数据。
# 逻辑：验证请求后复用原目录，按固定白名单与逐工具期望执行模式双重过滤，再分页。
# 约束：仅发布实验维护写入，不发布确认或未获明确授权的其他读取及实验维护工具，不隐式扩大权限。
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


# 功能：为实际返回的查询数据创建不会与其他读取冲突的来源。
# 输入：`read_id` 为新读取 UUID，`name` 为已允许工具名，`data` 为 JSON 业务结果。
# 输出：具有 source_id/source_type/title_or_label/content 的证据数组。
# 逻辑：客户搜索和实验表拆分行及分页证据；实验维护回执登记 experiment_mutation 来源，其他读取完整序列化。
# 约束：来源由读取 UUID 和记录标识区分，正文可能较长；Agent 自行选择预算内证据，不能把节选称为完整。
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


# 功能：执行本请求授权的数据工具调用并登记稳定证据。
# 输入：`owner` 为 Agent 凭证确定的员工，`payload` 为 CALL_SCHEMA 对象。
# 输出：成功回执含原业务 data、revision、http_status 及 read_id/evidence_items；工具错误保留 HTTP 状态。
# 逻辑：锁员工和请求后取同一工具声明，验证 Schema 并复用原处理器；成功操作与新 ToolRead 同事务；维护调用复用请求派生幂等键，stage 标记失败位置。
# 约束：请求错误向上抛出；业务 APIException 返回 scope=tool 且不结束聊天；未知异常回滚并传播，不重试或伪装空资料。
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
                if name in EXPERIMENT_WRITE_TOOLS:
                    # 相同请求、工具及参数只写一次；独立新问题使用不同请求 UUID。
                    digest = hashlib.sha256(json.dumps(payload["arguments"], sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
                    key = uuid.uuid5(uuid.UUID(str(request.pk)), name + ":" + digest)
                    business = tool_services.invoke(owner, None, name, payload["arguments"], str(key))
                    response = None
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
                if response is not None and not 200 <= response.status_code < 300:
                    raise APIException("读取及实验维护工具返回了非预期响应。")
                if response is not None:
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
