"""职责：为本地验收导入有明确标记的虚构销售业务数据。
实现：锁定指定员工，在单个事务内创建独立客户及关联记录，以审计批次清单保证重复运行不覆盖数据。
关联：复用 sales 的关系校验、金额序列化与审计；这是开发夹具，不调用业务任务调度或外部服务。
目录：
- seed_demo：原子创建或读取已有验收批次。
- seed_demo.save：校验并登记一条销售夹具记录。
- Command：提供仅 DEBUG 可用的管理命令。
- Command.add_arguments：要求明确指定数据归属员工。
- Command.handle：检查环境和账号并输出批次清单。
变量索引：
- BATCH：固定验收批次键，避免重复导入。
- PREFIX：所有可命名示例的可见标记。
- Command.help：管理命令帮助说明。
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


# 功能：创建一批独立的虚构验收记录。
# 输入：`actor` 为已启用的普通员工；读取当前日期作为相对跟进时间基准。
# 输出：包含实体 ID 清单和数量的字典，重复运行返回首次清单。
# 逻辑：员工行锁串行化批次检查，所有记录与完成标记在同一事务提交；错误整批回滚。
# 约束：仅 DEBUG 环境；不修改原有客户，不发信、不创建会议、不入队模型分析；状态是夹具情景而非真实交易证据。
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

    # 功能：保存一条经过约束校验的虚构记录。
    # 输入：`model` 为销售模型，`fields` 为夹具字段；读取外层 actor 与 manifest。
    # 输出：已保存实例；校验错误传播至批次事务。
    # 逻辑：先执行跨实体校验及模型校验，再登记 ID 和显式 fixture_created 审计。
    # 约束：仅供本批次调用，不替代生产写入服务；绕开任务调度是夹具导入的明确边界。
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
        # 夹具先建立可编辑明细，再设定情景状态；报价无发送时间或外部 ID，禁止伪造外发成功。
        quote.status = "approved" if index % 2 == 0 else "draft"
        quote.save(update_fields=["status", "updated_at"])
        order.status = order_status
        order.confirmed_at = now if order_status in ("confirmed", "fulfilled") else None
        order.save(update_fields=["status", "confirmed_at", "updated_at"])
        # 仅更新刚创建的虚构公司的交易投影；不调用会触发 Agent 调度的 sync_company。
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
            models.Conversation, company=company, title=PREFIX + "采购需求讨论"
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


# 功能：导入指定员工的本地验收数据。
# 逻辑：命令入口只接受明确账号，将原子事务结果以 JSON 输出供后续定位。
# 约束：重复执行不恢复已编辑或归档的示例；不创建账号、不清理既有数据。
class Command(BaseCommand):
    help = "导入带标记的本地销售验收数据，不发送邮件或触发分析。"

    # 功能：声明归属账号参数。
    # 输入：`parser` 为 Django 命令行解析器。
    # 输出：无；注册必填 username 参数。
    # 逻辑：要求显式指定已有员工，避免将数据写入任意首个账号。
    # 约束：不读取或输出员工凭证。
    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)

    # 功能：解析员工并导入验收批次。
    # 输入：`args` 为框架位置参数，`options` 包含 username 及标准命令选项。
    # 输出：向 stdout 输出批次、实体清单及数量；失败以异常结束。
    # 逻辑：先查询指定账号，业务写入全部委托 seed_demo 原子事务。
    # 约束：不捕获数据库或校验错误为成功，不改变配置或跳过检查。
    def handle(self, *args, **options):
        try:
            actor = get_user_model().objects.get(username=options["username"])
        except get_user_model().DoesNotExist as error:
            raise CommandError("指定员工不存在。") from error
        report = seed_demo(actor)
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
