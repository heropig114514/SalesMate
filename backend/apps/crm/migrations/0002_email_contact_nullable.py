"""Responsibility: Allow Gmail messages without an identified external contact to be stored.
Implementation: Make Email.contact nullable while keeping all other email fields and grouping relationships unchanged.
Relationships: Corresponds to apps.crm.models.Email.contact and is executed by Django migrate.
Directory:
- Migration: Changes the email-contact foreign-key constraint.
Variable index:
- Migration.dependencies: Dependency on the initial crm migration.
- Migration.operations: Changes Email.contact to a nullable foreign key.
"""

from django.db import migrations, models


# Function: Change the email-contact foreign-key constraint.
# Logic: Set only contact to null=True and retain existing contact relationships unchanged.
# Constraints: Messages with an empty contact must be handled before rolling back to the initial migration.
class Migration(migrations.Migration):
    dependencies = [("crm", "0001_initial")]

    operations = [
        migrations.AlterField(
            model_name="email",
            name="contact",
            field=models.ForeignKey(
                null=True,
                on_delete=models.PROTECT,
                related_name="messages",
                to="crm.contact",
            ),
        )
    ]
