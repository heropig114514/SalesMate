"""Responsibility: Allow conversations without companies for private general chat.
Implementation: Remove only the company foreign key's nonnull constraint; retain existing company relations and deletion policy.
Relationships: sales.models defines the nullable field; apply the migration before deployment.
Directory:
- Migration: Nullable company relation for general chat.
Variable index:
- Migration.dependencies: Previous migration in this application.
- Migration.operations: Change only company null/blank metadata and database constraints.
"""

import django.db.models.deletion
from django.db import migrations, models


# Function: Remove the required-company constraint.
# Logic: AlterField preserves existing foreign keys and records while allowing new company-free conversations.
# Constraints: Resolve company-free records before reverting to a required field; never automatically delete user history.
class Migration(migrations.Migration):
    dependencies = [("sales", "0004_qq_smtp_send")]
    operations = [
        migrations.AlterField(
            model_name="conversation",
            name="company",
            field=models.ForeignKey(
                to="crm.company",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="conversations",
                null=True,
                blank=True,
            ),
        )
    ]
