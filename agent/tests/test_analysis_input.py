"""L2 AnalysisInput 的核心行为测试。"""

import copy
import unittest
from datetime import datetime, timezone

from agent.workflows.analysis_input import (
    AnalysisInput,
    ValidationError,
    build_analysis_input,
    compute_input_version,
)
from agent.workflows.lead_score import compute_score
from agent.tests.fake_backend import FakeBackend


NOW = datetime(2026, 9, 12, 10, 0, tzinfo=timezone.utc)


class AnalysisInputTests(unittest.TestCase):
    def test_normal_snapshot_contains_sources_context_and_metrics(self):
        result = build_analysis_input(
            "company-demo",
            backend=FakeBackend(seed="l2"),
            merge_version="merge-v2",
            clock=lambda: NOW,
        )
        self.assertIsInstance(result, AnalysisInput)
        output = result.to_dict()
        self.assertEqual(output["company_id"], "company-demo")
        self.assertTrue(output["company"]["contacts"])
        self.assertTrue(output["business_context"]["tickets"])
        self.assertTrue(output["business_context"]["quotes"])
        self.assertTrue(output["business_context"]["orders"])
        self.assertTrue(output["facts"]["product_need"])
        fact = output["facts"]["product_need"][0]
        self.assertIn("dedupe_key", fact)
        self.assertIn("fact_time", fact)
        self.assertTrue(fact["evidences"])
        self.assertEqual(output["unparsed_message_count"], 1)
        self.assertEqual(output["metrics"]["inbound_count"], 2)
        self.assertEqual(output["metrics"]["outbound_count"], 1)

    def test_input_version_ignores_email_order_but_changes_with_snapshot(self):
        backend = FakeBackend(seed="version")
        context = backend.get_company_context("company-demo")
        original = compute_input_version(
            context["emails"], "merge-v2", context["external_snapshot_version"]
        )
        reversed_emails = list(reversed(copy.deepcopy(context["emails"])))
        reordered = compute_input_version(
            reversed_emails, "merge-v2", context["external_snapshot_version"]
        )
        changed = compute_input_version(reversed_emails, "merge-v2", "changed")
        self.assertEqual(original, reordered)
        self.assertNotEqual(original, changed)

    def test_empty_company_is_valid_and_has_null_summary(self):
        result = build_analysis_input(
            "company-empty",
            backend=FakeBackend(scenario="boundary-empty"),
            merge_version="merge-v2",
            clock=lambda: NOW,
        )
        self.assertIsInstance(result, AnalysisInput)
        self.assertEqual(result.member_dedupe_keys, [])
        self.assertIsNone(result.latest_message_summary)
        self.assertEqual(result.metrics.inbound_count, 0)

    def test_optional_l4_context_is_local_and_not_submitted_as_l2(self):
        class WithPriorityContext(FakeBackend):
            def get_company_context(self, company_id):
                context = super().get_company_context(company_id)
                context["priority_context"] = {"signals": []}
                return context

        result = build_analysis_input(
            "company-demo", backend=WithPriorityContext(seed="l2"), clock=lambda: NOW
        )
        self.assertIsInstance(result, AnalysisInput)
        self.assertEqual(result.priority_context["signals"][0]["type"], "L1 Exploring")
        self.assertTrue(result.priority_context["communications"])
        self.assertIn("message_id", result.priority_context["communications"][0])
        self.assertNotIn("priority_context", result.to_dict())

    def test_v7_stage_reaches_rule_score_without_deal_or_seller(self):
        class StageBackend(FakeBackend):
            def get_company_context(self, company_id):
                context = super().get_company_context(company_id)
                email = next(item for item in context["emails"] if item["direction"] == "inbound")
                email["extract_prompt_version"] = "extract-v7"
                email["facts"]["intent_hint"] = "L3 Qualified"
                email["facts"]["intent_evidences"] = ["industrial sensors"]
                return context

        result = build_analysis_input("company-demo", backend=StageBackend(seed="stage"), clock=lambda: NOW)
        self.assertIsInstance(result, AnalysisInput)
        self.assertEqual(result.priority_context["signals"][0]["type"], "L3 Qualified")
        score = compute_score({"status": "completed"}, result, clock=lambda: NOW,
                              priority_context=result.priority_context)
        self.assertEqual(score["score"], 35)
        self.assertIn("Provisional score", score["score_reasons"][2]["note"])

    def test_old_l1_email_is_rejected(self):
        class OldEmailBackend(FakeBackend):
            def get_company_context(self, company_id):
                context = super().get_company_context(company_id)
                context["emails"][0]["extract_prompt_version"] = "extract-v6"
                return context

        result = build_analysis_input(
            "company-demo", backend=OldEmailBackend(seed="old-l1"), clock=lambda: NOW
        )
        self.assertIsInstance(result, ValidationError)
        self.assertEqual(result.code, "invalid_backend_data")

    def test_backend_retrieval_and_scope_errors_are_returned(self):
        failed = build_analysis_input(
            "company-demo",
            backend=FakeBackend(scenario="fail-context-retrieval"),
            merge_version="merge-v2",
            clock=lambda: NOW,
        )
        self.assertIsInstance(failed, ValidationError)
        self.assertEqual(failed.code, "context_retrieval_failed")

        class WrongScope:
            def get_company_grouping(self, company_id):
                return {
                    "company_id": "other",
                    "company_name": None,
                    "crm_status": "unregistered",
                    "domains": [],
                    "contacts": [],
                    "member_dedupe_keys": [],
                }

            def get_company_context(self, company_id):
                raise AssertionError("invalid grouping 后不应读取 context")

        invalid = build_analysis_input(
            "company-demo",
            backend=WrongScope(),
            merge_version="merge-v2",
            clock=lambda: NOW,
        )
        self.assertIsInstance(invalid, ValidationError)
        self.assertEqual(invalid.code, "invalid_backend_data")


if __name__ == "__main__":
    unittest.main()
