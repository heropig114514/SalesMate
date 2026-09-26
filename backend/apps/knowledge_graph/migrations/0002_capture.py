"""职责：为首版十类业务来源安装 PostgreSQL 事务变更捕获。
实现：行级 AFTER 触发器记录 old/new 所有者；TRUNCATE 请求现存用户重建；安装时排队存量回填。
关联：0001 创建事件与图谱表；同步器检查全部触发器，非 PostgreSQL 明确不支持图谱运行。
目录：
- install：安装来源触发器并登记初始事件。
- uninstall：移除触发器和函数。
- Migration：声明依赖与可逆操作。
变量索引：
- SOURCES：冻结的数据库表名、模型标签映射。
- OWNER_SQL：解析归属外键的数据库函数。
- CAPTURE_SQL：同事务记录行变更和清表事件的函数。
- Migration.dependencies：图谱及来源表的迁移依赖。
- Migration.operations：安装与反向移除捕获。
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


# 功能：安装源表捕获并请求首次回填。
# 输入：`apps` 为迁移模型注册表；`schema_editor` 为数据库编辑器。
# 输出：无；创建函数、触发器与初始事件。
# 逻辑：硬删除级联由被监控上级公司/邮箱事件覆盖；只记录身份，不保存原始行 JSON。
# 约束：SQLite 仅建表不执行捕获 SQL；运行 API/Worker 明确拒绝，无替代实现。
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


# 功能：撤销本版捕获安装。
# 输入：`apps` 为迁移注册表；`schema_editor` 为数据库编辑器。
# 输出：无；删除本迁移触发器及函数。
# 逻辑：先解除触发器，再删除所依赖函数。
# 约束：不删除业务记录；图谱因缺少捕获而不可作为当前结果读取。
def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for table, _ in SOURCES:
        quoted = schema_editor.quote_name(table)
        schema_editor.execute(f"DROP TRIGGER salesmate_kg_capture ON {quoted}")
        schema_editor.execute(f"DROP TRIGGER salesmate_kg_truncate ON {quoted}")
    schema_editor.execute("DROP FUNCTION salesmate_kg_capture_row(); DROP FUNCTION salesmate_kg_capture_truncate(); DROP FUNCTION salesmate_kg_owner(text,jsonb);")


# 功能：将事务捕获接入已有来源模型。
# 逻辑：等待图谱和来源表就绪后安装可逆触发器。
# 约束：不调用同步器或外部模型，不在迁移里重算图谱。
class Migration(migrations.Migration):
    dependencies = [("knowledge_graph", "0001_initial"), ("crm", "0008_mailboxsyncrun_sync_options"), ("sales", "0008_development_support")]
    operations = [migrations.RunPython(install, uninstall)]
