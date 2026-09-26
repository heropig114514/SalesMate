"""Responsibility: Publish approved shared synthetic batches as read and business-maintenance tools.
Implementation: Reads reuse webpage and file validation; create, update, and delete reuse the audited catalog-maintenance service.
Relationships: Registered by ``registry`` and dispatched by ``dispatch`` after authentication and Schema validation; MCP dynamically discovers the same catalog.
Directory:
- experiment_specs: Declare closed parameter contracts for experiment reads and create, update, and delete operations.
- execute_experiment: Execute a fixed operation while retaining original data ownership and synthetic marker.
Variable index:
- logger: Records account, batch, model, and primary key for file reads without content.
"""

import logging

from django.db import transaction
from rest_framework.response import Response

from apps.sales.experiments import ExperimentView, file_content, load_batch
from .schemas import PAGE, object_schema
from .support import CHUNK_BYTES, read_content

logger = logging.getLogger("salesmate.experiments.tools")


# Function: Publish catalog, pagination, and attachment chunk-read tools.
# Inputs: ``tool`` is the registry declaration constructor.
# Outputs: Three read and three write tool declarations, with delete explicitly marked ``destructiveHint``.
# Logic: Batch and model must be explicit; server then checks the exact allowlist and accepts no arbitrary path or URL.
# Constraints: File format, offset, and length must be explicit; text bound reuses the existing file tool.
def experiment_specs(tool):
    text = {"type": "string", "minLength": 1, "maxLength": 500}
    location = {"batch": text, "model": text}
    entries = [
        tool("experiments.catalog", "列出所有登录账号可读的合成实验批次、44 类表及字段关系；不含真实私有数据。",
             "experiment", object_schema({}), operation="catalog"),
        tool("experiments.rows", "按批次和模型分页读取共享虚构记录，保留主键、外键和原归属；可按 pk、owner、q 筛选。",
             "experiment", object_schema({**location, **PAGE, "pk": text, "owner": text, "q": text}, ["batch", "model"]), operation="rows"),
        tool("experiments.file_read", "读取共享合成附件或方案文档的一个内容块；text 仅支持 UTF-8 TXT，其他文件使用 base64。",
             "experiment", object_schema({**location, "model": {"enum": ["sales.Attachment", "accounts.SetupDocument"]},
                 "pk": text, "format": {"enum": ["text", "base64"]},
                 "offset": {"type": "integer", "minimum": 0},
                 "limit": {"type": "integer", "minimum": 1, "maximum": CHUNK_BYTES}},
                 ["batch", "model", "pk", "format", "offset", "limit"]), operation="file_read"),
    ]
    for operation in ("create", "update", "delete"):
        properties = dict(location)
        required = ["batch", "model"]
        if operation != "create":
            properties.update({"pk": text, "expected": {"type": "string", "pattern": "^[0-9a-f]{64}$"}})
            required += ["pk", "expected"]
        if operation != "delete":
            properties["data"] = {"type": "object"}
            required.append("data")
        entry = tool(f"experiments.{operation}",
            f"共享虚构业务记录 {operation}；先查 catalog/rows 的 write 字段能力；修改删除必须传最新 fingerprint 为 expected。保留归属及审计，禁止级联删除。",
            "experiment", object_schema(properties, required), mode="write", operation=operation)
        entry["annotations"]["destructiveHint"] = operation == "delete"
        entries.append(entry)
    return entries


# Function: Execute an authorized experiment read or maintenance operation.
# Inputs: ``request`` contains the real user and query parameters, ``spec`` is the fixed tool declaration, and ``args`` are Schema-validated parameters.
# Outputs: ``Response`` containing JSON data; missing or drifted catalog retains original 404 or 409 errors.
# Logic: Maintenance dispatches to ``mutate``; reads reuse pagination and file verification and allow UTF-8 text chunks for ``text/plain`` experiment files.
# Constraints: Caller owns authentication, write idempotency, and tool authorization; attachment paths and credentials are not passed to callers.
def execute_experiment(request, spec, args):
    operation = spec["operation"]
    if operation in {"create", "update", "delete"}:
        from apps.sales.experiment_writes import mutate
        return Response(mutate(request.user, operation, args["batch"], args["model"],
                               args.get("data"), args.get("pk"), args.get("expected")))
    if operation == "catalog":
        return ExperimentView().get(request)
    if operation == "rows":
        return ExperimentView().get(request, args["batch"], args["model"])
    if operation != "file_read":
        raise ValueError("Unregistered experiment operation")
    with transaction.atomic():
        entry = load_batch(args["batch"])
        record, content = file_content(entry, args["model"], args["pk"])
        data = {**read_content(record, content, args, allow_plain_text=True), "batch": entry.object_id, "model": args["model"],
                "owner": {"id": record.owner_id, "username": record.owner.username},
                "synthetic": True, "read_only": True}
    logger.info("experiment_file_read actor_id=%s batch=%s model=%s pk=%s", request.user.pk, entry.object_id, args["model"], args["pk"])
    return Response(data)
