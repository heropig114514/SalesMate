"""Responsibility: Provide audited, removable creation, modification, and deletion of shared fictional business data.
Implementation: Active employee authentication is mandatory in every mode. Experiment mode omits stale client fingerprints and external drift checks; production compares strictly. Enforce model/field boundaries, manifest row locks, optimistic fingerprint locks, same-batch foreign keys, and no implicit cascading; commit data and manifest atomically.
Relationships: The web UI and Tool/MCP share mutate; experiments publishes capabilities; seed_kg_lab cleans up under the same manifest lock.
Directory:
- write_fields: Enumerate business fields users may submit.
- capabilities: Return writable model capabilities and field constraints.
- validate_relations: Restrict foreign keys to the same batch and check company consistency.
- delete_leaf: Reject deletion while any record still references the target.
- mutate: Mutate one shared record and append a manifest audit entry.
Variable index:
- WRITE_MODELS: Models open to data maintenance, excluding identity, authorization, execution queues, and audit evidence.
- PROTECTED: Server-managed ownership, version, execution credential, and file storage fields.
- INERT_STATUSES: The two model types consumed by background tasks allow only terminal states, preventing experiment maintenance from starting processing or reminders.
- logger: Log mutation identifiers without business content.
"""

import copy
import logging
import uuid
from collections import Counter

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as ModelValidationError
from django.db import IntegrityError, transaction
from django.db.models import AutoField, CharField
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.crm.access import Conflict
from common.fixture_integrity import fingerprint
from common.laboratory import enabled
from .experiments import load_batch, table_rows
from .models import AuditEvent

WRITE_MODELS = frozenset({
    "accounts.CompanyProfile", "accounts.SalesSetup", "sales.SellerProfile",
    "crm.Company", "crm.Contact", "crm.Email", "crm.StoredMessage", "crm.Extraction",
    "crm.AnalysisInput", "crm.Analysis", "crm.Score", "crm.SnapshotSource", "crm.SnapshotInvalidation",
    "sales.CompanySettings", "sales.CompanyAlias", "sales.ContactProfile", "sales.Product",
    "sales.Ticket", "sales.Opportunity", "sales.Quote", "sales.QuoteLine", "sales.SalesOrder",
    "sales.OrderLine", "sales.FollowUp", "sales.Conversation", "sales.Message", "sales.Draft",
    "chat.KnowledgeEntry",
})
PROTECTED = frozenset({"owner", "revision", "external_version", "review_revision",
    "assigned_to", "reviewed_by", "reviewed_at", "external_message_id", "confirmed_at",
    "client_key", "repair_generation"})
logger = logging.getLogger("salesmate.experiments.writes")
INERT_STATUSES = {"crm.StoredMessage": ("completed", "failed"), "sales.FollowUp": ("completed", "cancelled")}


# Function: Enumerate safe business fields.
# Inputs: `label`: fully qualified model name.
# Outputs: Mapping from attname to Django Field; empty for models not open to maintenance.
# Logic: Use an explicit model set, excluding primary keys, ownership, versions, and automatic timestamps.
# Constraints: Do not expose file bytes, identities, queues, authorization, or execution receipts for maintenance.
def write_fields(label):
    if label not in WRITE_MODELS:
        return {}
    historical = {"crm.AnalysisInput": {"revision"}, "crm.SnapshotSource": {"review_revision"}}
    protected = PROTECTED - historical.get(label, set())
    return {field.attname: field for field in apps.get_model(label)._meta.concrete_fields
            if not field.primary_key and field.name not in protected
            and not getattr(field, "auto_now", False) and not getattr(field, "auto_now_add", False)}


