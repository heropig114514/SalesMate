"""Responsibility: Allow ``answerrequest`` without a customer to support private general chat.
Implementation: Only remove the non-null constraint from the company foreign key; retain existing customer relation and deletion policy.
Relationships: Corresponds to nullable field in ``chat.models``; deployment must apply this migration first.
Directory:
- Migration: Nullable customer relation for general chat.
Variable index:
- Migration.dependencies: Previous migration in this application.
- Migration.operations: Adjust only company null or blank metadata and database constraint.
"""

import django.db.models.deletion
from django.db import migrations, models


# Function: Remove the required-customer constraint.
# Logic: ``AlterField`` retains existing foreign key and records and permits only newly created customerless conversations.
# Constraints: Before reverting to required, handle customerless records first; do not automatically delete user history.
class Migration(migrations.Migration):
    dependencies = [("chat", "0001_initial")]
    operations = [
        migrations.AlterField(
            model_name="answerrequest",
            name="company",
            field=models.ForeignKey(
                to="crm.company",
                on_delete=django.db.models.deletion.PROTECT,
                null=True,
                blank=True,
            ),
        )
    ]
