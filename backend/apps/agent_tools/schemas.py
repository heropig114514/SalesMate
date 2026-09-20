"""职责：从既有序列化器生成工具输入契约。
实现：递归投影嵌套资料和可写字段，JSON Schema 预检与原 DRF/业务校验共同生效。
关联：registry 发布 Schema；services 调用前验证，避免通用 ORM 或任意字段入口。
目录：
- object_schema：构造封闭对象。
- field_schema：投影 DRF 字段。
- record_schema：生成创建或更新载荷。
- validate：严格验证输入。
变量索引：
- UUID：UUID 文本结构。
- REVISION：既有乐观锁版本结构。
- PAGE：有限分页字段。
"""

from django.db import models
from jsonschema import Draft202012Validator, FormatChecker
from rest_framework import serializers as s
from rest_framework.exceptions import ValidationError

UUID = {"type": "string", "format": "uuid"}
REVISION = {"type": "integer", "minimum": 0}
PAGE = {
    "page": {"type": "integer", "minimum": 1},
    "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
}


# 功能：构造对象契约。
# 输入：`properties` 字段、`required` 必需名称。
# 输出：JSON Schema。
# 逻辑：默认拒绝额外字段。
# 约束：不补写业务默认值。
def object_schema(properties, required=()):
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


# 功能：投影字段类型。
# 输入：`field` 为 DRF 字段。
# 输出：JSON Schema。
# 逻辑：递归声明嵌套对象及数组，保持 decimal 字符串精度、关系主键类型及可空语义。
# 约束：未支持字段显式失败；动态关系权限仍由原序列化器执行。
def field_schema(field):
    if isinstance(field, s.ListSerializer):
        result = {"type": "array", "items": record_schema(type(field.child))}
    elif isinstance(field, s.Serializer):
        result = record_schema(type(field))
    elif isinstance(field, s.PrimaryKeyRelatedField):
        pk = field.queryset.model._meta.pk
        result = (
            dict(UUID)
            if isinstance(pk, models.UUIDField)
            else {"type": "integer", "minimum": 1}
        )
    elif isinstance(field, s.ChoiceField):
        result = {"enum": list(field.choices)}
    elif isinstance(field, s.BooleanField):
        result = {"type": "boolean"}
    elif isinstance(field, s.IntegerField):
        result = {"type": "integer"}
    elif isinstance(field, s.DecimalField):
        result = {
            "type": "string",
            "pattern": r"^-?\d+(\.\d+)?$",
            "description": "精确十进制字符串，禁止浮点金额。",
        }
    elif isinstance(field, s.FloatField):
        result = {"type": "number"}
    elif isinstance(field, s.UUIDField):
        result = dict(UUID)
    elif isinstance(field, s.DateTimeField):
        result = {"type": "string", "format": "date-time"}
    elif isinstance(field, s.DateField):
        result = {"type": "string", "format": "date"}
    elif isinstance(field, s.ListField):
        result = {"type": "array", "items": field_schema(field.child)}
    elif isinstance(field, s.JSONField):
        result = {"description": "既有 JSON 字段；嵌套业务内容仍由后端校验。"}
    elif isinstance(field, s.CharField):
        result = {"type": "string"}
        if field.max_length is not None:
            result["maxLength"] = field.max_length
        if not field.allow_blank:
            result["minLength"] = 1
    else:
        raise ValueError(f"Unsupported tool schema field: {type(field).__name__}")
    if result.get("type") == "array":
        for attribute, keyword in (("min_length", "minItems"), ("max_length", "maxItems")):
            if getattr(field, attribute, None) is not None:
                result[keyword] = getattr(field, attribute)
    if result.get("type") in {"integer", "number"}:
        for attribute, keyword in (("min_value", "minimum"), ("max_value", "maximum")):
            if getattr(field, attribute, None) is not None:
                result[keyword] = getattr(field, attribute)
    if field.help_text:
        result["description"] = str(field.help_text)
    return {"anyOf": [result, {"type": "null"}]} if field.allow_null else result


# 功能：生成记录写入契约。
# 输入：`serializer` 类、`partial` 是否更新。
# 输出：封闭 JSON Schema。
# 逻辑：原字段标识必填，更新不得为空。
# 约束：只读字段不发布，状态变更另走专用工具。
def record_schema(serializer, partial=False):
    fields = {
        name: field
        for name, field in serializer().fields.items()
        if not field.read_only
    }
    result = object_schema(
        {name: field_schema(field) for name, field in fields.items()},
        [] if partial else [name for name, field in fields.items() if field.required],
    )
    if partial:
        result["minProperties"] = 1
    return result


# 功能：严格校验工具载荷。
# 输入：`value` 原始 JSON、`schema` 契约。
# 输出：无；失败抛 400。
# 逻辑：同时检查格式、字段和数值类型。
# 约束：错误只报告路径与规则，不回显客户内容。
def validate(value, schema):
    error = next(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
        None,
    )
    if error:
        path = ".".join(str(part) for part in error.absolute_path) or "arguments"
        raise ValidationError(
            {"arguments": f"字段 {path} 不符合 {error.validator} 约束。"}
        )
