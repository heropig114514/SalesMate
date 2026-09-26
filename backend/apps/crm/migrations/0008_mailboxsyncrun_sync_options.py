"""Responsibility: Store frozen synchronization scope for each mailbox batch.
Implementation: Add a JSON field; legacy batches and Gmail default to empty objects without inferring past selections.
Relationships: Depends on crm.0007; processing_models defines the corresponding field.
Directory:
- Migration: Adds the batch-scope field.
Variable index:
- Migration.dependencies: Prior QQ schema migration.
- Migration.operations: Reversibly adds sync_options.
"""
from django.db import migrations, models


# Function: Add a batch scope snapshot.
# Logic: Add a JSON field to the database and assign empty objects to historical records.
# Constraints: Does not modify credentials, emails, or existing Gmail synchronization state.
class Migration(migrations.Migration):
    dependencies = [("crm", "0007_qq_mailbox")]
    operations = [migrations.AddField(model_name="mailboxsyncrun", name="sync_options", field=models.JSONField(default=dict))]
