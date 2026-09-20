"""职责：将获准共享的合成批次发布为只读工具。
实现：目录和分页复用网页处理器，文件复用行指纹及文件摘要校验，再分块编码。
关联：registry 注册，dispatch 在认证和 Schema 校验后分派；MCP 动态发现同一目录。
目录：
- experiment_specs：声明三个实验读取工具的封闭参数契约。
- execute_experiment：执行固定操作并保留原数据归属及合成标记。
变量索引：
- logger：记录文件读取的账号、批次、模型和主键，不记录正文。
"""

import logging

from django.db import transaction
from rest_framework.response import Response

from apps.sales.experiments import ExperimentView, file_content, load_batch
from .schemas import PAGE, object_schema
from .support import CHUNK_BYTES, read_content

logger = logging.getLogger("salesmate.experiments.tools")


# 功能：发布目录、分页和附件分块读取。
# 输入：`tool` 为注册表的声明构造函数。
# 输出：三个 executionMode=read 的工具声明。
# 逻辑：批次、模型必须显式传入；服务端再检查精确清单，不接受任意路径或 URL。
# 约束：文件格式、偏移和长度必须显式提供；文本上限沿用现有文件工具。
def experiment_specs(tool):
    text = {"type": "string", "minLength": 1, "maxLength": 500}
    location = {"batch": text, "model": text}
    return [
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


# 功能：执行已授权的实验读取。
# 输入：`request` 含真实用户与查询参数，`spec` 固定工具声明，`args` 经 Schema 校验的参数。
# 输出：含 JSON 数据的 Response；清单不存在或漂移时保留原 404/409 错误。
# 逻辑：复用网页目录分页；文件先校验指纹与摘要，显式允许 text/plain 实验文件的 UTF-8 文本块。
# 约束：调用方负责认证及工具授权；不写业务记录，不把附件路径或凭据传给调用者。
def execute_experiment(request, spec, args):
    operation = spec["operation"]
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
