"""职责：允许保存无法识别外部联系人的 Gmail 邮件。
实现：把 Email.contact 改为可空，其他邮件字段和归组关系保持不变。
关联：对应 apps.crm.models.Email.contact，由 Django migrate 执行。
目录：
- Migration：修改邮件联系人外键约束。
变量索引：
- Migration.dependencies：依赖 crm 初始迁移。
- Migration.operations：将 Email.contact 改为可空外键。
"""

from django.db import migrations, models


# 功能：修改邮件联系人外键约束。
# 逻辑：只把 contact 改为 null=True，已有联系人关系原样保留。
# 约束：回滚到初始迁移前必须先处理 contact 为空的邮件。
class Migration(migrations.Migration):
    dependencies = [("crm", "0001_initial")]

    operations = [
        migrations.AlterField(
            model_name="email",
            name="contact",
            field=models.ForeignKey(
                null=True,
                on_delete=models.PROTECT,
                related_name="messages",
                to="crm.contact",
            ),
        )
    ]
