"""Responsibility: Build L2 email facts and independent supplementary company data.
Implementation: Identify synthetic sources separately from fact structure and validate facts strictly; preserve L1/L4 rules, pass backend-verified supplementary data independently, and prefer CRM headcount.
Relationships: Backend company context, shared enrichment contract, and analysis orchestration; no new authorization tokens.
Directory:
- Metrics: Store deterministic email metrics.
- Metrics.to_dict: Return an independent metrics dictionary.
- AnalysisInput: Store original L2 facts and independent business data.
- AnalysisInput.to_dict: Return L2 data for archival.
- ValidationError: Represent L2 construction failure.
- ValidationError.to_dict: Serialize an L2 error.
- build_analysis_input: Read and build a company L2 snapshot.
- merge_facts: Fully merge completed email facts.
- calculate_metrics: Compute deterministic email metrics.
- compute_input_version: Compute a reusable analysis input key.
- parse_rfc3339: Parse a timezone-aware timestamp.
- _validate_grouping: Validate grouping identity and members.
- _validate_context: Validate grouping and the company snapshot.
- _validate_email: Validate an L1 email envelope.
- _validate_facts: Validate extract-v7 facts.
- _latest_summary: Select the latest parsed email summary.
- _time_endpoint: Select an email time endpoint.
- _response_gap_days: Compute the latest valid response interval.
- _email_time_key: Build a stable email sort key.
- _fact_sort_key: Build a stable fact sort key.
- _clock_text: Read the construction clock with an explicit timezone.
- _mapping: Require an object type.
- _list: Require an array type.
- _string_list: Validate a string array.
- _nonblank: Check for a nonempty string.
- _mailbox: Check basic mailbox syntax.
Variable index:
- AnalysisInput.built_at: L2 construction time.
- AnalysisInput.business_context: CRM and independent experimental supplementary data.
- AnalysisInput.company: Original company data.
- AnalysisInput.company_id: Backend company identifier.
- AnalysisInput.external_snapshot_version: External CRM snapshot version.
- AnalysisInput.facts: Complete email facts.
- AnalysisInput.input_version: Cache version bound to the data.
- AnalysisInput.latest_message_summary: Latest parsed summary.
- AnalysisInput.member_dedupe_keys: Email membership keys.
- AnalysisInput.merge_version: Merge-rule version.
- AnalysisInput.metrics: Deterministic email metrics.
- AnalysisInput.priority_context: L4 scoring context excluded from archival.
- AnalysisInput.unparsed_message_count: Count of incomplete extractions.
- CRM_STATUSES: Valid CRM states.
- DIRECTIONS: Valid email directions.
- EXTRACT_PROMPT_VERSION: Fixed protocol version of the L1 skill.
- EXTRACT_STATUSES: Valid extraction states.
- FACT_FIELDS: Complete L1 fact fields.
- INTENT_HINTS: Valid purchasing intents.
- Metrics.crm_status: CRM registration status.
- Metrics.first_contact_at: Earliest email time.
- Metrics.has_history_order: Whether historical orders exist.
- Metrics.inbound_count: Incoming email count.
- Metrics.last_inbound_at: Latest incoming email time.
- Metrics.last_outbound_at: Latest outgoing email time.
- Metrics.outbound_count: Outgoing email count.
- Metrics.response_gap_days: Latest valid response interval in days.
- Metrics.substantive_inbound_count: Incoming email count with substantive updates.
- ORDINARY_FACT_FIELDS: Mergeable multi-value fact fields.
- ValidationError.code: Error code.
- ValidationError.field: Optional error field.
- ValidationError.message: Error details.
- __all__: Publicly exported symbols.
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
from agent.workflows.priority_signals import deadline_signals
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


# Function: Store deterministic email metrics.
# Logic: Store counts, times, and CRM state in a dataclass.
# Constraints: Do not access the backend.
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

    # Function: Return an independent metrics dictionary.
    # Inputs: No external parameters; read instance fields.
    # Outputs: Independent dictionary.
    # Logic: Deep-copy instance fields.
    # Constraints: Do not mutate the instance.
    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.__dict__)


# Function: Store original L2 facts and independent business data.
# Logic: Retain versions, sources, metrics, and local scoring context in a dataclass.
# Constraints: Do not insert supplementary data into email facts.
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

    # Function: Return L2 data for archival.
    # Inputs: No external parameters; read instance fields.
    # Outputs: Independent JSON dictionary.
    # Logic: Copy persistent fields while excluding priority_context used only by L4.
    # Constraints: Do not rewrite business data or sources.
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


# Function: Represent L2 construction failure.
# Logic: Store error code, details, and an optional field path.
# Constraints: Do not raise exceptions or write to the backend.
@dataclass
class ValidationError:
    code: str
    message: str
    field: str | None = None

    # Function: Serialize an L2 error.
    # Inputs: No external parameters; read instance fields.
    # Outputs: error dictionary.
    # Logic: Attach the location field only when field is nonempty.
    # Constraints: Exclude credentials.
    def to_dict(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.field is not None:
            error["field"] = self.field
        return {"error": error}


# Function: Read and build a company L2 snapshot.
# Inputs: `company_id`: requested company identifier; `backend`: backend client configured with employee identity; `merge_version`: merge-rule version; `clock`: clock returning a timezone-aware datetime.
# Outputs: AnalysisInput or ValidationError.
# Logic: Read grouping and context in order; validate L1, merge facts, compute metrics and a version including supplementary data, and preserve the separate supplementary object.
# Constraints: The backend verifies supplementary data authenticity; convert retrieval/construction failures into explicit errors; L4 communication remains limited to the latest 20 emails.
def build_analysis_input(
    company_id: str,
    *,
    backend: BackendClient,
    merge_version: str = "merge-v2",
    clock: Callable[[], datetime],
) -> AnalysisInput | ValidationError:
    """Read one company snapshot and complete validation, merging, metrics, and version computation."""
    if not _nonblank(company_id):
        return ValidationError("invalid_input", "company_id must not be empty.", "company_id")
    if not _nonblank(merge_version):
        return ValidationError("invalid_input", "merge_version must not be empty.", "merge_version")

    try:
        grouping = backend.get_company_grouping(company_id)
    except Exception as error:
        return ValidationError("grouping_retrieval_failed", f"Company grouping read failed: {error}")
    try:
        grouping = _validate_grouping(grouping, company_id)
    except ValueError as error:
        return ValidationError("invalid_backend_data", str(error))
    try:
        context = backend.get_company_context(company_id)
    except Exception as error:
        return ValidationError("context_retrieval_failed", f"Company context read failed: {error}")

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
        return ValidationError("analysis_input_failed", f"L2 build failed: {error}")

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
    priority_context["signals"].extend(deadline_signals(recent_emails))

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


# Function: Fully merge completed email facts.
# Inputs: `emails`: standard email list.
# Outputs: Dictionary mapping fields to fact lists.
# Logic: Preserve values, sources, and evidence per field; sort stably and remove temporary source indices.
# Constraints: Do not overwrite historical facts or classify supplementary data as L1.
def merge_facts(emails: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Preserve facts, sources, original evidence, and fact times from all completed emails."""
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


