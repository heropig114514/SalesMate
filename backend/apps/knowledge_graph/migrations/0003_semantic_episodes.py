"""Responsibility: Add external observations and extend transactional capture to all business tables.
Implementation: Freeze the model catalog and ownership SQL, covering owner primary keys and indirect ownership; request explicit version backfill for existing accounts after installation.
Relationships: Depend on all controlled source tables; runtime business_schema remains consistent with this catalog.
Directory:
- install: Upgrade ownership resolution and install additional triggers.
- uninstall: Remove added capture and restore previous functions.
- Migration: Observation model and transactional capture migration.
Variable index:
- SOURCES: Frozen business-model list and external sources for this migration.
- OWNER_SQL: PostgreSQL function extending indirect ownership resolution.
- Migration.dependencies: Migration dependencies for all source tables.
- Migration.operations: New model and reversible capture operations.
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


# Function: Extend business-table event capture.
# Inputs: `apps`: migration model registry; `schema_editor`: database schema editor.
# Outputs: None; install triggers, replace ownership functions, and queue account backfill.
# Logic: Retain triggers on the initial ten tables and add others; source_key supports owner primary keys in the same transaction as source writes.
# Constraints: SQLite creates models only and still rejects runtime execution; do not call LLMs, read original text, or build the graph during migration.
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


# Function: Restore capture scope preceding this migration.
# Inputs: `apps`: historical models; `schema_editor`: database schema editor.
# Outputs: None; remove new triggers and restore prior ownership and row-capture functions.
# Logic: Reverse only sources added by this migration without deleting business records.
# Constraints: Rollback code must match database version; SQLite performs no capture operations.
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


# Function: Create the external-observation table and complete business-source capture.
# Logic: Create the source model before installing triggers; reverse in opposite order.
# Constraints: Require existing source tables; do not invoke models or import business data.
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
