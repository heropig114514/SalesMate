"""职责：验证验收夹具的幂等性、事务边界和外部副作用隔离。
实现：在隔离测试数据库创建普通员工及原有客户，检查生成内容与重复导入行为。
关联：覆盖 seed_sales_demo 管理命令的核心函数；不访问真实 Gmail、日历或模型服务。
目录：
- SalesDemoTests：验收批次集成测试。
- SalesDemoTests.setUp：创建隔离员工及原有客户。
- SalesDemoTests.test_seed_is_idempotent_and_preserves_existing_data：检查数据量、金额及无任务副作用。
- SalesDemoTests.test_conflict_rolls_back_whole_batch：检查唯一约束失败时整批回滚。
- SalesDemoTests.test_requires_debug：检查非开发环境拒绝导入。
变量索引：
- 无
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.crm.models import Company, Job
from apps.sales import models
from apps.sales.management.commands.seed_sales_demo import seed_demo
from apps.sales.serializers import SalesOrderSerializer


# 功能：验证虚构数据导入的隔离性与事务一致性。
# 逻辑：使用 Django 测试数据库；仅开启本测试的 DEBUG，保持生产配置不变。
# 约束：不把夹具情景状态当成真实外部交易已验证。
@override_settings(DEBUG=True)
class SalesDemoTests(TestCase):
    # 功能：创建已有数据作为不可覆盖的基线。
    # 输入：无外部参数；读取隔离测试数据库。
    # 输出：初始化 actor 和 original 实例状态。
    # 逻辑：创建一个普通员工和一个原有客户。
    # 约束：事务由 TestCase 管理，测试不使用本机业务账号。
    def setUp(self):
        self.actor = get_user_model().objects.create_user(username="demo-fixture-test")
        self.original = Company.objects.create(
            owner=self.actor, group_key="original", name="原有客户"
        )

    # 功能：检查批次关系、金额和重复导入不会覆盖验收编辑。
    # 输入：无外部参数；使用 setUp 创建的员工和客户。
    # 输出：断言数据量、金额、审计与零外部动作/任务。
    # 逻辑：首次导入后修改一个示例商品，再次导入必须返回原清单并保留修改。
    # 约束：只验证夹具数据及调度边界，不调用真实外部服务。
    def test_seed_is_idempotent_and_preserves_existing_data(self):
        report = seed_demo(self.actor)
        self.assertEqual(report["counts"]["company"], 4)
        self.assertEqual(report["counts"]["contact"], 8)
        self.assertEqual(report["counts"]["notification"], 3)
        self.assertEqual(Company.objects.count(), 5)
        self.original.refresh_from_db()
        self.assertEqual(self.original.name, "原有客户")
        order = models.SalesOrder.objects.get(number="DEMO-V1-O-002")
        self.assertEqual(
            Decimal(SalesOrderSerializer(order).data["total"]), Decimal("7400")
        )
        self.assertEqual(order.company.orders[0]["amount"], "7400.00")
        self.assertFalse(models.Quote.objects.exclude(sent_at=None).exists())
        self.assertFalse(models.ToolAction.objects.exists())
        self.assertFalse(Job.objects.exists())
        models.Product.objects.filter(sku="DEMO-V1-001").update(name="验收时编辑的名称")
        second = seed_demo(self.actor)
        self.assertEqual(report, second)
        self.assertEqual(models.Product.objects.count(), 6)
        self.assertEqual(
            models.Product.objects.get(sku="DEMO-V1-001").name, "验收时编辑的名称"
        )
        self.assertEqual(
            models.AuditEvent.objects.filter(event="acceptance_seed_completed").count(),
            1,
        )

    # 功能：检查导入中途发生唯一键冲突时不留下部分示例。
    # 输入：无外部参数；预先创建第六个示例 SKU 作为冲突边界。
    # 输出：断言校验异常、先前商品回滚及原有商品仍在。
    # 逻辑：前五个商品已尝试写入，第六个失败必须回滚整个事务。
    # 约束：不删除冲突记录或通过重命名隐式绕过冲突。
    def test_conflict_rolls_back_whole_batch(self):
        models.Product.objects.create(
            owner=self.actor,
            sku="DEMO-V1-006",
            name="已有商品",
            currency="CNY",
            unit_price=Decimal("1"),
        )
        with self.assertRaises(ValidationError):
            seed_demo(self.actor)
        self.assertEqual(models.Product.objects.count(), 1)
        self.assertEqual(Company.objects.count(), 1)
        self.assertFalse(models.AuditEvent.objects.exists())

    # 功能：检查非开发环境的命令保护。
    # 输入：无外部参数；本用例将 DEBUG 显式设为 False。
    # 输出：断言 CommandError 且无商品写入。
    # 逻辑：在任何导入之前拒绝非 DEBUG 环境。
    # 约束：仅测试设置覆盖，不修改本机 .env。
    @override_settings(DEBUG=False)
    def test_requires_debug(self):
        with self.assertRaises(CommandError):
            seed_demo(self.actor)
        self.assertFalse(models.Product.objects.exists())
