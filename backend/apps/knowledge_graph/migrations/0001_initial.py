"""职责：创建图谱实体、来源版本、事实、支持路径、事件及状态表。
实现：由 Django 从首版模型生成结构；来源身份和当前版本由唯一约束保护。
关联：0002 安装 PostgreSQL 捕获；不修改业务源表内容。
目录：
- Migration：首版图谱表结构。
变量索引：
- Migration.initial：标识初始结构迁移。
- Migration.dependencies：账号及可替换用户模型依赖。
- Migration.operations：七种图谱模型和唯一约束的创建操作。
"""

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


# 功能：创建派生图谱存储。
# 逻辑：表、关系和索引与 models 首版契约一致。
# 约束：不安装触发器、不回填业务数据。
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
