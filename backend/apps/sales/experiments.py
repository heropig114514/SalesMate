"""Responsibility: Expose cross-account views and writable capabilities for approved fictional batches to all active authenticated accounts.
Implementation: Disable shared batches under personal workspace isolation; restrict reads by model allowlist and exact manifest primary keys. Production also verifies current fingerprints; experiment mode returns existing records. Preserve ownership and foreign keys and provide pagination, exports, and attachments.
Relationships: seed_kg_lab creates manifests and experiment_writes maintains them atomically; web and agent_tools reuse validation, retaining ordinary business permissions.
Directory:
- load_batch: Locate an approved, uncleared complete batch.
- model_fields: Return displayable fields and relation descriptions.
- table_rows: Validate and project manifest records for one table.
- file_content: Verify and read file bytes listed in the manifest.
- batch_summary: Provide the table catalog, ownership, and provenance declarations.
- ExperimentView: Read-only experiment API for authenticated users.
- ExperimentView.get: Dispatch catalog, pagination, full export, and file download requests.
- ExperimentView.download: Verify and return a manifest-listed file.
- ExperimentCatalogView: Declare the separate batch catalog interface contract.
- ExperimentExportView: Declare the JSON export interface contract.
- ExperimentFileView: Declare the binary attachment interface contract.
Variable index:
- APPROVED_BATCHES: Complete batch names explicitly approved for sharing; other KGSEED batches are not opened automatically.
- TABLES: Chinese display names and owner relation paths for 44 readable models; None denotes the user itself.
- HIDDEN_FIELDS: Excluded login information, internal file paths, and authorization relation fields.
- logger: Audit logs containing only visitor, batch, model, and count.
- ExperimentView.http_method_names: Prohibit writes or action execution through the experiment entry point.
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
from common.laboratory import enabled, owner_only
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


# Function: Locate an approved, uncleared complete batch.
# Inputs: `batch`: complete batch name.
# Outputs: AuditEvent holding the current manifest and original identity; unavailable batches return 404 and invalid manifests return 409.
# Logic: Reject shared batches under personal isolation; otherwise read only the fixed event and name and verify the manifest.
# Constraints: Do not infer synthetic identity from name prefixes or extend access to unlisted records of other accounts.
def load_batch(batch):
    if owner_only() or batch not in APPROVED_BATCHES:
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


# Function: Return displayable fields and relation descriptions.
# Inputs: `label`: model name registered in TABLES.
# Outputs: Field descriptions; the catalog separately exposes the original database table name.
# Logic: Use concrete ORM fields, excluding reverse relations and excluded fields such as passwords.
# Constraints: Relations declare only their targets without expanding record contents; binary data uses a separate download.
def model_fields(label):
    return [{"name": field.attname, "type": field.get_internal_type(),
             "relation": field.related_model._meta.label if field.is_relation else None,
             "primary_key": field.primary_key}
            for field in apps.get_model(label)._meta.concrete_fields
            if field.attname not in HIDDEN_FIELDS.get(label, set())]


# Function: Validate and project one table's manifest records.
# Inputs: `entry`: approved manifest event; `label`: model name.
# Outputs: Records containing original primary keys, ownership, batch, current fingerprint, actual read_only capability, and fields.
# Logic: Read only manifest primary keys and trace foreign-key ownership along fixed paths. Experiment mode returns current contents and fingerprints; production verifies each manifest row.
# Constraints: Missing or modified production rows return 409. Experiment mode permits ordinary business endpoints to modify/delete synthetic rows, but cleanup still verifies the original manifest. Never export passwords or storage paths.
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
        if not enabled() and fingerprint(record) != expected[str(record.pk)]:
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
                     "fingerprint": fingerprint(record), "fields": data})
    if not enabled() and len(rows) != len(expected):
        logger.warning("experiment_rows_missing batch=%s model=%s expected=%s actual=%s", entry.object_id, label, len(expected), len(rows))
        raise Conflict("实验数据部分缺失，请维护者核验；本次读取已停止。")
    return rows


# Function: Verify and read file bytes listed in the manifest.
# Inputs: `entry`: authorized batch; `label`: file model; `pk`: manifest primary-key string.
# Outputs: Model record and complete bytes; unknown records return 404, file drift or path escape returns 409.
# Logic: After checking the full table manifest, reverify the record actually read to detect drift between READ COMMITTED queries; additionally check attachment path and digest.
# Constraints: The caller must validate user permissions first and call within a transaction; no download audit writes or reads outside the batch directory.
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


# Function: Provide the table catalog, ownership, and provenance declarations.
# Inputs: `entry`: approved batch event.
# Outputs: Batch metadata, table field schemas, and registered counts.
# Logic: Counts come from the current manifest; publish per-model write capabilities, mutation counts, and original scenario truth status. Validate actual rows during pagination/export.
# Constraints: Do not describe synthetic analyses or nonsemantic vectors as real model output; do not return internal file manifests.
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


# Function: Read-only experiment API for authenticated users.
# Logic: Inherit shared Session authentication and IsAuthenticated; routes accept only GET/HEAD/OPTIONS.
# Constraints: No permission management, task execution, or mutation capabilities; disable caching for every response.
class ExperimentView(APIView):
    http_method_names = ["get", "head", "options"]

    # Function: Dispatch catalog, pagination, full export, and file download requests.
    # Inputs: `request`: current authenticated request; `batch`: optional batch; `label`: optional model; `pk`: optional file primary key.
    # Outputs: JSON catalog/pages containing the write contract, a JSON download, or an attachment; permission and drift failures are explicit.
    # Logic: Personal isolation returns an empty batch catalog, while load_batch still rejects details; multi-table exports use a consistent snapshot.
    # Constraints: PostgreSQL only; no business-table writes, and query logs omit contents, passwords, and tokens.
    @extend_schema(operation_id="experiments_table", responses=OpenApiTypes.OBJECT, tags=["experiments"],
                   parameters=[OpenApiParameter(name, str) for name in ("q", "owner", "pk", "page", "page_size")])
    def get(self, request, batch=None, label=None, pk=None):
        from django.db import connection
        with transaction.atomic():
            # For standalone requests, the outermost transaction fixes the snapshot before the first business query; outer test transactions retain their test isolation level.
            if len(connection.atomic_blocks) == 1:
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            if batch is None:
                existing = set(AuditEvent.objects.filter(event="kg_synthetic_batch_v1", object_id__in=APPROVED_BATCHES).values_list("object_id", flat=True))
                response = Response({"batches": [batch_summary(load_batch(value)) for value in APPROVED_BATCHES if value in existing and not owner_only()]})
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

    # Function: Verify and return a manifest-listed file.
    # Inputs: `entry`: batch event; `label`: document or attachment model; `pk`: original primary-key string.
    # Outputs: Forced-download response; unknown files return 404, corruption or path escape returns 409.
    # Logic: Delegate row/file checks to file_content so downloads and Agent chunked reads share the same content boundary.
    # Constraints: Do not reuse private attachment audit-writing services, access files outside the batch directory, or execute content as inline HTML.
    def download(self, entry, label, pk):
        record, content = file_content(entry, label, pk)
        return FileResponse(io.BytesIO(content), as_attachment=True, filename=Path(record.name).name,
                            content_type="application/octet-stream")


# Function: Declare the separate batch catalog interface contract.
# Logic: Inherit identical read-only authorization and queries, distinguishing only OpenAPI operation names and parameters.
# Constraints: Do not change the parent read scope or method allowlist.
@extend_schema_view(get=extend_schema(operation_id="experiments_catalog", parameters=[
    OpenApiParameter(name, str, exclude=True) for name in ("q", "owner", "pk", "page", "page_size")]))
class ExperimentCatalogView(ExperimentView):
    pass


# Function: Declare the JSON export interface contract.
# Logic: Inherit read-only batch dispatch; exports return JSON attachments instead of pages.
# Constraints: Do not introduce a second serialization or permission implementation.
@extend_schema_view(get=extend_schema(operation_id="experiments_export", parameters=[
    OpenApiParameter(name, str, exclude=True) for name in ("q", "owner", "pk", "page", "page_size")]))
class ExperimentExportView(ExperimentView):
    pass


# Function: Declare the binary attachment interface contract.
# Logic: The parent verifies row and file digests; the contract explicitly declares a forced application/octet-stream download.
# Constraints: Do not declare file contents as JSON or expose arbitrary file paths.
@extend_schema_view(get=extend_schema(operation_id="experiments_file", responses={
    (200, "application/octet-stream"): OpenApiTypes.BINARY}, parameters=[
    OpenApiParameter(name, str, exclude=True) for name in ("q", "owner", "pk", "page", "page_size")]))
class ExperimentFileView(ExperimentView):
    pass