# Function: Compute deterministic email metrics.
# Inputs: `emails`: standard email list; `crm_status`: CRM status; `orders`: historical orders.
# Outputs: Metrics.
# Logic: Compute counts and response intervals from direction, valid extractions, times, and orders.
# Constraints: Do not infer missing times or orders.
def calculate_metrics(
    emails: list[dict[str, Any]],
    *,
    crm_status: str,
    orders: list[Any],
) -> Metrics:
    """Compute deterministic email metrics used by page sorting and L4."""
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


# Function: Compute a reusable analysis input key.
# Inputs: `emails`: standard email list; `merge_version`: merge-rule version; `external_snapshot_version`: original CRM snapshot version; `enrichment`: optional backend supplementary data.
# Outputs: SHA-256 string.
# Logic: Delegate to the shared version function to bind emails, merge rules, CRM, and optional experimental supplementary content.
# Constraints: Preserve the legacy hash structure when supplementary data is absent.
def compute_input_version(
    emails: list[dict[str, Any]],
    merge_version: str,
    external_snapshot_version: str,
    enrichment: Mapping[str, Any] | None = None,
) -> str:
    """Generate a stable version for analysis caching and idempotency."""
    return enrichment_input_version(emails, merge_version, external_snapshot_version, enrichment)


# Function: Parse a timezone-aware timestamp.
# Inputs: `value`: value to inspect.
# Outputs: datetime.
# Logic: Accept ISO timestamps and the Z suffix, checking timezone presence.
# Constraints: Invalid values raise ValueError; do not fill missing timezone from the local environment.
def parse_rfc3339(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Time must be a nonempty RFC3339 string.")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"Invalid time format: {value}") from None
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"Time must include a time zone: {value}")
    return result


