"""Responsibility: Add event time precision and cross-account Agent source uniqueness constraints.
Implementation: Report existing duplicates first and reject automatic edits/deletion; mark only strict legacy Agent date-protocol matches as date, retaining original timestamps.
Relationships: Shared insight storage in sales.models; freeze the compatibility protocol in the migration without runtime Agent/date-helper dependencies.
Directory:
- prepare_insights: Check historical duplicates and mark explicit date-placeholder records.
- Migration: Declare the new field, data checks, and uniqueness constraints.
Variable index:
- Migration.dependencies: Depend on existing insight source fields.
- Migration.operations: Add precision, validation, and database constraints.
"""

from datetime import time, timezone

from django.db import migrations, models
from django.db.models import Count


# Function: Prepare existing data for database constraints.
# Inputs: `apps`: historical model registry; `schema_editor`: current migration database connection.
# Outputs: None; duplicates raise RuntimeError and roll back the atomic migration.
# Logic: Check news URL duplicates and event URL/start-time duplicates separately; set date only for complete Agent date markers with increasing UTC-noon boundaries.
# Constraints: Do not delete, merge, archive, or change content, timestamps, or revisions. Duplicate diagnostics contain only model and group count, never source content.
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


# Function: Install storage contracts required by shared insights.
# Logic: Atomically add fields, validate data, then add constraints; reversal removes constraints and new fields, leaving original timestamps unchanged.
# Constraints: Deployment migrations perform actual application; no collection-service installation or external calls.
class Migration(migrations.Migration):
    dependencies = [("sales", "0008_development_support")]
    operations = [
        migrations.AddField(model_name="worldevent", name="time_precision", field=models.CharField(choices=[("datetime", "确切时间"), ("date", "仅日期")], default="datetime", max_length=8)),
        migrations.RunPython(prepare_insights, migrations.RunPython.noop),
        migrations.AddConstraint(model_name="worldnews", constraint=models.UniqueConstraint(fields=["source_url"], condition=models.Q(data_source="agent") & ~models.Q(source_url=""), name="world_news_agent_source")),
        migrations.AddConstraint(model_name="worldevent", constraint=models.UniqueConstraint(fields=["source_url", "starts_at"], condition=models.Q(data_source="agent") & ~models.Q(source_url=""), name="world_event_agent_source_start")),
    ]
