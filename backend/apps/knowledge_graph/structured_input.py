"""Responsibility: Convert incomplete records submitted under the business schema into graph observations.
Implementation: Validate only supplied fields and preserve unknowns; link existing entities by explicit IDs or unique same-type identity matches, creating traceable placeholders for missing foreign-key targets.
Relationships: business_schema is the sole field contract; episodes stores original JSON and episode_projection links authoritative records that appear later.
Directory:
- build_records: Validate and generate entity, attribute, and foreign-key observations.
Variable index:
- IDENTITY_FIELDS: Business identity fields for deterministic candidate alignment; name matching remains an observation association.
"""
import json
from django.core.serializers.json import DjangoJSONEncoder
from .business_schema import catalog, source_models, public_fields

IDENTITY_FIELDS = ("sku", "number", "email", "group_key", "source_key", "company_name", "name", "title")


# Function: Accept incomplete business records and establish verifiable field relationships.
# Inputs: `records`: at most 30 key/schema/source_id/fields records; `candidates`: current same-user entity candidates.
# Outputs: Entity/fact dictionary and canonical original JSON for auditing; errors raise ValueError or DjangoValidationError.
# Logic: Validate types, fields, and supplied values first, then create batch-local foreign keys or existing/placeholder targets; do not fill omitted fields.
# Constraints: Write graph observations only, without business authorization actions; unknown IDs do not access other users and ambiguous names create separate entities.
def build_records(records, candidates):
    schema = catalog()
    models = source_models()
    if not isinstance(records, list) or not 1 <= len(records) <= 30:
        raise ValueError("records_limit")
    normalized, keys = [], set()
    for record in records:
        if not isinstance(record, dict) or set(record) - {"key", "schema", "source_id", "fields"} or not {"key", "schema", "fields"} <= set(record):
            raise ValueError("record_keys")
        key, kind, fields = record["key"], record["schema"], record["fields"]
        if not isinstance(key, str) or not key.strip() or len(key) > 40 or key in keys or not isinstance(kind, str) or kind not in schema or not isinstance(fields, dict):
            raise ValueError("record_schema")
        if set(fields) - set(schema[kind]) or (record.get("source_id") is not None and (not isinstance(record["source_id"], str) or not record["source_id"].strip())):
            raise ValueError("record_fields")
        keys.add(key)
        field_types = {field.name: field for field in public_fields(models[kind][0])}
        for name, value in fields.items():
            if value is None:
                continue
            field = field_types[name]
            if field.is_relation:
                if not isinstance(value, (str, int, dict)) or isinstance(value, bool):
                    raise ValueError("foreign_key_type")
                if isinstance(value, dict) and (set(value) != {"record"} or not isinstance(value["record"], str)):
                    raise ValueError("foreign_key_record")
            else:
                # Reuse field type/enum/length constraints without calling Model.clean or filling required-field defaults.
                field.clean(value, models[kind][0]())
        normalized.append(record)
    text = json.dumps(normalized, ensure_ascii=False, sort_keys=True, cls=DjangoJSONEncoder)
    entities, facts, by_key = [], [], {record["key"]: record for record in normalized}
    for record in normalized:
        kind, fields, source_id = record["schema"], record["fields"], record.get("source_id")
        peers = [candidate for candidate in candidates if candidate["kind"] == kind]
        if source_id:
            matches = [candidate for candidate in peers if candidate.get("source_id") == source_id]
        else:
            supplied = {name: fields[name] for name in IDENTITY_FIELDS if name in fields and isinstance(fields[name], str) and fields[name].strip()}
            matches = [candidate for candidate in peers if supplied and all(candidate.get("fields", {}).get(name) == value for name, value in supplied.items())]
        name = next((str(fields[field]) for field in IDENTITY_FIELDS if fields.get(field)), source_id or record["key"])
        entities.append({"key": record["key"], "kind": kind, "name": name[:200], "source_id": source_id,
                         "existing_id": matches[0]["id"] if len(matches) == 1 else None, "quote": json.dumps(record, ensure_ascii=False, sort_keys=True)})
    for record in normalized:
        for name, value in record["fields"].items():
            if value is None:
                continue
            field = schema[record["schema"]][name]
            target = None
            if "target" in field:
                if isinstance(value, dict):
                    target = value["record"]
                    if target not in by_key or by_key[target]["schema"] != field["target"]:
                        raise ValueError("foreign_key_target")
                else:
                    source_id = str(value)
                    target = f"reference-{len(entities)}"
                    while target in keys:
                        target += "x"
                    keys.add(target)
                    matches = [candidate for candidate in candidates if candidate["kind"] == field["target"] and candidate.get("source_id") == source_id]
                    entities.append({"key": target, "kind": field["target"], "name": source_id[:200], "source_id": source_id,
                                     "existing_id": matches[0]["id"] if len(matches) == 1 else None, "quote": json.dumps(value, ensure_ascii=False)})
            facts.append({"subject": record["key"], "predicate": "field:" + name, "object": target,
                          "value": None if target else value, "quote": json.dumps(record, ensure_ascii=False, sort_keys=True)})
    return {"entities": entities, "facts": facts}, text