# Function: Validate grouping identity and members.
# Inputs: `raw`: original value to validate; `company_id`: requested company identifier.
# Outputs: Grouping dictionary.
# Logic: Check company, CRM enums, contacts, and email natural keys, copying required fields.
# Constraints: Reject duplicate members and invalid mailboxes immediately.
def _validate_grouping(raw: object, company_id: str) -> dict[str, Any]:
    grouping = _mapping(raw, "Grouping")
    if grouping.get("company_id") != company_id:
        raise ValueError("Grouping.company_id does not match the request.")
    if grouping.get("crm_status") not in CRM_STATUSES:
        raise ValueError("Grouping.crm_status is invalid.")
    domains = _list(grouping.get("domains"), "Grouping.domains")
    contacts = _list(grouping.get("contacts"), "Grouping.contacts")
    members = _string_list(
        grouping.get("member_dedupe_keys"), "Grouping.member_dedupe_keys"
    )
    if len(members) != len(set(members)):
        raise ValueError("Grouping.member_dedupe_keys must be unique.")

    normalized_contacts = []
    for index, raw_contact in enumerate(contacts):
        contact = _mapping(raw_contact, f"Grouping.contacts[{index}]")
        email = contact.get("contact_email")
        if not _mailbox(email):
            raise ValueError(f"Grouping.contacts[{index}].contact_email is invalid.")
        count = contact.get("interaction_count")
        if type(count) is not int or count < 0:
            raise ValueError(f"Grouping.contacts[{index}].interaction_count is invalid.")
        if type(contact.get("is_primary")) is not bool:
            raise ValueError(f"Grouping.contacts[{index}].is_primary is invalid.")
        name = contact.get("contact_name")
        if name is not None and not isinstance(name, str):
            raise ValueError(f"Grouping.contacts[{index}].contact_name is invalid.")
        normalized_contacts.append(copy.deepcopy(dict(contact)))

    return {
        "company_id": company_id,
        "company_name": grouping.get("company_name"),
        "crm_status": grouping["crm_status"],
        "domains": copy.deepcopy(domains),
        "contacts": normalized_contacts,
        "member_dedupe_keys": members,
    }


# Function: Validate grouping and the company snapshot.
# Inputs: `raw`: original value to validate; `grouping`: validated grouping.
# Outputs: Independent context dictionary.
# Logic: Check email membership, CRM headcount, and business collections; copy backend supplementary data and optional scoring context.
# Constraints: The backend validates supplementary data; do not repeat entity matching in the agent.
def _validate_context(raw: object, grouping: Mapping[str, Any]) -> dict[str, Any]:
    context = _mapping(raw, "CompanyContext")
    if context.get("company_id") != grouping["company_id"]:
        raise ValueError("CompanyContext.company_id does not match Grouping.")
    external_version = context.get("external_snapshot_version")
    if not _nonblank(external_version):
        raise ValueError("CompanyContext.external_snapshot_version must not be empty.")

    raw_emails = _list(context.get("emails"), "CompanyContext.emails")
    emails = [_validate_email(email, index) for index, email in enumerate(raw_emails)]
    email_keys = [email["dedupe_key"] for email in emails]
    if len(email_keys) != len(set(email_keys)):
        raise ValueError("CompanyContext.emails dedupe_key values must be unique.")
    if set(email_keys) != set(grouping["member_dedupe_keys"]):
        raise ValueError("CompanyContext.emails does not match company membership.")

    customer = _mapping(context.get("customer"), "CompanyContext.customer")
    tickets = _list(context.get("tickets"), "CompanyContext.tickets")
    quotes = _list(context.get("quotes"), "CompanyContext.quotes")
    orders = _list(context.get("orders"), "CompanyContext.orders")
    priority_context = context.get("priority_context")
    if priority_context is not None and not isinstance(priority_context, Mapping):
        raise ValueError("CompanyContext.priority_context must be an object.")
    employee_count = customer.get("employee_count")
    if employee_count is not None and (type(employee_count) is not int or employee_count < 0):
        raise ValueError("CompanyContext.customer.employee_count is invalid.")
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


