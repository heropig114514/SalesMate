"""职责：为每个邮箱批次保存冻结的同步范围。
实现：添加 JSON 字段；旧批次和 Gmail 默认空对象，不猜测历史选择。
关联：依赖 crm.0007；processing_models 定义对应字段。
目录：
- Migration：新增批次范围字段。
变量索引：
- Migration.dependencies：QQ 表结构前置迁移。
- Migration.operations：可逆地新增 sync_options。
"""
from django.db import migrations, models


# 功能：新增批次范围快照。
# 逻辑：数据库添加 JSON 字段，历史记录赋空对象。
# 约束：不修改凭证、邮件或既有 Gmail 同步状态。
class Migration(migrations.Migration):
    dependencies = [("crm", "0007_qq_mailbox")]
    operations = [migrations.AddField(model_name="mailboxsyncrun", name="sync_options", field=models.JSONField(default=dict))]
