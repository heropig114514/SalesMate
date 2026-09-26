"""职责：新增外部观察并扩展全业务表事务捕获。
实现：冻结模型目录与归属 SQL；覆盖 owner 主键和间接归属；安装后为既有账号请求显式版本回填。
关联：依赖所有受控来源表，运行代码的 business_schema 与此目录保持一致。
目录：
- install：升级归属解析并安装新增触发器。
- uninstall：删除新增捕获并恢复旧函数。
- Migration：观察模型及事务捕获迁移。
变量索引：
- SOURCES：此迁移冻结的业务模型列表及外部来源。
- OWNER_SQL：扩展间接归属的 PostgreSQL 函数。
- Migration.dependencies：全部来源表的迁移依赖。
- Migration.operations：新增模型及可逆捕获操作。
"""

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models
from importlib import import_module

SOURCES = (
    "accounts.companyprofile", "accounts.salessetup", "accounts.setupdocument", "crm.company", "crm.contact", "crm.mailbox",
    "crm.email", "crm.storedmessage", "crm.extraction", "crm.analysisinput", "crm.analysis", "crm.score", "crm.snapshotsource", "crm.snapshotinvalidation",
    "sales.companysettings", "sales.companyalias", "sales.contactprofile", "sales.team", "sales.membership", "sales.companygrant",
    "sales.product", "sales.ticket", "sales.opportunity", "sales.sellerprofile", "sales.quote", "sales.quoteline", "sales.salesorder",
    "sales.orderline", "sales.followup", "sales.conversation", "sales.message", "sales.draft", "sales.toolaction", "sales.attachment",
    "sales.auditevent", "sales.notification", "sales.connection", "sales.worldevent", "sales.worldnews", "sales.opportunitysignal",
    "sales.opportunitypriority", "vectors.vectordocument", "chat.knowledgeentry", "chat.answerrequest", "chat.citation", "chat.toolread",
    "agent_tools.toolcall", "agent_tools.toolproposal", "knowledge_graph.episode",
)
OWNER_SQL = """
CREATE OR REPLACE FUNCTION salesmate_kg_owner(kind text, row_data jsonb) RETURNS bigint
LANGUAGE plpgsql STABLE AS $$
DECLARE result bigint;
BEGIN
  IF row_data IS NULL THEN RETURN NULL; END IF;
  IF row_data ? 'owner_id' THEN RETURN (row_data->>'owner_id')::bigint; END IF;
  IF kind IN ('crm.contact', 'crm.analysisinput') THEN
    SELECT owner_id INTO result FROM crm_company WHERE id = (row_data->>'company_id')::uuid;
  ELSIF kind IN ('crm.email', 'crm.storedmessage') THEN
    SELECT owner_id INTO result FROM crm_mailbox WHERE id = (row_data->>'mailbox_id')::uuid;
  ELSIF kind = 'crm.extraction' THEN
    SELECT m.owner_id INTO result FROM crm_email e JOIN crm_mailbox m ON m.id=e.mailbox_id WHERE e.dedupe_key=row_data->>'email_id';
  ELSIF kind IN ('crm.analysis', 'crm.snapshotsource', 'crm.snapshotinvalidation') THEN
    SELECT c.owner_id INTO result FROM crm_analysisinput s JOIN crm_company c ON c.id=s.company_id WHERE s.id=(row_data->>'snapshot_id')::bigint;
  ELSIF kind = 'crm.score' THEN
    SELECT c.owner_id INTO result FROM crm_analysis a JOIN crm_analysisinput s ON s.id=a.snapshot_id JOIN crm_company c ON c.id=s.company_id WHERE a.id=(row_data->>'analysis_id')::bigint;
  ELSIF kind IN ('chat.citation', 'chat.toolread') THEN
    SELECT owner_id INTO result FROM chat_answerrequest WHERE id=(row_data->>'request_id')::uuid;
  END IF;
  RETURN result;
END $$;
"""


# 功能：扩展业务表事件捕获。
# 输入：`apps` 为迁移模型注册表；`schema_editor` 为数据库编辑器。
# 输出：无；安装触发器、替换归属函数并为账号排队回填。
# 逻辑：旧十表保留触发器，其余表新增；source_key 支持 owner 主键；与来源写入同事务。
# 约束：SQLite 仅建模型，运行仍拒绝；不调用 LLM，不在迁移中读取原文或构图。
def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    old = import_module("apps.knowledge_graph.migrations.0002_capture")
    schema_editor.execute(OWNER_SQL)
    row_sql = old.CAPTURE_SQL.split("CREATE FUNCTION salesmate_kg_capture_truncate")[0]
    row_sql = row_sql.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION").replace("before_data->>'dedupe_key');", "before_data->>'dedupe_key', after_data->>'owner_id', before_data->>'owner_id');")
    schema_editor.execute(row_sql)
    previous = {kind for _, kind in old.SOURCES}
    for kind in SOURCES:
        if kind not in previous:
            table = schema_editor.quote_name(apps.get_model(kind)._meta.db_table)
            schema_editor.execute(f"CREATE TRIGGER salesmate_kg_capture AFTER INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION salesmate_kg_capture_row('{kind}')")
            schema_editor.execute(f"CREATE TRIGGER salesmate_kg_truncate AFTER TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION salesmate_kg_capture_truncate('{kind}')")
    schema_editor.execute("INSERT INTO knowledge_graph_change(owner_id,kind,source_id,operation,status,error_code,created_at) SELECT id,'graph.backfill','*','BACKFILL','pending','',clock_timestamp() FROM accounts_user")


# 功能：恢复本迁移之前的捕获范围。
# 输入：`apps` 为历史模型；`schema_editor` 为数据库编辑器。
# 输出：无；移除新增触发器，恢复旧归属与行捕获函数。
# 逻辑：只撤销本迁移增加的来源，不删除业务记录。
# 约束：回滚代码需与数据库版本一致；SQLite 无捕获操作。
def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    old = import_module("apps.knowledge_graph.migrations.0002_capture")
    previous = {kind for _, kind in old.SOURCES}
    for kind in SOURCES:
        if kind not in previous:
            table = schema_editor.quote_name(apps.get_model(kind)._meta.db_table)
            schema_editor.execute(f"DROP TRIGGER salesmate_kg_capture ON {table}; DROP TRIGGER salesmate_kg_truncate ON {table}")
    schema_editor.execute(old.OWNER_SQL.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"))
    schema_editor.execute(old.CAPTURE_SQL.split("CREATE FUNCTION salesmate_kg_capture_truncate")[0].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"))


# 功能：建立外部观察表和完整业务来源捕获。
# 逻辑：先创建来源模型，再安装触发器，逆序回滚。
# 约束：依赖来源表已存在，不执行模型调用或业务数据导入。
class Migration(migrations.Migration):

    dependencies = [
        ("knowledge_graph", "0002_capture"),
        ("accounts", "0004_accountreset"),
        ("sales", "0010_news_signal_fields"),
        ("chat", "0003_tool_read"),
        ("vectors", "0001_initial"),
        ("agent_tools", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Episode",
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
                ("source_key", models.CharField(max_length=200)),
                ("text", models.TextField()),
                ("observed_at", models.DateTimeField()),
                ("extraction", models.JSONField()),
                ("model_audit", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("retracted", models.BooleanField(default=False)),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("owner", "source_key"), name="kg_episode_source"
                    )
                ],
            },
        ),
        migrations.RunPython(install, uninstall),
    ]
