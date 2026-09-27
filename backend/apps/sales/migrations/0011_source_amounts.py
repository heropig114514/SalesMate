"""Responsibility: Add independently sourced event amounts and preserve monetary qualifiers.
Implementation: Add nullable monetary fields without guessing historical values; expand source types and database consistency checks.
Relationships: Matches sales models and the shared source-amount serializers; historical records are curated separately through normal versioned services.
Directory:
- Migration: Apply additive source-amount schema changes.
Variable index:
- Migration.dependencies: Require existing news signal fields.
- Migration.operations: Add fields and enforce complete-or-empty event amount metadata.
"""

from django.conf import settings
from django.db import migrations, models


# Function: Apply the new source-amount storage contract.
# Logic: Add nullable event fields and news qualifiers, then rebuild relevant checks.
# Constraints: No automatic source extraction, financial backfill, or CRM updates.
class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0010_news_signal_fields"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="worldnews",
            name="world_news_amount_consistent",
        ),
        migrations.AddField(
            model_name="worldevent",
            name="amount",
            field=models.DecimalField(
                blank=True, decimal_places=6, max_digits=30, null=True
            ),
        ),
        migrations.AddField(
            model_name="worldevent",
            name="amount_evidence",
            field=models.CharField(blank=True, default="", max_length=400),
        ),
        migrations.AddField(
            model_name="worldevent",
            name="amount_qualifier",
            field=models.CharField(
                blank=True,
                choices=[
                    ("exact", "exact"),
                    ("up_to", "up_to"),
                    ("at_least", "at_least"),
                    ("more_than", "more_than"),
                    ("approximate", "approximate"),
                ],
                default="",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="worldevent",
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
            model_name="worldevent",
            name="amount_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("total_investment", "total_investment"),
                    ("procurement_budget", "procurement_budget"),
                    ("tender_amount", "tender_amount"),
                    ("contract_amount", "contract_amount"),
                    ("grant", "grant"),
                    ("registration_fee", "registration_fee"),
                    ("exhibition_fee", "exhibition_fee"),
                    ("other", "other"),
                ],
                default="",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="worldevent",
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
            model_name="worldevent",
            name="evidence",
            field=models.CharField(blank=True, default="", max_length=600),
        ),
        migrations.AddField(
            model_name="worldnews",
            name="amount_qualifier",
            field=models.CharField(
                blank=True,
                choices=[
                    ("exact", "exact"),
                    ("up_to", "up_to"),
                    ("at_least", "at_least"),
                    ("more_than", "more_than"),
                    ("approximate", "approximate"),
                ],
                default="",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="worldnews",
            name="amount_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("total_investment", "total_investment"),
                    ("procurement_budget", "procurement_budget"),
                    ("tender_amount", "tender_amount"),
                    ("contract_amount", "contract_amount"),
                    ("grant", "grant"),
                    ("registration_fee", "registration_fee"),
                    ("exhibition_fee", "exhibition_fee"),
                    ("other", "other"),
                ],
                default="",
                max_length=30,
            ),
        ),
        migrations.AddConstraint(
            model_name="worldevent",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("amount__isnull", True),
                        ("amount_evidence", ""),
                        ("amount_qualifier", ""),
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
                                "grant",
                                "registration_fee",
                                "exhibition_fee",
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
                name="world_event_amount_consistent",
            ),
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
                                "grant",
                                "registration_fee",
                                "exhibition_fee",
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
