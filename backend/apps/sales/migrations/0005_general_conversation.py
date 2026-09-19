"""职责：允许 conversation 不绑定客户以支持私有通用聊天。
实现：只解除 company 外键的非空约束；现有客户关系与删除策略保留。
关联：sales.models 对应可空字段；部署时需先应用迁移。
目录：
- Migration：通用聊天的可空客户关系。
变量索引：
- Migration.dependencies：本应用的前序迁移。
- Migration.operations：只调整 company 的 null/blank 元数据与数据库约束。
"""

import django.db.models.deletion
from django.db import migrations, models


# 功能：解除客户必填约束。
# 逻辑：AlterField 保留现有外键及记录，仅允许新增无客户会话。
# 约束：回滚为必填前须先处理无客户记录，不自动删除用户历史。
class Migration(migrations.Migration):
    dependencies = [("sales", "0004_qq_smtp_send")]
    operations = [
        migrations.AlterField(
            model_name="conversation",
            name="company",
            field=models.ForeignKey(
                to="crm.company",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="conversations",
                null=True,
                blank=True,
            ),
        )
    ]
