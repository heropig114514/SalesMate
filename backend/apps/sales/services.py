"""Responsibility: Execute authorized sales transactions, amount validation, state transitions, and Agent snapshot synchronization.
Implementation: All environments enforce owner, manager, and private relation permissions. Public news/events may omit currency when amount is unknown; retain other transaction currency/amount/state constraints. The database resolves cross-account shared-source uniqueness. Serialize writes by business owner, propagate opportunity/seller scoring dependencies, and commit audits, versions, and tasks atomically.
Internationalization: translate parameterized field errors when raised using the current language; preserve field names, validation conditions, states, and write behavior.
Relationships: views serializes first, permissions controls scope, and crm.jobs retains existing analysis-trigger semantics.
Directory:
- audit: Append operation events without bodies or credentials.
- company_of: Resolve the company owning a record.
- validate_record: Validate cross-entity relations, amounts, drafts, and assignee constraints.
- save_record: Create or version-update sales records.
- archive_record: Archive or restore records.
- transition_record: Execute business state transitions.
- sync_company: Map relational business data to existing Agent snapshots.
- notify_due: Create deduplicated reminders for due unfinished follow-ups.
- enqueue_analysis: Enqueue analysis and schedule it after commit using existing configuration.
- sync_priority_dependencies: Propagate order/product effects on other companies' scores.
Variable index:
- logger: Transaction logs without business bodies or credentials.
- TRANSITIONS: Allowed explicit state transitions for document types.
- IMMUTABLE_RELATIONS: Ownership relations immutable on existing entities.
"""


from contextlib import ExitStack
import logging
import re

from django.utils.translation import gettext
from django.contrib.auth import get_user_model
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.accounts.reset_locks import account_lock
from apps.accounts.reset_models import AccountReset
from apps.crm.access import Conflict, InvalidState, check_version
from apps.crm.jobs import enqueue
from apps.crm.models import Company
from . import models
from .permissions import company_access, managed_team_ids, require_edit

logger = logging.getLogger("salesmate.sales")
TRANSITIONS = {
    models.Ticket: {
        "open": ["in_progress", "closed"],
        "in_progress": ["resolved", "open"],
        "resolved": ["closed", "open"],
        "closed": ["open"],
    },
    models.Opportunity: {
        "new": ["qualified", "lost"],
        "qualified": ["proposal", "lost"],
        "proposal": ["won", "lost"],
        "won": [],
        "lost": ["new"],
    },
    models.Quote: {
        "draft": ["approved"],
        "approved": ["draft"],
        "sent": ["accepted", "rejected"],
        "accepted": [],
        "rejected": [],
    },
    models.SalesOrder: {
        "draft": ["confirmed", "cancelled"],
        "confirmed": ["fulfilled", "cancelled"],
        "fulfilled": [],
        "cancelled": [],
    },
    models.FollowUp: {
        "open": ["completed", "cancelled"],
        "completed": [],
        "cancelled": ["open"],
    },
}
IMMUTABLE_RELATIONS = (
    "company",
    "contact",
    "conversation",
    "team",
    "user",
    "quote",
    "order",
    "follow_up",
)


# Function: Enqueue and schedule after commit using the existing provider.
# Inputs: `company`: locked company; `trigger`: existing protocol event name.
# Outputs: Job.
# Logic: Agent mode only enqueues for independent workers; rules mode invokes the existing rules entry point after commit.
# Constraints: Preserve provider, prompts, and parameters; no error fallback. Rollback never starts analysis.
def enqueue_analysis(company, trigger):
    from functools import partial
    from apps.crm.rules import run_company

    job = enqueue(company, trigger)
    if settings.ANALYSIS_PROVIDER == "rules":
        transaction.on_commit(partial(run_company, company.owner, company.pk))
    elif settings.ANALYSIS_PROVIDER != "agent":
        raise InvalidState("ANALYSIS_PROVIDER 只能为 rules 或 agent。")
    return job


