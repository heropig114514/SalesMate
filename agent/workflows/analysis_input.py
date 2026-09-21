"""职责：构建 L2 邮件事实与独立公司补充资料。
实现：合成来源与事实结构分别识别，事实字段校验保持严格；保持原有 L1/L4 规则，资料经后端核验后独立传递，人数优先 CRM。
关联：后端公司上下文、共享 enrichment 契约与分析编排；不新增授权令牌。
目录：
- Metrics：存储确定性邮件指标。
- Metrics.to_dict：输出隔离的指标字典。
- AnalysisInput：存储 L2 原始事实和独立业务资料。
- AnalysisInput.to_dict：输出待归档 L2。
- ValidationError：表示 L2 构建失败。
- ValidationError.to_dict：序列化 L2 错误。
- build_analysis_input：读取和构建公司 L2 快照。
- merge_facts：完整归并已完成邮件事实。
- calculate_metrics：计算确定性邮件指标。
- compute_input_version：计算可复用的分析输入键。
- parse_rfc3339：解析带时区时间。
- _validate_grouping：核对归组身份及成员。
- _validate_context：核对归组与公司快照。
- _validate_email：核对 L1 邮件封装。
- _validate_facts：核对 extract-v7 事实。
- _latest_summary：选择最新已解析邮件摘要。
- _time_endpoint：选择邮件时间端点。
- _response_gap_days：计算最近有效回复间隔。
- _email_time_key：生成稳定邮件排序键。
- _fact_sort_key：生成稳定事实排序键。
- _clock_text：读取明确时区的构建时钟。
- _mapping：要求对象类型。
- _list：要求数组类型。
- _string_list：核对字符串数组。
- _nonblank：判断非空字符串。
- _mailbox：检查邮箱基本形式。
变量索引：
- AnalysisInput.built_at：L2 构建时间。
- AnalysisInput.business_context：CRM 与独立实验补充资料。
- AnalysisInput.company：原始公司资料。
- AnalysisInput.company_id：后端公司标识。
- AnalysisInput.external_snapshot_version：CRM 外部快照版本。
- AnalysisInput.facts：完整邮件事实。
- AnalysisInput.input_version：绑定资料的缓存版本。
- AnalysisInput.latest_message_summary：最近已解析摘要。
- AnalysisInput.member_dedupe_keys：邮件成员键。
- AnalysisInput.merge_version：归并规则版本。
- AnalysisInput.metrics：确定性邮件指标。
- AnalysisInput.priority_context：不归档的 L4 评分上下文。
- AnalysisInput.unparsed_message_count：未完成抽取数量。
- CRM_STATUSES：合法 CRM 状态。
- DIRECTIONS：合法邮件方向。
- EXTRACT_PROMPT_VERSION：L1 Skill 的固定协议版本。
- EXTRACT_STATUSES：合法抽取状态。
- FACT_FIELDS：完整 L1 事实字段。
- INTENT_HINTS：合法采购意向。
- Metrics.crm_status：CRM 建档状态。
- Metrics.first_contact_at：最早邮件时间。
- Metrics.has_history_order：是否有历史订单。
- Metrics.inbound_count：来信数量。
- Metrics.last_inbound_at：最近来信时间。
- Metrics.last_outbound_at：最近外发时间。
- Metrics.outbound_count：外发数量。
- Metrics.response_gap_days：最近有效响应天数。
- Metrics.substantive_inbound_count：实质更新来信数量。
- ORDINARY_FACT_FIELDS：可归并的多值事实字段。
- ValidationError.code：错误码。
- ValidationError.field：可选错误字段。
- ValidationError.message：错误说明。
- __all__：公开导出的符号。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable, Mapping

from integrations.extraction_contract import compatible_extraction
from agent.clients.backend_api import BackendClient
from agent.skills import load_skill
from integrations.company_enrichment import input_version as enrichment_input_version


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
INTENT_HINTS = frozenset({
    "L1 Exploring", "L2 Interested", "L3 Qualified", "L4 Evaluating",
    "L5 Negotiating", "L6 Purchase Ready", None,
})
CRM_STATUSES = frozenset({"unregistered", "registered"})


# 功能：存储确定性邮件指标。
# 逻辑：dataclass 保存计数、时间和 CRM 状态。
# 约束：不访问后端。
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

    # 功能：输出隔离的指标字典。
    # 输入：无外部参数，读取实例字段。
    # 输出：独立字典。
    # 逻辑：深复制实例字段。
    # 约束：不修改实例。
    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.__dict__)


# 功能：存储 L2 原始事实和独立业务资料。
# 逻辑：dataclass 保留版本、来源、指标及本地评分上下文。
# 约束：补充资料不写入邮件事实。
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

    # 功能：输出待归档 L2。
    # 输入：无外部参数，读取实例字段。
    # 输出：独立 JSON 字典。
    # 逻辑：复制持久字段并排除仅用于 L4 的 priority_context。
    # 约束：不改写业务数据和来源。
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


# 功能：表示 L2 构建失败。
# 逻辑：保存错误码、说明与可选字段路径。
# 约束：不抛出异常或写后端。
@dataclass
class ValidationError:
    code: str
    message: str
    field: str | None = None

    # 功能：序列化 L2 错误。
    # 输入：无外部参数，读取实例字段。
    # 输出：error 字典。
    # 逻辑：仅在 field 非空时附加定位字段。
    # 约束：不包含凭证。
    def to_dict(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.field is not None:
            error["field"] = self.field
        return {"error": error}


# 功能：读取和构建公司 L2 快照。
# 输入：`company_id` 为请求的公司标识；`backend` 为已配置员工身份的后端客户端；`merge_version` 为归并规则版本；`clock` 为返回带时区 datetime 的时钟。
# 输出：AnalysisInput 或 ValidationError。
# 逻辑：依次读取归组与上下文，校验 L1、归并事实、计算指标及含补充资料的版本；保留独立补充对象。
# 约束：后端负责补充资料真实性；读取和构建异常转为明确错误；L4 通信仍限最近 20 封。
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
            context.get("company_enrichment"),
        )
        built_at = _clock_text(clock)
    except ValueError as error:
        return ValidationError("invalid_backend_data", str(error))
    except Exception as error:
        return ValidationError("analysis_input_failed", f"L2 构建失败：{error}")

    priority_context = dict(context.get("priority_context") or {})
    recent_emails = sorted(
        (
            email for email in emails
            if email["extract_status"] == "completed"
            and email["direction"] in {"inbound", "outbound"}
        ),
        key=_email_time_key,
    )[-20:]
    priority_context["communications"] = [
        {
            "message_id": email["dedupe_key"],
            "sender": "customer" if email["direction"] == "inbound" else "employee",
            "timestamp": email["sent_at"],
            "content": "\n".join(
                part for part in (email["subject"], email.get("body_text"))
                if isinstance(part, str) and part.strip()
            ),
        }
        for email in recent_emails
        if email["subject"] or email.get("body_text")
    ]
    priority_context["signals"] = [
        {
            "type": email["facts"]["intent_hint"],
            "value": None,
            "confidence": 1.0,
            "evidence": email["facts"]["intent_evidences"][0],
            "source_id": email["dedupe_key"],
        }
        for email in recent_emails
        if email["direction"] == "inbound"
        and email["facts"]["intent_hint"] in INTENT_HINTS - {None}
        and email["facts"]["intent_evidences"]
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
            **({"company_enrichment": copy.deepcopy(context["company_enrichment"])}
               if "company_enrichment" in context else {}),
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


# 功能：完整归并已完成邮件事实。
# 输入：`emails` 为标准邮件列表。
# 输出：字段到事实列表的字典。
# 逻辑：逐字段保留值、来源和证据，稳定排序后去除临时来源索引。
# 约束：不覆盖历史事实，不将补充资料归为 L1。
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


# 功能：计算确定性邮件指标。
# 输入：`emails` 为标准邮件列表；`crm_status` 为CRM 状态；`orders` 为历史订单。
# 输出：Metrics。
# 逻辑：按方向、有效抽取、时间和订单计算计数与响应间隔。
# 约束：不推测缺失时间或订单。
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


# 功能：计算可复用的分析输入键。
# 输入：`emails` 为标准邮件列表；`merge_version` 为归并规则版本；`external_snapshot_version` 为原 CRM 快照版本；`enrichment` 为可选的后端补充资料。
# 输出：sha256 字符串。
# 逻辑：委托共享版本函数绑定邮件、归并、CRM 及可选实验资料内容。
# 约束：未提供补充资料时保留旧哈希结构。
def compute_input_version(
    emails: list[dict[str, Any]],
    merge_version: str,
    external_snapshot_version: str,
    enrichment: Mapping[str, Any] | None = None,
) -> str:
    """生成分析缓存和幂等使用的稳定版本。"""
    return enrichment_input_version(emails, merge_version, external_snapshot_version, enrichment)


# 功能：解析带时区时间。
# 输入：`value` 为待检查值。
# 输出：datetime。
# 逻辑：接受 ISO 时间及 Z 后缀，检查时区。
# 约束：无效值抛 ValueError，不使用本地时区补齐。
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


# 功能：核对归组身份及成员。
# 输入：`raw` 为待校验原始值；`company_id` 为请求的公司标识。
# 输出：归组字典。
# 逻辑：检查公司、CRM 枚举、联系人和邮件天然键，复制必要字段。
# 约束：重复成员及非法邮箱立即失败。
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


# 功能：核对归组与公司快照。
# 输入：`raw` 为待校验原始值；`grouping` 为已校验归组。
# 输出：独立上下文字典。
# 逻辑：检查邮件成员集合、CRM 人数和业务集合，复制后端补充资料及可选评分上下文。
# 约束：补充资料由后端核验，不在 Agent 重做实体匹配。
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
        **({"company_enrichment": copy.deepcopy(context["company_enrichment"])}
           if "company_enrichment" in context else {}),
        "priority_context": copy.deepcopy(dict(priority_context))
        if priority_context is not None else None,
    }


# 功能：核对 L1 邮件封装。
# 输入：`raw` 为待校验原始值；`index` 为邮件索引。
# 输出：邮件字典。
# 逻辑：检查天然键、方向、声明的事实结构与状态、时间和对应 facts；合成来源版本原样保留。
# 约束：非 completed 邮件不能带事实；非法结构抛 ValueError。
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
    extract_version = email.get("extract_prompt_version")
    if not compatible_extraction(email, EXTRACT_PROMPT_VERSION):
        raise ValueError(f"emails[{index}] 不是受支持的 L1 结构。")

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
        extract_prompt_version=extract_version,
        facts=facts,
    )
    return result


# 功能：核对 extract-v7 事实。
# 输入：`raw` 为待校验原始值；`email_index` 为事实所属邮件索引。
# 输出：规范事实字典。
# 逻辑：检查精确字段、摘要、意向与证据，复制去重的多值事实。
# 约束：不修改抽取版本、枚举或证据要求。
def _validate_facts(raw: object, email_index: int) -> dict[str, Any]:
    facts = _mapping(raw, f"emails[{email_index}].facts")
    if set(facts) != set(FACT_FIELDS):
        raise ValueError(f"emails[{email_index}].facts 字段与 {EXTRACT_PROMPT_VERSION} 不一致。")
    if type(facts["has_substantive_update"]) is not bool:
        raise ValueError("has_substantive_update 必须是布尔值。")
    summary = facts["message_summary"]
    if not isinstance(summary, str) or len(summary) > 80:
        raise ValueError("message_summary 必须是 80 字以内的字符串。")
    intent_hint = facts["intent_hint"]
    if (intent_hint is not None and not isinstance(intent_hint, str)) or intent_hint not in INTENT_HINTS:
        raise ValueError("intent_hint 枚举无效。")
    intent_evidences = _string_list(facts["intent_evidences"], "intent_evidences", allow_empty=True)
    if intent_hint is None and intent_evidences:
        raise ValueError("无采购阶段时 intent_evidences 必须为空。")
    if intent_hint is not None and not intent_evidences:
        raise ValueError("采购阶段必须提供原文依据。")

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


# 功能：选择最新已解析邮件摘要。
# 输入：`emails` 为标准邮件列表。
# 输出：摘要或 None。
# 逻辑：按时间和天然键选择最大 completed 邮件。
# 约束：不使用未解析邮件生成新摘要。
def _latest_summary(emails: list[dict[str, Any]]) -> str | None:
    completed = [email for email in emails if email["extract_status"] == "completed"]
    if not completed:
        return None
    latest = max(completed, key=_email_time_key)
    return latest["facts"]["message_summary"]


# 功能：选择邮件时间端点。
# 输入：`emails` 为标准邮件列表；`latest` 为是否选择最新端点。
# 输出：原始时间字符串或 None。
# 逻辑：过滤无时间邮件，按 latest 选择最大或最小时间键。
# 约束：不填补未知时间。
def _time_endpoint(emails: list[dict[str, Any]], *, latest: bool) -> str | None:
    candidates = [email for email in emails if email.get("sent_at") is not None]
    if not candidates:
        return None
    selected = max(candidates, key=_email_time_key) if latest else min(candidates, key=_email_time_key)
    return selected["sent_at"]


# 功能：计算最近有效回复间隔。
# 输入：`emails` 为标准邮件列表。
# 输出：天数或 None。
# 逻辑：同线程中取来信之后首封外发，以最近来信确定一对，半向上舍入到两位。
# 约束：不修改既定时间配对及舍入规则。
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


# 功能：生成稳定邮件排序键。
# 输入：`email` 为单封邮件。
# 输出：时间与天然键元组。
# 逻辑：转 UTC，无时间置最小瞬间，天然键打破平局。
# 约束：无时间邮件排列在前。
def _email_time_key(email: Mapping[str, Any]) -> tuple[datetime, str]:
    timestamp = email.get("sent_at")
    instant = (
        parse_rfc3339(timestamp).astimezone(timezone.utc)
        if timestamp is not None
        else datetime.min.replace(tzinfo=timezone.utc)
    )
    return instant, str(email.get("dedupe_key", ""))


# 功能：生成稳定事实排序键。
# 输入：`item` 为单条归并事实。
# 输出：排序元组。
# 逻辑：按是否无时间、UTC 时间、天然键和原索引排序。
# 约束：不修改事实内容。
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


# 功能：读取明确时区的构建时钟。
# 输入：`clock` 为返回带时区 datetime 的时钟。
# 输出：ISO 时间字符串。
# 逻辑：调用 clock 并检查 datetime 时区。
# 约束：无效时钟抛校验异常。
def _clock_text(clock: Callable[[], datetime]) -> str:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock 必须返回带时区的 datetime。")
    return value.isoformat()


# 功能：要求对象类型。
# 输入：`value` 为待检查值；`path` 为错误定位路径。
# 输出：Mapping。
# 逻辑：检查 Mapping 后返回原值。
# 约束：非法值抛 ValueError，path 定位错误。
def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} 必须是对象。")
    return value


# 功能：要求数组类型。
# 输入：`value` 为待检查值；`path` 为错误定位路径。
# 输出：list。
# 逻辑：检查 list 后返回原值。
# 约束：非法值抛 ValueError，path 定位错误。
def _list(value: object, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{path} 必须是数组。")
    return value


# 功能：核对字符串数组。
# 输入：`value` 为待检查值；`path` 为错误定位路径；`allow_empty` 为既有空数组选项。
# 输出：字符串列表。
# 逻辑：检查非空字符串和重复元素；允许空数组。
# 约束：allow_empty 保留既有签名，不改变空数组行为。
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


# 功能：判断非空字符串。
# 输入：`value` 为待检查值。
# 输出：bool。
# 逻辑：检查类型与去空白后的真值。
# 约束：不改变原字符串。
def _nonblank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


# 功能：检查邮箱基本形式。
# 输入：`value` 为待检查值。
# 输出：bool。
# 逻辑：要求单个 @、两端非空且不含空白。
# 约束：不验证 DNS 或邮箱可达性。
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
