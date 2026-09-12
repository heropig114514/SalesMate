"""SalesMate Agent MVP 的 L2-L4、同步和编排行为测试。"""

import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from agent.tools.gmail import create_service
from agent.workflows.analysis_input import build_analysis_input
from agent.workflows.customer_analysis import generate_analysis
from agent.workflows.gmail_sync import sync_gmail
from agent.workflows.lead_score import compute_score
from agent.workflows.orchestration import analyze_company, process_jobs_once
from agent.tests.fake_backend import FakeBackend


NOW = datetime(2024, 1, 3, 13, 18, tzinfo=timezone.utc)


def _payload(analysis_input):
    source = analysis_input["member_dedupe_keys"][0]
    business = analysis_input["business_context"]
    tickets = business["tickets"]
    orders = business["orders"]
    customer = business["customer"]
    employee_count = customer.get("employee_count")
    if employee_count is None:
        size_band = "unknown"
    elif employee_count < 50:
        size_band = "lt_50"
    elif employee_count < 100:
        size_band = "50_100"
    elif employee_count < 200:
        size_band = "100_200"
    elif employee_count < 500:
        size_band = "200_500"
    else:
        size_band = "gte_500"
    signal = "repeat_purchase" if orders else "inquiry_intent"

    def dimension(text):
        return {
            "facts": [{"text": text, "source_refs": [source]}],
            "inferences": [
                {
                    "text": "客户有明确的采购动作",
                    "basis": "邮件中出现产品和数量",
                    "confidence": "high",
                    "source_refs": [source],
                }
            ],
            "missing_fields": [],
        }

    return {
        "list_view": {
            "signal": signal,
            "signal_evidence": {"text": "邮件中有明确采购事实", "source_refs": [source]},
            "ticket_signals": [
                {
                    "ticket_id": ticket["ticket_id"],
                    "signal": signal,
                    "reason": "该工单存在明确采购动作",
                }
                for ticket in tickets
            ],
            "industry": "unknown",
            "industry_evidence": {"text": "行业信息不足", "source_refs": []},
            "size_band": size_band,
            "size_source": customer.get("employee_count_source") or "unknown",
            "headline_summary": analysis_input.get("latest_message_summary") or "客户提出采购需求",
            "score_features": {
                "demand_clarity": {"value": 3, "basis": "产品与数量明确"},
                "urgency": {"value": 2, "basis": "客户主动跟进"},
                "decision_visibility": {"value": 1, "basis": "仅知道联系人"},
            },
        },
        "detail_view": {
            "conflicts": [],
            "profile": {
                "industry_context": dimension("客户提出工业传感器需求"),
                "company_ops": dimension("已有联系人记录"),
                "intent": dimension("客户明确询问产品和数量"),
            },
            "analysis": {
                "timeline": dimension("客户发来采购邮件"),
                "opportunity": dimension("需求范围较明确"),
                "risk": dimension("决策链信息仍不完整"),
                "guidance": dimension("下一步确认预算与交期"),
            },
            "missing_fields": ["预算审批状态"],
            "context_completeness": {
                "unparsed_message_count": analysis_input["unparsed_message_count"],
                "note": (
                    "存在未解析邮件，当前分析未包含全部往来"
                    if analysis_input["unparsed_message_count"]
                    else None
                ),
            },
        },
    }


def _provider(analysis_input):
    return json.dumps(_payload(analysis_input), ensure_ascii=False)


def _completed_submission():
    backend = FakeBackend()
    source = backend.get_company_context("source-company")["emails"][0]
    source["dedupe_key"] = "sales@example.com:message-1"
    source["gmail_message_id"] = "message-1"
    source["contact_email"] = "buyer@example.com"
    source["from"] = "buyer@example.com"
    source["to"] = ["sales@example.com"]
    source["mailbox_address"] = "sales@example.com"
    return source


class AnalysisAndScoreTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend(seed="mvp")
        built = build_analysis_input(
            "company-demo",
            backend=self.backend,
            merge_version="merge-v2",
            clock=lambda: NOW,
        )
        self.input = built.to_dict()

    def test_l2_contains_company_and_business_context(self):
        self.assertIn("company", self.input)
        self.assertIn("business_context", self.input)
        self.assertTrue(self.input["business_context"]["tickets"])
        self.assertTrue(self.input["business_context"]["quotes"])
        self.assertTrue(self.input["business_context"]["orders"])

    def test_l3_generates_three_profile_and_four_analysis_dimensions(self):
        result = generate_analysis(
            self.input,
            analysis_provider=_provider,
            clock=lambda: NOW,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(set(result["detail_view"]["profile"]), {
            "industry_context", "company_ops", "intent"
        })
        self.assertEqual(set(result["detail_view"]["analysis"]), {
            "timeline", "opportunity", "risk", "guidance"
        })
        self.assertEqual(result["list_view"]["signal"], "repeat_purchase")

    def test_l3_rejects_unknown_source_and_percentage(self):
        bad_source = _payload(self.input)
        bad_source["detail_view"]["profile"]["intent"]["facts"][0]["source_refs"] = ["missing"]
        result = generate_analysis(
            self.input,
            analysis_provider=lambda _: json.dumps(bad_source, ensure_ascii=False),
            clock=lambda: NOW,
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("不存在的来源", result["error"]["message"])

        percentage = _payload(self.input)
        percentage["list_view"]["headline_summary"] = "成交概率 80%"
        result = generate_analysis(
            self.input,
            analysis_provider=lambda _: json.dumps(percentage, ensure_ascii=False),
            clock=lambda: NOW,
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("百分比", result["error"]["message"])

        invalid_enum = _payload(self.input)
        invalid_enum["list_view"]["signal"] = "won"
        result = generate_analysis(
            self.input,
            analysis_provider=lambda _: json.dumps(invalid_enum, ensure_ascii=False),
            clock=lambda: NOW,
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("枚举值无效", result["error"]["message"])

        missing_basis = _payload(self.input)
        del missing_basis["detail_view"]["analysis"]["risk"]["inferences"][0]["basis"]
        result = generate_analysis(
            self.input,
            analysis_provider=lambda _: json.dumps(missing_basis, ensure_ascii=False),
            clock=lambda: NOW,
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("字段必须", result["error"]["message"])

    def test_no_purchase_basis_only_accepts_unknown_signal(self):
        no_basis_input = copy.deepcopy(self.input)
        no_basis_input["business_context"]["orders"] = []
        no_basis_input["business_context"]["quotes"] = []
        for field in ("product_need", "quantity", "budget", "delivery_time"):
            no_basis_input["facts"][field] = []

        unknown = _payload(no_basis_input)
        unknown["list_view"]["signal"] = "unknown"
        unknown["list_view"]["signal_evidence"] = {
            "text": "缺少可验证的采购依据",
            "source_refs": [],
        }
        for ticket_signal in unknown["list_view"]["ticket_signals"]:
            ticket_signal["signal"] = "unknown"
            ticket_signal["reason"] = "信息不足"
        for group_name in ("profile", "analysis"):
            for dimension in unknown["detail_view"][group_name].values():
                dimension["facts"] = []
                dimension["inferences"] = []
                dimension["missing_fields"] = ["采购依据"]

        result = generate_analysis(
            no_basis_input,
            analysis_provider=lambda _: json.dumps(unknown, ensure_ascii=False),
            clock=lambda: NOW,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["list_view"]["signal"], "unknown")

        unsupported = copy.deepcopy(unknown)
        unsupported["list_view"]["signal"] = "inquiry_intent"
        unsupported["list_view"]["signal_evidence"] = {
            "text": "声称存在采购意向",
            "source_refs": [no_basis_input["member_dedupe_keys"][0]],
        }
        result = generate_analysis(
            no_basis_input,
            analysis_provider=lambda _: json.dumps(unsupported, ensure_ascii=False),
            clock=lambda: NOW,
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("缺少采购事实", result["error"]["message"])

    def test_signal_gates_use_quote_order_and_new_lead_context(self):
        no_orders_backend = FakeBackend(scenario="boundary-no-orders", seed="mvp")
        built = build_analysis_input(
            "company-demo",
            backend=no_orders_backend,
            merge_version="merge-v2",
            clock=lambda: NOW,
        ).to_dict()
        quoted = _payload(built)
        quoted["list_view"]["signal"] = "quoted_not_closed"
        quoted["list_view"]["ticket_signals"][0]["signal"] = "quoted_not_closed"
        result = generate_analysis(
            built,
            analysis_provider=lambda _: json.dumps(quoted, ensure_ascii=False),
            clock=lambda: NOW,
        )
        self.assertEqual(result["status"], "completed")

        backend = FakeBackend()
        backend.submit_emails([_completed_submission()])
        company_id = backend.claim_jobs(1)[0]["company_id"]
        new_lead_input = build_analysis_input(
            company_id,
            backend=backend,
            merge_version="merge-v2",
            clock=lambda: NOW,
        ).to_dict()
        new_lead = _payload(new_lead_input)
        new_lead["list_view"]["signal"] = "new_lead_no_profile"
        result = generate_analysis(
            new_lead_input,
            analysis_provider=lambda _: json.dumps(new_lead, ensure_ascii=False),
            clock=lambda: NOW,
        )
        self.assertEqual(result["status"], "completed")

        invalid_quote = _payload(new_lead_input)
        invalid_quote["list_view"]["signal"] = "quoted_not_closed"
        result = generate_analysis(
            new_lead_input,
            analysis_provider=lambda _: json.dumps(invalid_quote, ensure_ascii=False),
            clock=lambda: NOW,
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("真实外发报价", result["error"]["message"])

    def test_l4_score_is_explainable_and_missing_feature_returns_null(self):
        analysis = generate_analysis(
            self.input,
            analysis_provider=_provider,
            clock=lambda: NOW,
        )
        score = compute_score(analysis, self.input, clock=lambda: NOW)
        self.assertTrue(0 <= score["score"] <= 100)
        self.assertEqual(
            score["score"],
            sum(item["contribution"] for item in score["score_reasons"]),
        )

        incomplete = copy.deepcopy(analysis)
        incomplete["list_view"]["score_features"]["urgency"]["value"] = None
        score = compute_score(incomplete, self.input, clock=lambda: NOW)
        self.assertIsNone(score["score"])
        self.assertEqual(score["score_reasons"][0]["feature"], "insufficient_data")

    def test_analysis_cache_avoids_second_model_call(self):
        calls = []

        def provider(document):
            calls.append(document["input_version"])
            return _provider(document)

        first = analyze_company(
            "company-demo",
            backend=self.backend,
            analysis_provider=provider,
            clock=lambda: NOW,
        )
        second = analyze_company(
            "company-demo",
            backend=self.backend,
            analysis_provider=provider,
            clock=lambda: NOW + timedelta(minutes=1),
        )
        self.assertEqual(first["status"], "completed")
        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual(len(calls), 1)


class SyncAndOrchestrationTests(unittest.TestCase):
    def test_access_token_builds_gmail_service(self):
        sentinel = object()
        with patch("agent.tools.gmail.build", return_value=sentinel) as build:
            self.assertIs(create_service("token-value"), sentinel)
        credentials = build.call_args.kwargs["credentials"]
        self.assertEqual(credentials.token, "token-value")

    def test_sync_deduplicates_second_scan(self):
        backend = FakeBackend()
        submission = _completed_submission()
        authorization = {
            "mailbox_id": "mb1",
            "access_token": "token",
            "mailbox_address": "sales@example.com",
            "max_results": 1,
        }
        with (
            patch("agent.workflows.gmail_sync.resolve_mailbox_address", return_value="sales@example.com"),
            patch("agent.workflows.gmail_sync.read_sync_emails", return_value=[{"id": "raw"}]),
            patch("agent.workflows.gmail_sync.process_email", return_value=submission),
        ):
            first = sync_gmail(authorization, backend=backend, gmail_factory=lambda _: object())
            second = sync_gmail(authorization, backend=backend, gmail_factory=lambda _: object())
        self.assertEqual(first["created_count"], 1)
        self.assertEqual(second["duplicate_count"], 1)

    def test_failed_submission_can_be_replaced_and_nonbusiness_has_no_job(self):
        backend = FakeBackend()
        completed = _completed_submission()
        failed = copy.deepcopy(completed)
        failed.update(extract_status="failed", facts=None, extract_error="失败")
        self.assertEqual(backend.submit_emails([failed])["created_count"], 1)
        self.assertEqual(backend.submit_emails([completed])["updated_count"], 1)

        nonbusiness = copy.deepcopy(completed)
        nonbusiness["dedupe_key"] = "sales@example.com:nonbusiness"
        nonbusiness.update(
            gmail_message_id="nonbusiness",
            non_business_hint=True,
            extract_status="skipped_non_business",
            facts=None,
        )
        backend.submit_emails([nonbusiness])
        jobs = backend.claim_jobs(10)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["trigger"], "email_ingested")

    def test_one_job_runs_l2_l3_l4_and_reports(self):
        backend = FakeBackend()
        backend.submit_emails([_completed_submission()])
        reports = process_jobs_once(
            backend=backend,
            analysis_provider=_provider,
            clock=lambda: NOW,
        )
        self.assertEqual(reports[0]["status"], "completed")
        self.assertTrue(reports[0]["produced"]["analysis"])
        self.assertTrue(reports[0]["produced"]["score"])
        self.assertIsNotNone(reports[0]["input_version"])
        self.assertEqual(backend.job_reports, reports)


if __name__ == "__main__":
    unittest.main()
