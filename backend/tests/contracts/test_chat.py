"""Responsibility: Verify that chat reports undergo schema validation only, without database or model-service dependencies.
Implementation: Use a valid report as a baseline to cover open versions, citation metadata, error text, invalid types, and storage lengths.
Relationships: Directly invokes `apps.chat.contracts.report`; permissions, snapshots, and transactions are covered by `integration/test_chat.py`.
Directory:
- ChatReportSchemaTests: Database-free chat-report contract tests.
- ChatReportSchemaTests.setUp: Prepare valid reports and citations.
- ChatReportSchemaTests.test_content_is_not_validated: Accept new versions, arbitrary identifiers, and duplicate sources.
- ChatReportSchemaTests.test_custom_error_and_terminal_shapes: Accept custom errors and reject invalid mutually exclusive terminal-state shapes.
- ChatReportSchemaTests.test_invalid_types_and_fields: Reject invalid fields, UUIDs, and types.
- ChatReportSchemaTests.test_storage_lengths: Verify database string-field length boundaries.
Variable index:
- None
"""

from apps.chat.contracts import report
from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError


# Function: Verify boundaries between report-structure validation and response-content evaluation.
# Logic: Test the contract function directly with ordinary dictionaries without mocking database behavior.
# Constraints: Passing this test does not establish correct permissions, transactions, or real model quality.
class ChatReportSchemaTests(SimpleTestCase):
    # Function: Construct baseline input satisfying the current report schema.
    # Inputs: No external parameters; uses a fixed test UUID and ordinary text.
    # Outputs: Instance-state `payload` and `citation`.
    # Logic: The baseline excludes `company_id` and does not require a context snapshot.
    # Constraints: All identifiers are test constants and no real records are accessed.
    def setUp(self):
        self.citation = {
            "source_id": "unregistered-source",
            "source_type": "customer_context",
            "title_or_label": "Agent 声明的来源",
        }
        self.payload = {
            "request_id": "00000000-0000-0000-0000-000000000001",
            "chat_prompt_version": "workspace-chat-v1",
            "assistant_text": "文本中的 [99] 不作为后端拒绝依据。",
            "citations": [],
            "status": "completed",
            "error": None,
        }

    # Function: Verify that the backend does not scan body text, bind versions, or restrict source content.
    # Inputs: Instance baseline report, a source outside the allowlist, and duplicate citations.
    # Outputs: Each original report value is accepted.
    # Logic: Cover no citations, duplicate citations, body text without identifiers, and unestablished versions.
    # Constraints: Check structural acceptance only and make no claim that sources are real or semantically correct.
    def test_content_is_not_validated(self):
        for version in ("chat-v2", "general-chat-v1", "workspace-chat-v1", "future-v9"):
            for citations in ([], [self.citation, self.citation.copy()]):
                for body in (self.payload["assistant_text"], "没有引用标记的回答"):
                    payload = {
                        **self.payload,
                        "chat_prompt_version": version,
                        "assistant_text": body,
                        "citations": citations,
                    }
                    with self.subTest(
                        version=version, citations=len(citations), body=body
                    ):
                        self.assertEqual(report(payload), payload)

    # Function: Verify the boundary between open error text and the terminal-state schema.
    # Inputs: Baseline report and custom error object.
    # Outputs: A valid failure is accepted unchanged; success with an error or failure with a response is rejected.
    # Logic: Cover conditional-field constraints for `completed` and `failed` separately.
    # Constraints: Error redaction is the Agent's responsibility; this test does not assess text meaning.
    def test_custom_error_and_terminal_shapes(self):
        failure = {
            **self.payload,
            "status": "failed",
            "assistant_text": "",
            "error": {"code": "custom_error", "message": "自定义失败说明"},
        }
        self.assertEqual(report(failure), failure)
        for payload in (
            {**self.payload, "error": failure["error"]},
            {**failure, "assistant_text": "回答"},
            {**failure, "citations": [self.citation]},
            {**failure, "error": None},
            {**failure, "error": {"code": 1, "message": "说明"}},
            {**failure, "error": {"code": "code", "message": []}},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                report(payload)

    # Function: Verify that invalid JSON-object structures remain rejected after content restrictions are relaxed.
    # Inputs: Missing fields, unknown fields, and type variants of the baseline report.
    # Outputs: Every counterexample raises `ValidationError`.
    # Logic: Cover the top level, required UUID, strings, arrays, enum, and citation objects.
    # Constraints: Do not test type coercion through the ORM or HTTP.
    def test_invalid_types_and_fields(self):
        cases = [None, [], {**self.payload, "owner_id": 1}]
        cases.extend(
            {key: value for key, value in self.payload.items() if key != missing}
            for missing in self.payload
        )
        for key, values in (
            ("request_id", [None, 1, "not-a-uuid"]),
            ("chat_prompt_version", [None, {}, " "]),
            ("assistant_text", [None, 1, " "]),
            (
                "citations",
                [
                    None,
                    {},
                    [None],
                    [{**self.citation, "content": "自报正文"}],
                    [{**self.citation, "source_id": []}],
                ],
            ),
            ("status", [None, [], "processing"]),
        ):
            cases.extend({**self.payload, key: value} for value in values)
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                report(payload)

    # Function: Verify that version and source type match existing database field capacities.
    # Inputs: Text at the exact upper limit and text exceeding it by one character.
    # Outputs: Boundary values are valid and excessive lengths are rejected.
    # Logic: Use established storage limits of 100 characters for versions and 80 for `source_type`.
    # Constraints: Do not modify models, migrations, or established storage lengths.
    def test_storage_lengths(self):
        payload = {
            **self.payload,
            "chat_prompt_version": "v" * 100,
            "citations": [{**self.citation, "source_type": "s" * 80}],
        }
        self.assertEqual(report(payload), payload)
        for invalid in (
            {**payload, "chat_prompt_version": "v" * 101},
            {**payload, "citations": [{**self.citation, "source_type": "s" * 81}]},
        ):
            with self.subTest(payload=invalid), self.assertRaises(ValidationError):
                report(invalid)