# Function: Append operation events without bodies or credentials.
# Inputs: `actor`: operator; `instance`: record; `event`: event name; `changes`: safe metadata or None.
# Outputs: AuditEvent.
# Logic: Record ownership, entity, and field names/states; callers must not supply bodies or external parameters.
# Constraints: Audit rolls back with the caller transaction; record identifiers and controlled states only.
def audit(actor, instance, event, changes=None):
    record = models.AuditEvent.objects.create(
        owner_id=instance.owner_id,
        actor=actor,
        company=company_of(instance),
        event=event,
        object_type=instance._meta.model_name,
        object_id=str(instance.pk),
        changes=changes or {},
    )
    logger.info(
        "business_changed event=%s object_type=%s object_id=%s actor_id=%s",
        event,
        instance._meta.model_name,
        instance.pk,
        actor.pk,
    )
    return record


# Function: Resolve the company owning a record.
# Inputs: `instance`: sales record.
# Outputs: Company or None.
# Logic: Direct company, contact, document-line, and conversation links resolve to the same company.
# Constraints: Do not infer companies from text or return email data.
def company_of(instance):
    if hasattr(instance, "company"):
        return instance.company
    if isinstance(instance, models.ContactProfile):
        return instance.contact.company
    for key in ("quote", "order", "conversation", "follow_up"):
        parent = getattr(instance, key, None)
        if parent is not None:
            return parent.company
    return None


