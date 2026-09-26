"""Responsibility: Convert legacy rule extractions to the new multi-value fact format while preserving original audit records.
Implementation: Append a format-conversion version only for current successful rules-extract-v1 records and increment company revision.
Relationships: The single-value JSON in 0001 is incompatible with the new L2; this migration does not re-extract, alter source emails, or call a model.
Directory:
- migrate_facts: Append format-conversion records and invalidate prior analyses.
- Migration: Declares the one-time historical fact upgrade.
Variable index:
- FACT_FIELDS: Thirteen legacy-protocol fields that must change from single-value objects to arrays.
- CONVERTED_VERSION: Version that explicitly identifies a format migration rather than a new model extraction.
- logger: Migration logger that records conversion counts only.
- Migration.dependencies: Dependency on the employee Gmail authorization-table migration.
- Migration.operations: Runs the historical-data conversion transactionally, with no reverse operation that deletes audit records.
"""
import logging

from django.db import migrations
from django.db.models import F

FACT_FIELDS = ("contact_name", "contact_title", "company_self_reported", "business_background",
               "employee_scale_hint", "product_need", "quantity", "budget", "delivery_time",
               "decision_process", "concerns", "quote_reference", "order_reference")
CONVERTED_VERSION = "rules-extract-v1+multivalue-v1"
logger = logging.getLogger("salesmate.migrations")


# Function: Append format-conversion records and invalidate prior analyses.
# Inputs: `apps` is the migration historical-model registry; `schema_editor` supplies the current database connection alias.
# Outputs: None; creates Extraction records, increments affected company revisions, and logs the count.
# Logic: Convert only legacy successful rule records with no newer extraction; turn empty facts into empty arrays and preserve source evidence verbatim.
# Constraints: Unknown fields or malformed objects fail explicitly and roll back the transaction; original records and snapshots remain unchanged and newer versions are not overwritten.
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


# Function: Declare the one-time historical fact upgrade.
# Logic: Append a new-format version in the migration transaction after 0003 without reprocessing emails that already have a newer extraction.
# Constraints: This data migration is not automatically reversed to preserve audit records; database rollback requires a separate data plan.
class Migration(migrations.Migration):
    dependencies = [("crm", "0003_gmailcredential")]
    operations = [migrations.RunPython(migrate_facts)]
