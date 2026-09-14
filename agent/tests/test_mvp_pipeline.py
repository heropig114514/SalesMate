"""SalesMate Agent MVP 的 L2-L4、同步和编排行为测试。"""

import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event, Lock, get_ident
from unittest.mock import Mock, call, patch

from agent.tools.gmail import GmailHistoryExpiredError, create_service
from agent.workflows.analysis_input import build_analysis_input
from agent.workflows.authorized_gmail_sync import sync_authorized_mailboxes_once
from agent.workflows.customer_analysis import (
    ANALYSIS_PROMPT_VERSION,
    bailian_analysis_provider,
    generate_analysis,
)
from agent.workflows.gmail_sync import sync_gmail
from agent.workflows.lead_score import compute_score
from agent.workflows.orchestration import analyze_company, process_jobs_once
from agent.tests.fake_backend import FakeBackend


NOW = datetime(2024, 1, 3, 13, 18, tzinfo=timezone.utc)


class _IncrementalBackend(FakeBackend):
    """为 Gmail 增量同步测试提供现有 Django sync-state 契约。"""

    def __init__(self):
        super().__init__()
        self.sync_state = {
            "mailbox_id": "mb1",
            "cursor": None,
            "scope": {},
            "last_synced_at": None,
            "status": "authorization_required",
            "version": 0,
        }

    def get_sync_state(self, mailbox_id):
        self.assert_mailbox(mailbox_id)
        return copy.deepcopy(self.sync_state)

    def save_sync_state(self, sync_state):
        self.assert_mailbox(sync_state.get("mailbox_id"))
        if sync_state.get("version") != self.sync_state["version"]:
            raise ValueError("sync state version conflict")
        self.sync_state = copy.deepcopy(dict(sync_state))
        self.sync_state["version"] += 1
        return copy.deepcopy(self.sync_state)

    def get_stored_email(self, mailbox_id, dedupe_key):
        self.assert_mailbox(mailbox_id)
        value = self._emails.get(dedupe_key)
        return copy.deepcopy(value) if value is not None else None

    @staticmethod
    def assert_mailbox(mailbox_id):
        if mailbox_id != "mb1":
            raise ValueError("unexpected mailbox")