# Function: Validate cross-entity relations, amounts, drafts, and assignee constraints.
# Inputs: `instance`: model to save; `actor`: user; `changed`: current field set; `creating`: whether this is creation.
# Outputs: None; violated constraints raise ValidationError/PermissionDenied, with parameterized amount errors in the current language.
# Logic: News/events may omit currency only when amount=null and currency is empty; transaction currency requirements remain unchanged. Always enforce owner/manager and product ownership checks alongside frozen-document, same-currency, quantity/discount, and company-relation constraints. New conversations still have no preselected company.
# Constraints: Call only within authorized transactions; no automatic repricing, currency conversion, or transaction inference.
def validate_record(instance, actor, changed, creating):
    if creating and isinstance(instance, models.Conversation) and instance.company_id is not None:
        raise ValidationError("客户绑定聊天已停用，请创建无预选公司的工作空间会话。")
    company = company_of(instance)
    if company is not None:
        company_access(
            actor,
            company,
            write=not isinstance(
                instance,
                (models.Conversation, models.Message, models.Draft, models.Attachment),
            ),
        )
        if models.CompanySettings.objects.filter(
            company=company, archived=True
        ).exists():
            raise InvalidState("客户已归档，请先恢复。")
        if not isinstance(
            instance,
            (
                models.Conversation,
                models.Message,
                models.Draft,
                models.ToolAction,
                models.Attachment,
            ),
        ):
            instance.owner_id = company.owner_id
    if (
        isinstance(
            instance, (models.CompanyAlias, models.CompanySettings, models.CompanyGrant)
        )
        and instance.company.owner_id != actor.pk
    ):
        raise PermissionDenied("客户归组、生命周期和共享授权仅由所有者维护。")
    if isinstance(instance, (models.Message, models.Draft)) and (
        (instance.conversation.owner_id != actor.pk) or instance.conversation.archived
    ):
        raise PermissionDenied("会话不可写。")
    if isinstance(instance, models.Membership):
        if instance.team_id not in managed_team_ids(actor):
            raise PermissionDenied("需要团队管理权限。")
        instance.owner_id = instance.team.owner_id
        if instance.user_id == instance.team.owner_id:
            raise ValidationError("团队所有者具有固有管理权，不创建重复成员记录。")
        if instance.role == "manager" and instance.team.owner_id != actor.pk:
            raise PermissionDenied("只有团队所有者可以授予管理角色。")
        if (
            not creating
            and instance.team.owner_id != actor.pk
            and models.Membership.objects.filter(
                pk=instance.pk, role="manager"
            ).exists()
        ):
            raise PermissionDenied("管理者的角色只能由团队所有者修改。")
    if isinstance(
        instance, models.CompanyGrant
    ) and instance.team_id not in managed_team_ids(actor):
        raise PermissionDenied("只能向自己管理的团队授予公司权限。")
    if (
        isinstance(instance, models.CompanySettings)
        and instance.primary_contact_id
        and instance.primary_contact.company_id != instance.company_id
    ):
        raise ValidationError("主要联系人必须属于当前公司。")
    if isinstance(instance, models.CompanyAlias):
        key = instance.group_key.strip().casefold()
        if not re.fullmatch(r"(domain:[a-z0-9.-]+|contact:[^\s@]+@[^\s@]+)", key):
            raise ValidationError("归组键必须是 domain:完整域名 或 contact:邮箱。")
        instance.group_key = key
    if hasattr(instance, "currency") and not (
        isinstance(instance, (models.WorldNews, models.WorldEvent)) and instance.amount is None and instance.currency == ""
    ):
        if not re.fullmatch(r"[A-Z]{3}", instance.currency):
            raise ValidationError("currency 必须使用大写三字母币种代码。")
    for name in ("amount", "unit_price", "stock_quantity"):
        value = getattr(instance, name, None)
        if value is not None and value < 0:
            raise ValidationError(gettext("%(name)s 不得为负数。") % {"name": name})
    if (
        isinstance(instance, (models.Quote, models.SalesOrder))
        and not creating
        and instance.status != "draft"
    ):
        raise InvalidState("单据已冻结；仅草稿可编辑内容。")
    if (
        isinstance(instance, (models.Quote, models.SalesOrder))
        and not creating
        and "currency" in changed
    ):
        previous = type(instance).objects.get(pk=instance.pk)
        if (
            previous.currency != instance.currency
            and instance.lines.filter(archived=False).exists()
        ):
            raise InvalidState(
                "已有明细时不能直接改变币种；请先明确重新定价并重建明细。"
            )
    if isinstance(instance, (models.QuoteLine, models.OrderLine)):
        parent = (
            instance.quote if isinstance(instance, models.QuoteLine) else instance.order
        )
        if parent.archived or parent.status != "draft":
            raise InvalidState("只有未归档的草稿单据可编辑明细。")
        if (
            instance.quantity <= 0
            or instance.discount < 0
            or instance.discount > instance.quantity * instance.unit_price
        ):
            raise ValidationError("数量须大于零，整行折扣不得超过数量乘单价。")
        if instance.product_id and (
            (instance.product.owner_id != parent.owner_id)
            or instance.product.currency != parent.currency
            or instance.product.archived
        ):
            raise ValidationError("产品必须属于单据所有者，使用相同币种且未归档。")
    if isinstance(instance, models.SalesOrder) and instance.quote_id:
        if (
            instance.quote.company_id != instance.company_id
            or instance.quote.currency != instance.currency
        ):
            raise ValidationError("来源报价必须属于同一客户和币种。")
    if hasattr(instance, "assigned_to_id") and instance.assigned_to_id:
        if not instance.assigned_to.is_active:
            raise ValidationError("负责人账号已停用。")
        company_access(instance.assigned_to, company, write=True)
    if isinstance(instance, models.Draft):
        from django.core.validators import validate_email
        from django.core.exceptions import ValidationError as DjangoValidationError

        if not isinstance(instance.recipients, list) or not all(
            isinstance(value, str) for value in instance.recipients
        ):
            raise ValidationError("recipients 必须为邮箱字符串数组。")
        try:
            for address in instance.recipients:
                validate_email(address)
        except DjangoValidationError:
            raise ValidationError("草稿含无效收件人邮箱。") from None
        if len(set(instance.recipients)) != len(instance.recipients):
            raise ValidationError("收件人不可重复。")
        if instance.kind == "chat" and (instance.recipients or instance.subject):
            raise ValidationError("聊天草稿不应包含邮件收件人或主题。")
    if isinstance(instance, models.Message) and (
        not creating or instance.role != "user"
    ):
        raise InvalidState("消息不可编辑，浏览器只能记录用户消息。")


