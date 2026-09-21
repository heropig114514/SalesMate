"""职责：向所有有效登录账号提供已批准虚构批次的跨账号数据视图与可写能力说明。
实现：模型白名单、精确清单主键及当前指纹共同限制读取；保留归属与外键，提供分页、导出和附件。
关联：seed_kg_lab 建立清单，experiment_writes 原子维护；网页和 agent_tools 复用校验，普通业务权限保持原样。
目录：
- load_batch：定位获准且未清理的完整批次。
- model_fields：返回允许展示的字段及关系说明。
- table_rows：验证和投影清单中的单表记录。
- file_content：核验并读取清单内文件字节。
- batch_summary：提供表目录、归属与来源声明。
- ExperimentView：已登录用户的只读实验 API。
- ExperimentView.get：分派目录、分页、完整导出和文件下载。
- ExperimentView.download：核验并返回清单内文件。
- ExperimentCatalogView：声明批次目录的独立接口契约。
- ExperimentExportView：声明 JSON 导出接口契约。
- ExperimentFileView：声明二进制附件接口契约。
变量索引：
- APPROVED_BATCHES：用户明确批准共享的完整批次名称；不自动开放其他 KGSEED 批次。
- TABLES：44 个可读模型的中文名称与归属用户关联路径；None 表示用户自身。
- HIDDEN_FIELDS：不返回的登录信息、内部文件路径和授权关联字段。
- logger：只记录访问者、批次、模型和数量的审计日志。
- ExperimentView.http_method_names：禁止通过实验入口写入或执行动作。
"""

import hashlib
import io
import json
import logging
from collections import Counter
from pathlib import Path

from django.apps import apps
from django.core.serializers.json import DjangoJSONEncoder
from django.http import FileResponse, HttpResponse
from django.conf import settings
from django.db import transaction
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiParameter
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.crm.access import Conflict
from common.fixture_integrity import fingerprint
from .models import AuditEvent

APPROVED_BATCHES = ("KGSEED_20260921_01",)
TABLES = {
    "accounts.User": ("测试账号", None),
    "accounts.CompanyProfile": ("本公司资料", "owner"),
    "accounts.SetupDocument": ("产品与方案文档", "owner"),
    "accounts.SalesSetup": ("销售引导资料", "owner"),
    "sales.SellerProfile": ("销售方画像", "owner"),
    "crm.Company": ("客户公司", "owner"),
    "crm.Contact": ("客户联系人", "company__owner"),
    "sales.CompanySettings": ("客户设置", "owner"),
    "sales.CompanyAlias": ("客户别名", "owner"),
    "sales.ContactProfile": ("联系人补充资料", "owner"),
    "sales.Team": ("团队", "owner"),
    "sales.Membership": ("团队成员", "owner"),
    "sales.CompanyGrant": ("客户共享记录", "owner"),
    "sales.Product": ("产品", "owner"),
    "sales.Ticket": ("工单", "owner"),
    "sales.Opportunity": ("商机", "owner"),
    "sales.Quote": ("报价单", "owner"),
    "sales.QuoteLine": ("报价明细", "owner"),
    "sales.SalesOrder": ("订单", "owner"),
    "sales.OrderLine": ("订单明细", "owner"),
    "sales.FollowUp": ("跟进任务", "owner"),
    "sales.Notification": ("通知", "owner"),
    "sales.Conversation": ("助手会话", "owner"),
    "sales.Message": ("聊天消息", "owner"),
    "sales.Draft": ("草稿", "owner"),
    "sales.ToolAction": ("外部动作记录", "owner"),
    "sales.AuditEvent": ("业务审计记录", "owner"),
    "agent_tools.ToolCall": ("工具调用回执", "owner"),
    "agent_tools.ToolProposal": ("工具提案", "owner"),
    "crm.Mailbox": ("模拟邮箱", "owner"),
    "crm.Email": ("邮件原文", "mailbox__owner"),
    "crm.StoredMessage": ("持久邮件原文", "mailbox__owner"),
    "crm.Extraction": ("邮件抽取事实", "email__mailbox__owner"),
    "crm.AnalysisInput": ("分析输入快照", "company__owner"),
    "crm.Analysis": ("分析结果", "snapshot__company__owner"),
    "crm.Score": ("评分记录", "analysis__snapshot__company__owner"),
    "crm.SnapshotSource": ("快照来源血缘", "snapshot__company__owner"),
    "crm.SnapshotInvalidation": ("快照失效记录", "snapshot__company__owner"),
    "chat.KnowledgeEntry": ("知识条目及外部资料", "owner"),
    "chat.AnswerRequest": ("AI 回答生成记录", "owner"),
    "chat.Citation": ("回答引用证据", "request__owner"),
    "chat.ToolRead": ("工具读取证据", "request__owner"),
    "vectors.VectorDocument": ("向量文档", "owner"),
    "sales.Attachment": ("客户附件", "owner"),
}
HIDDEN_FIELDS = {
    "accounts.User": {"password", "last_login", "is_staff", "is_superuser", "email"},
    "sales.Attachment": {"storage_key"},
    "agent_tools.ToolProposal": {"credential_id"},
}
logger = logging.getLogger("salesmate.experiments")


