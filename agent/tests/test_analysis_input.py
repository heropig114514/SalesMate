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