# Function: Create or version-update sales records.
# Inputs: `serializer`: validated serializer; `actor`: user; `expected`: old revision or None.
# Outputs: Saved model; ownership-transfer rejection inserts the original field name into the current-language message.
# Logic: Lock owner, then check relations/version. Serializers precheck public sources, but the database resolves concurrent uniqueness to avoid full_clean turning races into 400; other models retain full model-constraint validation.
# Constraints: Existing views map public-source database uniqueness conflicts to 409; never swallow errors or retry. Actions, attachments, and reminders use dedicated entry points; roll back the entire transaction.
@transaction.atomic
def save_record(serializer, actor, expected=None):
    model = serializer.Meta.model
    if model in (
        models.ToolAction,
        models.Attachment,
        models.Notification,
        models.Connection,
    ):
        raise InvalidState("该记录必须通过专门业务入口创建或修改。")
    existing = serializer.instance
    data = dict(serializer.validated_data)
    candidate = existing if existing else model(owner=actor)
    target_company = company_of(candidate) if existing else None
    if not existing:
        for key, value in data.items():
            setattr(candidate, key, value)
        target_company = company_of(candidate)
    business_owner = (
        target_company.owner_id
        if target_company
        else candidate.team.owner_id
        if isinstance(candidate, models.Membership)
        else existing.owner_id
        if existing
        else actor.pk
    )
    get_user_model().objects.select_for_update().get(pk=business_owner)
    for value in data.values():
        if isinstance(value, (models.Record, Company, get_user_model())):
            value.refresh_from_db()
    if existing:
        candidate = model.objects.select_for_update().get(pk=existing.pk)
        require_edit(candidate, actor)
        check_version(expected, candidate.revision)
        if candidate.archived:
            raise InvalidState("记录已归档，请先恢复。")
        for relation in IMMUTABLE_RELATIONS:
            if relation in data and getattr(
                candidate, relation + "_id", None
            ) != getattr(data[relation], "pk", None):
                raise ValidationError(gettext("%(relation)s 归属不可通过编辑转移。") % {"relation": relation})
        for key, value in data.items():
            setattr(candidate, key, value)
    validate_record(candidate, actor, set(data), existing is None)
    if isinstance(candidate, models.Message):
        duplicate = models.Message.objects.filter(
            conversation=candidate.conversation, client_key=candidate.client_key
        ).first()
        if duplicate:
            if duplicate.content != candidate.content:
                raise Conflict("同一消息幂等键对应不同内容。")
            return duplicate
    candidate.full_clean(validate_constraints=not isinstance(candidate, (models.WorldEvent, models.WorldNews)))
    if existing:
        candidate.revision += 1
    candidate.save()
    if isinstance(candidate, (models.QuoteLine, models.OrderLine)):
        parent = (
            candidate.quote
            if isinstance(candidate, models.QuoteLine)
            else candidate.order
        )
        parent.revision += 1
        parent.save(update_fields=["revision", "updated_at"])
    audit(
        actor,
        candidate,
        "created" if existing is None else "updated",
        {"fields": sorted(data)},
    )
    if isinstance(
        candidate,
        (
            models.CompanySettings,
            models.ContactProfile,
            models.Ticket,
            models.Opportunity,
            models.Quote,
            models.QuoteLine,
            models.SalesOrder,
            models.OrderLine,
        ),
    ):
        sync_company(company_of(candidate))
    sync_priority_dependencies(candidate)
    return candidate


