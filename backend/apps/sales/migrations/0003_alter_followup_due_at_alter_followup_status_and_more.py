"""职责：为销售状态队列和到期跟进建立查询索引。
实现：通过 AlterField 为现有状态及 due_at 字段增加索引，不改变数据值或默认状态。
关联：sales_worker 和业务列表查询使用这些字段。
目录：
- Migration：状态与时间索引迁移。
变量索引：
- Migration.dependencies：依赖销售连接迁移。
- Migration.operations：索引字段变更清单。
"""

from django.db import migrations, models


# 功能：为状态和到期时间添加数据库索引。
# 逻辑：由 Django 创建索引，字段取值和默认值保持不变。
# 约束：大表建索引的部署耗时需由实际数据库规模评估。
class Migration(migrations.Migration):
    dependencies = [
        ("sales", "0002_alter_draft_recipients_connection"),
    ]

    operations = [
        migrations.AlterField(
            model_name="followup",
            name="due_at",
            field=models.DateTimeField(db_index=True),
        ),
        migrations.AlterField(
            model_name="followup",
            name="status",
            field=models.CharField(db_index=True, default="open", max_length=20),
        ),
        migrations.AlterField(
            model_name="opportunity",
            name="status",
            field=models.CharField(db_index=True, default="new", max_length=20),
        ),
        migrations.AlterField(
            model_name="quote",
            name="status",
            field=models.CharField(db_index=True, default="draft", max_length=20),
        ),
        migrations.AlterField(
            model_name="salesorder",
            name="status",
            field=models.CharField(db_index=True, default="draft", max_length=20),
        ),
        migrations.AlterField(
            model_name="ticket",
            name="status",
            field=models.CharField(db_index=True, default="open", max_length=20),
        ),
        migrations.AlterField(
            model_name="toolaction",
            name="status",
            field=models.CharField(
                db_index=True, default="pending_confirmation", max_length=32
            ),
        ),
    ]
