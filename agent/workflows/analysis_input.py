"""L2：把后端提供的公司邮件和业务数据整理为一份 AnalysisInput。"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable, Mapping

from agent.clients.backend_api import BackendClient
from agent.skills import load_skill


EXTRACT_PROMPT_VERSION = load_skill("email-fact-extraction").version
ORDINARY_FACT_FIELDS = (
    "contact_name",
    "contact_title",
    "company_self_reported",
    "business_background",
    "employee_scale_hint",
    "product_need",
    "quantity",
    "budget",
    "delivery_time",
    "decision_process",
    "concerns",
    "quote_reference",
    "order_reference",
)
FACT_FIELDS = (
    "has_substantive_update",
    "message_summary",
    "intent_hint",
    "intent_evidences",
    *ORDINARY_FACT_FIELDS,
)
DIRECTIONS = frozenset({"inbound", "outbound", "unknown"})
EXTRACT_STATUSES = frozenset({"completed", "failed", "skipped_non_business"})
INTENT_HINTS = frozenset(
    {"purchase_inquiry", "meeting", "support", "non_sales", "unknown"}
)
CRM_STATUSES = frozenset({"unregistered", "registered"})


@dataclass
class Metrics:
    inbound_count: int
    outbound_count: int
    substantive_inbound_count: int
    first_contact_at: str | None
    last_inbound_at: str | None
    last_outbound_at: str | None
    response_gap_days: float | None
    has_history_order: bool
    crm_status: str

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.__dict__)


@dataclass
class AnalysisInput:
    company_id: str
    input_version: str
    merge_version: str
    external_snapshot_version: str
    built_at: str
    company: dict[str, Any]
    business_context: dict[str, Any]
    latest_message_summary: str | None
    member_dedupe_keys: list[str]
    unparsed_message_count: int
    facts: dict[str, list[dict[str, Any]]]
    metrics: Metrics
    priority_context: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_id": self.company_id,
            "input_version": self.input_version,
            "merge_version": self.merge_version,
            "external_snapshot_version": self.external_snapshot_version,
            "built_at": self.built_at,
            "company": copy.deepcopy(self.company),
            "business_context": copy.deepcopy(self.business_context),
            "latest_message_summary": self.latest_message_summary,
            "member_dedupe_keys": list(self.member_dedupe_keys),
            "unparsed_message_count": self.unparsed_message_count,
            "facts": copy.deepcopy(self.facts),
            "metrics": self.metrics.to_dict(),
        }


@dataclass
class ValidationError:
    code: str
    message: str
    field: str | None = None

    def to_dict(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.field is not None:
            error["field"] = self.field
        return {"error": error}


def build_analysis_input(
    company_id: str,
    *,
    backend: BackendClient,
    merge_version: str = "merge-v2",
    clock: Callable[[], datetime],
) -> AnalysisInput | ValidationError:
    """读取一份公司快照，完成校验、归并、指标和版本计算。"""
    if not _nonblank(company_id):
        return ValidationError("invalid_input", "company_id 不能为空。", "company_id")
    if not _nonblank(merge_version):
        return ValidationError("invalid_input", "merge_version 不能为空。", "merge_version")

    try:
        grouping = backend.get_company_grouping(company_id)
    except Exception as error:
        return ValidationError("grouping_retrieval_failed", f"公司归组读取失败：{error}")
    try:
        grouping = _validate_grouping(grouping, company_id)
    except ValueError as error:
        return ValidationError("invalid_backend_data", str(error))
    try:
        context = backend.get_company_context(company_id)
    except Exception as error:
        return ValidationError("context_retrieval_failed", f"公司上下文读取失败：{error}")

    try:
        context = _validate_context(context, grouping)
        emails = context["emails"]
        facts = merge_facts(emails)
        metrics = calculate_metrics(
            emails,
            crm_status=grouping["crm_status"],
            orders=context["orders"],
        )
        input_version = compute_input_version(
            emails,
            merge_version,
            context["external_snapshot_version"],
        )
        built_at = _clock_text(clock)
    except ValueError as error:
        return ValidationError("invalid_backend_data", str(error))
    except Exception as error:
        return ValidationError("analysis_input_failed", f"L2 构建失败：{error}")

    priority_context = context.get("priority_context")
    if priority_context is not None and "communications" not in priority_context:
        recent_emails = sorted(
            (
                email for email in emails
                if email["extract_status"] == "completed"
                and email["facts"]["intent_hint"] != "non_sales"
                and email["direction"] in {"inbound", "outbound"}
            ),
            key=_email_time_key,
        )[-20:]
        priority_context["communications"] = [
            {
                "message_id": email["dedupe_key"],
                "sender": "customer" if email["direction"] == "inbound" else "employee",
                "timestamp": email["sent_at"],
                "content": "\n".join(part for part in (email["subject"], email.get("body_text"))
                                     if isinstance(part, str) and part.strip()),
            }
            for email in recent_emails
            if email["subject"] or email.get("body_text")
        ]

    return AnalysisInput(
        company_id=company_id,
        input_version=input_version,
        merge_version=merge_version,
        external_snapshot_version=context["external_snapshot_version"],
        built_at=built_at,
        company={
            "company_name": grouping.get("company_name"),
            "crm_status": grouping["crm_status"],
            "domains": copy.deepcopy(grouping["domains"]),
            "contacts": copy.deepcopy(grouping["contacts"]),
        },
        business_context={
            "customer": copy.deepcopy(context["customer"]),
            "tickets": copy.deepcopy(context["tickets"]),
            "quotes": copy.deepcopy(context["quotes"]),
            "orders": copy.deepcopy(context["orders"]),
        },
        latest_message_summary=_latest_summary(emails),
        member_dedupe_keys=list(grouping["member_dedupe_keys"]),
        unparsed_message_count=sum(
            email["extract_status"] != "completed" for email in emails
        ),
        facts=facts,
        metrics=metrics,
        priority_context=priority_context,
    )


def merge_facts(emails: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """保留所有已完成邮件的事实、来源、原文证据和事实时间。"""
    merged = {field: [] for field in ORDINARY_FACT_FIELDS}
    for email in emails:
        if email["extract_status"] != "completed":
            continue
        facts = email["facts"]
        for field in ORDINARY_FACT_FIELDS:
            for source_index, group in enumerate(facts[field]):
                merged[field].append(
                    {
                        "value": group["value"],
                        "dedupe_key": email["dedupe_key"],
                        "fact_time": email["sent_at"],
                        "evidences": list(group["evidences"]),
                        "_source_index": source_index,
                    }
                )

    for field in ORDINARY_FACT_FIELDS:
        merged[field].sort(key=_fact_sort_key)
        for item in merged[field]:
            item.pop("_source_index")
    return merged


def calculate_metrics(
    emails: list[dict[str, Any]],
    *,
    crm_status: str,
    orders: list[Any],
) -> Metrics:
    """计算页面排序和 L4 使用的确定性邮件指标。"""
    inbound = [email for email in emails if email["direction"] == "inbound"]
    outbound = [email for email in emails if email["direction"] == "outbound"]
    substantive_count = sum(
        email["direction"] == "inbound"
        and email["extract_status"] == "completed"
        and email["facts"]["has_substantive_update"] is True
        for email in emails
    )
    return Metrics(
        inbound_count=len(inbound),
        outbound_count=len(outbound),
        substantive_inbound_count=substantive_count,
        first_contact_at=_time_endpoint(emails, latest=False),
        last_inbound_at=_time_endpoint(inbound, latest=True),
        last_outbound_at=_time_endpoint(outbound, latest=True),
        response_gap_days=_response_gap_days(emails),
        has_history_order=bool(orders),
        crm_status=crm_status,
    )


def compute_input_version(
    emails: list[dict[str, Any]],
    merge_version: str,
    external_snapshot_version: str,
) -> str:
    """生成分析缓存和幂等使用的稳定版本。"""
    members = sorted(
        [
            email["dedupe_key"],
            email["extract_prompt_version"],
            email["extract_status"],
        ]
        for email in emails
    )
    payload = [members, merge_version, external_snapshot_version]
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def parse_rfc3339(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("时间必须是非空 RFC3339 字符串。")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"时间格式无效：{value}") from None
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"时间必须包含时区：{value}")
    return result


def _validate_grouping(raw: object, company_id: str) -> dict[str, Any]:
    grouping = _mapping(raw, "Grouping")
    if grouping.get("company_id") != company_id:
        raise ValueError("Grouping.company_id 与请求不一致。")
    if grouping.get("crm_status") not in CRM_STATUSES:
        raise ValueError("Grouping.crm_status 无效。")
    domains = _list(grouping.get("domains"), "Grouping.domains")
    contacts = _list(grouping.get("contacts"), "Grouping.contacts")
    members = _string_list(
        grouping.get("member_dedupe_keys"), "Grouping.member_dedupe_keys"
    )
    if len(members) != len(set(members)):
        raise ValueError("Grouping.member_dedupe_keys 不能重复。")

    normalized_contacts = []
    for index, raw_contact in enumerate(contacts):
        contact = _mapping(raw_contact, f"Grouping.contacts[{index}]")
        email = contact.get("contact_email")
        if not _mailbox(email):
            raise ValueError(f"Grouping.contacts[{index}].contact_email 无效。")
        count = contact.get("interaction_count")
        if type(count) is not int or count < 0:
            raise ValueError(f"Grouping.contacts[{index}].interaction_count 无效。")
        if type(contact.get("is_primary")) is not bool:
            raise ValueError(f"Grouping.contacts[{index}].is_primary 无效。")
        name = contact.get("contact_name")
        if name is not None and not isinstance(name, str):
            raise ValueError(f"Grouping.contacts[{index}].contact_name 无效。")
        normalized_contacts.append(copy.deepcopy(dict(contact)))

    return {
        "company_id": company_id,
        "company_name": grouping.get("company_name"),
        "crm_status": grouping["crm_status"],
        "domains": copy.deepcopy(domains),
        "contacts": normalized_contacts,
        "member_dedupe_keys": members,
    }


def _validate_context(raw: object, grouping: Mapping[str, Any]) -> dict[str, Any]:
    context = _mapping(raw, "CompanyContext")
    if context.get("company_id") != grouping["company_id"]:
        raise ValueError("CompanyContext.company_id 与 Grouping 不一致。")
    external_version = context.get("external_snapshot_version")
    if not _nonblank(external_version):
        raise ValueError("CompanyContext.external_snapshot_version 不能为空。")

    raw_emails = _list(context.get("emails"), "CompanyContext.emails")
    emails = [_validate_email(email, index) for index, email in enumerate(raw_emails)]
    email_keys = [email["dedupe_key"] for email in emails]
    if len(email_keys) != len(set(email_keys)):
        raise ValueError("CompanyContext.emails 的 dedupe_key 不能重复。")
    if set(email_keys) != set(grouping["member_dedupe_keys"]):
        raise ValueError("CompanyContext.emails 与公司成员邮件范围不一致。")

    customer = _mapping(context.get("customer"), "CompanyContext.customer")
    tickets = _list(context.get("tickets"), "CompanyContext.tickets")
    quotes = _list(context.get("quotes"), "CompanyContext.quotes")
    orders = _list(context.get("orders"), "CompanyContext.orders")
    priority_context = context.get("priority_context")
    if priority_context is not None and not isinstance(priority_context, Mapping):
        raise ValueError("CompanyContext.priority_context 必须是对象。")
    employee_count = customer.get("employee_count")
    if employee_count is not None and (type(employee_count) is not int or employee_count < 0):
        raise ValueError("CompanyContext.customer.employee_count 无效。")
    first_deal = customer.get("first_deal_at")
    if first_deal is not None:
        parse_rfc3339(first_deal)

    return {
        "company_id": context["company_id"],
        "external_snapshot_version": external_version,
        "emails": emails,
        "customer": copy.deepcopy(dict(customer)),
        "tickets": copy.deepcopy(tickets),
        "quotes": copy.deepcopy(quotes),
        "orders": copy.deepcopy(orders),
        "priority_context": copy.deepcopy(dict(priority_context))
        if priority_context is not None else None,
    }


def _validate_email(raw: object, index: int) -> dict[str, Any]:
    email = _mapping(raw, f"emails[{index}]")
    dedupe_key = email.get("dedupe_key")
    if not _nonblank(dedupe_key):
        raise ValueError(f"emails[{index}].dedupe_key 不能为空。")
    direction = email.get("direction")
    if direction not in DIRECTIONS:
        raise ValueError(f"emails[{index}].direction 无效。")
    status = email.get("extract_status")
    if status not in EXTRACT_STATUSES:
        raise ValueError(f"emails[{index}].extract_status 无效。")
    if email.get("extract_prompt_version") != EXTRACT_PROMPT_VERSION:
        raise ValueError(f"emails[{index}] 不是当前 extract-v6 结构。")

    sent_at = email.get("sent_at")
    if sent_at is not None:
        parse_rfc3339(sent_at)
    thread_id = email.get("thread_id")
    if thread_id is not None and not _nonblank(thread_id):
        raise ValueError(f"emails[{index}].thread_id 无效。")
    subject = email.get("subject", "")
    if not isinstance(subject, str):
        raise ValueError(f"emails[{index}].subject 无效。")

    facts = email.get("facts")
    if status == "completed":
        facts = _validate_facts(facts, index)
    elif facts is not None:
        raise ValueError(f"emails[{index}] 非 completed 状态的 facts 必须为 null。")

    result = copy.deepcopy(dict(email))
    result.update(
        dedupe_key=dedupe_key,
        direction=direction,
        sent_at=sent_at,
        thread_id=thread_id,
        subject=subject,
        extract_status=status,
        extract_prompt_version=EXTRACT_PROMPT_VERSION,
        facts=facts,
    )
    return result


def _validate_facts(raw: object, email_index: int) -> dict[str, Any]:
    facts = _mapping(raw, f"emails[{email_index}].facts")
    if set(facts) != set(FACT_FIELDS):
        raise ValueError(f"emails[{email_index}].facts 字段与 extract-v6 不一致。")
    if type(facts["has_substantive_update"]) is not bool:
        raise ValueError("has_substantive_update 必须是布尔值。")
    summary = facts["message_summary"]
    if not isinstance(summary, str) or len(summary) > 80:
        raise ValueError("message_summary 必须是 80 字以内的字符串。")
    if facts["intent_hint"] not in INTENT_HINTS:
        raise ValueError("intent_hint 枚举无效。")
    _string_list(facts["intent_evidences"], "intent_evidences", allow_empty=True)

    normalized = copy.deepcopy(dict(facts))
    for field in ORDINARY_FACT_FIELDS:
        groups = _list(facts[field], field)
        seen: set[str] = set()
        normalized_groups = []
        for index, raw_group in enumerate(groups):
            group = _mapping(raw_group, f"{field}[{index}]")
            if set(group) != {"value", "evidences"}:
                raise ValueError(f"{field}[{index}] 字段必须是 value 和 evidences。")
            value = group.get("value")
            if not _nonblank(value) or value in seen:
                raise ValueError(f"{field}[{index}].value 为空或重复。")
            seen.add(value)
            evidences = _string_list(
                group.get("evidences"), f"{field}[{index}].evidences"
            )
            if not evidences:
                raise ValueError(f"{field}[{index}].evidences 不能为空。")
            normalized_groups.append({"value": value, "evidences": evidences})
        normalized[field] = normalized_groups
    return normalized


def _latest_summary(emails: list[dict[str, Any]]) -> str | None:
    completed = [email for email in emails if email["extract_status"] == "completed"]
    if not completed:
        return None
    latest = max(completed, key=_email_time_key)
    return latest["facts"]["message_summary"]


def _time_endpoint(emails: list[dict[str, Any]], *, latest: bool) -> str | None:
    candidates = [email for email in emails if email.get("sent_at") is not None]
    if not candidates:
        return None
    selected = max(candidates, key=_email_time_key) if latest else min(candidates, key=_email_time_key)
    return selected["sent_at"]


def _response_gap_days(emails: list[dict[str, Any]]) -> float | None:
    pairs: list[tuple[datetime, datetime, str]] = []
    for inbound in emails:
        if inbound["direction"] != "inbound" or not inbound.get("thread_id") or not inbound.get("sent_at"):
            continue
        inbound_time = parse_rfc3339(inbound["sent_at"])
        replies = [
            outbound
            for outbound in emails
            if outbound["direction"] == "outbound"
            and outbound.get("thread_id") == inbound["thread_id"]
            and outbound.get("sent_at")
            and parse_rfc3339(outbound["sent_at"]) > inbound_time
        ]
        if replies:
            reply = min(replies, key=_email_time_key)
            pairs.append((inbound_time, parse_rfc3339(reply["sent_at"]), inbound["dedupe_key"]))
    if not pairs:
        return None
    inbound_time, outbound_time, _ = max(pairs, key=lambda item: (item[0], item[2]))
    days = Decimal(str((outbound_time - inbound_time).total_seconds())) / Decimal(86400)
    return float(days.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _email_time_key(email: Mapping[str, Any]) -> tuple[datetime, str]:
    timestamp = email.get("sent_at")
    instant = (
        parse_rfc3339(timestamp).astimezone(timezone.utc)
        if timestamp is not None
        else datetime.min.replace(tzinfo=timezone.utc)
    )
    return instant, str(email.get("dedupe_key", ""))


def _fact_sort_key(item: Mapping[str, Any]) -> tuple[bool, datetime, str, int]:
    timestamp = item.get("fact_time")
    return (
        timestamp is None,
        parse_rfc3339(timestamp).astimezone(timezone.utc)
        if timestamp is not None
        else datetime.min.replace(tzinfo=timezone.utc),
        str(item["dedupe_key"]),
        int(item["_source_index"]),
    )


def _clock_text(clock: Callable[[], datetime]) -> str:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock 必须返回带时区的 datetime。")
    return value.isoformat()


def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} 必须是对象。")
    return value


def _list(value: object, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{path} 必须是数组。")
    return value


def _string_list(value: object, path: str, *, allow_empty: bool = False) -> list[str]:
    items = _list(value, path)
    result = []
    for index, item in enumerate(items):
        if not _nonblank(item):
            raise ValueError(f"{path}[{index}] 必须是非空字符串。")
        result.append(item)
    if not allow_empty and not result:
        return []
    if len(result) != len(set(result)):
        raise ValueError(f"{path} 不能重复。")
    return result


def _nonblank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _mailbox(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.count("@") == 1
        and all(value.split("@"))
        and not any(character.isspace() for character in value)
    )


__all__ = [
    "AnalysisInput",
    "EXTRACT_PROMPT_VERSION",
    "FACT_FIELDS",
    "Metrics",
    "ORDINARY_FACT_FIELDS",
    "ValidationError",
    "build_analysis_input",
    "calculate_metrics",
    "compute_input_version",
    "merge_facts",
    "parse_rfc3339",
]
