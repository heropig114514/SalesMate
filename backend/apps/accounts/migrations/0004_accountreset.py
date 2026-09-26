"""Responsibility: Add account-cleanup coordination state without changing login identity.
Implementation: Create version, idempotency-key, and pending-attachment manifest fields.
Relationships: ``accounts.reset_models``; depends on the onboarding-information migration.
Directory:
- Migration: Declare the coordination-state table.
Variable index:
- Migration.dependencies: Onboarding-model migration.
- Migration.operations: ``AccountReset`` table structure.
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


# Function: Create account-reset coordination state.
# Logic: Adds only a table and does not initialize user business data.
# Constraints: Reverse migration deletes coordination records and does not restore cleaned business data.
class Migration(migrations.Migration):
    dependencies = [("accounts", "0003_salessetup_companyprofile_size_band_setupdocument")]
    operations = [migrations.CreateModel(name="AccountReset", fields=[
        ("owner", models.OneToOneField(primary_key=True, serialize=False, on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        ("generation", models.PositiveBigIntegerField(default=0)),
        ("key", models.UUIDField(null=True)),
        ("keys", models.JSONField(default=list)),
        ("pending_files", models.JSONField(default=list)),
        ("cleaning", models.BooleanField(default=False)),
    ])]
