"""职责：将按业务 schema 提交的不完整结构化记录转换为图谱观察。
实现：只校验已给字段，保留未知；明确 ID 或唯一同类型身份匹配既有实体，外键缺失目标创建可追溯占位。
关联：business_schema 为唯一字段契约；episodes 保存原始 JSON；episode_projection 负责绑定后来出现的权威记录。
目录：
- build_records：校验并生成实体、属性及外键观察。
变量索引：
- IDENTITY_FIELDS：用于确定性候选对齐的业务身份字段，名称匹配仍属于观察关联。
"""
import json
from django.core.serializers.json import DjangoJSONEncoder
from .business_schema import catalog, source_models, public_fields

IDENTITY_FIELDS = ("sku", "number", "email", "group_key", "source_key", "company_name", "name", "title")


# 功能：接收不完整业务记录并建立可验证的字段关系。
# 输入：`records` 为最多 30 条 key/schema/source_id/fields 记录；`candidates` 为同用户当前实体候选。
# 输出：实体/事实字典及用于审计的规范 JSON 原文；错误抛 ValueError 或 DjangoValidationError。
# 逻辑：先校验类型、字段和给定值，再建立本批次外键或已有/占位目标；不补全未提供字段。
# 约束：只写图谱观察，不执行业务权限动作；未知 ID 不访问其他用户，名称歧义建立独立实体。
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
                # 字段自己的类型/枚举/长度约束可复用；不调用 Model.clean 或填入必填字段默认值。
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
