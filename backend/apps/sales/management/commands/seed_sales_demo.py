"""Responsibility: Import explicitly labeled fictional sales data for local acceptance checks.
Implementation: Lock the selected employee and create independent companies, workspace conversations, and related records in one transaction; an audit batch manifest prevents repeat runs from overwriting data.
Relationships: Reuse sales relation validation, monetary serialization, and audits; these development fixtures invoke neither business scheduling nor external services.
Directory:
- seed_demo: Atomically create or read an existing acceptance batch.
- seed_demo.save: Validate and register one sales fixture record.
- Command: Provide a DEBUG-only management command.
- Command.add_arguments: Require an explicit employee owner.
- Command.handle: Check environment/account and output the batch manifest.
Variable index:
- BATCH: Fixed acceptance batch key preventing duplicate imports.
- PREFIX: Visible marker for all nameable examples.
- Command.help: Management-command help text.
"""

import json
import uuid
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.crm.models import Contact
from apps.sales import models
from apps.sales.grouping import create_company
from apps.sales.serializers import SalesOrderSerializer
from apps.sales.services import audit, validate_record

BATCH = "sales-demo-v1"
PREFIX = "【验收示例】"


# Function: Create an independent fictional acceptance batch.
# Inputs: `actor`: active ordinary employee; read the current date as the baseline for relative follow-up dates.
# Outputs: Dictionary of entity IDs and counts; repeated runs return the original manifest.
# Logic: An employee row lock serializes batch checks; conversations have no preselected company. Commit every record and completion marker together, rolling back the whole batch on error.
# Constraints: DEBUG only; preserve existing companies, send no emails, create no meetings, and enqueue no model analyses. States describe fixture scenarios, not real transaction evidence.
@transaction.atomic
def seed_demo(actor):
    if not settings.DEBUG:
        raise CommandError("验收数据仅允许在 DEBUG 环境导入。")
    actor = get_user_model().objects.select_for_update().get(pk=actor.pk)
    if not actor.is_active or actor.is_staff or actor.is_superuser:
        raise CommandError("请选择已启用的普通员工账号。")
    previous = models.AuditEvent.objects.filter(
        owner=actor, event="acceptance_seed_completed", object_id=BATCH
    ).first()
    if previous:
        return previous.changes
    manifest = {}
    now = timezone.now()

    # Function: Save one constraint-validated fictional record.
    # Inputs: `model`: sales model; `fields`: fixture fields; read enclosing actor and manifest.
    # Outputs: Saved instance; validation errors propagate to the batch transaction.
    # Logic: Validate cross-entity relationships and the model, then register the ID and explicit fixture_created audit.
    # Constraints: Only for this batch, not a replacement for production write services; bypassing scheduling is an explicit fixture-import boundary.
    def save(model, **fields):
        record = model(owner=actor, **fields)
        validate_record(record, actor, set(fields), creating=True)
        record.full_clean()
        record.save()
        manifest.setdefault(model._meta.model_name, []).append(str(record.pk))
        audit(actor, record, "fixture_created", {"batch": BATCH})
        return record

    products = []
    for index, (name, price, stock) in enumerate(
        [
            ("工业相机 500 万像素", "2800", "30"),
            ("远心镜头 0.5X", "1600", "18"),
            ("环形检测光源", "450", "60"),
            ("视觉控制器", "5200", "12"),
            ("现场安装调试服务", "1800", None),
            ("年度维护服务", "3600", None),
        ],
        1,
    ):
        products.append(
            save(
                models.Product,
                sku=f"DEMO-V1-{index:03}",
                name=PREFIX + name,
                description="虚构验收商品；价格和库存仅用于检查页面。",
                currency="CNY",
                unit_price=Decimal(price),
                stock_quantity=Decimal(stock) if stock else None,
            )
        )

    for index, (
        name,
        domain,
        contacts,
        ticket_status,
        opportunity_status,
        order_status,
    ) in enumerate(
        [
            (
                "星河精密制造",
                "xinghe.example",
                ("张明", "李岚"),
                "open",
                "new",
                "draft",
            ),
            (
                "澄海自动化",
                "chenghai.example",
                ("王晨", "赵宁"),
                "in_progress",
                "qualified",
                "confirmed",
            ),
            (
                "远帆电子",
                "yuanfan.example",
                ("陈安", "周悦"),
                "resolved",
                "proposal",
                "fulfilled",
            ),
            (
                "青禾实验室",
                "qinghe.example",
                ("林清", "何青"),
                "closed",
                "lost",
                "cancelled",
            ),
        ],
        1,
    ):
        company = create_company(actor, PREFIX + name)
        manifest.setdefault("company", []).append(str(company.pk))
        company.domains = [domain]
        company.customer = {
            "customer_id": str(company.pk),
            "name": company.name,
            "notes": f"{PREFIX}虚构客户，批次 {BATCH}，不代表真实客户或成交。",
        }
        company.save(update_fields=["domains", "customer"])
        for contact_index, name in enumerate(contacts, 1):
            contact = Contact.objects.create(
                company=company,
                name=PREFIX + name,
                email=f"contact{contact_index}@{domain}",
            )
            manifest.setdefault("contact", []).append(str(contact.pk))
            save(
                models.ContactProfile,
                contact=contact,
                title="采购负责人" if contact_index == 1 else "技术负责人",
                notes=PREFIX + "用于验收联系人关联、职位编辑和客户筛选。",
            )
            if contact_index == 1:
                primary = contact
        company_settings = company.business_settings
        company_settings.primary_contact = primary
        company_settings.notes = PREFIX + "验收后可归档本客户；所有关联记录为虚构。"
        company_settings.save(update_fields=["primary_contact", "notes", "updated_at"])
        manifest.setdefault("companysettings", []).append(str(company_settings.pk))
        save(models.CompanyAlias, company=company, group_key=f"domain:{domain}")
        ticket = save(
            models.Ticket,
            company=company,
            assigned_to=actor,
            title=PREFIX
            + ["样机安装咨询", "图像采集异常排查", "镜头校准复验", "维护工单结案"][
                index - 1
            ],
            description="虚构工单，用于验收处理状态与负责人展示。",
            status=ticket_status,
            priority="high" if index == 2 else "normal",
            due_at=now + timedelta(days=index),
        )
        save(
            models.Opportunity,
            company=company,
            assigned_to=actor,
            title=PREFIX + "产线视觉检测项目",
            description="虚构商机，用于验收阶段和金额统计。",
            status=opportunity_status,
            currency="CNY",
            amount=Decimal(20000 * index),
            expected_close=(now + timedelta(days=15 * index)).date(),
        )
        quote = save(
            models.Quote,
            company=company,
            assigned_to=actor,
            number=f"DEMO-V1-Q-{index:03}",
            currency="CNY",
            valid_until=(now + timedelta(days=14)).date(),
            notes=PREFIX + "报价夹具，未发送；不构成真实交易承诺。",
        )
        order = save(
            models.SalesOrder,
            company=company,
            assigned_to=actor,
            number=f"DEMO-V1-O-{index:03}",
            quote=quote,
            currency="CNY",
            notes=PREFIX + "虚构订单；确认及履约状态仅为验收情景。",
        )
        for product, quantity in [
            (products[0], Decimal(index)),
            (products[2], Decimal(index * 2)),
        ]:
            fields = dict(
                product=product,
                description=product.name,
                quantity=quantity,
                unit_price=product.unit_price,
                discount=Decimal("0"),
            )
            save(models.QuoteLine, quote=quote, **fields)
            save(models.OrderLine, order=order, **fields)
        # Create editable fixture details before setting scenario states; quotes have no sent timestamp or external ID, preventing fabricated send success.
        quote.status = "approved" if index % 2 == 0 else "draft"
        quote.save(update_fields=["status", "updated_at"])
        order.status = order_status
        order.confirmed_at = now if order_status in ("confirmed", "fulfilled") else None
        order.save(update_fields=["status", "confirmed_at", "updated_at"])
        # Update transaction projections only for newly created fictional companies; do not call sync_company, which schedules Agent work.
        company.tickets = [
            {
                "ticket_id": str(ticket.pk),
                "title": ticket.title,
                "description": ticket.description,
                "status": ticket.status,
                "source": "sales_record",
            }
        ]
        if order.confirmed_at:
            company.orders = [
                {
                    "order_id": str(order.pk),
                    "number": order.number,
                    "status": order.status,
                    "currency": order.currency,
                    "amount": SalesOrderSerializer(order).data["total"],
                    "ordered_at": order.confirmed_at.isoformat(),
                    "source": "sales_record",
                }
            ]
        company.save(update_fields=["tickets", "orders"])
        for offset in (-1, 3):
            follow_up = save(
                models.FollowUp,
                company=company,
                assigned_to=actor,
                title=PREFIX + ("回访样机试用情况" if offset < 0 else "确认采购需求"),
                description="虚构跟进；负偏移用于验收逾期提醒。",
                due_at=now + timedelta(days=offset),
                status="completed" if index == 4 else "open",
            )
            if offset < 0 and follow_up.status == "open":
                save(
                    models.Notification,
                    follow_up=follow_up,
                    source_revision=follow_up.revision,
                    title=follow_up.title,
                )
        conversation = save(
            models.Conversation, title=PREFIX + "采购需求讨论"
        )
        save(
            models.Message,
            conversation=conversation,
            client_key=uuid.uuid4(),
            content=PREFIX
            + "请根据客户的商机与报价整理下一次跟进要点。这是人工示例消息。",
        )
        save(
            models.Draft,
            conversation=conversation,
            kind="email",
            subject=PREFIX + "视觉检测方案沟通",
            recipients=[primary.email],
            content="您好，附件方案待完善，拟确认现场环境、样机数量和验收日期。\n此内容为虚构草稿，尚未发送。",
        )

    report = {
        "batch": BATCH,
        "records": manifest,
        "counts": {key: len(value) for key, value in manifest.items()},
    }
    models.AuditEvent.objects.create(
        owner=actor,
        actor=actor,
        event="acceptance_seed_completed",
        object_type="acceptance_batch",
        object_id=BATCH,
        changes=report,
    )
    return report


# Function: Import local acceptance data for a selected employee.
# Logic: Accept only an explicit account and output the atomic transaction result as JSON for subsequent inspection.
# Constraints: Repeat execution does not restore edited or archived examples; no account creation or existing-data cleanup.
class Command(BaseCommand):
    help = "导入带标记的本地销售验收数据，不发送邮件或触发分析。"

    # Function: Declare the owner-account argument.
    # Inputs: `parser`: Django command-line parser.
    # Outputs: None; register required username.
    # Logic: Require an explicitly selected existing employee rather than writing to an arbitrary first account.
    # Constraints: Do not read or output employee credentials.
    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)

    # Function: Resolve the employee and import the acceptance batch.
    # Inputs: `args`: framework positional arguments; `options`: username and standard command options.
    # Outputs: Write batch, entity manifest, and counts to stdout; failures terminate with exceptions.
    # Logic: Query the selected account first and delegate all business writes to the seed_demo atomic transaction.
    # Constraints: Do not treat database/validation errors as success, change configuration, or skip checks.
    def handle(self, *args, **options):
        try:
            actor = get_user_model().objects.get(username=options["username"])
        except get_user_model().DoesNotExist as error:
            raise CommandError("指定员工不存在。") from error
        report = seed_demo(actor)
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
