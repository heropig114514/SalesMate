"""Regression coverage for historical L3 reference/completeness/repair failures."""

import copy
import json
import unittest
from unittest.mock import patch

from agent.tests.fake_backend import FakeBackend
from agent.clients.backend_api import BackendRequestError
from agent.workflows.orchestration import process_jobs_once
from agent.tests.test_mvp_pipeline import NOW, _payload
from agent.workflows.analysis_input import build_analysis_input
from agent.workflows.customer_analysis import (
    AnalysisValidationError,
    _source_aliases,
    bailian_analysis_provider,
    generate_analysis,
    validate_analysis_payload,
    _normalize_probability_denial,
    _contains_deal_probability,
)


class AnalysisValidationTests(unittest.TestCase):
    def setUp(self):
        self.document = build_analysis_input(
            "company-demo", backend=FakeBackend(seed="mvp"), clock=lambda: NOW,
        ).to_dict()

    def test_model_sees_source_numbers_but_no_unsourced_summary(self):
        original = copy.deepcopy(self.document)
        with patch("agent.workflows.customer_analysis.generate_json", return_value="{}") as model:
            bailian_analysis_provider(self.document)
        text = model.call_args.args[1]
        model_input = json.loads(text.split("\nANALYSIS_INPUT:\n")[1])
        aliases = _source_aliases(self.document)
        self.assertNotIn("latest_message_summary", model_input)
        for groups in model_input["facts"].values():
            for group in groups:
                self.assertEqual(aliases[group["source_ref"]], group["dedupe_key"])
        self.assertEqual(self.document, original)

    def test_size_band_uses_authoritative_count_without_model_retry(self):
        for count, expected in ((None, "unknown"), (35, "lt_50"), (180, "100_200"), (500, "gte_500")):
            with self.subTest(count=count):
                document = copy.deepcopy(self.document)
                document["business_context"]["customer"]["employee_count"] = count
                document["business_context"].pop("company_enrichment", None)
                candidate = _payload(document)
                candidate["list_view"]["size_band"] = "50_100"
                original = copy.deepcopy(candidate)
                with patch("agent.workflows.customer_analysis.generate_json", return_value=json.dumps(candidate)) as model:
                    result = generate_analysis(document, clock=lambda: NOW)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["list_view"]["size_band"], expected)
                model.assert_called_once()
                self.assertEqual(candidate, original)

    def test_aliases_expand_only_in_reference_arrays_before_validation(self):
        payload = _payload(self.document)
        ref = self.document["member_dedupe_keys"][0]
        alias = next(a for a, r in _source_aliases(self.document).items() if r == ref)
        fact = payload["detail_view"]["profile"]["intent"]["facts"][0]
        fact.update(source_refs=[alias, ref, alias], text=f"产品编号 {alias}")
        original = copy.deepcopy(payload)
        result = validate_analysis_payload(payload, self.document)
        resolved = result["detail_view"]["profile"]["intent"]["facts"][0]
        self.assertEqual(resolved["source_refs"], [ref])
        self.assertEqual(resolved["text"], f"产品编号 {alias}")
        self.assertEqual(payload, original)

    def test_unknown_source_and_wrong_mailbox_are_never_guessed(self):
        self.document["member_dedupe_keys"] += ["right@example.com:123456", "other@example.com:123456"]
        for ref in ("wrong@example.com:123456", "123456", "latest_message_summary", "src_999999"):
            payload = _payload(self.document)
            payload["detail_view"]["profile"]["intent"]["facts"][0]["source_refs"] = [ref]
            with self.subTest(ref=ref), self.assertRaisesRegex(AnalysisValidationError, "source absent from the input"):
                validate_analysis_payload(payload, self.document)

    def test_source_aliases_do_not_shadow_real_source_ids(self):
        self.document["member_dedupe_keys"] += ["src_001"]
        aliases = _source_aliases(self.document)
        self.assertNotIn("src_001", aliases)
        self.assertIn("src_001", aliases.values())
        self.assertEqual(len(aliases), len(set(aliases.values())))

    def test_completeness_comes_from_input_even_if_model_omits_or_malforms_it(self):
        self.document["unparsed_message_count"] = 0
        for raw in (None, "", [], {"note": ""}, {"note": [], "unparsed_message_count": 123},
                    {"note": "无法判断成交概率", "unparsed_message_count": 0}):
            with self.subTest(raw=raw):
                payload = _payload(self.document)
                payload["detail_view"]["context_completeness"] = raw
                result = validate_analysis_payload(payload, self.document)
                self.assertEqual(result["detail_view"]["context_completeness"],
                                 {"unparsed_message_count": 0, "note": None})
        payload = _payload(self.document)
        del payload["detail_view"]["context_completeness"]
        result = validate_analysis_payload(payload, self.document)
        self.assertIsNone(result["detail_view"]["context_completeness"]["note"])

    def test_unparsed_mail_gets_required_note_and_missing_field(self):
        self.document["unparsed_message_count"] = 2
        payload = _payload(self.document)
        payload["detail_view"]["context_completeness"] = {"unparsed_message_count": 0, "note": None}
        payload["detail_view"]["missing_fields"] = []
        result = validate_analysis_payload(payload, self.document)
        completeness = result["detail_view"]["context_completeness"]
        self.assertEqual(completeness["unparsed_message_count"], 2)
        self.assertIn("2 unparsed emails", completeness["note"])
        self.assertTrue(result["detail_view"]["missing_fields"])
        self.assertEqual(payload["detail_view"]["missing_fields"], [])
        for invalid in (-1, True, "2"):
            self.document["unparsed_message_count"] = invalid
            with self.assertRaisesRegex(AnalysisValidationError, "nonnegative integer"):
                validate_analysis_payload(payload, self.document)

    def test_bad_note_is_normalized_without_second_model_call(self):
        payload = _payload(self.document)
        payload["detail_view"]["context_completeness"]["note"] = ""
        with patch("agent.workflows.customer_analysis.generate_json", return_value=json.dumps(payload)) as model:
            result = generate_analysis(self.document, clock=lambda: NOW)
        self.assertEqual(result["status"], "completed")
        model.assert_called_once()

    def test_repair_receives_previous_output_and_exact_failure_path(self):
        bad = _payload(self.document)
        bad["detail_view"]["profile"]["intent"]["facts"][0]["source_refs"] = ["latest_message_summary"]
        previous = json.dumps(bad, ensure_ascii=False)
        with patch("agent.workflows.customer_analysis.generate_json", side_effect=[previous, json.dumps(_payload(self.document))]) as model:
            result = generate_analysis(self.document, clock=lambda: NOW)
        self.assertEqual(result["status"], "completed")
        repair = model.call_args_list[1].args[1]
        self.assertIn(json.dumps(bad, ensure_ascii=False), repair)
        self.assertNotIn(json.dumps(previous, ensure_ascii=False), repair)
        self.assertIn("not a JSON string, array, patch", repair)
        self.assertIn("profile.intent.facts[0].source_refs", repair)
        self.assertIn("SOURCE_CATALOG", repair)
        self.assertEqual(model.call_count, 2)

    def test_probability_diagnostic_has_path_and_keyword_without_customer_sentence(self):
        payload = _payload(self.document)
        payload["detail_view"]["analysis"]["risk"]["inferences"][0]["text"] = "PRIVATE_PERSON 的订单无法判断成交概率。"
        raw = json.dumps(payload, ensure_ascii=False)
        with (patch("agent.workflows.customer_analysis.generate_json", side_effect=[raw, raw]) as model,
              self.assertLogs("salesmate.agent.customer_analysis", level="WARNING") as captured):
            result = generate_analysis(self.document, clock=lambda: NOW)
        self.assertEqual(result["status"], "failed")
        message = result["error"]["message"]
        self.assertIn("detail_view.analysis.risk.inferences[0].text", message)
        self.assertIn("matched='成交概率'", message)
        self.assertNotIn("PRIVATE_PERSON", message)
        self.assertNotIn("PRIVATE_PERSON", "\n".join(captured.output))
        self.assertEqual(model.call_count, 2)

    def test_standalone_denial_is_rephrased_without_model_retry(self):
        payload = _payload(self.document)
        payload["list_view"]["headline_summary"] = "目前无法判断成交概率。"
        with patch("agent.workflows.customer_analysis.generate_json",
                   side_effect=[json.dumps(payload), json.dumps(_payload(self.document))]) as model:
            result = generate_analysis(self.document, clock=lambda: NOW)
        self.assertEqual(result["status"], "completed")
        model.assert_called_once()
        self.assertEqual(result["list_view"]["headline_summary"], "The available information does not establish a deal outcome.")
        self.assertFalse(_contains_deal_probability(result))

    def test_denial_normalization_does_not_hide_predictions_or_conditional_statements(self):
        for text in ("成交概率 80%", "无法判断成交概率为80%", "目前无法判断成交概率，但预计能达到80%",
                     "目前无法判断成交概率。预计80%。", "尚需确认签约可能性。", "赢单率低于去年"):
            with self.subTest(text=text):
                self.assertEqual(_normalize_probability_denial(text), text)
                self.assertTrue(_contains_deal_probability(text))
        for text in ("目前无法判断成交概率。", "不能估算该客户的签约概率", "暂时难以评估赢单率。"):
            with self.subTest(text=text):
                self.assertEqual(_normalize_probability_denial(text), "The available information does not establish a deal outcome.")
        self.assertTrue(_contains_deal_probability("Estimated win rate is 80%."))
        self.assertEqual(
            _normalize_probability_denial("We cannot estimate the win rate."),
            "The available information does not establish a deal outcome.",
        )

    def test_denial_normalization_preserves_sources_and_original_candidate(self):
        payload = _payload(self.document)
        fact = payload["detail_view"]["profile"]["intent"]["inferences"][0]
        fact["text"] = "目前无法判断成交概率。"
        original = copy.deepcopy(payload)
        result = validate_analysis_payload(payload, self.document)
        updated = result["detail_view"]["profile"]["intent"]["inferences"][0]
        self.assertEqual(updated["source_refs"], fact["source_refs"])
        self.assertEqual(updated["basis"], fact["basis"])
        self.assertEqual(payload, original)

    def test_provider_network_error_is_not_retried(self):
        with patch("agent.workflows.customer_analysis.generate_json", side_effect=TimeoutError) as model:
            result = generate_analysis(self.document, clock=lambda: NOW)
        self.assertEqual(result["status"], "failed")
        model.assert_called_once()

    def test_backend_conflict_is_logged_and_reported_without_blind_retry(self):
        backend = FakeBackend(seed="mvp")
        backend._enqueue_job("email_ingested", "company-demo")
        with (patch.object(backend, "save_analysis", side_effect=BackendRequestError(409, "conflict", "changed")) as save,
              self.assertLogs("salesmate.agent.orchestration", level="WARNING") as captured):
            reports = process_jobs_once(backend=backend, analysis_provider=lambda document: json.dumps(_payload(document)), clock=lambda: NOW)
        save.assert_called_once()
        self.assertEqual(reports[0]["status"], "failed")
        self.assertEqual(reports[0]["error"]["code"], "job_failed")
        self.assertIn("http_status=409 backend_code=conflict retry=explicit", "\n".join(captured.output))
