"""Responsibility: Create tables for business-tool authorization, receipts, and confirmation proposals.
Implementation: Create only models and their unique constraints; does not migrate or modify existing business data.
Relationships: ``agent_tools.models``; depends on the swappable user model.
Directory:
- Migration: Initial table-creation operations.
Variable index:
- Migration.initial: Initial-migration marker.
- Migration.dependencies: User-table dependency.
- Migration.operations: Three models and the receipt unique constraint.
"""

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


# Function: Add tool-persistence structures.
# Logic: New tables and inline constraints.
# Constraints: Rollback deletes records in these new tables.
class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ToolCredential",
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
                ("name", models.CharField(max_length=120)),
                ("digest", models.CharField(max_length=64, unique=True)),
                ("allowed_tools", models.JSONField()),
                ("expires_at", models.DateTimeField()),
                ("revoked_at", models.DateTimeField(null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ToolProposal",
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
                ("tool", models.CharField(max_length=120)),
                ("arguments", models.JSONField()),
                ("status", models.CharField(default="pending", max_length=16)),
                ("result", models.JSONField(null=True)),
                ("expires_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "credential",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="agent_tools.toolcredential",
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ToolCall",
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
                ("key", models.UUIDField()),
                ("tool", models.CharField(max_length=120)),
                ("input_hash", models.CharField(max_length=64)),
                ("result", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("owner", "key"), name="agent_tools_owner_call_key"
                    )
                ],
            },
        ),
    ]
