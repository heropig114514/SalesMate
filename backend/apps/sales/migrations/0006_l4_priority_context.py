"""Responsibility: Add seller profiles and opportunity product fields required for formal L4.
Implementation: Create an owner-unique configuration table and nullable product fields on existing opportunities without inventing historical business data.
Relationships: Database definitions for sales.models.SellerProfile and Opportunity.product_names.
Directory:
- Migration: Incremental migration for formal scoring inputs.
Variable index:
- Migration.dependencies: Account table and preceding sales migration.
- Migration.operations: Reversible operations adding the table and product field.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


# Function: Incremental migration for formal scoring inputs.
# Logic: Django creates the profile table and nullable JSON product field; existing opportunities retain unknown products as null.
# Constraints: Do not write default industries, averages, or fictional orders; reversal removes the added configuration and product field.
class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
        ("sales", "0005_general_conversation"),
    ]

    operations = [
        migrations.CreateModel(
            name="SellerProfile",
            fields=[
                (
                    "owner",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        serialize=False,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ("revision", models.PositiveIntegerField(default=0)),
                ("profile", models.JSONField(default=dict)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.AddField(
            model_name="opportunity",
            name="product_names",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
