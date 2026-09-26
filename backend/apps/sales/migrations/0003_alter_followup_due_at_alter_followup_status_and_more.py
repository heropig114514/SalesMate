"""Responsibility: Add query indexes for sales status queues and due follow-ups.
Implementation: Use AlterField to index existing status and due_at fields without changing values or default states.
Relationships: sales_worker and business list queries use these fields.
Directory:
- Migration: Status and time index migration.
Variable index:
- Migration.dependencies: Depend on the sales connection migration.
- Migration.operations: List of indexed-field changes.
"""

from django.db import migrations, models


# Function: Add database indexes for status and due time.
# Logic: Django creates indexes while preserving field values and defaults.
# Constraints: Assess index creation time for large tables against actual database size.
class Migration(migrations.Migration):
    dependencies = [
        ("sales", "0002_alter_draft_recipients_connection"),
    ]

    operations = [
        migrations.AlterField(
            model_name="followup",
            name="due_at",
            field=models.DateTimeField(db_index=True),
        ),
        migrations.AlterField(
            model_name="followup",
            name="status",
            field=models.CharField(db_index=True, default="open", max_length=20),
        ),
        migrations.AlterField(
            model_name="opportunity",
            name="status",
            field=models.CharField(db_index=True, default="new", max_length=20),
        ),
        migrations.AlterField(
            model_name="quote",
            name="status",
            field=models.CharField(db_index=True, default="draft", max_length=20),
        ),
        migrations.AlterField(
            model_name="salesorder",
            name="status",
            field=models.CharField(db_index=True, default="draft", max_length=20),
        ),
        migrations.AlterField(
            model_name="ticket",
            name="status",
            field=models.CharField(db_index=True, default="open", max_length=20),
        ),
        migrations.AlterField(
            model_name="toolaction",
            name="status",
            field=models.CharField(
                db_index=True, default="pending_confirmation", max_length=32
            ),
        ),
    ]
