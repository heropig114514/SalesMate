"""职责：建立业务工具授权、回执与确认提案表。
实现：仅新建模型及其唯一约束；不迁移或修改既有业务数据。
关联：agent_tools.models；依赖可替换用户模型。
目录：
- Migration：初始建表操作。
变量索引：
- Migration.initial：初始迁移标记。
- Migration.dependencies：用户表依赖。
- Migration.operations：三个模型及回执唯一约束。
"""

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


# 功能：新增工具持久化结构。
# 逻辑：新表及内联约束。
# 约束：回退会删除这些新表中的记录。
class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ToolCredential",
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
                ("name", models.CharField(max_length=120)),
                ("digest", models.CharField(max_length=64, unique=True)),
                ("allowed_tools", models.JSONField()),
                ("expires_at", models.DateTimeField()),
                ("revoked_at", models.DateTimeField(null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
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
            name="ToolProposal",
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
                ("tool", models.CharField(max_length=120)),
                ("arguments", models.JSONField()),
                ("status", models.CharField(default="pending", max_length=16)),
                ("result", models.JSONField(null=True)),
                ("expires_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "credential",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="agent_tools.toolcredential",
                    ),
                ),
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
            name="ToolCall",
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
                ("key", models.UUIDField()),
                ("tool", models.CharField(max_length=120)),
                ("input_hash", models.CharField(max_length=64)),
                ("result", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
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
                        fields=("owner", "key"), name="agent_tools_owner_call_key"
                    )
                ],
            },
        ),
    ]