# 功能：定位获准且未清理的完整批次。
# 输入：`batch` 完整批次名称。
# 输出：保存当前清单及原始身份的 AuditEvent；未开放返回 404，清单异常返回 409。
# 逻辑：只读取固定事件及精确名称，核对生成器、来源、归属和清理状态。
# 约束：不按名称前缀推断合成身份，不扩大到其他账号的非清单记录。
def load_batch(batch):
    if batch not in APPROVED_BATCHES:
        raise NotFound("实验批次未开放。")
    entries = list(AuditEvent.objects.filter(event="kg_synthetic_batch_v1", object_id=batch).select_related("owner")[:2])
    if not entries:
        raise NotFound("实验批次不存在或已清理。")
    if len(entries) != 1:
        raise Conflict("实验批次清单不唯一，请联系维护者。")
    entry = entries[0]
    manifest = entry.changes
    if (manifest.get("batch") != batch or manifest.get("owner_id") != entry.owner_id
            or manifest.get("generator") != "kg-business-fixture-v1"
            or manifest.get("source") != "synthetic_sample" or manifest.get("cleanup_state") is not None
            or not isinstance(manifest.get("rows"), list)):
        raise Conflict("实验清单状态异常，停止共享。")
    return entry


# 功能：返回允许展示的字段及关系说明。
# 输入：`label` 已在 TABLES 注册的模型名称。
# 输出：字段说明列表，含原始数据库表名可由目录另行读取。
# 逻辑：使用 ORM 具体字段，不包含反向关系及密码等排除字段。
# 约束：关系仅声明目标，不自动展开其他记录内容；二进制经单独下载提供。
def model_fields(label):
    return [{"name": field.attname, "type": field.get_internal_type(),
             "relation": field.related_model._meta.label if field.is_relation else None,
             "primary_key": field.primary_key}
            for field in apps.get_model(label)._meta.concrete_fields
            if field.attname not in HIDDEN_FIELDS.get(label, set())]


# 功能：验证并投影清单中的单表记录。
# 输入：`entry` 已批准的清单事件、`label` 模型名称。
# 输出：原始主键、归属、批次、当前 fingerprint、真实 read_only 能力及字段组成的记录列表。
# 逻辑：只按清单主键读取；逐行复核内容指纹，外键归属通过确定路径追溯。
# 约束：缺失或修改的记录触发 409，不默默返回部分数据；不导出密码或文件存储路径。
def table_rows(entry, label):
    from .experiment_writes import capabilities
    if label not in TABLES:
        raise NotFound("该表未开放实验读取。")
    expected = {row["pk"]: row["fingerprint"] for row in entry.changes["rows"] if row["model"] == label}
    model = apps.get_model(label)
    query = model.objects.filter(pk__in=expected).order_by(model._meta.pk.name)
    owner_path = TABLES[label][1]
    if owner_path:
        query = query.select_related(owner_path)
    fields = model_fields(label)
    rows = []
    for record in query:
        if fingerprint(record) != expected[str(record.pk)]:
            logger.warning("experiment_row_changed batch=%s model=%s pk=%s", entry.object_id, label, record.pk)
            raise Conflict("实验数据已被修改，请维护者核验后重新发布；本次读取已停止。")
        owner = record
        if owner_path:
            for part in owner_path.split("__"):
                owner = getattr(owner, part)
        data = {}
        for field in fields:
            value = getattr(record, field["name"])
            if isinstance(value, (bytes, memoryview)):
                value = {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest(), "download": True}
            elif hasattr(value, "tolist"):
                value = value.tolist()
            data[field["name"]] = value
        rows.append({"pk": str(record.pk), "owner": {"id": owner.pk, "username": owner.username},
                     "batch": entry.object_id, "synthetic": True, "read_only": not capabilities(label)["update"],
                     "fingerprint": expected[str(record.pk)], "fields": data})
    if len(rows) != len(expected):
        logger.warning("experiment_rows_missing batch=%s model=%s expected=%s actual=%s", entry.object_id, label, len(expected), len(rows))
        raise Conflict("实验数据部分缺失，请维护者核验；本次读取已停止。")
    return rows


