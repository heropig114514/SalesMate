"""Responsibility: Create graph entity, source-version, fact, support-path, event, and state tables.
Implementation: Django generates the initial model structure; unique constraints protect source identity and current versions.
Relationships: 0002 installs PostgreSQL capture without changing business source-table contents.
Directory:
- Migration: Initial graph table structure.
Variable index:
- Migration.initial: Marks the initial schema migration.
- Migration.dependencies: Dependencies on accounts and the swappable user model.
- Migration.operations: Creation operations for seven graph models and unique constraints.
"""

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


# Function: Create derived graph storage.
# Logic: Tables, relationships, and indexes match the initial models contract.
# Constraints: Do not install triggers or backfill business data.
class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("accounts", "0004_accountreset"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Change",
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
                ("owner_id", models.BigIntegerField(db_index=True)),
                ("kind", models.CharField(max_length=80)),
                ("source_id", models.CharField(max_length=400)),
                ("operation", models.CharField(max_length=16)),
                (
                    "status",
                    models.CharField(db_index=True, default="pending", max_length=16),
                ),
                (
                    "error_code",
                    models.CharField(blank=True, default="", max_length=100),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name="ProjectionState",
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
                ("ready", models.BooleanField(default=False)),
                ("generation", models.PositiveBigIntegerField(default=0)),
                ("synced_at", models.DateTimeField(null=True)),
            ],
        ),
        migrations.CreateModel(
            name="Entity",
            fields=[
                (
                    "id",
                    models.UUIDField(editable=False, primary_key=True, serialize=False),
                ),
                ("kind", models.CharField(max_length=80)),
                ("source_id", models.CharField(max_length=400)),
                ("label", models.TextField()),
                ("active", models.BooleanField(default=True)),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="Fact",
            fields=[
                (
                    "id",
                    models.UUIDField(editable=False, primary_key=True, serialize=False),
                ),
                ("predicate", models.CharField(max_length=100)),
                ("value", models.JSONField(null=True)),
                ("origin", models.CharField(max_length=24)),
                (
                    "status",
                    models.CharField(db_index=True, default="active", max_length=24),
                ),
                (
                    "object",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="incoming",
                        to="knowledge_graph.entity",
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "subject",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="outgoing",
                        to="knowledge_graph.entity",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="SourceVersion",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("kind", models.CharField(max_length=80)),
                ("source_id", models.CharField(max_length=400)),
                ("fingerprint", models.CharField(max_length=64)),
                ("snapshot", models.JSONField()),
                ("current", models.BooleanField(default=True)),
                ("recorded_at", models.DateTimeField(auto_now_add=True)),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="Derivation",
            fields=[
                (
                    "id",
                    models.UUIDField(editable=False, primary_key=True, serialize=False),
                ),
                ("rule", models.CharField(max_length=120)),
                ("evidence", models.JSONField(default=list)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "inputs",
                    models.ManyToManyField(
                        related_name="derivations", to="knowledge_graph.sourceversion"
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="Support",
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
                (
                    "derivation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="supports",
                        to="knowledge_graph.derivation",
                    ),
                ),
                (
                    "fact",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="supports",
                        to="knowledge_graph.fact",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="entity",
            constraint=models.UniqueConstraint(
                fields=("owner", "kind", "source_id"), name="kg_entity_source"
            ),
        ),
        migrations.AddConstraint(
            model_name="sourceversion",
            constraint=models.UniqueConstraint(
                condition=models.Q(("current", True)),
                fields=("owner", "kind", "source_id"),
                name="kg_current_source",
            ),
        ),
        migrations.AddConstraint(
            model_name="support",
            constraint=models.UniqueConstraint(
                fields=("fact", "derivation"), name="kg_fact_support"
            ),
        ),
    ]
