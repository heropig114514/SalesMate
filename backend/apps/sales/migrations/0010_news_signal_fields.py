"""Responsibility: Add public news sales leads, exact monetary amounts, and source evidence.
Implementation: Existing text fields default to empty strings and amounts remain null; database constraints enforce nonnegative amounts and all-or-none metadata.
Relationships: WorldNews and WorldNewsSerializer; no old-article parsing, Agent calls, or CRM creation in this migration.
Directory:
- Migration: Add thirteen optional fields and monetary consistency constraints.
Variable index:
- Migration.dependencies: Depend on the shared global-insight migration and swappable user model.
- Migration.operations: Add fields and amount-combination checks without inferred backfills.
"""

from django.conf import settings
from django.db import migrations, models


# Function: Install public-news lead storage contracts.
# Logic: Atomically add fields and constraints; existing records naturally satisfy the empty-value combination.
# Constraints: Reverse migration removes added columns; production execution follows existing backup/release procedures.
class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0009_shared_insights"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="worldnews",
            name="amount",
            field=models.DecimalField(
                blank=True, decimal_places=6, max_digits=30, null=True
            ),
        ),
        migrations.AddField(
            model_name="worldnews",
            name="amount_evidence",
            field=models.CharField(blank=True, default="", max_length=400),
        ),
        migrations.AddField(
            model_name="worldnews",
            name="amount_scope",
            field=models.CharField(
                blank=True,
                choices=[
                    ("whole_project", "whole_project"),
                    ("equipment_procurement", "equipment_procurement"),
                    ("other", "other"),
                ],
                default="",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="worldnews",
            name="amount_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("total_investment", "total_investment"),
                    ("procurement_budget", "procurement_budget"),
                    ("tender_amount", "tender_amount"),
                    ("contract_amount", "contract_amount"),
                    ("other", "other"),
                ],
                default="",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="worldnews",
            name="company_name",
            field=models.CharField(blank=True, default="", max_length=240),
        ),
        migrations.AddField(
            model_name="worldnews",
            name="currency",
            field=models.CharField(
                blank=True,
                choices=[
                    ("CNY", "CNY"),
                    ("USD", "USD"),
                    ("EUR", "EUR"),
                    ("GBP", "GBP"),
                    ("JPY", "JPY"),
                    ("KRW", "KRW"),
                    ("SGD", "SGD"),
                    ("TWD", "TWD"),
                    ("HKD", "HKD"),
                    ("INR", "INR"),
                    ("CAD", "CAD"),
                    ("AUD", "AUD"),
                    ("CHF", "CHF"),
                ],
                default="",
                max_length=3,
            ),
        ),
        migrations.AddField(
            model_name="worldnews",
            name="demand_description",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="worldnews",
            name="evidence",
            field=models.CharField(blank=True, default="", max_length=600),
        ),
        migrations.AddField(
            model_name="worldnews",
            name="opportunity_reason",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="worldnews",
            name="potential_sales_need",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="worldnews",
            name="project_name",
            field=models.CharField(blank=True, default="", max_length=240),
        ),
        migrations.AddField(
            model_name="worldnews",
            name="signal_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("expansion", "expansion"),
                    ("new_factory", "new_factory"),
                    ("tender", "tender"),
                    ("equipment_upgrade", "equipment_upgrade"),
                    ("procurement", "procurement"),
                    ("other", "other"),
                ],
                default="",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="worldnews",
            name="time_window",
            field=models.CharField(blank=True, default="", max_length=240),
        ),
        migrations.AddConstraint(
            model_name="worldnews",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("amount__isnull", True),
                        ("amount_evidence", ""),
                        ("amount_scope", ""),
                        ("amount_type", ""),
                        ("currency", ""),
                    ),
                    models.Q(
                        ("amount__gte", 0),
                        ("amount__isnull", False),
                        (
                            "amount_scope__in",
                            ["whole_project", "equipment_procurement", "other"],
                        ),
                        (
                            "amount_type__in",
                            [
                                "total_investment",
                                "procurement_budget",
                                "tender_amount",
                                "contract_amount",
                                "other",
                            ],
                        ),
                        (
                            "currency__in",
                            [
                                "CNY",
                                "USD",
                                "EUR",
                                "GBP",
                                "JPY",
                                "KRW",
                                "SGD",
                                "TWD",
                                "HKD",
                                "INR",
                                "CAD",
                                "AUD",
                                "CHF",
                            ],
                        ),
                        models.Q(("amount_evidence", ""), _negated=True),
                    ),
                    _connector="OR",
                ),
                name="world_news_amount_consistent",
            ),
        ),
    ]
