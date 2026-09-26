"""Responsibility: Create the employee-isolated vector-document table and enable pgvector.
Implementation: Create the vector extension before the table, using a variable-dimension field and source uniqueness constraint.
Relationships: Corresponds to vectors.models; administrators pre-install the extension on the server, so application migration needs no superuser permission.
Directory:
- Migration: Initial vector-storage migration.
Variable index:
- Migration.initial: Initial-migration marker.
- Migration.dependencies: User-model dependency.
- Migration.operations: Extension and table-creation operations.
"""
import django.db.models.deletion
import pgvector.django.vector
from pgvector.django import VectorExtension
from django.conf import settings
from django.db import migrations, models


# Function: Initialize vector-storage structures.
# Logic: Ensure the extension exists before creating the vector table.
# Constraints: Extension software must be preinstalled; a reverse migration deletes vector data.
class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        VectorExtension(),
        migrations.CreateModel(
            name="VectorDocument",
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
                ("namespace", models.CharField(max_length=100)),
                ("source", models.CharField(max_length=255)),
                ("model", models.CharField(max_length=150)),
                ("dimensions", models.PositiveIntegerField()),
                ("content", models.TextField()),
                ("content_hash", models.CharField(max_length=64)),
                ("embedding", pgvector.django.vector.VectorField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("owner", "namespace", "source", "model"),
                        name="vector_owner_source_model_unique",
                    )
                ],
            },
        ),
    ]
