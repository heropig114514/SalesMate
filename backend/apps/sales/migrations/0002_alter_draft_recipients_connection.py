"""Responsibility: Create sales relation records and database constraints.
Implementation: Use declarative Django table/field operations, depending on existing CRM company and account tables.
Relationships: apps.sales.models holds current definitions; preserve existing company and email data.
Directory:
- Migration: Sales database migration.
Variable index:
- Migration.dependencies: Prerequisite migrations and swappable user-model dependency.
- Migration.operations: New tables/fields and uniqueness/amount constraint operations.
"""

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


# Function: Update the sales database through declarative operations.
# Logic: The migration graph enforces dependency order; operations use standard Django reversal definitions.
# Constraints: Reversing table creation deletes business data introduced by this version; follow backup procedures after deployment.
class Migration(migrations.Migration):
    dependencies = [
        ("sales", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="draft",
            name="recipients",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.CreateModel(
            name="Connection",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("revision", models.PositiveIntegerField(default=0)),
                ("archived", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "provider",
                    models.CharField(
                        choices=[("gmail", "Gmail 发信"), ("calendar", "Google 日历")],
                        max_length=16,
                    ),
                ),
                ("account", models.CharField(max_length=320)),
                ("encrypted_credentials", models.TextField()),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("owner", "provider", "account"),
                        name="sales_connection_identity",
                    )
                ],
            },
        ),
    ]
