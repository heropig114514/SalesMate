"""Responsibility: Add independent result and evidence storage for chat read-only queries.
Implementation: Create only the ``ToolRead`` table and request foreign-key index without rewriting existing chat or business records.
Relationships: ``chat.models.ToolRead``; apply this migration before deploying the new endpoint.
Directory:
- Migration: New-table migration for tool-read records.
Variable index:
- Migration.dependencies: Requires the nullable-company migration for general chat requests.
- Migration.operations: Create a new table with UUID, request foreign key, tool arguments, result, and evidence.
"""

import uuid
from typing import ClassVar

import django.db.models.deletion
from django.db import migrations, models


# Function: Persist independent tool results without adding internal fields to the original context HTTP contract.
# Logic: Create a new table through ``CreateModel``; request foreign key uses ``PROTECT`` to retain referenced historical dependencies.
# Constraints: Does not move old evidence or modify old-version answers; rollback removes the new table and deployers must first assess data already produced.
class Migration(migrations.Migration):
    dependencies: ClassVar[list] = [
        ("chat", "0002_general_answer_request"),
    ]

    operations: ClassVar[list] = [
        migrations.CreateModel(
            name="ToolRead",
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
                ("result", models.JSONField()),
                ("evidence_items", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "request",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="tool_reads",
                        to="chat.answerrequest",
                    ),
                ),
            ],
        ),
    ]
