"""Responsibility: Verify that inference configuration retains business context and preserves explicit failure semantics at the HTTP boundary.
Implementation: Uses the complete production schema and mocked local HTTP responses to check prompts, caching, budgets, and auditing; does not start a real model.
Relationships: inference_profiles, semantic_provider, and semantic_contract; an independent benchmark measures real performance.
Directory:
- GraphInferenceTests: Configuration and invocation-contract tests.
- GraphInferenceTests.setUp: Construct complete schema and dynamic context.
- GraphInferenceTests.test_schema_first_preserves_all_content: Check that key reordering changes no data.
- GraphInferenceTests.test_prefix_stays_stable_before_dynamic_time: Check that different times retain a shared schema prefix.
- GraphInferenceTests.test_invalid_profile_and_envelope_fail: Reject unknown configuration and missing-field input.
- GraphInferenceTests.test_explicit_server_configuration: Verify consistency of context and cache parameters.
- GraphInferenceTests.test_provider_payload_and_audit: Check consistency of actual payload, timeout, and audit.
- GraphInferenceTests.test_provider_failure_is_not_retried: A model timeout is called once and propagates.
- GraphInferenceTests.test_unknown_profile_does_not_call_http: Configuration error occurs before network invocation.
Variable index:
- None
"""
import copy
import hashlib
import json
from unittest.mock import patch

import requests
from django.test import SimpleTestCase

from apps.knowledge_graph.business_schema import catalog
from apps.knowledge_graph.inference_profiles import PROFILES, get_profile, prepare_messages, server_arguments
from apps.knowledge_graph.semantic_contract import messages
from apps.knowledge_graph.semantic_provider import generate


