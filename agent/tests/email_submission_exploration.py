"""Task 1 offline bug-condition fixtures and Property 1 exploration tests.

The finite mutation tables in this module intentionally target the unfixed L1
workflow.  They never load local configuration, credentials, OAuth, Gmail, or
Bailian and make no network requests.
"""

import copy
import json
import unittest
from unittest.mock import Mock

from agent.workflows.l1_email import process_email


EMAIL_SUBMISSION_KEYS = frozenset(
    {
        "dedupe_key",
        "mailbox_address",
        "gmail_message_id",
        "thread_id",
        "from",
        "to",
        "cc",
        "sent_at",
        "received_at",
        "subject",
        "body_text",
        "direction",
        "contact_email",
        "non_business_hint",
        "non_business_reason",
        "extract_status",
        "extract_prompt_version",
        "extract_error",
        "facts",
    }
)
LEGACY_OR_INTERNAL_KEYS = frozenset(
    {"source", "mailbox_id", "message_id", "error", "headers", "eligible_body_text"}
)
MULTI_VALUE_FACT_FIELDS = (
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
FACTS_KEYS = frozenset(
    {
        "has_substantive_update",
        "message_summary",
        "intent_hint",
        "intent_evidences",
        *MULTI_VALUE_FACT_FIELDS,
    }
)

CANONICAL_GMAIL_ID = "GmAiL-ID-AbC123"
CANONICAL_THREAD_ID = "thread-77"
CANONICAL_RECEIVED_AT = "2026-09-08T02:12:30+00:00"
CANONICAL_SENT_AT = "2026-09-08T10:12:00+08:00"
CANONICAL_MAILBOX = "Sales@Example.com"
CANONICAL_SUBJECT = "检测设备采购询价"
CANONICAL_BODY = "本邮件新增需求：请提供 50 台检测设备报价，预算不超过 2 万，交期两周。"


def fact_group(value, *evidences):
    return {"value": value, "evidences": list(evidences)}


def canonical_complete_facts():
    """Return the extract-v7 completed-facts oracle."""
    facts = {
        "has_substantive_update": True,
        "message_summary": "客户询问 50 台检测设备的报价、预算和交期",
        "intent_hint": "L4 Evaluating",
        "intent_evidences": ["请提供 50 台检测设备报价"],
    }
    facts.update({field: [] for field in MULTI_VALUE_FACT_FIELDS})
    facts["product_need"] = [fact_group("检测设备", "检测设备")]
    facts["quantity"] = [fact_group("50 台", "50 台")]
    facts["budget"] = [fact_group("不超过 2 万", "预算不超过 2 万")]
    facts["delivery_time"] = [fact_group("两周", "交期两周")]
    return facts


def empty_content_facts():
    facts = {
        "has_substantive_update": False,
        "message_summary": "当前邮件没有可提取的正文",
        "intent_hint": None,
        "intent_evidences": [],
    }
    facts.update({field: [] for field in MULTI_VALUE_FACT_FIELDS})
    return facts


def old_four_field_facts():
    return {
        "has_substantive_update": True,
        "message_summary": "客户请求报价",
        "intent_hint": "L4 Evaluating",
        "intent_evidence": "请提供 50 台检测设备报价",
    }


def canonical_normalized_email(**updates):
    email = {
        "gmail_message_id": CANONICAL_GMAIL_ID,
        "thread_id": CANONICAL_THREAD_ID,
        "received_at": CANONICAL_RECEIVED_AT,
        "from": "buyer@example.com",
        "to": [CANONICAL_MAILBOX, "second@example.com"],
        "cc": ["assistant@example.com"],
        "sent_at": CANONICAL_SENT_AT,
        "subject": CANONICAL_SUBJECT,
        "body_text": CANONICAL_BODY,
        "eligible_body_text": CANONICAL_BODY,
        "headers": {},
    }
    email.update(updates)
    return email


class ProviderSpy:
    """Deterministic provider spy; no production provider can be reached."""

    def __init__(self, response=None, exception=None):
        self.response = canonical_complete_facts() if response is None else response
        self.exception = exception
        self.calls = []

    def __call__(self, subject, body_text):
        self.calls.append((subject, body_text))
        if self.exception is not None:
            raise self.exception
        return copy.deepcopy(self.response)


def observe(callable_):
    """Capture result/error as a non-sensitive tagged observation."""
    try:
        return {"kind": "result", "value": callable_()}
    except Exception as error:  # Exploration explicitly observes both branches.
        return {"kind": "error", "safe_category": type(error).__name__}


def mutate_facts(case):
    name, mutation = case
    facts = canonical_complete_facts()
    mutation(facts)
    return name, facts


def _delete(field):
    return lambda facts: facts.pop(field)


def _set(field, value):
    return lambda facts: facts.__setitem__(field, value)


FACTS_MUTATIONS = (
    ("legacy four-field object", lambda _: None, old_four_field_facts),
    ("missing order_reference", _delete("order_reference"), None),
    ("extra top-level fact", _set("unexpected", []), None),
    ("integer instead of strict bool", _set("has_substantive_update", 1), None),
    ("unsupported intent", _set("intent_hint", "deal"), None),
    ("81-character summary", _set("message_summary", "界" * 81), None),
    ("non-array fact field", _set("quantity", "50 台"), None),
    (
        "value without evidences",
        _set("quantity", [{"value": "50 台", "evidences": []}]),
        None,
    ),
    (
        "fabricated evidence",
        _set("quantity", [fact_group("100 台", "100 台")]),
        None,
    ),
)

NON_BUSINESS_CASES = (
    (
        "list-unsubscribe",
        {"List-Unsubscribe": "<mailto:leave@example.com>"},
        "buyer@example.com",
        "Matched the List-Unsubscribe rule.",
    ),
    (
        "precedence",
        {"Precedence": "BULK"},
        "buyer@example.com",
        "Matched the Precedence: bulk rule.",
    ),
    (
        "auto-submitted",
        {"Auto-Submitted": "auto-generated"},
        "buyer@example.com",
        "Matched the Auto-Submitted rule.",
    ),
    (
        "no-reply sender",
        {},
        "no-reply@example.com",
        "Matched the no-reply sender rule.",
    ),
    (
        "overlap uses stable first match",
        {
            "List-Unsubscribe": "<mailto:leave@example.com>",
            "Precedence": "bulk",
            "Auto-Submitted": "auto-generated",
        },
        "no-reply@example.com",
        "Matched the List-Unsubscribe rule.",
    ),
)


class ExplorationAssertions:
    def assert_exact_submission(self, result):
        self.assertEqual(set(result), EMAIL_SUBMISSION_KEYS)
        self.assertTrue(LEGACY_OR_INTERNAL_KEYS.isdisjoint(result))

    def assert_exact_completed_facts(self, facts):
        self.assertIsInstance(facts, dict)
        self.assertEqual(set(facts), FACTS_KEYS)
        self.assertIsInstance(facts["intent_evidences"], list)
        for field in MULTI_VALUE_FACT_FIELDS:
            self.assertIsInstance(facts[field], list)
            for group in facts[field]:
                self.assertEqual(set(group), {"value", "evidences"})
                self.assertIsInstance(group["evidences"], list)


class EmailSubmissionBugConditionExplorationTests(
    ExplorationAssertions, unittest.TestCase
):
    """Property 1 finite exploration over concern-specific mutations.

    **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7,
    2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12**
    """

    def test_property_1_contract_shape_mailbox_dedupe_and_complete_facts(self):
        provider = ProviderSpy()
        observation = observe(
            lambda: process_email(
                canonical_normalized_email(), CANONICAL_MAILBOX, provider
            )
        )

        self.assertEqual(observation["kind"], "result")
        result = observation["value"]
        self.assert_exact_submission(result)
        self.assertEqual(result["mailbox_address"], CANONICAL_MAILBOX)
        self.assertEqual(result["gmail_message_id"], CANONICAL_GMAIL_ID)
        self.assertEqual(
            result["dedupe_key"],
            f"{CANONICAL_MAILBOX.casefold()}:{CANONICAL_GMAIL_ID}",
        )
        self.assertEqual(result["extract_prompt_version"], "extract-v7")
        self.assertEqual(result["extract_status"], "completed")
        self.assert_exact_completed_facts(result["facts"])
        self.assertEqual(provider.calls, [(CANONICAL_SUBJECT, CANONICAL_BODY)])

    def test_property_1_optional_metadata_time_direction_and_contact_mutations(self):
        cases = (
            (
                "missing thread remains processable",
                {"thread_id": None},
                {"thread_id": None, "direction": "inbound", "contact_email": "buyer@example.com"},
            ),
            (
                "missing Gmail receive time remains processable",
                {"received_at": None},
                {"received_at": None, "sent_at": CANONICAL_SENT_AT},
            ),
            (
                "missing MIME sent time remains processable",
                {"sent_at": None},
                {"sent_at": None, "received_at": CANONICAL_RECEIVED_AT},
            ),
            (
                "missing both times never cross-fills",
                {"sent_at": None, "received_at": None},
                {"sent_at": None, "received_at": None},
            ),
            (
                "invalid or missing From is unknown",
                {"from": None},
                {"from": None, "direction": "unknown", "contact_email": None},
            ),
            (
                "outbound without external recipient still extracts",
                {"from": CANONICAL_MAILBOX, "to": ["sales@example.com"], "cc": []},
                {"direction": "outbound", "contact_email": None},
            ),
        )

        for name, updates, expected in cases:
            with self.subTest(name=name):
                provider = ProviderSpy()
                observation = observe(
                    lambda: process_email(
                        canonical_normalized_email(**updates),
                        CANONICAL_MAILBOX,
                        provider,
                    )
                )
                self.assertEqual(observation["kind"], "result")
                result = observation["value"]
                for field, value in expected.items():
                    self.assertEqual(result[field], value)
                self.assertEqual(result["extract_status"], "completed")
                self.assertEqual(provider.calls, [(CANONICAL_SUBJECT, CANONICAL_BODY)])

    def test_property_1_rejects_every_invalid_facts_mutation(self):
        for name, mutation, replacement in FACTS_MUTATIONS:
            with self.subTest(name=name):
                if replacement is not None:
                    candidate = replacement()
                else:
                    candidate = canonical_complete_facts()
                    mutation(candidate)
                provider = ProviderSpy(candidate)
                result = process_email(
                    canonical_normalized_email(), CANONICAL_MAILBOX, provider
                )

                self.assertEqual(result["extract_status"], "failed")
                self.assertIsNone(result["facts"])
                self.assertEqual(result["extract_error"], "Fact extraction failed.")
                self.assertIsNone(result["non_business_reason"])
                self.assertEqual(provider.calls, [(CANONICAL_SUBJECT, CANONICAL_BODY)])

    def test_property_1_rejects_evidence_found_only_in_quoted_history(self):
        body = (
            "本次仅确认收到。\n\n"
            "On Mon, 7 Sep 2026, buyer@example.com wrote:\n"
            "> 请提供 50 台检测设备报价"
        )
        eligible_body = "本次仅确认收到。"
        facts = canonical_complete_facts()
        provider = ProviderSpy(facts)

        result = process_email(
            canonical_normalized_email(
                body_text=body,
                eligible_body_text=eligible_body,
            ),
            CANONICAL_MAILBOX,
            provider,
        )

        self.assertEqual(provider.calls, [(CANONICAL_SUBJECT, eligible_body)])
        self.assertEqual(result["extract_status"], "failed")
        self.assertIsNone(result["facts"])
        self.assertEqual(result["extract_error"], "Fact extraction failed.")

    def test_property_1_non_business_reasons_priority_and_zero_calls(self):
        for name, headers, sender, expected_reason in NON_BUSINESS_CASES:
            with self.subTest(name=name):
                provider = ProviderSpy()
                result = process_email(
                    canonical_normalized_email(
                        **{"from": sender, "headers": headers}
                    ),
                    CANONICAL_MAILBOX,
                    provider,
                )

                self.assertTrue(result["non_business_hint"])
                self.assertEqual(result["non_business_reason"], expected_reason)
                self.assertEqual(result["extract_status"], "skipped_non_business")
                self.assertIsNone(result["facts"])
                self.assertIsNone(result["extract_error"])
                self.assertEqual(provider.calls, [])

    def test_property_1_provider_json_and_validation_failures_are_safe(self):
        secret_values = (
            "Authorization",
            "secret-token",
            "raw-provider-response",
            "邮件敏感片段-不可泄露",
        )
        cases = (
            (
                "provider exception",
                ProviderSpy(
                    exception=RuntimeError(
                        "Authorization: Bearer secret-token raw-provider-response 邮件敏感片段-不可泄露"
                    )
                ),
            ),
            (
                "invalid JSON",
                ProviderSpy("not-json secret-token raw-provider-response"),
            ),
        )

        for name, provider in cases:
            with self.subTest(name=name):
                result = process_email(
                    canonical_normalized_email(), CANONICAL_MAILBOX, provider
                )
                self.assertEqual(result["extract_status"], "failed")
                self.assertIsNone(result["facts"])
                self.assertEqual(result["extract_error"], "Fact extraction failed.")
                self.assertNotIn("error", result)
                serialized_error = json.dumps(
                    result["extract_error"], ensure_ascii=False
                )
                for secret in secret_values:
                    self.assertNotIn(secret, serialized_error)
