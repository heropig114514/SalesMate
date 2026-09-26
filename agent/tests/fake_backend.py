"""Responsibility: In-process BackendClient fake used only by automated agent tests.
Implementation: Exercise real local functions against fixed in-memory data and mocked service boundaries; assertions check outputs, errors, and interactions.
Relationships: Uses agent workflows and clients without proving live mailbox, model, or backend availability.

Directory:
- FakeBackend: Provide deterministic offline fixtures with plain dictionaries; excluded from production execution.
- FakeBackend.__init__: Initialize isolated fixture state and configured simulated responses.
- FakeBackend.submit_emails: Declare the standard L1 email submission interface.
- FakeBackend.get_company_grouping: Read company grouping.
- FakeBackend.get_company_context: Read company business context.
- FakeBackend.save_analysis_input: Save L2 input and track its version.
- FakeBackend.get_latest_analysis_input: Read the latest L2 snapshot.
- FakeBackend.get_cached_analysis: Look up the L3 cache for an input and prompt version.
- FakeBackend.save_analysis: Submit L3 analysis.
- FakeBackend.save_score: Submit L4 scores.
- FakeBackend.claim_jobs: Claim and cache company leases.
- FakeBackend.report_job: Report a company job result.
- FakeBackend.job_reports: Return an independent copy of recorded fixture job reports.
- FakeBackend._enqueue_job: Queue a fixture company job.
- FakeBackend._dynamic_grouping: Build grouping from submitted in-memory emails.
- FakeBackend._dynamic_context: Build context from in-memory company state.
- FakeBackend._static_snapshot: Read a fixture snapshot or simulated retrieval failure.
- FakeBackend._build_static_snapshot: Build the selected deterministic company fixture.
- _sample_email: Build a synthetic email fixture.
- _sample_facts: Build synthetic extract-v7 facts.
- _company_id: Derive a deterministic fixture company identifier.
- _is_substantive_business_email: Check completed substantive business facts in the test fixture.
- _empty_customer: Build empty customer context for a fixture.

Variable index:
- RETRIEVAL_SCENARIO_NAMES: Explicit simulated retrieval failure scenarios.
- VALID_SCENARIO_NAMES: Supported successful and boundary fixture scenarios.
- _FACT_FIELDS: Multi-value fixture fact field names.
- _PUBLIC_EMAIL_DOMAINS: Public domains excluded from fixture company-domain grouping.
- __all__: Public exports of this module.
"""

from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timezone
from typing import Any, Mapping

from agent.clients.backend_api import BackendRetrievalError


