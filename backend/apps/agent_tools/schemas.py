"""Responsibility: Generate tool-input contracts from existing serializers.
Implementation: Nullable enums explicitly accept empty strings and nonnegative Decimal uses nonnegative string syntax; recursively project nested information and writable fields, and JSON Schema prevalidation works with original DRF and business validation.
Relationships: ``registry`` publishes Schema; ``services`` validates before invocation, avoiding generic ORM or arbitrary-field entry points.
Directory:
- object_schema: Construct a closed object.
- field_schema: Project a DRF field.
- record_schema: Generate create or update payload.
- validate: Strictly validate input.
Variable index:
- UUID: UUID text structure.
- REVISION: Existing optimistic-lock version structure.
- PAGE: Bounded pagination fields.
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


# Function: Construct an object contract.
# Inputs: Fields ``properties`` and required names ``required``.
# Outputs: JSON Schema.
# Logic: Reject additional fields by default.
# Constraints: Does not populate business defaults.
def object_schema(properties, required=()):
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


# Function: Project a field type.
# Inputs: DRF ``field``.
# Outputs: JSON Schema.
# Logic: Recursively declare nested objects and arrays; preserve ``allow_blank`` for enums, strings and nonnegative bounds for decimal, and express relation type independently from null.
# Constraints: Unsupported fields fail explicitly; original serializers still enforce dynamic relation permissions.
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
        if field.allow_blank and "" not in result["enum"]:
            result["enum"].append("")
    elif isinstance(field, s.BooleanField):
        result = {"type": "boolean"}
    elif isinstance(field, s.IntegerField):
        result = {"type": "integer"}
    elif isinstance(field, s.DecimalField):
        result = {
            "type": "string",
            "pattern": r"^[0-9]+(\.[0-9]+)?$" if field.min_value is not None and field.min_value >= 0 else r"^-?\d+(\.\d+)?$",
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


# Function: Generate a record-write contract.
# Inputs: Serializer class ``serializer`` and update flag ``partial``.
# Outputs: Closed JSON Schema.
# Logic: Original field identifiers are required and updates cannot be empty.
# Constraints: Does not publish read-only fields; state changes use dedicated tools.
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


# Function: Strictly validate a tool payload.
# Inputs: Raw JSON ``value`` and contract ``schema``.
# Outputs: None; failure raises HTTP 400.
# Logic: Check format, fields, and numeric types together.
# Constraints: Errors report only path and rule and never echo customer content.
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