# Function: Validate an L1 email envelope.
# Inputs: `raw`: original value to validate; `index`: email index.
# Outputs: Email dictionary.
# Logic: Check natural key, direction, declared fact structure/status, timestamps, and corresponding facts; retain synthetic source versions unchanged.
# Constraints: Emails outside completed status cannot carry facts; invalid structure raises ValueError.
def _validate_email(raw: object, index: int) -> dict[str, Any]:
    email = _mapping(raw, f"emails[{index}]")
    dedupe_key = email.get("dedupe_key")
    if not _nonblank(dedupe_key):
        raise ValueError(f"emails[{index}].dedupe_key must not be empty.")
    direction = email.get("direction")
    if direction not in DIRECTIONS:
        raise ValueError(f"emails[{index}].direction is invalid.")
    status = email.get("extract_status")
    if status not in EXTRACT_STATUSES:
        raise ValueError(f"emails[{index}].extract_status is invalid.")
    extract_version = email.get("extract_prompt_version")
    if not compatible_extraction(email, EXTRACT_PROMPT_VERSION):
        raise ValueError(f"emails[{index}] is not a supported L1 structure.")

    sent_at = email.get("sent_at")
    if sent_at is not None:
        parse_rfc3339(sent_at)
    thread_id = email.get("thread_id")
    if thread_id is not None and not _nonblank(thread_id):
        raise ValueError(f"emails[{index}].thread_id is invalid.")
    subject = email.get("subject", "")
    if not isinstance(subject, str):
        raise ValueError(f"emails[{index}].subject is invalid.")

    facts = email.get("facts")
    if status == "completed":
        facts = _validate_facts(facts, index)
    elif facts is not None:
        raise ValueError(f"emails[{index}] facts must be null unless status is completed.")

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


# Function: Validate extract-v7 facts.
# Inputs: `raw`: original value to validate; `email_index`: index of the email containing these facts.
# Outputs: Normalized fact dictionary.
# Logic: Check exact fields, summary, intent, and evidence; copy deduplicated multi-value facts.
# Constraints: Do not change extraction versions, enums, or evidence requirements.
def _validate_facts(raw: object, email_index: int) -> dict[str, Any]:
    facts = _mapping(raw, f"emails[{email_index}].facts")
    if set(facts) != set(FACT_FIELDS):
        raise ValueError(f"emails[{email_index}].facts fields do not match {EXTRACT_PROMPT_VERSION}.")
    if type(facts["has_substantive_update"]) is not bool:
        raise ValueError("has_substantive_update must be a boolean.")
    summary = facts["message_summary"]
    if not isinstance(summary, str) or len(summary) > 80:
        raise ValueError("message_summary must be a string of at most 80 characters.")
    intent_hint = facts["intent_hint"]
    if (intent_hint is not None and not isinstance(intent_hint, str)) or intent_hint not in INTENT_HINTS:
        raise ValueError("intent_hint has an invalid enum value.")
    intent_evidences = _string_list(facts["intent_evidences"], "intent_evidences", allow_empty=True)
    if intent_hint is None and intent_evidences:
        raise ValueError("intent_evidences must be empty when there is no purchase stage.")
    if intent_hint is not None and not intent_evidences:
        raise ValueError("A purchase stage requires verbatim evidence.")

    normalized = copy.deepcopy(dict(facts))
    for field in ORDINARY_FACT_FIELDS:
        groups = _list(facts[field], field)
        seen: set[str] = set()
        normalized_groups = []
        for index, raw_group in enumerate(groups):
            group = _mapping(raw_group, f"{field}[{index}]")
            if set(group) != {"value", "evidences"}:
                raise ValueError(f"{field}[{index}] fields must be value and evidences.")
            value = group.get("value")
            if not _nonblank(value) or value in seen:
                raise ValueError(f"{field}[{index}].value is empty or duplicated.")
            seen.add(value)
            evidences = _string_list(
                group.get("evidences"), f"{field}[{index}].evidences"
            )
            if not evidences:
                raise ValueError(f"{field}[{index}].evidences must not be empty.")
            normalized_groups.append({"value": value, "evidences": evidences})
        normalized[field] = normalized_groups
    return normalized