# 功能：核验并读取清单内文件字节。
# 输入：`entry` 为已授权批次，`label` 为文件模型，`pk` 为清单主键字符串。
# 输出：模型记录与完整 bytes；未知记录 404，文件漂移或路径越界 409。
# 逻辑：检查整表清单后再次核验实际读取的记录，避免 READ COMMITTED 下两次查询间漂移；附件另验路径和摘要。
# 约束：调用方必须先验证用户权限并在事务中调用；不写下载审计，不读取批次目录外文件。
def file_content(entry, label, pk):
    if label not in {"sales.Attachment", "accounts.SetupDocument"}:
        raise NotFound("该记录没有文件下载。")
    rows = table_rows(entry, label)
    if not any(row["pk"] == pk for row in rows):
        raise NotFound("实验文件不存在。")
    record = apps.get_model(label).objects.filter(pk=pk).first()
    expected_row = next(row["fingerprint"] for row in entry.changes["rows"] if row["model"] == label and row["pk"] == pk)
    if record is None or fingerprint(record) != expected_row:
        raise Conflict("实验文件记录已变化，停止读取。")
    if label == "accounts.SetupDocument":
        content = bytes(record.content)
    else:
        root = (settings.BASE_DIR / "private_uploads" / str(entry.owner_id) / entry.object_id).resolve()
        path = (settings.BASE_DIR / "private_uploads" / record.storage_key).resolve()
        expected = {item["key"]: item["sha256"] for item in entry.changes.get("files", [])}
        if path.parent != root or record.storage_key not in expected or not path.is_file():
            raise Conflict("实验附件路径或文件状态异常。")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected[record.storage_key]:
            raise Conflict("实验附件内容已变化，停止下载。")
    return record, content


# 功能：提供表目录、归属与来源声明。
# 输入：`entry` 已批准的批次事件。
# 输出：批次元数据、表字段结构与登记数量。
# 逻辑：数量来自当前清单；发布逐模型 write 能力、变更数量及原始场景真值状态，实际行在分页和导出时验证。
# 约束：不将 synthetic 分析或非语义向量描述为真实模型输出，不返回内部文件清单。
def batch_summary(entry):
    from .experiment_writes import capabilities
    counts = Counter(row["model"] for row in entry.changes["rows"])
    return {"batch": entry.object_id, "owner": {"id": entry.owner_id, "username": entry.owner.username},
            "synthetic": True, "read_only": False, "visibility": "authenticated_users",
            "mutation_count": len(entry.changes.get("mutations", [])),
            "scenario_links_status": "original_before_edits" if entry.changes.get("mutations") else "original",
            "notice": "人工生成的实验数据；AI 结论、工具执行和向量均为模拟记录，不代表真实模型运行。",
            "tables": [{"model": label, "name": title, "table": apps.get_model(label)._meta.db_table,
                        "count": counts[label], "fields": model_fields(label), "write": capabilities(label)} for label, (title, _) in TABLES.items()],
            "total": sum(counts[label] for label in TABLES)}


