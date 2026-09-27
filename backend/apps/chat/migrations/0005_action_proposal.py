"""Responsibility: Store independent employee-confirmed chat business proposals.
Implementation: Add a proposal table with unique preparation identity and optional approved draft/action links; no existing business rows are changed.
Relationships: chat.action_services owns decisions; sales.ToolAction remains the worker execution source.
Directory:
- Migration: Add the durable proposal schema.
Variable index:
- Migration.dependencies: Existing chat, sales and employee tables.
- Migration.operations: Proposal fields, protected bindings, unique preparation key and state constraint.
"""

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


# Function: Add proposal persistence without executing business operations.
# Logic: Create protected foreign keys, unique idempotency identity and a legal status constraint.
# Constraints: Applying the migration does not approve proposals or send mail; reversing removes proposal history.
class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0004_chatapproval_and_more"),
        ("sales", "0012_news_map_locations"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ActionProposal",
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
                ("preview", models.JSONField()),
                ("target_id", models.UUIDField()),
                ("key", models.CharField(max_length=64, unique=True)),
                (
                    "status",
                    models.CharField(default="pending_confirmation", max_length=24),
                ),
                ("revision", models.PositiveIntegerField(default=1)),
                ("expires_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("decided_at", models.DateTimeField(null=True)),
                ("decision", models.CharField(blank=True, max_length=10)),
                (
                    "action",
                    models.OneToOneField(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="sales.toolaction",
                    ),
                ),
                (
                    "decided_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "draft",
                    models.OneToOneField(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="sales.draft",
                    ),
                ),
                (
                    "request",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="action_proposals",
                        to="chat.answerrequest",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "status__in",
                                [
                                    "pending_confirmation",
                                    "approved",
                                    "running",
                                    "succeeded",
                                    "failed",
                                    "uncertain",
                                    "cancelled",
                                    "expired",
                                    "conflicted",
                                ],
                            )
                        ),
                        name="chat_action_proposal_status",
                    )
                ],
            },
        ),
    ]
