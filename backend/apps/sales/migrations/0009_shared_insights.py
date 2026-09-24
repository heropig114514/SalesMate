"""职责：增加活动时间精度和跨账号 Agent 来源唯一约束。
实现：先报告既有重复，拒绝自动删改；仅将严格匹配旧 Agent 日期协议的记录标为 date，原时间不变。
关联：sales.models 的共享资讯存储；迁移冻结兼容协议，不依赖运行时 Agent 或日期辅助代码。
目录：
- prepare_insights：检查历史重复并标记明确的日期占位记录。
- Migration：声明新增字段、数据校验和唯一约束。
变量索引：
- Migration.dependencies：依赖现有资讯来源字段。
- Migration.operations：新增精度、校验和数据库约束。
"""

from datetime import time, timezone

from django.db import migrations, models
from django.db.models import Count


# 功能：为数据库约束准备既有数据。
# 输入：`apps` 历史模型注册表、`schema_editor` 当前数据库迁移连接。
# 输出：无；存在重复时抛 RuntimeError 并在原子迁移中回滚。
# 逻辑：分别检查新闻 URL、活动 URL 与开始时间的重复；匹配完整 Agent 日期标记及 UTC 中午递增边界才设置 date。
# 约束：不删除/合并/归档记录，不改正文、时间和 revision；重复诊断仅输出模型和组数，不输出来源正文。
def prepare_insights(apps, schema_editor):
    database = schema_editor.connection.alias
    for name, fields in (("WorldNews", ["source_url"]), ("WorldEvent", ["source_url", "starts_at"])):
        model = apps.get_model("sales", name)
        duplicates = model.objects.using(database).filter(data_source="agent").exclude(source_url="").values(*fields).annotate(total=Count("pk")).filter(total__gt=1)
        count = duplicates.count()
        if count:
            raise RuntimeError(f"{name} 存在 {count} 组重复 Agent 来源；请人工核对后重新迁移，未自动删除或合并。")
    model = apps.get_model("sales", "WorldEvent")
    marker = "来源仅提供日期；起止钟点是系统占位值，请以来源页为准。"
    for event in model.objects.using(database).filter(data_source="agent", description__contains=marker).iterator():
        if (marker in event.description.splitlines()
                and event.starts_at.astimezone(timezone.utc).time() == time(12)
                and event.ends_at.astimezone(timezone.utc).time() == time(12)
                and event.ends_at > event.starts_at):
            model.objects.using(database).filter(pk=event.pk).update(time_precision="date")


# 功能：安装共享资讯必要的存储契约。
# 逻辑：原子新增字段后检查数据再加约束；反向移除约束与新增字段，原时间未变。
# 约束：实际应用由部署迁移执行；不安装采集服务或调用外网。
class Migration(migrations.Migration):
    dependencies = [("sales", "0008_development_support")]
    operations = [
        migrations.AddField(model_name="worldevent", name="time_precision", field=models.CharField(choices=[("datetime", "确切时间"), ("date", "仅日期")], default="datetime", max_length=8)),
        migrations.RunPython(prepare_insights, migrations.RunPython.noop),
        migrations.AddConstraint(model_name="worldnews", constraint=models.UniqueConstraint(fields=["source_url"], condition=models.Q(data_source="agent") & ~models.Q(source_url=""), name="world_news_agent_source")),
        migrations.AddConstraint(model_name="worldevent", constraint=models.UniqueConstraint(fields=["source_url", "starts_at"], condition=models.Q(data_source="agent") & ~models.Q(source_url=""), name="world_event_agent_source_start")),
    ]
