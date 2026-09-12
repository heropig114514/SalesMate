"""L4：使用可复现的 Python 规则计算销售跟进优先级。"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable, Mapping


SCORE_VERSION = "score-v1"
WEIGHTS = {
    "signal": Decimal("0.30"),
    "demand_clarity": Decimal("0.20"),
    "urgency": Decimal("0.20"),
    "decision_visibility": Decimal("0.10"),
    "recency": Decimal("0.15"),
    "substantive_inbound_count": Decimal("0.05"),
}
SIGNAL_VALUES = {
    "repeat_purchase": Decimal("1.00"),
    "quoted_not_closed": Decimal("0.80"),
    "inquiry_intent": Decimal("0.65"),
    "new_lead_no_profile": Decimal("0.35"),
}


def compute_score(
    analysis: object,
    analysis_input: object,
    *,
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    """返回 0-100 的处理优先级；资料不足时返回 ``score=null``。"""
    analysis_doc = _document(analysis)
    input_doc = _document(analysis_input)
    now = clock()
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("clock 必须返回带时区的 datetime。")

    result = {
        "company_id": str(input_doc.get("company_id", "")),
        "input_version": str(input_doc.get("input_version", "")),
        "score": None,
        "score_reasons": [
            {
                "feature": "insufficient_data",
                "contribution": 0,
                "note": "资料不足，未评分",
            }
        ],
        "score_version": SCORE_VERSION,
        "scored_at": now.isoformat(),
    }

    if analysis_doc.get("status") != "completed":
        return result
    list_view = analysis_doc.get("list_view")
    if not isinstance(list_view, Mapping):
        return result
    signal = list_view.get("signal")
    if signal not in SIGNAL_VALUES:
        return result
    features = list_view.get("score_features")
    if not isinstance(features, Mapping):
        return result

    normalized: dict[str, Decimal] = {"signal": SIGNAL_VALUES[str(signal)]}
    notes: dict[str, str] = {
        "signal": str(list_view.get("signal_evidence", {}).get("text", signal))
        if isinstance(list_view.get("signal_evidence"), Mapping)
        else str(signal)
    }
    for name in ("demand_clarity", "urgency", "decision_visibility"):
        feature = features.get(name)
        if not isinstance(feature, Mapping):
            return result
        value = feature.get("value")
        if type(value) is not int or value not in range(4):
            return result
        normalized[name] = Decimal(value) / Decimal(3)
        notes[name] = str(feature.get("basis", "")) or "无说明"

    metrics = input_doc.get("metrics")
    if not isinstance(metrics, Mapping):
        return result
    last_inbound_at = _timestamp(metrics.get("last_inbound_at"))
    if last_inbound_at is None:
        return result
    days = max(Decimal(0), Decimal(str((now - last_inbound_at).total_seconds())) / Decimal(86400))
    normalized["recency"] = max(Decimal(0), Decimal(1) - days / Decimal(30))
    notes["recency"] = f"最近有效来信距今 {days.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)} 天"

    count = metrics.get("substantive_inbound_count")
    if type(count) is not int or count < 0:
        return result
    normalized["substantive_inbound_count"] = Decimal(min(count, 5)) / Decimal(5)
    notes["substantive_inbound_count"] = f"有效入站邮件 {count} 封"

    reasons = []
    for name in WEIGHTS:
        contribution = int(
            (Decimal(100) * WEIGHTS[name] * normalized[name]).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        reasons.append(
            {"feature": name, "contribution": contribution, "note": notes[name]}
        )

    result["score_reasons"] = reasons
    result["score"] = max(0, min(100, sum(item["contribution"] for item in reasons)))
    return result


def _document(value: object) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        raise ValueError("输入必须是对象。")
    return dict(value)


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


__all__ = ["SCORE_VERSION", "SIGNAL_VALUES", "WEIGHTS", "compute_score"]
