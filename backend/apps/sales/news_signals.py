"""Responsibility: Validate consistency between one exact public news/event amount and its evidence.
Implementation: Use nonnegative decimal strings; merge existing/update values to validate monetary metadata and source-excerpt inclusion without generating leads.
Relationships: WorldNewsSerializer and WorldEventSerializer use the amount field and validation; no database-model, CRM, or Agent imports.
Directory:
- NewsAmountField: Accept only contract-defined amount strings.
- NewsAmountField.to_internal_value: Reject floating-point values, exponential notation, and negative values.
- validate_news_signal: Check monetary-field combinations and evidence in complete records.
Variable index:
- logger: Log validation location and field only, never evidence bodies.
"""

import logging
import re

from rest_framework import serializers

logger = logging.getLogger("salesmate.news_signals")


# Function: Preserve exact decimal-string inputs for news source amounts.
# Logic: Inherit DRF precision/null validation and additionally reject implicit numeric and exponent conversions.
# Constraints: The serializer explicitly specifies bounds; reject overflow without truncating or rounding inputs.
class NewsAmountField(serializers.DecimalField):
    # Function: Validate amount transport type and ordinary decimal syntax.
    # Inputs: `data`: nonnull raw JSON field; parent nullable validation handles null.
    # Outputs: Exact Decimal; type/range errors raise ValidationError.
    # Logic: Validate a nonnegative decimal string, then delegate total/fractional digit checks to the parent.
    # Constraints: Reject floats, integer JSON, negatives, whitespace, NaN, and scientific notation; logs omit values.
    def to_internal_value(self, data):
        if not isinstance(data, str) or re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", data) is None:
            logger.warning("news_signal_rejected field=amount reason=decimal_string_required")
            raise serializers.ValidationError("amount 必须是非负普通十进制字符串或 null。")
        return super().to_internal_value(data)


# Function: Validate public-news monetary and evidence constraints.
# Inputs: `serializer`: optional existing instance; `attrs`: create/partial-update data after field validation.
# Outputs: Original attrs; contradictory combinations raise field-level ValidationError.
# Logic: Merge existing records and patches. With an amount, all four metadata fields require nonblank content; without one, monetary metadata and qualifier must be empty. Qualifiers preserve bounds and approximations; blank remains valid for legacy amounts. Amount evidence must appear verbatim in evidence.
# Constraints: No source access, missing-information inference, or CRM links; excerpt inclusion proves internal consistency only, not external news authenticity.
def validate_news_signal(serializer, attrs):
    names = ("amount", "currency", "amount_type", "amount_scope", "amount_evidence", "evidence", "amount_qualifier")
    record = {name: attrs.get(name, getattr(serializer.instance, name, None if name == "amount" else "")) for name in names}
    metadata = ("currency", "amount_type", "amount_scope", "amount_evidence")
    errors = {}
    if record["amount"] is None and record["amount_qualifier"]:
        errors["amount_qualifier"] = "未提供金额时不得提供金额限定词。"
    for name in metadata:
        if record["amount"] is None and record[name]:
            errors[name] = "amount 为 null 时金额元数据必须为空。"
        elif record["amount"] is not None and not record[name].strip():
            errors[name] = "提供 amount 时必须同时提供币种、类型、范围和金额证据。"
    if record["amount_evidence"] and record["amount_evidence"] not in record["evidence"]:
        errors["amount_evidence"] = "金额证据必须逐字包含在同一条 evidence 中。"
    if errors:
        logger.warning("news_signal_rejected operation=%s fields=%s", "update" if serializer.instance else "create", ",".join(sorted(errors)))
        raise serializers.ValidationError(errors)
    return attrs
