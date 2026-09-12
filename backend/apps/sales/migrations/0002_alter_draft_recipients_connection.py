"""职责：建立销售关系记录和数据库约束。
实现：Django 声明式建表和字段变更，依赖原 crm 公司及账号表。
关联：apps.sales.models 为当前定义；不修改既有公司和邮件数据。
目录：
- Migration：销售数据库迁移。
变量索引：
- Migration.dependencies：前置迁移及可替换用户模型依赖。
- Migration.operations：新增表、字段及唯一性、金额约束操作。
"""

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


# 功能：以声明式操作更新销售数据库。
# 逻辑：迁移图保证依赖顺序，操作使用 Django 标准回滚定义。
# 约束：回滚建表会删除该版本新增业务数据；上线后须按备份流程处理。
class Migration(migrations.Migration):
    dependencies = [
        ("sales", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="draft",
            name="recipients",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.CreateModel(
            name="Connection",
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
                ("revision", models.PositiveIntegerField(default=0)),
                ("archived", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "provider",
                    models.CharField(
                        choices=[("gmail", "Gmail 发信"), ("calendar", "Google 日历")],
                        max_length=16,
                    ),
                ),
                ("account", models.CharField(max_length=320)),
                ("encrypted_credentials", models.TextField()),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("owner", "provider", "account"),
                        name="sales_connection_identity",
                    )
                ],
            },
        ),
    ]