# Function: Archive or restore records.
# Inputs: `instance`, `actor`, and `expected`: old version; `archived`: target boolean.
# Outputs: Updated instance.
# Logic: Lock the original record and owner within the transaction. Require team-manager authority and the expected version in every environment; retain running-action/related-document state checks and save audits.
# Constraints: Immutable messages, reminders, and execution records cannot be archived; archiving details must not alter frozen document amounts.
@transaction.atomic
def archive_record(instance, actor, expected, archived):
    if type(archived) is not bool:
        raise ValidationError("archived 必须为布尔值。")
    get_user_model().objects.select_for_update().get(pk=instance.owner_id)
    instance = type(instance).objects.select_for_update().get(pk=instance.pk)
    require_edit(instance, actor)
    check_version(expected, instance.revision)
    if isinstance(instance, (models.Message, models.ToolAction, models.Notification)):
        raise InvalidState("该记录不支持通用归档。")
    if (
        isinstance(instance, models.Membership)
        and instance.role == "manager"
        and actor.pk != instance.team.owner_id
    ):
        raise PermissionDenied("仅团队所有者可以归档或恢复管理者。")
    if (
        isinstance(instance, models.CompanySettings)
        and archived
        and models.ToolAction.objects.filter(
            company=instance.company, status__in=["approved", "running", "uncertain"]
        ).exists()
    ):
        raise InvalidState("客户存在已确认或待核对的外部动作，暂不可归档。")
    if isinstance(instance, (models.QuoteLine, models.OrderLine)):
        parent = (
            instance.quote if isinstance(instance, models.QuoteLine) else instance.order
        )
        if parent.status != "draft" or parent.archived:
            raise InvalidState("冻结单据的明细不可归档或恢复。")
        parent.revision += 1
        parent.save(update_fields=["revision", "updated_at"])
    if isinstance(
        instance, (models.Quote, models.SalesOrder)
    ) and instance.status not in ("draft", "cancelled", "rejected"):
        raise InvalidState("仅草稿、已取消订单或被拒绝报价可归档；有效交易历史保留。")
    instance.archived, instance.revision = archived, instance.revision + 1
    instance.save(update_fields=["archived", "revision", "updated_at"])
    audit(actor, instance, "archived" if archived else "restored")
    if isinstance(
        instance,
        (
            models.Ticket,
            models.Opportunity,
            models.Quote,
            models.QuoteLine,
            models.SalesOrder,
            models.OrderLine,
        ),
    ):
        sync_company(company_of(instance))
    sync_priority_dependencies(instance)
    return instance


# Function: Execute business state transitions.
# Inputs: `instance`, `actor`, `expected`: version; `target`: explicit target state.
# Outputs: Transitioned record.
# Logic: Validate state edges and document details; opportunity transitions update the current company, while order confirmation/cancellation also updates companies dependent on that transaction history.
# Constraints: Clients cannot declare quotes sent; no inferred completed transactions or external-service calls.
@transaction.atomic
def transition_record(instance, actor, expected, target):
    get_user_model().objects.select_for_update().get(pk=instance.owner_id)
    instance = type(instance).objects.select_for_update().get(pk=instance.pk)
    require_edit(instance, actor)
    check_version(expected, instance.revision)
    if instance.archived or target not in TRANSITIONS.get(type(instance), {}).get(
        getattr(instance, "status", None), []
    ):
        raise InvalidState("当前记录不允许该状态转换。")
    # Confirmed or uncertain external sends retain the quote version, preventing review revocation during network calls from changing its meaning.
    if (
        isinstance(instance, models.Quote)
        and models.ToolAction.objects.filter(
            company=instance.company,
            parameters__quote_id=str(instance.pk),
            status__in=["approved", "running", "uncertain"],
        ).exists()
    ):
        raise InvalidState("报价存在已确认或待核对的外发动作，暂不可改变状态。")
    if (
        isinstance(instance, (models.Quote, models.SalesOrder))
        and target in ("approved", "confirmed")
        and not instance.lines.filter(archived=False).exists()
    ):
        raise ValidationError("至少需要一个有效明细才能审核或确认。")
    previous = instance.status
    instance.status, instance.revision = target, instance.revision + 1
    if isinstance(instance, models.SalesOrder) and target == "confirmed":
        instance.confirmed_at = timezone.now()
    instance.save()
    audit(actor, instance, "status_changed", {"from": previous, "to": target})
    if isinstance(instance, (models.Ticket, models.Opportunity, models.Quote, models.SalesOrder)):
        sync_company(instance.company)
    sync_priority_dependencies(instance)
    return instance


# Function: Propagate order/product effects on other companies' scores.
# Inputs: `instance`: sales record just saved, archived, or transitioned.
# Outputs: None; update both versions and enqueue work for related companies.
# Logic: Orders/order lines affect same-owner historical averages and similar wins; products affect catalogs and historical product names.
# Constraints: The caller holds the owner lock and has synchronized the record's company; ordinary opportunities do not trigger owner-wide fan-out.
def sync_priority_dependencies(instance):
    from .priority import refresh_owner_priority

    if isinstance(instance, (models.SalesOrder, models.OrderLine, models.Product)):
        company = company_of(instance)
        refresh_owner_priority(instance.owner_id, exclude=[company.pk] if company else [])


