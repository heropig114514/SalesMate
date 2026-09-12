"""职责：将旧规则抽取转换为新协议的多值事实格式，同时保留原始审计记录。
实现：只为仍是当前版本的 rules-extract-v1 成功记录追加格式转换版本，并递增公司 revision。
关联：0001 的单值 JSON 不兼容新 L2；本迁移不重提取、不改原邮件、不调用模型。
目录：
- migrate_facts：追加格式转换记录并使旧分析过期。
- Migration：声明一次性历史事实升级。
变量索引：
- FACT_FIELDS：旧协议中需要从单值对象转换为数组的十三个字段。
- CONVERTED_VERSION：明确标记格式迁移而非新模型抽取的版本。
- logger：仅记录转换数量的迁移日志。
- Migration.dependencies：依赖员工 Gmail 授权表迁移。
- Migration.operations：事务执行历史数据转换，不提供自动删除审计记录的逆操作。
"""
import logging

from django.db import migrations
from django.db.models import F

FACT_FIELDS = ("contact_name", "contact_title", "company_self_reported", "business_background",
               "employee_scale_hint", "product_need", "quantity", "budget", "delivery_time",
               "decision_process", "concerns", "quote_reference", "order_reference")
CONVERTED_VERSION = "rules-extract-v1+multivalue-v1"
logger = logging.getLogger("salesmate.migrations")


# 功能：追加格式转换记录并使旧分析过期。
# 输入：`apps` 为迁移历史模型注册表；`schema_editor` 提供当前数据库连接别名。
# 输出：无；新增 Extraction、递增受影响公司 revision 并记录数量。
# 逻辑：只转换没有更新抽取的旧规则成功记录；空事实变为空数组，原证据逐字保留。
# 约束：未知字段或畸形对象明确失败，事务回滚；原记录与快照不修改，不覆盖更新版本。
def migrate_facts(apps, schema_editor):
    extraction_model = apps.get_model("crm", "Extraction")
    company_model = apps.get_model("crm", "Company")
    alias = schema_editor.connection.alias
    extractions = extraction_model.objects.using(alias)
    affected = set()
    converted_count = 0
    for old in extractions.filter(prompt_version="rules-extract-v1", status="completed").select_related("email").iterator():
        if extractions.filter(email_id=old.email_id, pk__gt=old.pk).exists():
            continue
        data = old.facts
        expected = set(FACT_FIELDS) | {"has_substantive_update", "message_summary", "intent_hint", "intent_evidence"}
        if not isinstance(data, dict) or set(data) != expected:
            raise ValueError(f"Legacy extraction has an unknown field structure; inspect its schema before migrating. extraction_id={old.pk}")
        result = {key: data[key] for key in ("has_substantive_update", "message_summary", "intent_hint")}
        evidence = data["intent_evidence"]
        if evidence is not None and (not isinstance(evidence, str) or not evidence.strip()):
            raise ValueError(f"Legacy intent evidence is invalid. extraction_id={old.pk}")
        result["intent_evidences"] = [] if evidence is None else [evidence]
        for field in FACT_FIELDS:
            fact = data[field]
            if not isinstance(fact, dict) or set(fact) != {"value", "evidence"}:
                raise ValueError(f"Legacy fact is not a value/evidence object. extraction_id={old.pk}")
            if fact["value"] is None and fact["evidence"] is None:
                result[field] = []
            elif all(isinstance(fact[key], str) and fact[key].strip() for key in ("value", "evidence")):
                result[field] = [{"value": fact["value"], "evidences": [fact["evidence"]]}]
            else:
                raise ValueError(f"Legacy fact has inconsistent value and evidence. extraction_id={old.pk}")
        extractions.create(email_id=old.email_id, prompt_version=CONVERTED_VERSION,
                           status=old.status, facts=result, error=old.error)
        affected.add(old.email.company_id)
        converted_count += 1
    company_model.objects.using(alias).filter(pk__in=affected).update(revision=F("revision") + 1)
    logger.info("legacy_fact_groups_migrated extractions=%s companies=%s", converted_count, len(affected))


# 功能：声明一次性历史事实升级。
# 逻辑：0003 后在迁移事务中追加新格式版本，不重复处理已有更新抽取的邮件。
# 约束：为保留审计记录，不自动逆转此数据迁移；数据库回退须另行制定数据方案。
class Migration(migrations.Migration):
    dependencies = [("crm", "0003_gmailcredential")]
    operations = [migrations.RunPython(migrate_facts)]