# Function: Publish maintainable fields and required-field constraints to clients.
# Inputs: `label`: fully qualified model name.
# Outputs: create/update/delete capabilities and field metadata.
# Logic: Derive required fields from model defaults, null, and blank; prohibit creation of single-account profiles to avoid overwriting real profiles.
# Constraints: Deletion still requires live reference checks; the server validates fields.
def capabilities(label):
    writable = label in WRITE_MODELS
    return {"create": writable and label not in {"accounts.CompanyProfile", "accounts.SalesSetup", "sales.SellerProfile"},
            "update": writable, "delete": writable,
            "fields": [{"name": name, "type": field.get_internal_type(), "nullable": field.null,
                        "required": (label in INERT_STATUSES and name == "status") or (not field.has_default() and not field.null and not field.blank),
                        "choices": [(value, value) for value in INERT_STATUSES[label]] if label in INERT_STATUSES and name == "status" else list(field.choices or []), "max_length": field.max_length,
                        "relation": field.related_model._meta.label if field.is_relation else None}
                       for name, field in write_fields(label).items()]}


# Function: Validate that relations after creation or update remain within the shared experiment.
# Inputs: `record`: instance to save; `entry`: locked manifest.
# Outputs: None; invalid relations raise a 400 error.
# Logic: Ownership is not writable; every other foreign key must be explicitly registered. Production checks related fingerprints, and company relations within a row must agree.
# Constraints: No references to private real rows or authorization by name prefix; retain existing noneditable relations.
def validate_relations(record, entry):
    members = {(row["model"], row["pk"]): row["fingerprint"] for row in entry.changes["rows"]}
    companies = set()
    for name, field in write_fields(record._meta.label).items():
        value = getattr(record, name)
        if not field.is_relation or value is None:
            continue
        if (field.related_model._meta.label, str(value)) not in members:
            raise ValidationError({name: "关联记录必须属于同一共享虚构批次。"})
        related = field.related_model.objects.get(pk=value)
        if not enabled() and fingerprint(related) != members[(field.related_model._meta.label, str(value))]:
            raise Conflict("关联记录已在维护入口之外变化，请先核验。")
        if field.related_model._meta.label == "crm.Company":
            companies.add(str(value))
        elif getattr(related, "company_id", None):
            companies.add(str(related.company_id))
    if len(companies) > 1:
        raise ValidationError("关联记录属于不同客户，请修正客户、联系人或单据关系。")


# Function: Delete one experiment record with no reverse references.
# Inputs: `record`: locked instance with a matching fingerprint.
# Outputs: None; references produce 409, while success deletes the database row.
# Logic: Inspect all reverse relations, including SET_NULL, without automatic cascading or modifying other records.
# Constraints: The caller holds transaction row locks; errors disclose only the related model, never private record content.
def delete_leaf(record):
    blockers = []
    for relation in record._meta.related_objects:
        field = relation.field
        if relation.related_model.objects.filter(**{field.attname: record.pk}).exists():
            blockers.append(relation.related_model._meta.label)
    if blockers:
        raise Conflict("该记录仍被引用，请先删除或调整关联记录：" + ", ".join(sorted(set(blockers))))
    record.delete()


