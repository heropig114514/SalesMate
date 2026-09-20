"""职责：执行销售记录的授权事务、金额校验、状态流转和 Agent 快照同步。
实现：新聊天会话不绑定公司；按业务 owner 串行化写入；商机变更更新当前客户，订单及产品变更传播销售方评分依赖；审计、版本和任务原子提交；到期提醒在账号共享锁下重读。
国际化：参数化字段错误在产生时按当前语言翻译；字段名、校验条件、状态和写入行为不变。
关联：views 先执行序列化，permissions 控制范围，crm.jobs 保持原分析触发语义。
目录：
- audit：追加不含正文或凭证的操作事件。
- company_of：解析记录所属公司。
- validate_record：验证跨实体关系和金额、草稿及负责人约束。
- save_record：创建或版本化修改销售记录。
- archive_record：归档或恢复记录。
- transition_record：执行业务状态流转。
- sync_company：将关系业务映射到已有 Agent 业务快照。
- notify_due：为到期未完成跟进创建去重提醒。
- enqueue_analysis：入队并在事务提交后按既有配置调度分析。
- sync_priority_dependencies：传播订单及产品对其他客户评分的影响。
变量索引：
- logger：不输出业务正文或凭证的事务日志。
- TRANSITIONS：各类单据允许的显式状态边。
- IMMUTABLE_RELATIONS：已有实体禁止变更的归属关系。
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


# 功能：入队并在事务提交后按既有 provider 调度。
# 输入：`company` 为已锁定公司，`trigger` 为原协议事件名。
# 输出：Job。
# 逻辑：agent 模式只入队由独立 Worker 消费；rules 模式事务提交后执行既有规则入口。
# 约束：不修改 provider、提示词或参数，无错误降级；回滚不会启动分析。
def enqueue_analysis(company, trigger):
    from functools import partial
    from apps.crm.rules import run_company

    job = enqueue(company, trigger)
    if settings.ANALYSIS_PROVIDER == "rules":
        transaction.on_commit(partial(run_company, company.owner, company.pk))
    elif settings.ANALYSIS_PROVIDER != "agent":
        raise InvalidState("ANALYSIS_PROVIDER 只能为 rules 或 agent。")
    return job


# 功能：追加不含正文或凭证的操作事件。
# 输入：`actor` 为操作者，`instance` 为记录，`event` 为事件名，`changes` 为安全元数据或 None。
# 输出：AuditEvent。
# 逻辑：记录归属、实体和字段名/状态，正文与外部参数不得由调用者传入。
# 约束：调用方事务回滚时审计同步回滚；仅记录标识和受控状态。
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


# 功能：解析记录所属公司。
# 输入：`instance` 为销售记录。
# 输出：Company 或 None。
# 逻辑：直接公司、联系人、单据行和会话关联均映射到同一公司。
# 约束：不从文本猜测公司，不返回邮件数据。
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


# 功能：验证跨实体关系、金额、草稿和负责人约束。
# 输入：`instance` 为待保存的模型，`actor` 为用户，`changed` 为本次字段集合，`creating` 为是否新增。
# 输出：无；业务约束不满足抛 ValidationError/PermissionDenied，参数化金额错误使用当前语言。
# 逻辑：验证公司共享编辑权、个人会话归属、冻结单据、同币种及折扣边界；新会话必须无预选公司。
# 约束：仅在授权事务内调用；不自动改价、换汇或推断交易事实。
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
        instance.conversation.owner_id != actor.pk or instance.conversation.archived
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
    if hasattr(instance, "currency"):
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
            instance.product.owner_id != parent.owner_id
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


# 功能：创建或版本化修改销售记录。
# 输入：`serializer` 为已校验序列化器，`actor` 为用户，`expected` 为旧 revision 或 None。
# 输出：已保存模型；归属转移拒绝文案按当前语言插入原字段名。
# 逻辑：锁 owner 后检查关系及版本，保存商机产品与业务记录；当前客户同步后传播订单、产品的共享评分依赖。
# 约束：动作、附件与提醒使用专门入口；写入错误整体回滚，无自动重试。
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
    candidate.full_clean()
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


# 功能：归档或恢复记录。
# 输入：`instance`、`actor`、`expected` 旧版本，`archived` 为目标布尔值。
# 输出：更新后的实例。
# 逻辑：保留历史数据并递增版本，商机及行项目变化更新当前公司，订单和产品归档传播评分依赖。
# 约束：不可归档不可变消息、提醒或执行记录；冻结单据不得通过归档明细改变金额。
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


# 功能：执行业务状态流转。
# 输入：`instance`、`actor`、`expected` 为版本，`target` 为明确目标状态。
# 输出：已转换记录。
# 逻辑：校验状态边及单据明细；商机状态更新当前公司，确认或取消订单同步更新依赖该成交历史的其他公司。
# 约束：禁止客户端声明报价已发送；不推断成交，也不触发外部服务。
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
    # 已确认或结果不明的外发保留报价版本，防止网络调用期间撤销审核改变其含义。
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


# 功能：传播订单及产品对其他客户评分的影响。
# 输入：`instance` 为刚保存、归档或完成状态转换的销售记录。
# 输出：无；为相关客户更新两个版本并入队。
# 逻辑：订单和订单行影响同 owner 历史均值与相似赢单，产品影响目录及历史产品名称。
# 约束：调用者已持 owner 锁且已同步记录所属公司；普通商机不触发全 owner 扇出。
def sync_priority_dependencies(instance):
    from .priority import refresh_owner_priority

    if isinstance(instance, (models.SalesOrder, models.OrderLine, models.Product)):
        company = company_of(instance)
        refresh_owner_priority(instance.owner_id, exclude=[company.pk] if company else [])


# 功能：将关系业务映射到已有 Agent 业务快照。
# 输入：`company` 为关系业务的客户。
# 输出：无。
# 逻辑：只替换 source=sales_record 的受管条目，保留原有历史 JSON；同步递增两个版本并入队。
# 约束：草稿订单不是历史订单，未真实发送的报价不提供 actual_outbound 证据；提交后沿用现有 provider 调度。
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


# 功能：为到期未完成跟进创建去重提醒。
# 输入：无参数；读取当前时钟和数据库中的 open 跟进。
# 输出：新增提醒条数。
# 逻辑：先取得所有者与收件人的账号共享锁，再事务重读跟进；清理未完成或关系变化时本轮不生成提醒。
# 约束：只写应用内通知，不发送邮件；先取得账号锁再取得行锁，避免与重置删除形成等待环。
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
