"""Responsibility: Verify quota-only failover and durable model eligibility without real provider calls.
Implementation: Mock only HTTP transport; exercise real SQLite, independent instances, concurrent connections, and a CLI subprocess.
Relationships: Covers agent.llm.bailian and pool; existing core tests retain single-model transport contracts.
Directory:
- response: Construct a synthetic provider response.
- ModelPoolTests: Model pool safety and persistence regressions.
- ModelPoolTests.setUp: Create isolated configuration and SQLite path.
- ModelPoolTests.pool: Open the same scoped persistent pool.
- ModelPoolTests.test_quota_switch_preserves_request: Switch only after quota failure while preserving caller conditions.
- ModelPoolTests.test_exhaustion_stops_and_persists: Bound calls and retain exhaustion across fresh instances.
- ModelPoolTests.test_non_quota_errors_never_switch: Reject nonquota failures without excluding models.
- ModelPoolTests.test_transport_and_output_failures_do_not_switch: Avoid retrying timeouts or incomplete success.
- ModelPoolTests.test_native_quota_code: Recognize the documented free-tier-only error.
- ModelPoolTests.test_concurrent_exclusions_and_credential_scope: Preserve concurrent exclusions and credential isolation.
- ModelPoolTests.test_restore_requires_explicit_model: Restore only the explicitly selected configured model.
- ModelPoolTests.test_invalid_config_and_corrupt_state_fail_closed: Stop before HTTP on invalid or unavailable state.
- ModelPoolTests.test_cli_restart_reads_persisted_state: Verify a new process sees the same exclusion.
- ModelPoolTests.test_legacy_single_model_does_not_switch: Preserve unconfigured-pool behavior.
Variable index:
- None
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests
from agent.llm.bailian import LLMError, generate_chat_json, generate_json
from agent.llm.pool import ModelPool, PoolError


# Function: Build a synthetic HTTP response.
# Inputs: HTTP `status`, optional quota `code`, and complete JSON `content`.
# Outputs: Mock response whose JSON body matches provider envelopes.
# Logic: Success contains a terminal assistant response; failure retains only a structured error code.
# Constraints: Contains no real key, endpoint, or business data.
def response(status=200, code=None, content='{"ok":true}'):
    result = Mock(status_code=status)
    result.json.return_value = ({"choices": [{"finish_reason": "stop", "message": {"content": content}}]} if status == 200 else {"error": {"code": code, "message": "secret-body must never be logged"}})
    return result


# Function: Exercise pool behavior under isolated environment configuration.
# Logic: Temporary directories retain state within a test and are deleted afterward.
# Constraints: Every provider HTTP boundary is mocked; these tests do not establish live model availability.
class ModelPoolTests(unittest.TestCase):
    # Function: Configure two synthetic candidates and a real SQLite file.
    # Inputs: Temporary filesystem and current test instance.
    # Outputs: env dictionary, file path, and cleanup registrations.
    # Logic: Clear external configuration so developer settings cannot affect selection.
    # Constraints: Provider endpoint is intentionally invalid and no real credentials are loaded.
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "models.sqlite3"
        self.env = {"DASHSCOPE_API_KEY": "test-private-key", "BAILIAN_BASE_URL": "https://example.invalid/v1", "BAILIAN_MODEL": "legacy",
                    "BAILIAN_MODELS": "first,second", "BAILIAN_MODEL_STATE_DB": str(self.path), "BAILIAN_ENABLE_THINKING": "true"}
        settings = patch.dict(os.environ, self.env, clear=True)
        settings.start()
        self.addCleanup(settings.stop)

    # Function: Open the same provider credential scope.
    # Inputs: Instance environment.
    # Outputs: Fresh ModelPool instance using the shared test path.
    # Logic: Reconstruct on each call to avoid relying on process-local caches.
    # Constraints: Does not make network requests.
    def pool(self):
        return ModelPool.from_env(self.env["BAILIAN_BASE_URL"], self.env["DASHSCOPE_API_KEY"])

    # Function: Switch after quota rejection while retaining all caller conditions.
    # Inputs: Quota failure followed by complete success and ordered chat messages.
    # Outputs: Second model text, durable first-model exclusion, and identical non-model payload fields.
    # Logic: Repeat from a fresh client call and verify it never selects the blocked model again.
    # Constraints: No business tools execute or successful responses replay.
    def test_quota_switch_preserves_request(self):
        messages = [{"role": "system", "content": "Return JSON"}, {"role": "user", "content": "question"}]
        with patch("agent.llm.bailian.requests.post", side_effect=[response(403, "insufficient_quota"), response(), response()]) as post:
            with self.assertLogs("salesmate", level="INFO") as logs:
                self.assertEqual(generate_chat_json(messages, max_tokens=731), '{"ok":true}')
            first, second = [call.kwargs for call in post.call_args_list]
            self.assertEqual(first["json"]["model"], "first")
            self.assertEqual(second["json"], {**first["json"], "model": "second"})
            self.assertEqual(second["json"]["messages"], messages)
            self.assertEqual(second["json"]["max_tokens"], 731)
            self.assertTrue(second["json"]["enable_thinking"])
            self.assertEqual(second["timeout"], (10, 90))
            self.assertFalse(second["allow_redirects"])
            generate_json("Return JSON", "again")
            self.assertEqual(post.call_args.kwargs["json"]["model"], "second")
        self.assertEqual(self.pool().status()["current"], "second")
        self.assertNotIn("secret-body", str(logs.output))
        self.assertNotIn("test-private-key", str(logs.output))

    # Function: Stop once every configured candidate is exhausted.
    # Inputs: Two explicit quota failures and another subsequent generation.
    # Outputs: Two attempts total, then zero attempts on the next call; no eligible model remains.
    # Logic: Persist every observed rejection and reload through a new instance.
    # Constraints: Does not reset state on restart or silently use the legacy model.
    def test_exhaustion_stops_and_persists(self):
        with patch("agent.llm.bailian.requests.post", side_effect=[response(403, "insufficient_quota"), response(429, "insufficient_quota")]) as post:
            for _ in range(2):
                with self.assertRaisesRegex(LLMError, "All configured"):
                    generate_json("JSON", "question")
            self.assertEqual(post.call_count, 2)
        self.assertIsNone(self.pool().status()["current"])
        self.assertEqual(len(self.pool().status()["unavailable"]), 2)

    # Function: Distinguish quota exhaustion from unrelated provider failures.
    # Inputs: Authentication, permission, generic rate limit, server, and malformed error responses.
    # Outputs: Exactly one failed attempt and no disabled model.
    # Logic: Require the exact HTTP/code pair instead of inferring from status or message text.
    # Constraints: Error strings and logs cannot expose raw server bodies or credentials.
    def test_non_quota_errors_never_switch(self):
        cases = [(401, "insufficient_quota"), (403, "access_denied"), (429, "rate_limit_exceeded"), (500, "insufficient_quota"), (400, "invalid_model")]
        for status, code in cases:
            with self.subTest(status=status, code=code), patch("agent.llm.bailian.requests.post", return_value=response(status, code)) as post:
                with self.assertRaises(LLMError) as error:
                    generate_json("JSON", "question")
                self.assertNotIn("secret-body", str(error.exception))
                self.assertEqual(post.call_count, 1)
                self.assertEqual(self.pool().disabled(), {})
        broken = Mock(status_code=403)
        broken.json.side_effect = ValueError("bad body")
        with patch("agent.llm.bailian.requests.post", return_value=broken) as post:
            with self.assertRaises(LLMError):
                generate_json("JSON", "question")
            self.assertEqual(post.call_count, 1)
        self.assertEqual(self.pool().disabled(), {})

    # Function: Avoid switching on uncertain transport or invalid successful output.
    # Inputs: Timeout, truncated response, and malformed success body.
    # Outputs: One attempt per case and no quota exclusions.
    # Logic: Preserve the original failure semantics for all nonquota errors.
    # Constraints: No automatic retry after a request may have been processed by the provider.
    def test_transport_and_output_failures_do_not_switch(self):
        truncated = response()
        truncated.json.return_value["choices"][0]["finish_reason"] = "length"
        malformed = response()
        malformed.json.return_value = {}
        for outcome in (requests.Timeout("secret timeout"), truncated, malformed):
            with patch("agent.llm.bailian.requests.post", side_effect=[outcome]) as post:
                with self.assertRaises(LLMError):
                    generate_json("JSON", "question")
                self.assertEqual(post.call_count, 1)
        self.assertEqual(self.pool().disabled(), {})

    # Function: Recognize native free-tier-only quota responses.
    # Inputs: Documented top-level AllocationQuota.FreeTierOnly response then success.
    # Outputs: First candidate becomes unavailable and second succeeds.
    # Logic: Native and compatible provider envelopes share one strict quota classification.
    # Constraints: Does not disable free-tier billing protection.
    def test_native_quota_code(self):
        exhausted = Mock(status_code=403)
        exhausted.json.return_value = {"code": "AllocationQuota.FreeTierOnly"}
        with patch("agent.llm.bailian.requests.post", side_effect=[exhausted, response()]):
            generate_json("JSON", "question")
        self.assertEqual(self.pool().disabled()["first"]["code"], "AllocationQuota.FreeTierOnly")

    # Function: Preserve concurrent exclusions across independent database connections.
    # Inputs: Parallel reports for two models and alternative credential/endpoint scopes.
    # Outputs: Exactly two durable entries, no lost updates, and isolated scopes see no exclusions.
    # Logic: Use actual SQLite transactions in independently constructed pool instances.
    # Constraints: The test permits already in-flight provider requests; no global network lock is claimed.
    def test_concurrent_exclusions_and_credential_scope(self):
        with ThreadPoolExecutor(max_workers=4) as workers:
            futures = [workers.submit(self.pool().disable, model, "insufficient_quota", 403) for model in ["first", "second"] * 6]
            for future in futures:
                future.result()
        self.assertEqual(set(self.pool().disabled()), {"first", "second"})
        for base, key in ((self.env["BAILIAN_BASE_URL"], "other-key"), ("https://other.invalid/v1", self.env["DASHSCOPE_API_KEY"])):
            self.assertEqual(ModelPool(["first", "second"], str(self.path), base, key).disabled(), {})

    # Function: Restore a model only on explicit operator request.
    # Inputs: One persisted exclusion and configured or unknown model IDs.
    # Outputs: Explicit restore reselects the first model; unknown IDs are rejected.
    # Logic: Validate the target before deleting its scoped entry.
    # Constraints: Restoration makes a candidate eligible without claiming provider recovery.
    def test_restore_requires_explicit_model(self):
        pool = self.pool()
        pool.disable("first", "insufficient_quota", 403)
        with self.assertRaises(PoolError):
            pool.restore("unknown")
        self.assertTrue(pool.restore("first"))
        self.assertFalse(pool.restore("first"))
        self.assertEqual(pool.status()["current"], "first")

    # Function: Fail before networking on invalid configuration or corrupt state.
    # Inputs: Duplicate/empty candidate IDs, missing/relative paths, and non-SQLite bytes.
    # Outputs: LLMError and zero transport calls.
    # Logic: Exercise validation and database exception sanitization without an in-memory substitute.
    # Constraints: Does not adjust timeout, token budgets, or selection order to manufacture success.
    def test_invalid_config_and_corrupt_state_fail_closed(self):
        for change in ({"BAILIAN_MODELS": "first,first"}, {"BAILIAN_MODELS": "first,,second"}, {"BAILIAN_MODEL_STATE_DB": ""}, {"BAILIAN_MODEL_STATE_DB": "relative.sqlite3"}):
            with patch.dict(os.environ, change), patch("agent.llm.bailian.requests.post") as post:
                with self.assertRaises(LLMError):
                    generate_json("JSON", "question")
                post.assert_not_called()
        self.path.write_bytes(b"not a sqlite database")
        with patch("agent.llm.bailian.requests.post") as post:
            with self.assertRaisesRegex(LLMError, "state is unavailable"):
                generate_json("JSON", "question")
            post.assert_not_called()

    # Function: Verify state survives an actual process restart.
    # Inputs: Persisted quota exclusion and subprocess CLI using the same explicit configuration.
    # Outputs: New process reports the second model as current and never outputs the API key.
    # Logic: Execute the real Python module rather than mocking command behavior.
    # Constraints: CLI performs no provider calls and does not auto-restore models.
    def test_cli_restart_reads_persisted_state(self):
        self.pool().disable("first", "insufficient_quota", 403)
        run = subprocess.run([sys.executable, "-m", "agent.llm.pool", "status"], capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"}, check=True)
        self.assertEqual(json.loads(run.stdout)["current"], "second")
        self.assertNotIn(self.env["DASHSCOPE_API_KEY"], run.stdout + run.stderr)

    # Function: Retain explicitly unpooled single-model behavior.
    # Inputs: No pool setting and a quota failure from the configured legacy model.
    # Outputs: One HTTP error without creating SQLite state or selecting another model.
    # Logic: Pool activation is explicit rather than inferred from provider failures.
    # Constraints: Does not alter the existing BAILIAN_MODEL default.
    def test_legacy_single_model_does_not_switch(self):
        with patch.dict(os.environ, {"BAILIAN_MODELS": ""}), patch("agent.llm.bailian.requests.post", return_value=response(403, "insufficient_quota")) as post:
            with self.assertRaises(LLMError):
                generate_json("JSON", "question")
            self.assertEqual(post.call_count, 1)
            self.assertEqual(post.call_args.kwargs["json"]["model"], "legacy")
        self.assertFalse(self.path.exists())
