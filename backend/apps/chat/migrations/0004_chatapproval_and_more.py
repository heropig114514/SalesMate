"""Responsibility: Persist chat approval checkpoints and distinguish suspended/cancelled requests.
Implementation: Add ChatApproval and the question submitter; extend active-conversation uniqueness to approval waits without changing existing records or business operations.
Relationships: chat.models and chat.approvals use these tables; stop old chat workers before applying this migration.
Directory:
- Migration: Schema-only approval upgrade.
Variable index:
- Migration.dependencies: Existing chat, business, and swappable user models.
- Migration.operations: Approval table, relationships, and legal state constraints.
"""

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


# Function: Install durable chat approvals without data backfills.
# Logic: Retain all prior states and enforce one active conversation including approval waits.
# Constraints: Reversing requires resolving new states and approvals first; no external jobs execute here.
class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0003_tool_read"),
        ("crm", "0008_mailboxsyncrun_sync_options"),
        ("sales", "0010_news_signal_fields"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ChatApproval",
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
                ("schema", models.JSONField()),
                ("target_fingerprint", models.CharField(blank=True, max_length=64)),
                ("continuation", models.JSONField()),
                ("status", models.CharField(default="pending", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField()),
                ("decided_at", models.DateTimeField(null=True)),
                ("receipt", models.JSONField(null=True)),
            ],
        ),
        migrations.RemoveConstraint(
            model_name="answerrequest",
            name="chat_one_active_conversation",
        ),
        migrations.RemoveConstraint(
            model_name="answerrequest",
            name="chat_request_status",
        ),
        migrations.AddField(
            model_name="answerrequest",
            name="requested_by",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="submitted_chat_requests",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddConstraint(
            model_name="answerrequest",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("status__in", ["pending", "processing", "awaiting_approval"])
                ),
                fields=("conversation",),
                name="chat_one_active_conversation",
            ),
        ),
        migrations.AddConstraint(
            model_name="answerrequest",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "status__in",
                        [
                            "pending",
                            "processing",
                            "awaiting_approval",
                            "completed",
                            "failed",
                            "cancelled",
                        ],
                    )
                ),
                name="chat_request_status",
            ),
        ),
        migrations.AddField(
            model_name="chatapproval",
            name="decided_by",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="chatapproval",
            name="request",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="approvals",
                to="chat.answerrequest",
            ),
        ),
        migrations.AddConstraint(
            model_name="chatapproval",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "pending")),
                fields=("request",),
                name="chat_one_pending_approval",
            ),
        ),
        migrations.AddConstraint(
            model_name="chatapproval",
            constraint=models.CheckConstraint(
                condition=models.Q(("status__in", ["pending", "approved", "rejected"])),
                name="chat_approval_status",
            ),
        ),
    ]
