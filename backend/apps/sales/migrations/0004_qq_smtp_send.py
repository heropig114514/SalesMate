"""Responsibility: Allow independent QQ sending connections and qq.send actions.
Implementation: Extend choices while retaining existing Gmail/calendar values and data.
Relationships: Connection and ToolAction in sales.models; preserve the QQ read-only credential table.
Directory:
- Migration: Declare QQ sending choice changes.
Variable index:
- Migration.dependencies: Existing sales.0003 migration.
- Migration.operations: Extend provider and tool choices.
"""

from django.db import migrations, models


# Function: Synchronize QQ sending model choices.
# Logic: Add choices only, retaining existing fields and data.
# Constraints: Do not create connections or send emails.
class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0003_alter_followup_due_at_alter_followup_status_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="connection",
            name="provider",
            field=models.CharField(
                choices=[
                    ("gmail", "Gmail 发信"),
                    ("qq", "QQ 发信"),
                    ("calendar", "Google 日历"),
                ],
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="toolaction",
            name="tool",
            field=models.CharField(
                choices=[
                    ("gmail.send", "Gmail 发信"),
                    ("qq.send", "QQ 发信"),
                    ("calendar.create", "创建日历会议"),
                ],
                max_length=40,
            ),
        ),
    ]
