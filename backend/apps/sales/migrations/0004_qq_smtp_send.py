"""职责：允许独立 QQ 发信连接和 qq.send 动作。
实现：扩展枚举选项，保留原 Gmail 和日历值及既有数据。
关联：sales.models 的 Connection 与 ToolAction；不修改 QQ 只读凭证表。
目录：
- Migration：声明 QQ 发信枚举变更。
变量索引：
- Migration.dependencies：既有 sales.0003 迁移。
- Migration.operations：扩展 provider 和 tool 的 choices。
"""

from django.db import migrations, models


# 功能：同步 QQ 发信模型枚举。
# 逻辑：仅增加 choices，保留既有字段与数据。
# 约束：不创建连接或发送邮件。
class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0003_alter_followup_due_at_alter_followup_status_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="connection",
            name="provider",
            field=models.CharField(
                choices=[
                    ("gmail", "Gmail 发信"),
                    ("qq", "QQ 发信"),
                    ("calendar", "Google 日历"),
                ],
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="toolaction",
            name="tool",
            field=models.CharField(
                choices=[
                    ("gmail.send", "Gmail 发信"),
                    ("qq.send", "QQ 发信"),
                    ("calendar.create", "创建日历会议"),
                ],
                max_length=40,
            ),
        ),
    ]
