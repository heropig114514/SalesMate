"""Responsibility: Create the employee Gmail authorization-credential table.
Implementation: Add JSON credentials and authorization and update timestamps in a one-to-one relationship with Mailbox.
Relationships: gmail_oauth writes this table, and deleting a Mailbox cascades to its credential.
Directory:
- Migration: Declares database migration 0003.
Variable index:
- Migration.dependencies: Dependency on migration 0002, which makes the email contact nullable.
- Migration.operations: Creates the GmailCredential table.
"""

from django.db import migrations, models
import django.db.models.deletion


# Function: Declare the GmailCredential table-schema migration.
# Inputs: The Django migration executor provides historical apps and a schema editor.
# Outputs: Creates an authorization table with a one-to-one relationship to Mailbox.
# Logic: Executes CreateModel after migration 0002.
# Constraints: Describes schema only and contains no real Google credential data.
class Migration(migrations.Migration):
    dependencies = [("crm", "0002_email_contact_nullable")]

    operations = [
        migrations.CreateModel(
            name="GmailCredential",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("credentials", models.JSONField(default=dict)),
                ("authorized_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "mailbox",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="gmail_credential",
                        to="crm.mailbox",
                    ),
                ),
            ],
        )
    ]
