"""职责：为共享虚构业务数据提供有审计、可清理的新增、修改和删除。
实现：实验模式省略客户端旧指纹及外部漂移检查，正式模式保持严格比较；固定模型及字段边界、清单行锁、指纹乐观锁、同批次外键和禁止隐式级联；清单与数据原子提交。
关联：网页与 Tool/MCP 共用 mutate；experiments 发布能力；seed_kg_lab 使用同一清单锁清理。
目录：
- write_fields：枚举允许用户提交的业务字段。
- capabilities：返回模型可写能力与字段约束。
- validate_relations：限制外键到同批次并核对客户一致性。
- delete_leaf：拒绝仍被任何记录引用的删除。
- mutate：执行单条共享记录变更并追加清单审计。
变量索引：
- WRITE_MODELS：开放数据维护的模型，不含身份、授权、执行队列和审计证据。
- PROTECTED：服务器维护的归属、版本、执行凭据和文件存储字段。
- INERT_STATUSES：会被后台任务消费的两类模型仅允许终态，避免实验维护启动处理或提醒。
- logger：记录变更定位信息，避免输出业务正文。
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


# 功能：枚举安全业务字段。
# 输入：`label` 模型全名。
# 输出：attname 到 Django Field 的映射，未开放模型为空。
# 逻辑：使用显式模型集合，再排除主键、归属、版本和自动时间。
# 约束：不开放文件字节、身份、队列、授权或执行回执。
def write_fields(label):
    if label not in WRITE_MODELS:
        return {}
    historical = {"crm.AnalysisInput": {"revision"}, "crm.SnapshotSource": {"review_revision"}}
    protected = PROTECTED - historical.get(label, set())
    return {field.attname: field for field in apps.get_model(label)._meta.concrete_fields
            if not field.primary_key and field.name not in protected
            and not getattr(field, "auto_now", False) and not getattr(field, "auto_now_add", False)}


# 功能：向客户端发布可维护字段及必填约束。
# 输入：`label` 模型全名。
# 输出：create/update/delete 和字段元数据。
# 逻辑：字段必填来自模型默认值、null 与 blank；单账号资料禁止创建以免覆盖真实资料。
# 约束：删除能力仍须通过实时引用检查；字段校验由服务端执行。
def capabilities(label):
    writable = label in WRITE_MODELS
    return {"create": writable and label not in {"accounts.CompanyProfile", "accounts.SalesSetup", "sales.SellerProfile"},
            "update": writable, "delete": writable,
            "fields": [{"name": name, "type": field.get_internal_type(), "nullable": field.null,
                        "required": (label in INERT_STATUSES and name == "status") or (not field.has_default() and not field.null and not field.blank),
                        "choices": [(value, value) for value in INERT_STATUSES[label]] if label in INERT_STATUSES and name == "status" else list(field.choices or []), "max_length": field.max_length,
                        "relation": field.related_model._meta.label if field.is_relation else None}
                       for name, field in write_fields(label).items()]}


# 功能：验证新增及修改后的关系仍位于共享实验内。
# 输入：`record` 待保存实例、`entry` 锁定清单。
# 输出：无；不合法关系抛出 400。
# 逻辑：用户归属不可提交，其余外键必须精确登记；正式模式核对关联指纹，同一行的客户关系必须一致。
# 约束：不允许引用私有真实行，不依据名字前缀授权；历史不可编辑关系保持原值。
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


# 功能：删除没有任何反向引用的单条实验记录。
# 输入：`record` 已锁定且指纹匹配的实例。
# 输出：无；存在引用返回 409，成功删除数据库行。
# 逻辑：检查包括 SET_NULL 在内的全部反向关系，不自动级联或修改其他记录。
# 约束：调用方持有事务行锁；错误只公布关联模型，不泄露私有记录内容。
def delete_leaf(record):
    blockers = []
    for relation in record._meta.related_objects:
        field = relation.field
        if relation.related_model.objects.filter(**{field.attname: record.pk}).exists():
            blockers.append(relation.related_model._meta.label)
    if blockers:
        raise Conflict("该记录仍被引用，请先删除或调整关联记录：" + ", ".join(sorted(set(blockers))))
    record.delete()


# 功能：原子维护共享批次中的单条业务记录。
# 输入：`actor` 已认证账号、`operation` 为 create/update/delete、`batch` 精确批次、`label` 模型、`data` 业务字段、`pk` 和 `expected` 为修改/删除的主键及旧指纹。
# 输出：包含操作、主键、批次、操作者与最新记录的 JSON 对象。
# 逻辑：按批次归属、清单、目标顺序锁定，核对清单完整性；实验模式不要求客户端 expected；正式模式检查旧指纹；保存后刷新清单与审计并保留原清单。
# 约束：无自动重试；数据库冲突转 409；不执行邮件、分析或队列任务；历史 truth 保留并标注可能过期。
def mutate(actor, operation, batch, label, data=None, pk=None, expected=None):
    if not enabled() and (not actor.is_authenticated or not actor.is_active):
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
            # 所有维护与批次清理均先锁同一清单；等待后重新读取状态而不沿用旧 JSON。
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
                # API 接受数据库允许的 NULL 和空 JSON 容器，不应用 ModelForm 的 blank 限制；其余类型、唯一性和约束均验证。
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