# Function: Atomically maintain one business record in a shared batch.
# Inputs: `actor`: authenticated account; `operation`: create/update/delete; `batch`: exact batch; `label`: model; `data`: business fields; `pk` and `expected`: primary key and old fingerprint for update/delete.
# Outputs: A JSON object containing operation, primary key, batch, actor, and latest record.
# Logic: Require an authenticated active actor even in experiment mode. Lock batch owner, manifest, then target; verify manifest integrity. Experiment mode does not require client expected; production checks the old fingerprint. Refresh manifest and audit after saving while retaining the original manifest.
# Constraints: No automatic retries; database conflicts become 409. Do not execute email, analysis, or queue tasks; retain historical truth and flag possible staleness.
def mutate(actor, operation, batch, label, data=None, pk=None, expected=None):
    if not actor.is_authenticated or not actor.is_active:
        raise PermissionDenied("需要有效登录账号。")
    if operation not in {"create", "update", "delete"} or not capabilities(label).get(operation):
        raise PermissionDenied("此模型或操作未开放；身份、授权、文件和执行证据保持只读。")
    values = {} if data is None else data
    fields = write_fields(label)
    if not isinstance(values, dict) or set(values) - set(fields):
        raise ValidationError("字段包含未开放项；请按目录中的 write.fields 提交。")
    if operation == "delete" and values:
        raise ValidationError("删除不接受业务字段。")
    try:
        with transaction.atomic():
            initial = load_batch(batch)
            get_user_model().objects.select_for_update().get(pk=initial.owner_id)
            # Maintenance and batch cleanup first lock the same manifest; reread state after waiting instead of reusing stale JSON.
            AuditEvent.objects.select_for_update().get(pk=initial.pk)
            entry = load_batch(batch)
            manifest = entry.changes
            model = apps.get_model(label)
            previous = None
            if operation == "create":
                record = model()
                if any(field.name == "owner" for field in model._meta.fields):
                    record.owner_id = entry.owner_id
                if isinstance(model._meta.pk, CharField) and not isinstance(model._meta.pk, AutoField) and not model._meta.pk.has_default():
                    record.pk = f"{batch}:{uuid.uuid4()}"
                if hasattr(record, "client_key"):
                    record.client_key = uuid.uuid4()
            else:
                member = next((row for row in manifest["rows"] if row["model"] == label and row["pk"] == str(pk)), None)
                if member is None:
                    raise NotFound("记录不在共享批次中。")
                record = model.objects.select_for_update().filter(pk=pk).first()
                if record is None or (not enabled() and fingerprint(record) != member["fingerprint"]):
                    raise Conflict("记录已在共享维护入口之外变化，请先核验。")
                previous = fingerprint(record)
                if not enabled() and expected != previous:
                    raise Conflict("记录已更新，请重新读取后再提交，避免覆盖其他人的修改。")
            identity = str(record.pk)
            if "original_rows" not in manifest:
                manifest["original_rows"] = copy.deepcopy(manifest["rows"])
            if operation == "delete":
                delete_leaf(record)
                manifest["rows"] = [row for row in manifest["rows"] if (row["model"], row["pk"]) != (label, identity)]
                current = None
            else:
                for name, value in values.items():
                    setattr(record, name, fields[name].to_python(value))
                if label in INERT_STATUSES and record.status not in INERT_STATUSES[label]:
                    raise ValidationError({"status": "实验维护不启动后台处理或提醒，允许状态：" + ", ".join(INERT_STATUSES[label])})
                validate_relations(record, entry)
                if operation == "update" and hasattr(record, "revision") and label != "crm.AnalysisInput":
                    record.revision += 1
                # The API accepts database-permitted NULL and empty JSON containers without ModelForm blank restrictions; validate other types, uniqueness, and constraints.
                record.full_clean(exclude=[field.name for field in model._meta.fields
                    if (field.null and getattr(record, field.attname) is None)
                    or (field.get_internal_type() == "JSONField" and getattr(record, field.attname) in ({}, []))], validate_unique=True)
                record.save(force_insert=operation == "create")
                record.refresh_from_db()
                identity, current = str(record.pk), fingerprint(record)
                if operation == "create":
                    manifest["rows"].append({"model": label, "pk": identity, "fingerprint": current})
                else:
                    member["fingerprint"] = current
            manifest["table_counts"] = dict(Counter(row["model"] for row in manifest["rows"]))
            audit = {"operation": operation, "model": label, "pk": identity, "actor_id": actor.pk,
                     "actor_username": actor.username, "at": timezone.now().isoformat(),
                     "before": previous, "after": current, "fields": sorted(values)}
            manifest.setdefault("mutations", []).append(audit)
            entry.changes = manifest
            entry.save(update_fields=["changes"])
            result = {"batch": batch, "model": label, "pk": identity, "operation": operation, "synthetic": True, "audit": audit}
            if operation != "delete":
                result["record"] = next(row for row in table_rows(entry, label) if row["pk"] == identity)
    except ModelValidationError as error:
        raise ValidationError(getattr(error, "message_dict", error.messages)) from error
    except (ValueError, TypeError) as error:
        raise ValidationError("字段类型不符合模型约束。") from error
    except IntegrityError as error:
        logger.warning("experiment_write_conflict actor_id=%s batch=%s model=%s operation=%s", actor.pk, batch, label, operation)
        raise Conflict("数据库唯一性或关联约束冲突；本次操作已回滚。") from error
    logger.info("experiment_write actor_id=%s batch=%s model=%s pk=%s operation=%s", actor.pk, batch, label, identity, operation)
    return result
