"""职责：创建员工 Gmail 授权凭证表。
实现：增加与 Mailbox 一对一的 JSON 凭证及授权和更新时间。
关联：gmail_oauth 服务写入本表，删除 Mailbox 时级联删除凭证。
目录：
- Migration：声明 0003 数据库迁移。
变量索引：
- Migration.dependencies：依赖可空邮件联系人的 0002 迁移。
- Migration.operations：创建 GmailCredential 表。
"""

from django.db import migrations, models
import django.db.models.deletion


# 功能：声明 GmailCredential 表结构迁移。
# 输入：Django migration executor 提供历史 apps 和 schema editor。
# 输出：创建一张与 Mailbox 一对一的授权表。
# 逻辑：在 0002 后执行 CreateModel。
# 约束：只描述结构，不包含真实 Google 凭证数据。
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
