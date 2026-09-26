"""Responsibility: Install PostgreSQL transactional change capture for the initial ten business source types.
Implementation: Row-level AFTER triggers record old/new owners; TRUNCATE requests rebuilding for existing users; installation queues initial backfill.
Relationships: 0001 creates graph/event tables; the synchronizer checks all triggers and explicitly rejects graph execution outside PostgreSQL.
Directory:
- install: Install source triggers and register initial events.
- uninstall: Remove triggers and functions.
- Migration: Declare dependencies and reversible operations.
Variable index:
- SOURCES: Frozen database table-name and model-label mapping.
- OWNER_SQL: Database function resolving ownership foreign keys.
- CAPTURE_SQL: Functions recording row changes and truncation events in the same transaction.
- Migration.dependencies: Migration dependencies for graph and source tables.
- Migration.operations: Install capture and remove it on reversal.
"""
from django.db import migrations

SOURCES = (
    ("crm_company", "crm.company"), ("crm_contact", "crm.contact"),
    ("crm_mailbox", "crm.mailbox"), ("crm_email", "crm.email"),
    ("crm_extraction", "crm.extraction"), ("sales_companysettings", "sales.companysettings"),
    ("sales_product", "sales.product"), ("sales_opportunity", "sales.opportunity"),
    ("sales_salesorder", "sales.salesorder"), ("sales_orderline", "sales.orderline"),
)
OWNER_SQL = """
CREATE FUNCTION salesmate_kg_owner(kind text, row_data jsonb) RETURNS bigint
LANGUAGE plpgsql STABLE AS $$
DECLARE result bigint;
BEGIN
  IF row_data IS NULL THEN RETURN NULL; END IF;
  IF row_data ? 'owner_id' THEN RETURN (row_data->>'owner_id')::bigint; END IF;
  IF kind = 'crm.contact' THEN
    SELECT owner_id INTO result FROM crm_company WHERE id = (row_data->>'company_id')::uuid;
  ELSIF kind = 'crm.email' THEN
    SELECT owner_id INTO result FROM crm_mailbox WHERE id = (row_data->>'mailbox_id')::uuid;
  ELSIF kind = 'crm.extraction' THEN
    SELECT m.owner_id INTO result FROM crm_email e JOIN crm_mailbox m ON m.id = e.mailbox_id
      WHERE e.dedupe_key = row_data->>'email_id';
  END IF;
  RETURN result;
END $$;
"""
CAPTURE_SQL = """
CREATE FUNCTION salesmate_kg_capture_row() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE before_data jsonb; after_data jsonb; owner_key bigint; source_key text;
BEGIN
  IF TG_OP = 'UPDATE' AND OLD IS NOT DISTINCT FROM NEW THEN RETURN NULL; END IF;
  IF TG_OP <> 'INSERT' THEN before_data := to_jsonb(OLD); END IF;
  IF TG_OP <> 'DELETE' THEN after_data := to_jsonb(NEW); END IF;
  source_key := COALESCE(after_data->>'id', after_data->>'dedupe_key', before_data->>'id', before_data->>'dedupe_key');
  FOR owner_key IN SELECT DISTINCT value FROM unnest(ARRAY[
      salesmate_kg_owner(TG_ARGV[0], before_data), salesmate_kg_owner(TG_ARGV[0], after_data)
    ]) AS value WHERE value IS NOT NULL
  LOOP
    INSERT INTO knowledge_graph_change(owner_id, kind, source_id, operation, status, error_code, created_at)
      VALUES(owner_key, TG_ARGV[0], source_key, TG_OP, 'pending', '', clock_timestamp());
  END LOOP;
  RETURN NULL;
END $$;
CREATE FUNCTION salesmate_kg_capture_truncate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO knowledge_graph_change(owner_id, kind, source_id, operation, status, error_code, created_at)
    SELECT id, TG_ARGV[0], '*', 'TRUNCATE', 'pending', '', clock_timestamp() FROM accounts_user;
  RETURN NULL;
END $$;
"""


# Function: Install source-table capture and request initial backfill.
# Inputs: `apps`: migration model registry; `schema_editor`: database schema editor.
# Outputs: None; create functions, triggers, and initial events.
# Logic: Monitored parent company/mailbox events cover hard-delete cascades; record only identity, not original row JSON.
# Constraints: SQLite creates tables without capture SQL; APIs/workers explicitly reject execution, with no substitute implementation.
def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(OWNER_SQL)
    schema_editor.execute(CAPTURE_SQL)
    for table, kind in SOURCES:
        quoted = schema_editor.quote_name(table)
        schema_editor.execute(f"CREATE TRIGGER salesmate_kg_capture AFTER INSERT OR UPDATE OR DELETE ON {quoted} FOR EACH ROW EXECUTE FUNCTION salesmate_kg_capture_row('{kind}')")
        schema_editor.execute(f"CREATE TRIGGER salesmate_kg_truncate AFTER TRUNCATE ON {quoted} FOR EACH STATEMENT EXECUTE FUNCTION salesmate_kg_capture_truncate('{kind}')")
    user_model = apps.get_model("accounts", "User")
    change_model = apps.get_model("knowledge_graph", "Change")
    alias = schema_editor.connection.alias
    change_model.objects.using(alias).bulk_create([change_model(owner_id=key, kind="graph.backfill", source_id=str(key), operation="BACKFILL") for key in user_model.objects.using(alias).values_list("pk", flat=True)])


# Function: Reverse this version's capture installation.
# Inputs: `apps`: migration registry; `schema_editor`: database schema editor.
# Outputs: None; delete this migration's triggers and functions.
# Logic: Detach triggers before deleting their dependent functions.
# Constraints: Do not delete business records; without capture, the graph cannot be read as current results.
def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for table, _ in SOURCES:
        quoted = schema_editor.quote_name(table)
        schema_editor.execute(f"DROP TRIGGER salesmate_kg_capture ON {quoted}")
        schema_editor.execute(f"DROP TRIGGER salesmate_kg_truncate ON {quoted}")
    schema_editor.execute("DROP FUNCTION salesmate_kg_capture_row(); DROP FUNCTION salesmate_kg_capture_truncate(); DROP FUNCTION salesmate_kg_owner(text,jsonb);")


# Function: Connect transactional capture to existing source models.
# Logic: Install reversible triggers after graph and source tables are ready.
# Constraints: Do not invoke the synchronizer or external models, or recompute the graph during migration.
class Migration(migrations.Migration):
    dependencies = [("knowledge_graph", "0001_initial"), ("crm", "0008_mailboxsyncrun_sync_options"), ("sales", "0008_development_support")]
    operations = [migrations.RunPython(install, uninstall)]