# Function: Map relational business data to existing Agent snapshots.
# Inputs: `company`: company associated with relational business data.
# Outputs: Outputs: None.
# Logic: Replace only managed source=sales_record entries, retaining original historical JSON; increment both versions and enqueue together.
# Constraints: Draft orders are not historical orders, and unsent quotes provide no actual_outbound evidence; use existing provider scheduling after commit.
def sync_company(company):
    from .serializers import QuoteSerializer, SalesOrderSerializer

    company = Company.objects.select_for_update().get(pk=company.pk)
    company.tickets = [
        item for item in company.tickets if item.get("source") != "sales_record"
    ] + [
        {
            "ticket_id": str(item.pk),
            "title": item.title,
            "description": item.description,
            "status": item.status,
            "source": "sales_record",
        }
        for item in models.Ticket.objects.filter(
            company=company, archived=False
        ).order_by("created_at", "id")
    ]
    company.quotes = [
        item for item in company.quotes if item.get("source") != "sales_record"
    ] + [
        {
            "quote_id": str(item.pk),
            "number": item.number,
            "status": item.status,
            "currency": item.currency,
            "amount": QuoteSerializer(item).data["total"],
            "sent_at": item.sent_at.isoformat(),
            "evidence_type": "actual_outbound",
            "external_message_id": item.external_message_id,
            "source": "sales_record",
        }
        for item in models.Quote.objects.filter(
            company=company, archived=False, sent_at__isnull=False
        ).order_by("created_at", "id")
    ]
    company.orders = [
        item for item in company.orders if item.get("source") != "sales_record"
    ] + [
        {
            "order_id": str(item.pk),
            "number": item.number,
            "status": item.status,
            "currency": item.currency,
            "amount": SalesOrderSerializer(item).data["total"],
            "ordered_at": item.confirmed_at.isoformat(),
            "source": "sales_record",
        }
        for item in models.SalesOrder.objects.filter(
            company=company, archived=False, status__in=["confirmed", "fulfilled"]
        ).order_by("created_at", "id")
    ]
    company.revision += 1
    company.external_version += 1
    company.save(
        update_fields=["tickets", "quotes", "orders", "revision", "external_version"]
    )
    enqueue_analysis(company, "external_updated")


# Function: Create deduplicated reminders for due unfinished follow-ups.
# Inputs: No parameters; read the current clock and open follow-ups from the database.
# Outputs: Number of newly created reminders.
# Logic: Acquire shared account locks for owners/recipients, then reread follow-ups transactionally; unfinished cleanup or changed relations suppress reminders for this iteration.
# Constraints: Write in-app notifications only, never email; account locks precede row locks to avoid wait cycles with reset deletion.
def notify_due():
    created = 0
    candidates = list(models.FollowUp.objects.filter(
        status="open", archived=False, due_at__lte=timezone.now()
    ).exclude(company__business_settings__archived=True).values_list("pk", "owner_id", "assigned_to_id"))
    for task_id, owner_id, assigned_id in candidates:
        owners = sorted({owner_id, assigned_id or owner_id})
        with ExitStack() as stack:
            for account_id in owners:
                stack.enter_context(account_lock(account_id))
            if AccountReset.objects.filter(owner_id__in=owners, cleaning=True).exists():
                continue
            with transaction.atomic():
                task = models.FollowUp.objects.select_for_update(of=("self",)).filter(
                    pk=task_id, status="open", archived=False, due_at__lte=timezone.now(),
                    owner_id=owner_id, assigned_to_id=assigned_id,
                ).exclude(company__business_settings__archived=True).first()
                if task is None:
                    continue
                recipient = task.assigned_to or task.owner
                try:
                    company_access(recipient, task.company)
                except (PermissionDenied, NotFound):
                    logger.debug("follow_up_reminder_skipped task_id=%s reason=recipient_access_revoked", task.pk)
                    continue
                _, fresh = models.Notification.objects.get_or_create(
                    owner=recipient, follow_up=task, source_revision=task.revision,
                    defaults={"title": task.title},
                )
                created += int(fresh)
    if created:
        logger.info("follow_up_reminders_created count=%s", created)
    return created