# 功能：已登录用户的只读实验 API。
# 逻辑：继承统一 Session 认证及 IsAuthenticated；所有路由仅接受 GET/HEAD/OPTIONS。
# 约束：不提供权限管理、任务执行或修改能力，所有响应禁止缓存。
class ExperimentView(APIView):
    http_method_names = ["get", "head", "options"]

    # 功能：分派目录、分页、完整导出和文件下载。
    # 输入：`request` 当前登录请求，`batch` 可选批次，`label` 可选模型，`pk` 可选文件主键。
    # 输出：含 write 字段契约的 JSON 目录/分页、JSON 下载或附件；无权限及漂移使用明确错误。
    # 逻辑：REPEATABLE READ 保证一次多表导出的一致性；筛选只作用于已验证的清单投影。
    # 约束：仅 PostgreSQL；不写业务表，查询日志不含正文、密码或令牌。
    @extend_schema(operation_id="experiments_table", responses=OpenApiTypes.OBJECT, tags=["experiments"],
                   parameters=[OpenApiParameter(name, str) for name in ("q", "owner", "pk", "page", "page_size")])
    def get(self, request, batch=None, label=None, pk=None):
        from django.db import connection
        with transaction.atomic():
            # 独立请求的最外层事务在首次业务查询前固定快照；测试外层事务保留测试隔离级别。
            if len(connection.atomic_blocks) == 1:
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            if batch is None:
                existing = set(AuditEvent.objects.filter(event="kg_synthetic_batch_v1", object_id__in=APPROVED_BATCHES).values_list("object_id", flat=True))
                response = Response({"batches": [batch_summary(load_batch(value)) for value in APPROVED_BATCHES if value in existing]})
            else:
                entry = load_batch(batch)
                if pk is not None:
                    response = self.download(entry, label, pk)
                elif label is None:
                    payload = batch_summary(entry)
                    payload["records"] = {name: table_rows(entry, name) for name in TABLES}
                    payload["scenario_links"] = entry.changes.get("truth", [])
                    body = json.dumps(payload, cls=DjangoJSONEncoder, ensure_ascii=False, allow_nan=False)
                    response = HttpResponse(body, content_type="application/json; charset=utf-8")
                    response["Content-Disposition"] = f'attachment; filename="{batch}.json"'
                else:
                    from .experiment_writes import capabilities
                    rows = table_rows(entry, label)
                    query = request.query_params
                    if query.get("pk"):
                        rows = [row for row in rows if row["pk"] == query["pk"]]
                    if query.get("owner"):
                        rows = [row for row in rows if str(row["owner"]["id"]) == query["owner"] or row["owner"]["username"] == query["owner"]]
                    if query.get("q"):
                        term = query["q"].casefold()
                        rows = [row for row in rows if term in json.dumps(row, cls=DjangoJSONEncoder, ensure_ascii=False).casefold()]
                    try:
                        page = int(query.get("page", 1))
                        size = int(query.get("page_size", 50))
                    except (ValueError, TypeError):
                        raise ValidationError("页码和每页数量必须为整数。") from None
                    if page < 1 or not 1 <= size <= 200:
                        raise ValidationError("页码须大于零，每页数量须在 1–200 之间。")
                    response = Response({"batch": batch, "model": label, "fields": model_fields(label), "write": capabilities(label),
                                         "count": len(rows), "page": page, "page_size": size,
                                         "results": rows[(page - 1) * size:page * size]})
        logger.info("experiment_read actor_id=%s batch=%s model=%s download=%s", request.user.pk, batch or "catalog", label or "all", bool(pk or (batch and not label)))
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    # 功能：核验并返回清单内文件。
    # 输入：`entry` 批次事件、`label` 文档或附件模型、`pk` 原始主键字符串。
    # 输出：强制下载响应；未知文件 404，文件损坏或路径越界 409。
    # 逻辑：委托 file_content 核验行及文件，下载与 Agent 分块读取使用同一内容边界。
    # 约束：不沿用私有附件的写审计服务，不访问批次目录之外的文件，不以内联 HTML 执行。
    def download(self, entry, label, pk):
        record, content = file_content(entry, label, pk)
        return FileResponse(io.BytesIO(content), as_attachment=True, filename=Path(record.name).name,
                            content_type="application/octet-stream")


# 功能：声明批次目录的独立接口契约。
# 逻辑：继承同一只读鉴权和查询实现，仅区分 OpenAPI 操作名称及参数。
# 约束：不改变父类的读取范围或方法白名单。
@extend_schema_view(get=extend_schema(operation_id="experiments_catalog", parameters=[
    OpenApiParameter(name, str, exclude=True) for name in ("q", "owner", "pk", "page", "page_size")]))
class ExperimentCatalogView(ExperimentView):
    pass


# 功能：声明 JSON 导出接口契约。
# 逻辑：继承只读批次分派，导出返回 JSON 附件而非分页。
# 约束：不引入第二套序列化或权限实现。
@extend_schema_view(get=extend_schema(operation_id="experiments_export", parameters=[
    OpenApiParameter(name, str, exclude=True) for name in ("q", "owner", "pk", "page", "page_size")]))
class ExperimentExportView(ExperimentView):
    pass


# 功能：声明二进制附件接口契约。
# 逻辑：父类核验行与文件摘要，契约显式为强制下载的 application/octet-stream。
# 约束：不将文件内容误声明为 JSON，不开放任意文件路径。
@extend_schema_view(get=extend_schema(operation_id="experiments_file", responses={
    (200, "application/octet-stream"): OpenApiTypes.BINARY}, parameters=[
    OpenApiParameter(name, str, exclude=True) for name in ("q", "owner", "pk", "page", "page_size")]))
class ExperimentFileView(ExperimentView):
    pass