# Function: Select the latest parsed email summary.
# Inputs: `emails`: standard email list.
# Outputs: Summary or None.
# Logic: Select the greatest completed email by time and natural key.
# Constraints: Do not generate a new summary from unparsed emails.
def _latest_summary(emails: list[dict[str, Any]]) -> str | None:
    completed = [email for email in emails if email["extract_status"] == "completed"]
    if not completed:
        return None
    latest = max(completed, key=_email_time_key)
    return latest["facts"]["message_summary"]


# Function: Select an email time endpoint.
# Inputs: `emails`: standard email list; `latest`: whether to select the latest endpoint.
# Outputs: Original timestamp string or None.
# Logic: Filter emails without timestamps, then choose the maximum or minimum time key according to latest.
# Constraints: Do not fill unknown times.
def _time_endpoint(emails: list[dict[str, Any]], *, latest: bool) -> str | None:
    candidates = [email for email in emails if email.get("sent_at") is not None]
    if not candidates:
        return None
    selected = max(candidates, key=_email_time_key) if latest else min(candidates, key=_email_time_key)
    return selected["sent_at"]


# Function: Compute the latest valid response interval.
# Inputs: `emails`: standard email list.
# Outputs: Day count or None.
# Logic: Within one thread, pair incoming mail with its first subsequent outgoing mail; choose the latest incoming pair and round half up to two decimals.
# Constraints: Preserve existing time-pairing and rounding rules.
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


# Function: Build a stable email sort key.
# Inputs: `email`: one email.
# Outputs: Timestamp and natural-key tuple.
# Logic: Convert to UTC, place missing timestamps at the minimum instant, and break ties with natural keys.
# Constraints: Emails without timestamps sort first.
def _email_time_key(email: Mapping[str, Any]) -> tuple[datetime, str]:
    timestamp = email.get("sent_at")
    instant = (
        parse_rfc3339(timestamp).astimezone(timezone.utc)
        if timestamp is not None
        else datetime.min.replace(tzinfo=timezone.utc)
    )
    return instant, str(email.get("dedupe_key", ""))


# Function: Build a stable fact sort key.
# Inputs: `item`: one merged fact.
# Outputs: Sort tuple.
# Logic: Sort by missing-time status, UTC time, natural key, and original index.
# Constraints: Do not change fact content.
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


# Function: Read the construction clock with an explicit timezone.
# Inputs: `clock`: clock returning a timezone-aware datetime.
# Outputs: ISO timestamp string.
# Logic: Call clock and check the datetime timezone.
# Constraints: Invalid clocks raise a validation exception.
def _clock_text(clock: Callable[[], datetime]) -> str:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime.")
    return value.isoformat()


# Function: Require an object type.
# Inputs: `value`: value to inspect; `path`: error location path.
# Outputs: Mapping.
# Logic: Check Mapping and return the original value.
# Constraints: Invalid values raise ValueError; path identifies the error location.
def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must be an object.")
    return value


# Function: Require an array type.
# Inputs: `value`: value to inspect; `path`: error location path.
# Outputs: list.
# Logic: Check list and return the original value.
# Constraints: Invalid values raise ValueError; path identifies the error location.
def _list(value: object, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be an array.")
    return value


# Function: Validate a string array.
# Inputs: `value`: value to inspect; `path`: error location path; `allow_empty`: existing empty-array option.
# Outputs: String list.
# Logic: Check nonempty strings and duplicates; allow empty arrays.
# Constraints: Retain allow_empty in the existing signature without changing empty-array behavior.
def _string_list(value: object, path: str, *, allow_empty: bool = False) -> list[str]:
    items = _list(value, path)
    result = []
    for index, item in enumerate(items):
        if not _nonblank(item):
            raise ValueError(f"{path}[{index}] must be a nonempty string.")
        result.append(item)
    if not allow_empty and not result:
        return []
    if len(result) != len(set(result)):
        raise ValueError(f"{path} must be unique.")
    return result


# Function: Check for a nonempty string.
# Inputs: `value`: value to inspect.
# Outputs: bool.
# Logic: Check type and truthiness after stripping whitespace.
# Constraints: Do not change the original string.
def _nonblank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


# Function: Check basic mailbox syntax.
# Inputs: `value`: value to inspect.
# Outputs: bool.
# Logic: Require one @, nonempty parts, and no whitespace.
# Constraints: Do not validate DNS or mailbox reachability.
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