# Function: Verify optimization boundaries and explicit failure behavior.
# Logic: Production schema and functions participate in validation; only HTTP responses are mocked.
# Constraints: These tests cannot infer real Qwen quality, cache hits, or server speed.
class GraphInferenceTests(SimpleTestCase):
    # Function: Construct a message with complete dynamic fields.
    # Inputs: No external parameters; reads the production business catalog.
    # Outputs: prompt instance state containing the full schema and nonempty candidates and facts.
    # Logic: Dynamic values expose lost fields or side effects during reordering.
    # Constraints: Candidates are purely synthetic dictionaries and do not access the database.
    def setUp(self):
        self.prompt = messages("新邮件：Acme needs Edge.", "2026-09-25T01:00:00+00:00", {
            "schema": catalog(), "entities": [{"id": "synthetic-1", "name": "Acme"}],
            "facts": [{"subject": "synthetic-1", "predicate": "name", "value": "Acme"}], "generation": 7,
        })

    # Function: Prove that the complete schema and dynamic values are preserved unchanged.
    # Inputs: No external parameters; uses prompt and every declared configuration.
    # Outputs: Semantically equal message JSON and an unmodified source message.
    # Logic: Compares an independent deep copy rather than building the expected result through the function itself.
    # Constraints: Only user JSON key order may change; the system trust boundary may not change.
    def test_schema_first_preserves_all_content(self):
        original = copy.deepcopy(self.prompt)
        for name in PROFILES:
            result = prepare_messages(self.prompt, get_profile(name))
            self.assertEqual(result[0], original[0])
            self.assertEqual(json.loads(result[1]["content"]), json.loads(original[1]["content"]))
            self.assertEqual(self.prompt, original)
            self.assertIsNot(result, self.prompt)
            if name == "baseline":
                self.assertEqual(result, original)

    # Function: Verify that the stable schema prefix precedes changing time.
    # Inputs: No external parameters; changes only observed_at on the same message.
    # Outputs: Identical serialized prefix before time while the complete catalog remains present.
    # Logic: Checks the first field and prefix boundary without equating a character prefix to a real token-cache hit.
    # Constraints: Real benchmark responses verify actual cache_n.
    def test_prefix_stays_stable_before_dynamic_time(self):
        changed = copy.deepcopy(self.prompt)
        body = json.loads(changed[1]["content"])
        body["observed_at"] = "2026-09-26T01:00:00+00:00"
        changed[1]["content"] = json.dumps(body, ensure_ascii=False)
        first, second = [prepare_messages(prompt, get_profile("prefix16"))[1]["content"] for prompt in (self.prompt, changed)]
        self.assertTrue(first.startswith('{"existing_context": {"schema":'))
        self.assertEqual(first.split('"observed_at":')[0], second.split('"observed_at":')[0])
        self.assertNotEqual(first, second)

    # Function: Explicitly reject unknown configuration and incomplete envelopes.
    # Inputs: No external parameters; simulates caller protocol mismatch.
    # Outputs: ValueError; mutations to a retrieved configuration do not contaminate subsequent reads.
    # Logic: Covers unknown names, missing generation, and mutable-configuration side effects.
    # Constraints: Does not add fallback configuration or implicitly fill default fields.
    def test_invalid_profile_and_envelope_fail(self):
        with self.assertRaises(ValueError):
            get_profile("automatic")
        profile = get_profile("prefix16")
        profile["context"] = 1
        self.assertEqual(get_profile("prefix16")["context"], 16384)
        broken = copy.deepcopy(self.prompt)
        body = json.loads(broken[1]["content"])
        del body["existing_context"]["generation"]
        broken[1]["content"] = json.dumps(body)
        with self.assertRaises(ValueError):
            prepare_messages(broken, profile)

    # Function: Ensure model startup parameters cannot silently shrink or slide.
    # Inputs: No external parameters; iterates configurations and simulates valid and invalid thread counts.
    # Outputs: Assertions for one slot, explicit context/KV types, disabled fit/sliding, and cache switches.
    # Logic: Checks CLI argument positions and mutually exclusive options.
    # Constraints: Does not replace real-binary validation that parameters are supported.
    def test_explicit_server_configuration(self):
        for name, profile in PROFILES.items():
            args = server_arguments(name, 2)
            self.assertEqual(args[args.index("-c") + 1], str(profile["context"]))
            self.assertEqual(args[args.index("-ctv") + 1], profile["kv"])
            self.assertEqual(args[args.index("--fit") + 1], "off")
            self.assertIn("--no-context-shift", args)
            self.assertEqual("--cache-prompt" in args, profile["cache"])
            self.assertEqual("--no-cache-prompt" in args, not profile["cache"])
        for threads in (0, -1, True):
            with self.assertRaises(ValueError):
                server_arguments("baseline", threads)

    # Function: Verify consistency between actual model requests and audit records.
    # Inputs: No external parameters; mocks successful HTTP and covers default, first-round cache, and supplemental prefix8 configuration.
    # Outputs: Assertions for cache, messages, hashes, budgets, timeouts, and proxy behavior.
    # Logic: Checks actual sent content at the requests.Session boundary while retaining input unchanged.
    # Constraints: Mocked cache counts validate forwarding only and do not prove real cache acceleration.
    def test_provider_payload_and_audit(self):
        for name in ("baseline", "prefix16", "combined8", "prefix8"):
            env = {"GRAPH_LLM_URL": "http://127.0.0.1:8088/v1", "GRAPH_LLM_MODEL": "salesmate-graph"}
            if name != "baseline":
                env["GRAPH_LLM_PROFILE"] = name
            with patch.dict("os.environ", env, clear=True), patch("requests.Session") as factory:
                session = factory.return_value.__enter__.return_value
                session.post.return_value.status_code = 200
                session.post.return_value.json.return_value = {"model": "salesmate-graph", "timings": {"cache_n": 123},
                    "choices": [{"finish_reason": "stop", "message": {"content": '{"entities": [], "facts": []}'}}]}
                parsed, audit = generate(self.prompt)
                call = session.post.call_args
                self.assertEqual(call.kwargs["timeout"], (10, 300))
                self.assertFalse(call.kwargs["allow_redirects"])
                self.assertFalse(session.trust_env)
                payload = call.kwargs["json"]
                self.assertEqual(payload["cache_prompt"], get_profile(name)["cache"])
                self.assertEqual((payload["temperature"], payload["seed"], payload["max_tokens"]), (0, 2026, 1536))
                self.assertEqual(json.loads(payload["messages"][1]["content"]), json.loads(self.prompt[1]["content"]))
                expected = hashlib.sha256(json.dumps(payload["messages"], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                self.assertEqual(audit["prompt_sha256"], expected)
                self.assertEqual(audit["inference_profile"], name)
                self.assertEqual(audit["timings"], {"cache_n": 123})
                self.assertEqual(parsed, {"entities": [], "facts": []})

    # Function: Prevent automatic retry or configuration switching after model timeout.
    # Inputs: No external parameters; mocks ReadTimeout at the HTTP boundary.
    # Outputs: The same exception propagates and post is called once only.
    # Logic: Verifies that optimization paths do not alter failure semantics.
    # Constraints: Does not wait for a real 300 seconds or claim that a network timeout was measured.
    def test_provider_failure_is_not_retried(self):
        with patch.dict("os.environ", {"GRAPH_LLM_URL": "http://127.0.0.1:8088/v1", "GRAPH_LLM_MODEL": "salesmate-graph",
                                     "GRAPH_LLM_PROFILE": "combined8"}), patch("requests.Session") as factory:
            session = factory.return_value.__enter__.return_value
            session.post.side_effect = requests.ReadTimeout("synthetic timeout")
            with self.assertRaises(requests.ReadTimeout):
                generate(self.prompt)
            session.post.assert_called_once()

    # Function: Prevent unknown configuration from silently using the default model call.
    # Inputs: No external parameters; explicitly invalid GRAPH_LLM_PROFILE.
    # Outputs: ValueError and no Session creation.
    # Logic: Rejects configuration before network side effects.
    # Constraints: Existing integration tests cover backend standard-error wrapping.
    def test_unknown_profile_does_not_call_http(self):
        with patch.dict("os.environ", {"GRAPH_LLM_URL": "http://127.0.0.1:8088/v1", "GRAPH_LLM_MODEL": "salesmate-graph",
                                     "GRAPH_LLM_PROFILE": "typo"}), patch("requests.Session") as factory:
            with self.assertRaises(ValueError):
                generate(self.prompt)
            factory.assert_not_called()