class _PartialSubmitBackend(_IncrementalBackend):
    def __init__(self, failed_message_id):
        super().__init__()
        self.failed_message_id = failed_message_id
        self.submitted_batch_sizes = []

    def submit_emails(self, submissions):
        self.submitted_batch_sizes.append(len(submissions))
        if submissions[0]["gmail_message_id"] == self.failed_message_id:
            raise ValueError("single email rejected")
        return super().submit_emails(submissions)


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

    @patch("agent.workflows.customer_analysis.generate_json", return_value="{}")
    def test_l3_provider_sends_compact_skill_input(self, generate):
        bailian_analysis_provider(self.input)

        system_prompt, user_text = generate.call_args.args
        model_input = json.loads(user_text.split("\nANALYSIS_INPUT：\n", 1)[1])
        first_fact = model_input["facts"]["product_need"][0]
        self.assertNotIn("member_dedupe_keys", model_input)
        self.assertNotIn("input_version", model_input)
        self.assertNotIn("evidences", first_fact)
        self.assertEqual(
            set(first_fact),
            {"value", "dedupe_key", "fact_time"},
        )
        self.assertIn("business_context", model_input)
        self.assertIn("ALLOWED_SOURCE_REFS", user_text)
        self.assertTrue(system_prompt)
        self.assertEqual(generate.call_args.kwargs["max_tokens"], 4000)
        self.assertEqual(ANALYSIS_PROMPT_VERSION, "analysis-v3")

    def test_l3_accepts_single_json_code_fence_without_model_retry(self):
        payload = _payload(self.input)
        result = generate_analysis(
            self.input,
            analysis_provider=lambda _: (
                "```json\n"
                + json.dumps(payload, ensure_ascii=False)
                + "\n```"
            ),
            clock=lambda: NOW,
        )

        self.assertEqual(result["status"], "completed")

    def test_l3_derives_size_source_from_backend_context(self):
        known = _payload(self.input)
        known["list_view"]["size_source"] = "模型猜测来源"
        result = generate_analysis(
            self.input,
            analysis_provider=lambda _: json.dumps(known, ensure_ascii=False),
            clock=lambda: NOW,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["list_view"]["size_source"], "mock-crm")

        unknown_input = copy.deepcopy(self.input)
        unknown_input["business_context"]["customer"]["employee_count"] = None
        unknown_input["business_context"]["customer"]["employee_count_source"] = None
        unknown = _payload(unknown_input)
        unknown["list_view"]["size_source"] = ""
        result = generate_analysis(
            unknown_input,
            analysis_provider=lambda _: json.dumps(unknown, ensure_ascii=False),
            clock=lambda: NOW,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["list_view"]["size_source"], "unknown")

    def test_l3_normalizes_and_validates_conflict_fields_before_submission(self):
        source_refs = [self.input["member_dedupe_keys"][0], self.input["company_id"]]
        aliased = _payload(self.input)
        aliased["detail_view"]["conflicts"] = [
            {
                "field": "company_name",
                "kind": "value_changed",
                "summary": "不同邮件中的公司自报名称发生变化",
                "source_refs": source_refs,
            }
        ]
        result = generate_analysis(
            self.input,
            analysis_provider=lambda _: json.dumps(aliased, ensure_ascii=False),
            clock=lambda: NOW,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            result["detail_view"]["conflicts"][0]["field"],
            "company_self_reported",
        )

        invalid = _payload(self.input)
        invalid["detail_view"]["conflicts"] = [
            {
                "field": "unsupported_field",
                "kind": "source_disagree",
                "summary": "不受支持的冲突字段",
                "source_refs": source_refs,
            }
        ]
        result = generate_analysis(
            self.input,
            analysis_provider=lambda _: json.dumps(invalid, ensure_ascii=False),
            clock=lambda: NOW,
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("枚举值无效", result["error"]["message"])

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

        deal_likelihood = _payload(self.input)
        deal_likelihood["detail_view"]["analysis"]["opportunity"]["facts"][0][
            "text"
        ] = "成交可能 80%"
        result = generate_analysis(
            self.input,
            analysis_provider=lambda _: json.dumps(
                deal_likelihood, ensure_ascii=False
            ),
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

    def test_l3_allows_business_percentage_and_normalizes_typed_source_ref(self):
        payload = _payload(self.input)
        payload["list_view"]["headline_summary"] = (
            "客户要求首付款 30%，验收后支付 60%，剩余 10% 作为质保金"
        )
        payload["detail_view"]["analysis"]["risk"]["facts"][0]["source_refs"] = [
            f"company_id:{self.input['company_id']}",
            f"company_id:{self.input['company_id']}",
        ]

        result = generate_analysis(
            self.input,
            analysis_provider=lambda _: json.dumps(payload, ensure_ascii=False),
            clock=lambda: NOW,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            result["detail_view"]["analysis"]["risk"]["facts"][0]["source_refs"],
            [self.input["company_id"]],
        )

    @patch("agent.workflows.customer_analysis.generate_json")
    def test_default_l3_provider_retries_one_validation_failure(self, generate):
        invalid = _payload(self.input)
        invalid["list_view"]["signal"] = "repeat_purchase"
        retry_input = copy.deepcopy(self.input)
        retry_input["business_context"]["orders"] = []
        corrected = _payload(retry_input)
        corrected["list_view"]["signal"] = "inquiry_intent"

        generate.side_effect = [
            json.dumps(invalid, ensure_ascii=False),
            json.dumps(corrected, ensure_ascii=False),
        ]
        result = generate_analysis(
            retry_input,
            clock=lambda: NOW,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["list_view"]["signal"], "inquiry_intent")
        self.assertEqual(generate.call_count, 2)
        self.assertIn("上一次分析未通过", generate.call_args_list[1].args[1])

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

    def test_authorized_sync_reports_mailbox_before_company_analysis(self):
        events = []
        backend = Mock()
        backend.claim_mailbox_syncs.return_value = [
            {
                "mailbox_id": "mb1",
                "mailbox_address": "sales@example.com",
                "authorization": {"token": "token"},
                "max_results": 20,
            }
        ]
        backend.report_mailbox_sync.side_effect = lambda _report: events.append(
            "mailbox_reported"
        )

        def process_jobs(**_kwargs):
            events.append("company_jobs_started")
            return []

        with (
            patch(
                "agent.workflows.authorized_gmail_sync.create_service_from_authorization",
                return_value=(object(), None),
            ),
            patch(
                "agent.workflows.authorized_gmail_sync.sync_gmail",
                return_value={
                    "mailbox_id": "mb1",
                    "status": "completed",
                    "job_reports": [],
                    "error": None,
                },
            ),
            patch(
                "agent.workflows.authorized_gmail_sync.process_jobs_once",
                side_effect=process_jobs,
            ),
        ):
            reports = sync_authorized_mailboxes_once(backend=backend)

        self.assertEqual(events, ["mailbox_reported", "company_jobs_started"])
        self.assertEqual(reports[0]["status"], "completed")

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

    def test_l1_processes_multiple_new_emails_concurrently(self):
        backend = FakeBackend()
        authorization = {
            "mailbox_id": "mb1",
            "access_token": "token",
            "mailbox_address": "sales@example.com",
            "max_results": 4,
        }
        barrier = Barrier(4)
        active = 0
        maximum_active = 0
        lock = Lock()

        def process(email, _mailbox_address, _provider):
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            barrier.wait(timeout=2)
            submission = _completed_submission()
            message_id = email["gmail_message_id"]
            submission.update(
                gmail_message_id=message_id,
                thread_id=message_id,
                dedupe_key=f"sales@example.com:{message_id}",
            )
            with lock:
                active -= 1
            return submission

        emails = [
            {"gmail_message_id": f"message-{index}"} for index in range(1, 5)
        ]
        with (
            patch("agent.workflows.gmail_sync.resolve_mailbox_address", return_value="sales@example.com"),
            patch("agent.workflows.gmail_sync.read_sync_emails", return_value=emails),
            patch("agent.workflows.gmail_sync.process_email", side_effect=process),
        ):
            result = sync_gmail(
                authorization, backend=backend, gmail_factory=lambda _: object()
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["l1_processed_count"], 4)
        self.assertEqual(result["created_count"], 4)
        self.assertEqual(maximum_active, 4)

    def test_l1_progress_callbacks_are_serialized_on_sync_thread(self):
        backend = FakeBackend()
        authorization = {
            "mailbox_id": "mb1",
            "access_token": "token",
            "mailbox_address": "sales@example.com",
            "max_results": 4,
        }
        caller_thread = get_ident()
        callback_threads = []
        emails = [
            {"gmail_message_id": f"message-{index}"} for index in range(1, 5)
        ]

        def process(email, _mailbox_address, _provider):
            submission = _completed_submission()
            message_id = email["gmail_message_id"]
            submission.update(
                gmail_message_id=message_id,
                thread_id=message_id,
                dedupe_key=f"sales@example.com:{message_id}",
            )
            return submission

        with (
            patch(
                "agent.workflows.gmail_sync.resolve_mailbox_address",
                return_value="sales@example.com",
            ),
            patch(
                "agent.workflows.gmail_sync.read_sync_emails",
                return_value=emails,
            ),
            patch(
                "agent.workflows.gmail_sync.process_email",
                side_effect=process,
            ),
        ):
            result = sync_gmail(
                authorization,
                backend=backend,
                gmail_factory=lambda _: object(),
                progress=lambda _stage, _data: callback_threads.append(get_ident()),
            )

        self.assertEqual(result["status"], "completed")
        self.assertTrue(callback_threads)
        self.assertEqual(set(callback_threads), {caller_thread})

    def test_completed_l1_email_is_submitted_before_slower_email_finishes(self):
        submitted_fast = Event()

        class StreamingBackend(_IncrementalBackend):
            def submit_emails(self, submissions):
                result = super().submit_emails(submissions)
                if submissions[0]["gmail_message_id"] == "message-fast":
                    submitted_fast.set()
                return result

        backend = StreamingBackend()
        authorization = {
            "mailbox_id": "mb1",
            "access_token": "token",
            "mailbox_address": "sales@example.com",
            "max_results": 2,
        }
        emails = [
            {"gmail_message_id": "message-slow"},
            {"gmail_message_id": "message-fast"},
        ]

        def process(email, _mailbox_address, _provider):
            message_id = email["gmail_message_id"]
            if message_id == "message-slow" and not submitted_fast.wait(timeout=1):
                raise RuntimeError("fast email was not submitted while slow L1 waited")
            submission = _completed_submission()
            submission.update(
                gmail_message_id=message_id,
                thread_id=message_id,
                dedupe_key=f"sales@example.com:{message_id}",
            )
            return submission

        with (
            patch("agent.workflows.gmail_sync.resolve_mailbox_address", return_value="sales@example.com"),
            patch("agent.workflows.gmail_sync.get_profile_history_id", return_value="100"),
            patch("agent.workflows.gmail_sync.read_sync_emails", return_value=emails),
            patch("agent.workflows.gmail_sync.process_email", side_effect=process),
        ):
            result = sync_gmail(
                authorization, backend=backend, gmail_factory=lambda _: object()
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["created_count"], 2)
        self.assertEqual(result["failed_email_count"], 0)

    def test_one_l1_exception_does_not_block_other_emails(self):
        backend = _IncrementalBackend()
        authorization = {
            "mailbox_id": "mb1",
            "access_token": "token",
            "mailbox_address": "sales@example.com",
            "max_results": 2,
        }
        emails = [
            {"gmail_message_id": "message-1"},
            {"gmail_message_id": "message-2"},
        ]

        def process(email, _mailbox_address, _provider):
            if email["gmail_message_id"] == "message-2":
                raise RuntimeError("broken email")
            return _completed_submission()

        with (
            patch("agent.workflows.gmail_sync.resolve_mailbox_address", return_value="sales@example.com"),
            patch("agent.workflows.gmail_sync.get_profile_history_id", return_value="100"),
            patch("agent.workflows.gmail_sync.read_sync_emails", return_value=emails),
            patch("agent.workflows.gmail_sync.process_email", side_effect=process),
        ):
            result = sync_gmail(
                authorization, backend=backend, gmail_factory=lambda _: object()
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["created_count"], 1)
        self.assertEqual(result["retry_message_count"], 1)
        self.assertEqual(result["email_errors"][0]["stage"], "l1")
        self.assertEqual(
            backend.sync_state["scope"]["failed_message_ids"], ["message-2"]
        )

    def test_one_backend_rejection_does_not_roll_back_other_emails(self):
        backend = _PartialSubmitBackend("message-2")
        authorization = {
            "mailbox_id": "mb1",
            "access_token": "token",
            "mailbox_address": "sales@example.com",
            "max_results": 2,
        }
        emails = [
            {"gmail_message_id": "message-1"},
            {"gmail_message_id": "message-2"},
        ]

        def process(email, _mailbox_address, _provider):
            submission = _completed_submission()
            message_id = email["gmail_message_id"]
            submission.update(
                gmail_message_id=message_id,
                thread_id=message_id,
                dedupe_key=f"sales@example.com:{message_id}",
            )
            return submission

        with (
            patch("agent.workflows.gmail_sync.resolve_mailbox_address", return_value="sales@example.com"),
            patch("agent.workflows.gmail_sync.get_profile_history_id", return_value="100"),
            patch("agent.workflows.gmail_sync.read_sync_emails", return_value=emails),
            patch("agent.workflows.gmail_sync.process_email", side_effect=process),
        ):
            result = sync_gmail(
                authorization, backend=backend, gmail_factory=lambda _: object()
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["created_count"], 1)
        self.assertEqual(result["failed_submission_count"], 1)
        self.assertEqual(result["retry_message_count"], 1)
        self.assertEqual(result["email_errors"][0]["stage"], "submission")
        self.assertEqual(backend.submitted_batch_sizes, [1, 1])
        self.assertIn("sales@example.com:message-1", backend._emails)
        self.assertNotIn("sales@example.com:message-2", backend._emails)

    def test_incremental_cursor_avoids_second_l1_call_when_history_is_empty(self):
        backend = _IncrementalBackend()
        submission = _completed_submission()
        service = object()
        authorization = {
            "mailbox_id": "mb1",
            "access_token": "token",
            "mailbox_address": "sales@example.com",
            "max_results": 20,
        }
        with (
            patch("agent.workflows.gmail_sync.resolve_mailbox_address", return_value="sales@example.com"),
            patch("agent.workflows.gmail_sync.get_profile_history_id", return_value="100"),
            patch("agent.workflows.gmail_sync.read_sync_emails", return_value=[{"gmail_message_id": "message-1"}]) as recent,
            patch("agent.workflows.gmail_sync.list_history_message_ids", return_value=([], "100")) as history,
            patch("agent.workflows.gmail_sync.read_messages", return_value=[]) as selected,
            patch("agent.workflows.gmail_sync.process_email", return_value=submission) as process,
        ):
            first = sync_gmail(authorization, backend=backend, gmail_factory=lambda _: service)
            second = sync_gmail(authorization, backend=backend, gmail_factory=lambda _: service)

        self.assertEqual(first["sync_mode"], "initial")
        self.assertTrue(first["cursor_saved"])
        self.assertEqual(first["created_count"], 1)
        self.assertEqual(second["sync_mode"], "incremental")
        self.assertEqual(second["fetched_count"], 0)
        self.assertEqual(second["duplicate_count"], 0)
        self.assertEqual(process.call_count, 1)
        recent.assert_called_once_with(service, limit=20)
        history.assert_called_once_with(service, "100")
        selected.assert_called_once_with(service, [])

    def test_initial_scan_skips_existing_completed_extraction_before_l1(self):
        backend = _IncrementalBackend()
        backend.submit_emails([_completed_submission()])
        service = object()
        authorization = {
            "mailbox_id": "mb1",
            "access_token": "token",
            "mailbox_address": "sales@example.com",
            "max_results": 20,
        }
        with (
            patch("agent.workflows.gmail_sync.resolve_mailbox_address", return_value="sales@example.com"),
            patch("agent.workflows.gmail_sync.get_profile_history_id", return_value="100"),
            patch(
                "agent.workflows.gmail_sync.read_sync_emails",
                return_value=[{"gmail_message_id": "message-1"}],
            ),
            patch("agent.workflows.gmail_sync.process_email") as process,
        ):
            result = sync_gmail(
                authorization, backend=backend, gmail_factory=lambda _: service
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["sync_mode"], "initial")
        self.assertEqual(result["skipped_existing_count"], 1)
        self.assertEqual(result["duplicate_count"], 1)
        self.assertTrue(result["cursor_saved"])
        process.assert_not_called()

    def test_incremental_cursor_retries_failed_l1_message(self):
        backend = _IncrementalBackend()
        completed = _completed_submission()
        failed = copy.deepcopy(completed)
        failed.update(extract_status="failed", facts=None, extract_error="失败")
        service = object()
        authorization = {
            "mailbox_id": "mb1",
            "access_token": "token",
            "mailbox_address": "sales@example.com",
            "max_results": 20,
        }
        with (
            patch("agent.workflows.gmail_sync.resolve_mailbox_address", return_value="sales@example.com"),
            patch("agent.workflows.gmail_sync.get_profile_history_id", return_value="100"),
            patch("agent.workflows.gmail_sync.read_sync_emails", return_value=[{"gmail_message_id": "message-1"}]),
            patch("agent.workflows.gmail_sync.list_history_message_ids", return_value=([], "100")),
            patch("agent.workflows.gmail_sync.read_messages", return_value=[{"gmail_message_id": "message-1"}]) as selected,
            patch("agent.workflows.gmail_sync.process_email", side_effect=[failed, completed]) as process,
        ):
            first = sync_gmail(authorization, backend=backend, gmail_factory=lambda _: service)
            self.assertEqual(
                backend.sync_state["scope"]["failed_message_ids"], ["message-1"]
            )
            second = sync_gmail(authorization, backend=backend, gmail_factory=lambda _: service)

        self.assertEqual(first["failed_extraction_count"], 1)
        self.assertEqual(backend.sync_state["scope"]["failed_message_ids"], [])
        self.assertEqual(second["updated_count"], 1)
        self.assertEqual(process.call_count, 2)
        selected.assert_called_once_with(service, ["message-1"])

    def test_existing_failed_extraction_is_preserved_when_retry_still_fails(self):
        backend = _IncrementalBackend()
        existing_failed = _completed_submission()
        existing_failed.update(
            extract_status="failed", facts=None, extract_error="original failure"
        )
        backend.submit_emails([existing_failed])
        retry_failed = copy.deepcopy(existing_failed)
        retry_failed["extract_error"] = "new failure"
        service = object()
        authorization = {
            "mailbox_id": "mb1",
            "access_token": "token",
            "mailbox_address": "sales@example.com",
            "max_results": 20,
        }

        with (
            patch("agent.workflows.gmail_sync.resolve_mailbox_address", return_value="sales@example.com"),
            patch("agent.workflows.gmail_sync.get_profile_history_id", return_value="100"),
            patch(
                "agent.workflows.gmail_sync.read_sync_emails",
                return_value=[{"gmail_message_id": "message-1"}],
            ),
            patch("agent.workflows.gmail_sync.process_email", return_value=retry_failed),
        ):
            result = sync_gmail(
                authorization, backend=backend, gmail_factory=lambda _: service
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["failed_extraction_count"], 1)
        self.assertEqual(result["duplicate_count"], 1)
        self.assertEqual(
            backend._emails["sales@example.com:message-1"]["extract_error"],
            "original failure",
        )
        self.assertEqual(
            backend.sync_state["scope"]["failed_message_ids"], ["message-1"]
        )

    def test_incremental_cursor_preserves_overflow_for_the_next_sync(self):
        backend = _IncrementalBackend()
        backend.sync_state.update(cursor="100", status="ok")
        service = object()
        authorization = {
            "mailbox_id": "mb1",
            "access_token": "token",
            "mailbox_address": "sales@example.com",
            "max_results": 2,
        }
        submissions = []
        for message_id in ("message-1", "message-2", "message-3"):
            submission = _completed_submission()
            submission.update(
                gmail_message_id=message_id,
                thread_id=message_id,
                dedupe_key=f"sales@example.com:{message_id}",
            )
            submissions.append(submission)

        with (
            patch("agent.workflows.gmail_sync.resolve_mailbox_address", return_value="sales@example.com"),
            patch(
                "agent.workflows.gmail_sync.list_history_message_ids",
                side_effect=[(["message-1", "message-2", "message-3"], "120"), ([], "120")],
            ),
            patch(
                "agent.workflows.gmail_sync.read_messages",
                side_effect=[
                    [{"gmail_message_id": "message-1"}, {"gmail_message_id": "message-2"}],
                    [{"gmail_message_id": "message-3"}],
                ],
            ) as selected,
            patch("agent.workflows.gmail_sync.process_email", side_effect=submissions) as process,
        ):
            first = sync_gmail(authorization, backend=backend, gmail_factory=lambda _: service)
            self.assertEqual(
                backend.sync_state["scope"]["pending_message_ids"], ["message-3"]
            )
            second = sync_gmail(authorization, backend=backend, gmail_factory=lambda _: service)

        self.assertEqual(first["fetched_count"], 2)
        self.assertEqual(first["pending_message_count"], 1)
        self.assertEqual(second["fetched_count"], 1)
        self.assertEqual(second["pending_message_count"], 0)
        self.assertEqual(process.call_count, 3)
        self.assertEqual(
            selected.call_args_list,
            [call(service, ["message-1", "message-2"]), call(service, ["message-3"])],
        )

    def test_expired_history_cursor_falls_back_to_recent_scan(self):
        backend = _IncrementalBackend()
        backend.sync_state.update(cursor="expired", status="ok")
        submission = _completed_submission()
        service = object()
        authorization = {
            "mailbox_id": "mb1",
            "access_token": "token",
            "mailbox_address": "sales@example.com",
            "max_results": 20,
        }

        with (
            patch("agent.workflows.gmail_sync.resolve_mailbox_address", return_value="sales@example.com"),
            patch(
                "agent.workflows.gmail_sync.list_history_message_ids",
                side_effect=GmailHistoryExpiredError("expired"),
            ),
            patch("agent.workflows.gmail_sync.get_profile_history_id", return_value="200"),
            patch(
                "agent.workflows.gmail_sync.read_sync_emails",
                return_value=[{"gmail_message_id": "message-1"}],
            ) as recent,
            patch("agent.workflows.gmail_sync.process_email", return_value=submission),
        ):
            result = sync_gmail(
                authorization, backend=backend, gmail_factory=lambda _: service
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["sync_mode"], "initial")
        self.assertTrue(result["cursor_saved"])
        self.assertEqual(backend.sync_state["cursor"], "200")
        recent.assert_called_once_with(service, limit=20)

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