VALID_SCENARIO_NAMES = (
    "normal",
    "boundary-empty",
    "boundary-null-times",
    "boundary-no-orders",
)
RETRIEVAL_SCENARIO_NAMES = (
    "fail-grouping-retrieval",
    "fail-context-retrieval",
)
_PUBLIC_EMAIL_DOMAINS = frozenset(
    {"gmail.com", "outlook.com", "hotmail.com", "qq.com", "163.com", "126.com"}
)
_FACT_FIELDS = (
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


class FakeBackend:
    """Provide deterministic offline fixtures with plain dictionaries; excluded from production execution."""

    def __init__(self, *, scenario: str = "normal", seed: str | int | None = None):
        allowed = VALID_SCENARIO_NAMES + RETRIEVAL_SCENARIO_NAMES
        if scenario not in allowed:
            raise ValueError(f"未知 FakeBackend 场景：{scenario}")
        self.scenario = scenario
        self.seed = "salesmate-demo" if seed is None else str(seed)
        self._static_snapshots: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        self._emails: dict[str, dict[str, Any]] = {}
        self._email_companies: dict[str, str] = {}
        self._members: dict[str, list[str]] = {}
        self._versions: dict[str, int] = {}
        self._analysis_inputs: dict[str, dict[str, Any]] = {}
        self._analyses: dict[tuple[str, str], dict[str, Any]] = {}
        self._scores: dict[tuple[str, str], dict[str, Any]] = {}
        self._jobs: list[dict[str, Any]] = []
        self._job_reports: list[dict[str, Any]] = []
        self._job_sequence = 0

    def submit_emails(self, submissions: list[dict[str, Any]]) -> dict[str, Any]:
        created = updated = duplicate = 0
        affected: list[str] = []

        for submission in submissions:
            if not isinstance(submission, dict):
                raise ValueError("邮件提交必须是对象。")
            dedupe_key = submission.get("dedupe_key")
            if not isinstance(dedupe_key, str) or not dedupe_key:
                raise ValueError("邮件提交缺少 dedupe_key。")

            current = self._emails.get(dedupe_key)
            replace_failed = (
                current is not None
                and current.get("extract_status") == "failed"
                and submission.get("extract_status") == "completed"
            )
            if current is not None and not replace_failed:
                duplicate += 1
                continue

            if current is None:
                company_id = _company_id(submission)
                self._email_companies[dedupe_key] = company_id
                self._members.setdefault(company_id, []).append(dedupe_key)
                created += 1
            else:
                company_id = self._email_companies[dedupe_key]
                updated += 1

            self._emails[dedupe_key] = copy.deepcopy(submission)
            self._versions[company_id] = self._versions.get(company_id, 0) + 1
            if company_id not in affected:
                affected.append(company_id)
            if _is_substantive_business_email(submission):
                self._enqueue_job("email_ingested", company_id)

        return {
            "created_count": created,
            "updated_count": updated,
            "duplicate_count": duplicate,
            "affected_company_ids": affected,
        }

    def get_company_grouping(self, company_id: str) -> dict[str, Any]:
        if self.scenario == "fail-grouping-retrieval":
            raise BackendRetrievalError("FakeBackend 公司归组读取失败。")
        if company_id in self._members:
            return copy.deepcopy(self._dynamic_grouping(company_id))
        grouping, _ = self._static_snapshot(company_id)
        return copy.deepcopy(grouping)

    def get_company_context(self, company_id: str) -> dict[str, Any]:
        if self.scenario == "fail-context-retrieval":
            raise BackendRetrievalError("FakeBackend 公司上下文读取失败。")
        if company_id in self._members:
            return copy.deepcopy(self._dynamic_context(company_id))
        _, context = self._static_snapshot(company_id)
        return copy.deepcopy(context)

    def save_analysis_input(self, analysis_input: Mapping[str, Any]) -> None:
        document = copy.deepcopy(dict(analysis_input))
        self._analysis_inputs[str(document["company_id"])] = document

    def get_latest_analysis_input(self, company_id: str) -> dict[str, Any] | None:
        value = self._analysis_inputs.get(company_id)
        return None if value is None else copy.deepcopy(value)

    def get_cached_analysis(
        self, company_id: str, input_version: str
    ) -> dict[str, Any] | None:
        value = self._analyses.get((company_id, input_version))
        return None if value is None else copy.deepcopy(value)

    def save_analysis(self, analysis: Mapping[str, Any]) -> None:
        document = copy.deepcopy(dict(analysis))
        if document.get("status") == "completed":
            key = (str(document["company_id"]), str(document["input_version"]))
            self._analyses[key] = document

    def save_score(self, score: Mapping[str, Any]) -> None:
        document = copy.deepcopy(dict(score))
        key = (str(document["company_id"]), str(document["input_version"]))
        self._scores[key] = document

    def claim_jobs(self, limit: int) -> list[dict[str, Any]]:
        if type(limit) is not int or limit <= 0:
            return []
        jobs = self._jobs[:limit]
        del self._jobs[:limit]
        return copy.deepcopy(jobs)

    def report_job(self, report: Mapping[str, Any]) -> None:
        self._job_reports.append(copy.deepcopy(dict(report)))

    @property
    def job_reports(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._job_reports)

    def _enqueue_job(self, trigger: str, company_id: str) -> None:
        self._job_sequence += 1
        self._jobs.append(
            {
                "job_id": f"job-{self._job_sequence}",
                "trigger": trigger,
                "company_id": company_id,
                "enqueued_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    def _dynamic_grouping(self, company_id: str) -> dict[str, Any]:
        emails = [self._emails[key] for key in self._members[company_id]]
        contacts: dict[str, dict[str, Any]] = {}
        domains: list[str] = []
        company_name: str | None = None

        for email in emails:
            facts = email.get("facts") if isinstance(email.get("facts"), dict) else {}
            contact_email = email.get("contact_email")
            if isinstance(contact_email, str):
                contact = contacts.setdefault(
                    contact_email,
                    {
                        "contact_email": contact_email,
                        "contact_name": None,
                        "interaction_count": 0,
                        "is_primary": False,
                    },
                )
                contact["interaction_count"] += 1
                domain = contact_email.rsplit("@", 1)[-1].casefold()
                if domain not in domains:
                    domains.append(domain)
                name_facts = facts.get("contact_name", [])
                if name_facts:
                    contact["contact_name"] = name_facts[0].get("value")

            company_facts = facts.get("company_self_reported", [])
            if company_name is None and company_facts:
                company_name = company_facts[0].get("value")

        if contacts:
            primary = max(
                contacts.values(),
                key=lambda item: (item["interaction_count"], item["contact_email"]),
            )
            primary["is_primary"] = True
        if not company_name:
            company_name = domains[0].split(".")[0].title() if domains else None

        return {
            "company_id": company_id,
            "company_name": company_name,
            "crm_status": "unregistered",
            "domains": domains,
            "contacts": list(contacts.values()),
            "member_dedupe_keys": list(self._members[company_id]),
        }

    def _dynamic_context(self, company_id: str) -> dict[str, Any]:
        return {
            "company_id": company_id,
            "external_snapshot_version": f"ext-{self._versions[company_id]}",
            "emails": [
                copy.deepcopy(self._emails[key]) for key in self._members[company_id]
            ],
            "customer": _empty_customer(),
            "tickets": [],
            "quotes": [],
            "orders": [],
        }

    def _static_snapshot(
        self, company_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if company_id not in self._static_snapshots:
            self._static_snapshots[company_id] = self._build_static_snapshot(company_id)
        return self._static_snapshots[company_id]

    def _build_static_snapshot(
        self, company_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        token = hashlib.sha256(f"{self.seed}:{company_id}".encode("utf-8")).hexdigest()[:12]
        if self.scenario == "boundary-empty":
            emails: list[dict[str, Any]] = []
        else:
            null_times = self.scenario == "boundary-null-times"
            emails = [
                _sample_email(
                    token,
                    1,
                    "inbound",
                    None if null_times else "2026-09-10T09:00:00+08:00",
                    "completed",
                ),
                _sample_email(
                    token,
                    2,
                    "outbound",
                    None if null_times else "2026-09-10T11:00:00+08:00",
                    "completed",
                ),
                _sample_email(
                    token,
                    3,
                    "inbound",
                    None if null_times else "2026-09-11T09:00:00+08:00",
                    "failed",
                ),
            ]

        contact_email = f"buyer@customer-{token}.example"
        grouping = {
            "company_id": company_id,
            "company_name": f"Mock Company {token}",
            "crm_status": "registered",
            "domains": [f"customer-{token}.example"],
            "contacts": (
                []
                if not emails
                else [
                    {
                        "contact_email": contact_email,
                        "contact_name": "Mock Buyer",
                        "interaction_count": len(emails),
                        "is_primary": True,
                    }
                ]
            ),
            "member_dedupe_keys": [email["dedupe_key"] for email in emails],
        }
        orders = [] if self.scenario == "boundary-no-orders" else [
            {
                "order_id": f"order-{token}",
                "closed_at": "2025-06-01T08:30:00+08:00",
                "amount": 45000,
                "currency": "CNY",
                "products": ["Industrial sensor"],
                "source_system": "mock-erp",
            }
        ]
        context = {
            "company_id": company_id,
            "external_snapshot_version": f"snapshot-{token}-v1",
            "emails": emails,
            "customer": {
                "customer_id": f"customer-{token}",
                "industry_from_crm": "Industrial Automation",
                "employee_count": 260,
                "employee_count_source": "mock-crm",
                "first_deal_at": "2025-06-01T08:30:00+08:00",
            },
            "tickets": [
                {
                    "ticket_id": f"ticket-{token}",
                    "name": "Industrial sensor follow-up",
                    "stage": "需求沟通",
                    "amount": None,
                    "currency": "CNY",
                    "owner": "Demo Sales",
                }
            ],
            "quotes": [
                {
                    "quote_id": f"quote-{token}",
                    "ticket_id": f"ticket-{token}",
                    "sent_at": "2026-09-10T11:00:00+08:00",
                    "amount": 28000,
                    "currency": "CNY",
                    "status": "sent",
                    "evidence_type": "actual_outbound",
                }
            ],
            "orders": orders,
        }
        return grouping, context


def _sample_email(
    token: str,
    sequence: int,
    direction: str,
    sent_at: str | None,
    status: str,
) -> dict[str, Any]:
    mailbox = "sales@salesmate.example"
    contact = f"buyer@customer-{token}.example"
    message_id = f"mock-{token}-{sequence}"
    subject = "Industrial sensor quotation"
    body = "We need industrial sensors, quantity 120 units."
    return {
        "dedupe_key": f"{mailbox}:{message_id}",
        "mailbox_address": mailbox,
        "gmail_message_id": message_id,
        "thread_id": f"thread-{token}",
        "from": contact if direction == "inbound" else mailbox,
        "to": [mailbox] if direction == "inbound" else [contact],
        "cc": [],
        "sent_at": sent_at,
        "received_at": sent_at,
        "subject": subject,
        "body_text": body,
        "direction": direction,
        "contact_email": contact,
        "non_business_hint": False,
        "non_business_reason": None,
        "extract_status": status,
        "extract_prompt_version": "extract-v7",
        "extract_error": None if status == "completed" else "Mock extraction failure.",
        "facts": _sample_facts(token, sequence) if status == "completed" else None,
    }


def _sample_facts(token: str, sequence: int) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "has_substantive_update": True,
        "message_summary": (
            "Customer requested sensors and quantity."
            if sequence == 1
            else "Sales sent a formal quotation."
        ),
        "intent_hint": "L1 Exploring",
        "intent_evidences": ["industrial sensors"],
    }
    for field in _FACT_FIELDS:
        facts[field] = []
    if sequence == 1:
        facts["company_self_reported"] = [
            {"value": f"Mock Company {token}", "evidences": ["Mock Company"]}
        ]
        facts["product_need"] = [
            {"value": "Industrial sensors", "evidences": ["industrial sensors"]}
        ]
        facts["quantity"] = [
            {"value": "120 units", "evidences": ["quantity 120 units"]}
        ]
    else:
        facts["quote_reference"] = [
            {"value": "Q-2026-001", "evidences": ["quotation"]}
        ]
    return facts


def _company_id(submission: Mapping[str, Any]) -> str:
    contact = submission.get("contact_email")
    if isinstance(contact, str) and contact.count("@") == 1:
        normalized = contact.casefold()
        domain = normalized.rsplit("@", 1)[1]
        if domain in _PUBLIC_EMAIL_DOMAINS:
            return f"contact:{normalized}"
        return f"company:{domain}"
    digest = hashlib.sha256(str(submission.get("dedupe_key", "")).encode("utf-8")).hexdigest()
    return f"message:{digest[:12]}"


def _is_substantive_business_email(submission: Mapping[str, Any]) -> bool:
    facts = submission.get("facts")
    return (
        submission.get("extract_status") == "completed"
        and submission.get("non_business_hint") is not True
        and isinstance(facts, Mapping)
        and facts.get("has_substantive_update") is True
    )


def _empty_customer() -> dict[str, Any]:
    return {
        "customer_id": None,
        "industry_from_crm": None,
        "employee_count": None,
        "employee_count_source": None,
        "first_deal_at": None,
    }


__all__ = [
    "FakeBackend",
    "RETRIEVAL_SCENARIO_NAMES",
    "VALID_SCENARIO_NAMES",
]
