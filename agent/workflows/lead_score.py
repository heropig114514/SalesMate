"""L4：使用可复现的 Python 规则计算销售跟进优先级。"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger("salesmate.agent.lead_score")
SCORE_VERSION = "score-v2"
PRIORITY_WEIGHTS = {
    "urgency": Decimal("0.35"),
    "buying_intent": Decimal("0.35"),
    "opportunity_value": Decimal("0.30"),
}
INTENT_POINTS = {
    "L1 Exploring": 20,
    "L2 Interested": 40,
    "L3 Qualified": 60,
    "L4 Evaluating": 75,
    "L5 Negotiating": 90,
    "L6 Purchase Ready": 100,
    "GENERAL_INQUIRY": 20,
    "PRODUCT_CONFIRMED": 40,
    "DEMO_REQUEST": 40,
    "QUANTITY_CONFIRMED": 60,
    "BUDGET_CONFIRMED": 60,
    "PURCHASE_TIMELINE": 60,
    "FORMAL_QUOTATION_REQUEST": 75,
    "DECISION_MAKER_INVOLVED": 75,
    "CONTRACT_DISCUSSION": 90,
    "PAYMENT_DISCUSSION": 90,
    "APPROVAL_CONFIRMED": 100,
    "PURCHASE_CONFIRMATION": 100,
}
FIT_WEIGHTS = {
    "industry": 25,
    "company_size": 15,
    "geography": 10,
    "product": 35,
    "similar_won_deals": 15,
}
TIME_SIGNALS = frozenset({"DEADLINE", "UPCOMING_MEETING", "PROMISED_ACTION", "OVERDUE_ACTION"})


def compute_score(
    analysis: object,
    analysis_input: object,
    *,
    clock: Callable[[], datetime],
    priority_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """按正式规则返回公司级 0–100 跟进优先级；依据不足时返回空分。"""
    return compute_priority_result(
        analysis, analysis_input, clock=clock, priority_context=priority_context,
    )["score"]


def compute_priority_result(
    analysis: object,
    analysis_input: object,
    *,
    clock: Callable[[], datetime],
    priority_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """同一次 L4 计算中生成后端 Score 和可展示的解释信息。"""
    analysis_doc = _document(analysis)
    input_doc = _document(analysis_input)
    now = clock()
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("clock 必须返回带时区的 datetime。")
    priority_doc = dict(priority_context) if priority_context is not None else {}
    score = _compute_priority(analysis_doc, input_doc, priority_doc, now)
    logger.info(
        "l4_score_computed company_id=%s score=%s reason_features=%s",
        input_doc.get("company_id"), score.get("score"),
        [reason.get("feature") for reason in score.get("score_reasons", [])],
    )
    return {"score": score, "details": _priority_details(score, priority_doc, input_doc, now)}


def _compute_priority(
    analysis: Mapping[str, Any],
    analysis_input: Mapping[str, Any],
    context: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """使用 L1 阶段和现有业务资料，按 35/35/30 计算公司级分数。"""
    result = {
        "company_id": str(analysis_input.get("company_id", "")),
        "input_version": str(analysis_input.get("input_version", "")),
        "score": None,
        "score_reasons": [{"feature": "insufficient_data", "contribution": 0, "note": "评分依据不足"}],
        "score_version": SCORE_VERSION,
        "scored_at": now.isoformat(),
    }
    if analysis.get("status") != "completed":
        return result
    if not isinstance(context, Mapping):
        raise ValueError("priority_context 必须是对象。")
    messages = (
        _priority_messages(context["communications"], analysis_input)
        if "communications" in context else None
    )
    signals = _priority_signals(context.get("signals"), analysis_input, messages)
    urgency, urgency_source = _urgency_points(signals, now, context.get("seller"))
    intent_source = max(
        (item for item in signals if item["type"] in INTENT_POINTS),
        key=lambda item: (INTENT_POINTS[item["type"]], item.get("confidence", 0)),
        default=None,
    )
    if intent_source is None:
        return result
    buying_intent = INTENT_POINTS[intent_source["type"]]
    deal = _deal_points(context.get("deal"), context.get("seller"))
    fit = _fit_points(context.get("customer"), context.get("deal"), context.get("seller"))
    missing = []
    if deal is None:
        missing.append("活跃商机金额及同币种销售均值")
    if fit is None:
        missing.append("完整客户匹配资料")
    if deal is not None and fit is not None:
        opportunity_value = _round(Decimal(deal) * Decimal("0.60") + Decimal(fit) * Decimal("0.40"))
    elif deal is not None:
        opportunity_value = deal
    else:
        opportunity_value = fit
    points = {"urgency": urgency, "buying_intent": buying_intent}
    if opportunity_value is not None:
        points["opportunity_value"] = opportunity_value
    used_weight = sum((PRIORITY_WEIGHTS[name] for name in points), Decimal(0))
    raw = {name: Decimal(value) * PRIORITY_WEIGHTS[name] / used_weight for name, value in points.items()}
    score = _round(sum(raw.values()))
    contributions = {name: int(value) for name, value in raw.items()}
    remainder = score - sum(contributions.values())
    for name in sorted(raw, key=lambda key: (-(raw[key] - contributions[key]), key))[:remainder]:
        contributions[name] += 1
    urgency_note = (
        f"{urgency_source['type']}：{urgency_source['evidence']} [{urgency_source['source_id']}]"
        if urgency_source else "无明确紧急时间，按基础档位 10 分"
    )
    result["score"] = score
    provisional_note = f"暂定分；缺少{'、'.join(missing)}；按已有维度折算。" if missing else ""
    if missing:
        logger.info("l4_score_provisional company_id=%s missing=%s", result["company_id"], ",".join(missing))
    if deal is not None and fit is not None:
        opportunity_note = f"公司级商机价值 {opportunity_value}/100；金额档位 {deal}/100，客户匹配 {fit}/100"
    elif deal is not None:
        opportunity_note = f"暂用金额档位 {deal}/100。{provisional_note}"
    elif fit is not None:
        opportunity_note = f"暂用客户匹配 {fit}/100。{provisional_note}"
    else:
        opportunity_note = provisional_note
    result["score_reasons"] = [
        {"feature": "urgency", "contribution": contributions["urgency"], "note": urgency_note},
        {"feature": "buying_intent", "contribution": contributions["buying_intent"],
         "note": f"{intent_source['type']}：{intent_source['evidence']} [{intent_source['source_id']}]"},
        {"feature": "opportunity_value", "contribution": contributions.get("opportunity_value", 0),
         "note": opportunity_note},
    ]
    return result


def _priority_details(
    score: Mapping[str, Any],
    context: Mapping[str, Any],
    analysis_input: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    empty = {
        "score_breakdown": None,
        "top_reasons": [],
        "evidence": [],
        "recommended_next_action": None,
    }
    if score["score"] is None:
        return empty
    if (_deal_points(context.get("deal"), context.get("seller")) is None
            or _fit_points(context.get("customer"), context.get("deal"), context.get("seller")) is None):
        return empty
    messages = (
        _priority_messages(context["communications"], analysis_input)
        if "communications" in context else None
    )
    signals = _priority_signals(context.get("signals"), analysis_input, messages)
    urgency, urgency_signal = _urgency_points(signals, now, context.get("seller"))
    intent_signal = max(
        (item for item in signals if item["type"] in INTENT_POINTS),
        key=lambda item: (INTENT_POINTS[item["type"]], item["confidence"]),
    )
    intent = INTENT_POINTS[intent_signal["type"]]
    deal = _deal_points(context["deal"], context["seller"])
    fit = _fit_points(context["customer"], context["deal"], context["seller"])
    opportunity = _round(Decimal(deal) * Decimal("0.60") + Decimal(fit) * Decimal("0.40"))
    contributions = {item["feature"]: item["contribution"] for item in score["score_reasons"]}
    reasons = []
    if urgency_signal is not None:
        reasons.append({
            "type": urgency_signal["type"],
            "title": "存在需要优先处理的时间节点",
            "evidence": urgency_signal["evidence"],
            "source_id": urgency_signal["source_id"],
            "impact_score": contributions["urgency"],
        })
    reasons.append({
        "type": "BUYING_INTENT",
        "title": "客户采购阶段已有明确证据",
        "evidence": intent_signal["evidence"],
        "source_id": intent_signal["source_id"],
        "impact_score": contributions["buying_intent"],
    })
    amount = _money(context["deal"]["deal_value"])
    average = _money(context["seller"]["average_deal_value"])
    ratio = (amount / average).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    reasons.append({
        "type": "DEAL_VALUE",
        "title": "公司级商机价值",
        "evidence": f"商机金额为本公司同币种平均成交额的 {ratio} 倍；客户匹配 {fit}/100。",
        "source_id": None,
        "impact_score": contributions["opportunity_value"],
    })
    top_reasons = sorted(reasons, key=lambda item: (-item["impact_score"], item["type"]))[:3]
    return {
        "score_breakdown": {
            "urgency": urgency,
            "buying_intent": intent,
            "opportunity_value": opportunity,
            "deal_value": deal,
            "customer_fit": fit,
            "contributions": contributions,
        },
        "top_reasons": top_reasons,
        "evidence": [
            {"source_id": item["source_id"], "text": item["evidence"]}
            for item in top_reasons if item["source_id"] is not None
        ],
        "recommended_next_action": _next_action(urgency_signal, intent_signal),
    }


def _next_action(urgency_signal: Mapping[str, Any] | None, intent_signal: Mapping[str, Any]) -> str:
    if urgency_signal and urgency_signal["type"] == "OVERDUE_ACTION":
        return "核对逾期事项并尽快联系客户；对外动作由员工确认。"
    if intent_signal["type"] == "FORMAL_QUOTATION_REQUEST":
        return "核对金额、产品和截止时间，准备正式报价供员工确认。"
    if urgency_signal and urgency_signal["type"] == "UPCOMING_MEETING":
        return "核对会议时间与参会人员，准备讨论材料。"
    if urgency_signal:
        return "先核对时间节点，再安排员工跟进。"
    if intent_signal["type"] in {"CONTRACT_DISCUSSION", "PAYMENT_DISCUSSION"}:
        return "核对商务条款并安排员工跟进。"
    return "确认客户下一步需求并安排跟进。"


def rank_company_scores(scores: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """按公司处理优先级排序；未评分排后，同分比较紧急度和公司 ID。"""
    def key(item: Mapping[str, Any]) -> tuple[Any, ...]:
        score = item.get("score")
        reasons = item.get("score_reasons") or []
        urgency = next((reason.get("contribution", 0) for reason in reasons
                        if reason.get("feature") == "urgency"), 0)
        return (score is None, -(score or 0), -urgency, str(item.get("company_id", "")))

    return sorted((dict(item) for item in scores), key=key)


def _priority_messages(raw: object, analysis_input: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError("L4 communications 必须是数组。")
    allowed = analysis_input.get("member_dedupe_keys")
    allowed_refs = set(allowed) if isinstance(allowed, list) else set()
    messages = {}
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("L4 邮件通信必须是对象。")
        source_id = item.get("message_id")
        if not isinstance(source_id, str) or source_id not in allowed_refs or source_id in messages:
            raise ValueError("L4 邮件通信来源不在当前公司或重复。")
        if not isinstance(item.get("content"), str) or not item["content"].strip():
            raise ValueError("L4 邮件通信缺少正文。")
        if item.get("sender") not in ("customer", "employee"):
            raise ValueError("L4 邮件通信 sender 无效。")
        messages[source_id] = item
    return messages


def _priority_signals(
    raw: object,
    analysis_input: Mapping[str, Any],
    messages: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("L4 signals 必须是数组。")
    if len(raw) > 30:
        raise ValueError("L4 信号最多 30 项。")
    allowed = analysis_input.get("member_dedupe_keys")
    allowed_refs = set(allowed) if isinstance(allowed, list) else set()
    supported = TIME_SIGNALS | {"CUSTOMER_WAITING"} | INTENT_POINTS.keys()
    signals = []
    seen = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("评分信号必须是对象。")
        kind, source_id, evidence = item.get("type"), item.get("source_id"), item.get("evidence")
        if not isinstance(kind, str) or kind not in supported:
            raise ValueError("评分信号类型无效。")
        if not isinstance(source_id, str) or source_id not in allowed_refs:
            raise ValueError("评分信号必须引用本公司邮件。")
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError("评分信号缺少原文证据。")
        if messages is not None:
            message = messages.get(source_id)
            if message is None or re.sub(r"\s+", "", evidence) not in re.sub(r"\s+", "", message["content"]):
                raise ValueError("L4 信号证据不在来源邮件中。")
            if kind in INTENT_POINTS or kind in {"DEADLINE", "CUSTOMER_WAITING"}:
                if message["sender"] != "customer":
                    raise ValueError("客户意向和催促信号必须来自客户邮件。")
        confidence = item.get("confidence")
        if type(confidence) not in (int, float) or not 0 <= confidence <= 1:
            raise ValueError("评分信号置信度必须在 0–1。")
        value = item.get("value")
        if kind in TIME_SIGNALS and _timestamp(value) is None and _calendar_date(value) is None:
            raise ValueError("时间信号必须是日期或带时区的时间。")
        if kind == "QUANTITY_CONFIRMED" and (type(value) is not int or value <= 0):
            raise ValueError("数量信号必须是正整数。")
        if kind == "BUDGET_CONFIRMED":
            if not isinstance(value, Mapping) or _money(value.get("amount")) is None or _currency(value.get("currency")) is None:
                raise ValueError("预算信号必须包含金额和三位币种。")
        if kind not in TIME_SIGNALS | {"QUANTITY_CONFIRMED", "BUDGET_CONFIRMED"}:
            if value is not None and not isinstance(value, str):
                raise ValueError("L4 信号值类型无效。")
        key = (kind, source_id, evidence)
        if key not in seen:
            signals.append(dict(item))
            seen.add(key)
    return signals


def _urgency_points(
    signals: list[dict[str, Any]], now: datetime, seller: object = None,
) -> tuple[int, dict[str, Any] | None]:
    current = now
    if isinstance(seller, Mapping) and seller.get("time_zone") is not None:
        zone = seller["time_zone"]
        if not isinstance(zone, str) or not zone.strip():
            raise ValueError("seller.time_zone 必须是 IANA 时区。")
        try:
            current = now.astimezone(ZoneInfo(zone))
        except ZoneInfoNotFoundError:
            raise ValueError("seller.time_zone 不是有效的 IANA 时区。") from None
    timed = [
        (points, item) for item in signals if item["type"] in TIME_SIGNALS
        if (points := _time_urgency(item, current)) is not None
    ]
    if timed:
        return max(timed, key=lambda pair: (pair[0], pair[1]["confidence"]))
    return 10, None


def _time_urgency(signal: Mapping[str, Any], now: datetime) -> int | None:
    instant = _timestamp(signal["value"])
    if instant is not None:
        hours = (instant - now).total_seconds() / 3600
        if hours < 0 and signal["type"] != "OVERDUE_ACTION":
            return None
        if hours <= 4:
            return 100
        if hours <= 24:
            return 90
        if hours <= 48:
            return 80
        if hours <= 168:
            return 65
        if hours <= 336:
            return 45
        return 25
    calendar_day = _calendar_date(signal["value"])
    days = (calendar_day - now.date()).days
    if days < 0 and signal["type"] != "OVERDUE_ACTION":
        return None
    if days < 0:
        return 100
    if days == 0:
        return 90  # 只有日期时不能假定剩余时间不超过四小时。
    if days <= 2:
        return 80
    if days <= 7:
        return 65
    if days <= 14:
        return 45
    return 25


def _deal_points(deal: object, seller: object) -> int | None:
    if not isinstance(deal, Mapping) or not isinstance(seller, Mapping):
        return None
    if deal.get("status") != "ACTIVE":
        return None
    amount = _money(deal.get("deal_value"))
    average = _money(seller.get("average_deal_value"))
    if amount is None or amount <= 0 or average is None or average <= 0:
        return None
    currency = _currency(deal.get("currency"))
    average_currency = _currency(seller.get("average_deal_currency"))
    if currency is None or average_currency is None or currency != average_currency:
        return None
    ratio = amount / average
    if ratio < Decimal("0.5"):
        return 20
    if ratio < 1:
        return 40
    if ratio < 2:
        return 60
    if ratio <= 5:
        return 80
    return 100


def _fit_points(customer: object, deal: object, seller: object) -> int | None:
    if not all(isinstance(item, Mapping) for item in (customer, deal, seller)):
        return None
    industry = customer.get("industry")
    size = customer.get("company_size")
    country = customer.get("country")
    products = deal.get("product")
    target_industries = seller.get("target_industries")
    bounds = seller.get("target_company_size")
    regions = seller.get("service_regions")
    offered = seller.get("products")
    won = seller.get("similar_won_deals")
    if (not isinstance(industry, str) or not industry.strip() or industry.casefold() == "unknown"
            or type(size) is not int or size < 0 or not isinstance(country, str) or not country.strip()
            or country.casefold() == "unknown"
            or not _strings(products) or not _strings(target_industries)
            or not isinstance(bounds, Mapping) or type(bounds.get("min")) is not int
            or type(bounds.get("max")) is not int or bounds["min"] < 0
            or bounds["min"] > bounds["max"]
            or not _strings(regions) or not _strings(offered) or type(won) is not bool):
        return None
    checks = {
        "industry": industry.casefold() in {item.casefold() for item in target_industries},
        "company_size": bounds["min"] <= size <= bounds["max"],
        "geography": country.casefold() in {item.casefold() for item in regions},
        "product": bool({item.casefold() for item in products} & {item.casefold() for item in offered}),
        "similar_won_deals": won,
    }
    return sum(weight for name, weight in FIT_WEIGHTS.items() if checks[name])


def _strings(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) and item.strip() for item in value)


def _money(value: object) -> Decimal | None:
    if type(value) not in (int, str):
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    return number if number.is_finite() and number >= 0 else None


def _currency(value: object) -> str | None:
    return value.upper() if isinstance(value, str) and re.fullmatch(r"[A-Za-z]{3}", value) else None


def _round(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _document(value: object) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        raise ValueError("输入必须是对象。")
    return dict(value)


def _calendar_date(value: object) -> date | None:
    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


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


__all__ = [
    "SCORE_VERSION", "PRIORITY_WEIGHTS", "INTENT_POINTS", "FIT_WEIGHTS", "compute_score",
    "compute_priority_result", "rank_company_scores",
]
