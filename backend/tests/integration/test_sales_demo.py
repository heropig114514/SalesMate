"""Responsibility: Verify acceptance-fixture idempotency, transaction boundaries, and isolation of external side effects.
Implementation: Create an ordinary worker and existing customer in an isolated test database, then check generated content and repeated-import behavior.
Relationships: Covers the core function of the `seed_sales_demo` management command; does not access real Gmail, calendar, or model services.
Directory:
- SalesDemoTests: Acceptance-batch integration tests.
- SalesDemoTests.setUp: Create an isolated worker and existing customer.
- SalesDemoTests.test_seed_is_idempotent_and_preserves_existing_data: Check record counts, amounts, and absent task side effects.
- SalesDemoTests.test_conflict_rolls_back_whole_batch: Check whole-batch rollback when a unique constraint fails.
- SalesDemoTests.test_requires_debug: Check that import is rejected outside development environments.
Variable index:
- None
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


# Function: Verify isolation and transaction consistency of fictional-data import.
# Logic: Use the Django test database and enable DEBUG for this test only, preserving production configuration.
# Constraints: Do not treat fixture scenario state as verified real external transactions.
@override_settings(DEBUG=True)
class SalesDemoTests(TestCase):
    # Function: Create existing data as a baseline that cannot be overwritten.
    # Inputs: No external parameters; reads the isolated test database.
    # Outputs: Initializes `actor` and `original` instance state.
    # Logic: Create one ordinary worker and one existing customer.
    # Constraints: `TestCase` manages transactions and the test does not use a local business account.
    def setUp(self):
        self.actor = get_user_model().objects.create_user(username="demo-fixture-test")
        self.original = Company.objects.create(
            owner=self.actor, group_key="original", name="原有客户"
        )

    # Function: Check that batch relationships, amounts, and repeated import do not overwrite acceptance edits.
    # Inputs: No external parameters; uses worker and customer created by setup.
    # Outputs: Asserts record counts, amounts, audit, and zero external actions or jobs.
    # Logic: Modify one example product after initial import; the second import must return the original report and retain the edit.
    # Constraints: Verify fixture data and scheduling boundaries only and do not call real external services.
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

    # Function: Check that a unique-key conflict during import leaves no partial examples.
    # Inputs: No external parameters; pre-creates the sixth example SKU as the conflict boundary.
    # Outputs: Asserts validation exception, rollback of prior products, and retained existing product.
    # Logic: The first five products have attempted writes; failure on the sixth must roll back the entire transaction.
    # Constraints: Do not delete the conflicting record or silently bypass the conflict by renaming.
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

    # Function: Check command protection outside development environments.
    # Inputs: No external parameters; this case explicitly sets DEBUG to False.
    # Outputs: Asserts `CommandError` and no product write.
    # Logic: Reject non-DEBUG environments before any import.
    # Constraints: Override test settings only and do not modify local `.env`.
    @override_settings(DEBUG=False)
    def test_requires_debug(self):
        with self.assertRaises(CommandError):
            seed_demo(self.actor)
        self.assertFalse(models.Product.objects.exists())
