"""职责：建立聊天请求、引用与内部知识关系表。
实现：约束随各新表一起声明；仅新增 chat 表，保留现有会话、消息与 L1–L4 数据。
关联：依赖 crm/sales 当前迁移及可替换用户模型。
目录：
- Migration：初始聊天 Schema。
变量索引：
- Migration.initial：声明应用初始迁移。
- Migration.dependencies：关联模型已存在的前置版本。
- Migration.operations：建表及活动会话、引用位置、知识版本约束。
"""

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


# 功能：创建聊天适配所需持久结构。
# 逻辑：Django 按声明顺序创建含约束的新表，满足保守在线迁移门禁。
# 约束：首次发布前整理初始迁移，不改变约束语义，不迁移或补造既有历史问题、答案和知识。
class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("crm", "0008_mailboxsyncrun_sync_options"),
        ("sales", "0004_qq_smtp_send"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AnswerRequest",
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
                (
                    "status",
                    models.CharField(db_index=True, default="pending", max_length=20),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("processing_started_at", models.DateTimeField(null=True)),
                ("finished_at", models.DateTimeField(null=True)),
                ("recent_history", models.JSONField(default=list)),
                ("context_snapshot", models.JSONField(null=True)),
                ("result", models.JSONField(null=True)),
                ("chat_prompt_version", models.CharField(blank=True, max_length=100)),
                ("error", models.JSONField(null=True)),
                (
                    "assistant_message",
                    models.OneToOneField(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="chat_answer",
                        to="sales.message",
                    ),
                ),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="crm.company"
                    ),
                ),
                (
                    "conversation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="sales.conversation",
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "retry_of",
                    models.OneToOneField(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="retry_request",
                        to="chat.answerrequest",
                    ),
                ),
                (
                    "user_message",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="chat_requests",
                        to="sales.message",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("status__in", ["pending", "processing"])),
                        fields=("conversation",),
                        name="chat_one_active_conversation",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("status__in", ["pending", "processing", "completed", "failed"])),
                        name="chat_request_status",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="Citation",
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
                ("position", models.PositiveIntegerField()),
                ("source_id", models.TextField()),
                ("source_type", models.CharField(max_length=80)),
                ("title_or_label", models.TextField()),
                ("content", models.TextField()),
                (
                    "request",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="citations",
                        to="chat.answerrequest",
                    ),
                ),
            ],
            options={
                "constraints": [models.UniqueConstraint(
                    fields=("request", "position"), name="chat_citation_position"
                )],
            },
        ),
        migrations.CreateModel(
            name="KnowledgeEntry",
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
                ("source_key", models.CharField(max_length=160)),
                ("version", models.CharField(max_length=80)),
                ("title", models.CharField(max_length=240)),
                ("content", models.TextField()),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "constraints": [models.UniqueConstraint(
                    fields=("owner", "source_key", "version"), name="chat_knowledge_version"
                )],
            },
        ),
    ]
