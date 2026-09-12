"""检查 Gmail 只读边界、配置路径及课程 CLI 集成流程。"""

import base64
import io
import json
import unittest
from contextlib import redirect_stdout
from email.message import EmailMessage
from unittest.mock import Mock, call, patch

from agent.config import AGENT_DIR
from agent.llm.bailian import generate_json
from agent.main import main as cli_main
from agent.tools.gmail import (
    SCOPES,
    connect_gmail,
    get_profile_address,
    read_email,
    read_recent_emails,
)
from agent.workflows.l1_email import (
    EMAIL_SUBMISSION_FIELDS,
    MULTI_VALUE_FACT_FIELDS,
    process_email,
)
from agent.tests.email_submission_exploration import (
    EmailSubmissionCLIExplorationTests,
)


BAILIAN_CONFIG = {
    "DASHSCOPE_API_KEY": "fake-test-key",
    "BAILIAN_BASE_URL": "https://example.invalid/compatible-mode/v1",
    "BAILIAN_MODEL": "fake-model",
}


def _gmail_raw(message_id, *, subject=None, body=None, headers=None):
    message = EmailMessage()
    message["From"] = "buyer@example.com"
    message["To"] = "sales@example.com"
    message["Subject"] = subject or f"Subject {message_id}"
    for name, value in headers or ():
        message[name] = value
    message.set_content(body or f"Body {message_id}")
    return base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")


class RecordingFakeProvider:
    """返回基于当前正文的有效事实，并记录每次离线调用。"""

    def __init__(self):
        self.calls = []

    def __call__(self, subject, body_text):
        self.calls.append((subject, body_text))
        return {
            "has_substantive_update": True,
            "message_summary": "已处理中文邮件",
            "intent_hint": "purchase_inquiry",
            "intent_evidences": [body_text],
            **{
                field: []
                for field in MULTI_VALUE_FACT_FIELDS
            },
        }


class GmailReadOnlyIntegrationTests(unittest.TestCase):
    """使用 mocked Google service 验证 Gmail 读取边界，全程离线。"""

    def setUp(self):
        boundary_targets = {
            "connect": "agent.tools.gmail.connect_gmail",
            "oauth_flow": (
                "agent.tools.gmail.InstalledAppFlow.from_client_secrets_file"
            ),
            "load_token": (
                "agent.tools.gmail.Credentials.from_authorized_user_file"
            ),
            "refresh_request": "agent.tools.gmail.Request",
            "build": "agent.tools.gmail.build",
        }
        self.boundary_patchers = {
            name: patch(target) for name, target in boundary_targets.items()
        }
        for name, patcher in self.boundary_patchers.items():
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

        self.service = Mock(name="mock_gmail_service")
        self.users = self.service.users.return_value
        self.messages = self.users.messages.return_value
        self.threads = self.users.threads.return_value
        self.drafts = self.users.drafts.return_value
        self.labels = self.users.labels.return_value

    def assert_fully_offline_and_read_only(self):
        self.assertEqual(
            SCOPES, ["https://www.googleapis.com/auth/gmail.readonly"]
        )
        # These boundaries would read gmail_token.json/credentials.json, start
        # browser OAuth, create a refresh transport, or construct a real client.
        self.connect.assert_not_called()
        self.oauth_flow.assert_not_called()
        self.oauth_flow.return_value.run_local_server.assert_not_called()
        self.load_token.assert_not_called()
        self.refresh_request.assert_not_called()
        self.build.assert_not_called()

        for method_name in (
            "modify",
            "batchModify",
            "trash",
            "delete",
            "batchDelete",
            "send",
            "insert",
            "import_",
        ):
            getattr(self.messages, method_name).assert_not_called()
        for method_name in ("modify", "trash", "delete"):
            getattr(self.threads, method_name).assert_not_called()
        for method_name in ("create", "update", "send", "delete"):
            getattr(self.drafts, method_name).assert_not_called()
        for method_name in ("create", "update", "patch", "delete"):
            getattr(self.labels, method_name).assert_not_called()
        self.users.threads.assert_not_called()
        self.users.drafts.assert_not_called()
        self.users.labels.assert_not_called()

    def test_profile_address_uses_mocked_authorized_profile(self):
        profile_request = Mock(name="profile_request")
        profile_request.execute.return_value = {
            "emailAddress": "sales@example.com"
        }
        self.users.getProfile.return_value = profile_request

        self.assertEqual(get_profile_address(self.service), "sales@example.com")

        self.users.getProfile.assert_called_once_with(userId="me")
        profile_request.execute.assert_called_once_with()
        self.messages.get.assert_not_called()
        self.messages.list.assert_not_called()
        self.assert_fully_offline_and_read_only()

    def test_specified_message_gets_only_target_id_as_raw(self):
        message_request = Mock(name="message_request")
        message_request.execute.return_value = {
            "id": "target-id",
            "threadId": "thread-target",
            "raw": _gmail_raw("target-id"),
        }
        self.messages.get.return_value = message_request

        email = read_email(self.service, "target-id")

        self.messages.get.assert_called_once_with(
            userId="me", id="target-id", format="raw"
        )
        message_request.execute.assert_called_once_with()
        self.messages.list.assert_not_called()
        self.assertEqual(email["gmail_message_id"], "target-id")
        self.assertEqual(email["thread_id"], "thread-target")
        self.assertIsNone(email["received_at"])
        self.assertNotIn("source", email)
        self.assertNotIn("message_id", email)
        self.assertEqual(email["body_text"], "Body target-id")
        self.assert_fully_offline_and_read_only()

    def test_recent_lists_once_and_gets_at_most_five_raw_in_order(self):
        listed_ids = ["m-3", "m-1", "m-5", "m-2", "m-4", "m-6"]
        list_request = Mock(name="list_request")
        list_request.execute.return_value = {
            "messages": [{"id": message_id} for message_id in listed_ids],
            "nextPageToken": "must-not-be-followed",
        }
        self.messages.list.return_value = list_request
        get_requests = []

        def message_request(*, userId, id, format):
            request = Mock(name=f"get_{id}")
            request.execute.return_value = {
                "id": id,
                "threadId": f"thread-{id}",
                "raw": _gmail_raw(id),
            }
            get_requests.append(request)
            return request

        self.messages.get.side_effect = message_request

        emails = read_recent_emails(self.service, limit=20)

        self.messages.list.assert_called_once_with(userId="me", maxResults=5)
        list_request.execute.assert_called_once_with()
        expected_ids = listed_ids[:5]
        self.assertEqual(
            self.messages.get.call_args_list,
            [
                call(userId="me", id=message_id, format="raw")
                for message_id in expected_ids
            ],
        )
        self.assertEqual(
            [email["gmail_message_id"] for email in emails], expected_ids
        )
        self.assertEqual([email["body_text"] for email in emails], [
            f"Body {message_id}" for message_id in expected_ids
        ])
        for request in get_requests:
            request.execute.assert_called_once_with()
        self.assert_fully_offline_and_read_only()


    def _run_property_2_cli(self, service, provider, argv):
        """Run the real CLI assembly with every external/config boundary blocked."""
        stdout = io.StringIO()
        with (
            patch("agent.main.load_environment") as load_environment,
            patch("agent.main.connect_gmail", return_value=service) as connect,
            patch("agent.main.bailian_extraction_provider", provider),
            patch(
                "agent.llm.bailian.requests.post",
                side_effect=AssertionError("HTTP is forbidden in preservation tests"),
            ) as post,
            redirect_stdout(stdout),
        ):
            exit_code = cli_main(argv)

        load_environment.assert_called_once_with()
        connect.assert_called_once_with()
        post.assert_not_called()
        return exit_code, stdout.getvalue(), json.loads(stdout.getvalue())

    @staticmethod
    def _project_message_id(result):
        """Project only the documented message_id -> gmail_message_id migration."""
        if "gmail_message_id" in result:
            return result["gmail_message_id"]
        return result["message_id"]

    def test_property_2_cli_profile_fallback_emits_one_unicode_json_object(self):
        """**Validates: Requirements 3.1, 3.2, 3.3, 3.8, 3.10**"""
        from agent.tests.email_submission_exploration import (
            CANONICAL_BODY,
            CANONICAL_GMAIL_ID,
            CANONICAL_SUBJECT,
            PROFILE_MAILBOX,
            ProviderSpy,
            canonical_resource,
        )

        profile_request = Mock(name="property_2_profile_request")
        profile_request.execute.return_value = {"emailAddress": PROFILE_MAILBOX}
        self.users.getProfile.return_value = profile_request
        message_request = Mock(name="property_2_message_request")
        message_request.execute.return_value = canonical_resource()
        self.messages.get.return_value = message_request
        provider = ProviderSpy()

        exit_code, output_text, document = self._run_property_2_cli(
            self.service,
            provider,
            ["--message-id", CANONICAL_GMAIL_ID],
        )

        self.assertEqual(exit_code, 0)
        self.assertIsInstance(document, dict)
        self.assertNotIn("\\u", output_text)
        self.assertEqual(self._project_message_id(document), CANONICAL_GMAIL_ID)
        self.assertEqual(document["subject"], CANONICAL_SUBJECT)
        self.assertEqual(document["body_text"], CANONICAL_BODY)
        self.users.getProfile.assert_called_once_with(userId="me")
        profile_request.execute.assert_called_once_with()
        self.messages.get.assert_called_once_with(
            userId="me", id=CANONICAL_GMAIL_ID, format="raw"
        )
        self.messages.list.assert_not_called()
        self.assertEqual(provider.calls, [(CANONICAL_SUBJECT, CANONICAL_BODY)])
        self.assert_fully_offline_and_read_only()

    def test_property_2_cli_override_recent_limit_order_and_one_get_per_id(self):
        """**Validates: Requirements 3.1, 3.2, 3.3, 3.7, 3.10**"""
        from agent.tests.email_submission_exploration import (
            CANONICAL_BODY,
            CANONICAL_MAILBOX,
            CANONICAL_SUBJECT,
            ProviderSpy,
            canonical_resource,
        )

        listed_ids = ("邮件-3", "邮件-1", "邮件-5", "邮件-2", "邮件-4", "邮件-6")
        self.assertGreater(len(listed_ids), 0, "recent witness set must be non-empty")
        list_request = Mock(name="property_2_list_request")
        list_request.execute.return_value = {
            "messages": [{"id": message_id} for message_id in listed_ids],
            "nextPageToken": "must-not-be-followed",
        }
        self.messages.list.return_value = list_request
        get_requests = []

        def get_message(*, userId, id, format):
            request = Mock(name=f"property_2_get_{id}")
            request.execute.return_value = canonical_resource(
                id=id,
                threadId=f"thread-{id}",
            )
            get_requests.append(request)
            return request

        self.messages.get.side_effect = get_message
        provider = ProviderSpy()
        exit_code, output_text, document = self._run_property_2_cli(
            self.service,
            provider,
            ["--recent", "--mailbox-address", CANONICAL_MAILBOX],
        )

        expected_ids = list(listed_ids[:5])
        self.assertEqual(exit_code, 0)
        self.assertIsInstance(document, list)
        self.assertNotIn("\\u", output_text)
        self.assertEqual(
            [self._project_message_id(result) for result in document],
            expected_ids,
        )
        self.assertEqual([result["subject"] for result in document], [CANONICAL_SUBJECT] * 5)
        self.assertEqual([result["body_text"] for result in document], [CANONICAL_BODY] * 5)
        self.users.getProfile.assert_not_called()
        self.messages.list.assert_called_once_with(userId="me", maxResults=5)
        list_request.execute.assert_called_once_with()
        self.assertEqual(
            self.messages.get.call_args_list,
            [
                call(userId="me", id=message_id, format="raw")
                for message_id in expected_ids
            ],
        )
        for request in get_requests:
            request.execute.assert_called_once_with()
        self.assertEqual(provider.calls, [(CANONICAL_SUBJECT, CANONICAL_BODY)] * 5)
        self.assert_fully_offline_and_read_only()

    def test_property_2_cli_argument_surface_remains_gmail_only(self):
        """**Validates: Requirements 3.1, 3.10**"""
        argument_witnesses = (
            ("missing selection", []),
            ("conflicting selections", ["--message-id", "m1", "--recent"]),
            ("no mailbox-id", ["--message-id", "m1", "--mailbox-id", "business-7"]),
            ("no local source", ["--message-id", "m1", "--source", "local"]),
        )
        self.assertGreater(len(argument_witnesses), 0, "CLI argument witness set must be non-empty")

        for name, argv in argument_witnesses:
            with self.subTest(name=name):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    patch("agent.main.load_environment") as load_environment,
                    patch("agent.main.connect_gmail") as connect,
                    patch("sys.stderr", stderr),
                    redirect_stdout(stdout),
                    self.assertRaises(SystemExit) as raised,
                ):
                    cli_main(argv)

                self.assertEqual(raised.exception.code, 2)
                self.assertEqual(stdout.getvalue(), "")
                load_environment.assert_not_called()
                connect.assert_not_called()

    def test_property_2_unrecoverable_reads_keep_one_safe_nonzero_json_error(self):
        """**Validates: Requirements 3.8, 3.9**"""
        from agent.tests.email_submission_exploration import (
            CANONICAL_GMAIL_ID,
            CANONICAL_MAILBOX,
            ProviderSpy,
            canonical_resource,
        )

        secret = "Authorization-Bearer-read-secret-sensitive-message"

        def profile_failure(service):
            request = Mock(name="failed_profile_request")
            request.execute.side_effect = RuntimeError(secret)
            service.users.return_value.getProfile.return_value = request
            return ["--message-id", CANONICAL_GMAIL_ID]

        def list_failure(service):
            request = Mock(name="failed_list_request")
            request.execute.side_effect = RuntimeError(secret)
            service.users.return_value.messages.return_value.list.return_value = request
            return ["--recent", "--mailbox-address", CANONICAL_MAILBOX]

        def invalid_message_id(service):
            request = Mock(name="invalid_message_request")
            resource = canonical_resource()
            resource.pop("id")
            request.execute.return_value = resource
            service.users.return_value.messages.return_value.get.return_value = request
            return ["--message-id", CANONICAL_GMAIL_ID, "--mailbox-address", CANONICAL_MAILBOX]

        def invalid_raw(service):
            request = Mock(name="invalid_raw_request")
            request.execute.return_value = canonical_resource(raw="invalid-base64%")
            service.users.return_value.messages.return_value.get.return_value = request
            return ["--message-id", CANONICAL_GMAIL_ID, "--mailbox-address", CANONICAL_MAILBOX]

        read_witnesses = (
            ("profile", profile_failure),
            ("list", list_failure),
            ("message id", invalid_message_id),
            ("raw", invalid_raw),
        )
        self.assertGreater(len(read_witnesses), 0, "safe-read witness set must be non-empty")

        expected = {"error": {"code": "input_read_failed", "message": "邮件读取失败。"}}
        for name, configure in read_witnesses:
            with self.subTest(name=name):
                service = Mock(name=f"property_2_{name}_service")
                provider = ProviderSpy()
                argv = configure(service)
                exit_code, output_text, document = self._run_property_2_cli(
                    service, provider, argv
                )

                self.assertNotEqual(exit_code, 0)
                self.assertEqual(document, expected)
                self.assertEqual(json.loads(output_text), expected)
                self.assertNotIn(secret, output_text)
                self.assertEqual(provider.calls, [])


class CLIEmailSubmissionContractTests(unittest.TestCase):
    """通过真实 CLI 组装路径与 mocked Gmail service 验证 19 字段输出。"""

    SAFE_INPUT_ERROR = {
        "error": {
            "code": "input_read_failed",
            "message": "邮件读取失败。",
        }
    }

    def test_message_id_uses_profile_fallback_and_processes_target_once(self):
        service = Mock(name="specified_message_service")
        users = service.users.return_value
        messages = users.messages.return_value
        profile_request = Mock(name="profile_request")
        profile_request.execute.return_value = {"emailAddress": "sales@example.com"}
        users.getProfile.return_value = profile_request
        message_request = Mock(name="target_message_request")
        message_request.execute.return_value = {
            "id": "目标-id",
            "threadId": "thread-target",
            "raw": _gmail_raw(
                "目标-id", subject="指定邮件询价", body="指定邮件的完整中文正文。"
            ),
        }
        messages.get.return_value = message_request
        provider = RecordingFakeProvider()
        process_spy = Mock(wraps=process_email)
        stdout = io.StringIO()

        with (
            patch("agent.main.load_environment"),
            patch("agent.main.connect_gmail", return_value=service) as connect,
            patch("agent.main.bailian_extraction_provider", provider),
            patch("agent.main.process_email", process_spy),
            redirect_stdout(stdout),
        ):
            exit_code = cli_main(["--message-id", "目标-id"])

        output_text = stdout.getvalue()
        document = json.loads(output_text)
        self.assertEqual(exit_code, 0)
        self.assertNotIn("\\u", output_text)
        self.assertEqual(tuple(document), EMAIL_SUBMISSION_FIELDS)
        self.assertEqual(len(document), 19)
        self.assertNotIn("source", document)
        self.assertNotIn("mailbox_id", document)
        self.assertNotIn("message_id", document)
        self.assertNotIn("error", document)
        self.assertEqual(document["gmail_message_id"], "目标-id")
        self.assertEqual(document["thread_id"], "thread-target")
        self.assertEqual(document["subject"], "指定邮件询价")
        self.assertEqual(document["body_text"], "指定邮件的完整中文正文。")
        connect.assert_called_once_with()
        users.getProfile.assert_called_once_with(userId="me")
        profile_request.execute.assert_called_once_with()
        messages.get.assert_called_once_with(
            userId="me", id="目标-id", format="raw"
        )
        messages.list.assert_not_called()
        process_spy.assert_called_once()
        called_email, mailbox_address, called_provider = process_spy.call_args.args
        self.assertEqual(called_email["gmail_message_id"], "目标-id")
        self.assertEqual(mailbox_address, "sales@example.com")
        self.assertIs(called_provider, provider)
        self.assertEqual(provider.calls, [
            ("指定邮件询价", "指定邮件的完整中文正文。")
        ])

    def test_recent_uses_explicit_mailbox_and_processes_each_email_once_in_order(self):
        service = Mock(name="recent_message_service")
        users = service.users.return_value
        messages = users.messages.return_value
        listed_ids = ["最近-2", "最近-1"]
        list_request = Mock(name="list_request")
        list_request.execute.return_value = {
            "messages": [{"id": message_id} for message_id in listed_ids]
        }
        messages.list.return_value = list_request

        def get_message(*, userId, id, format):
            request = Mock(name=f"get_{id}")
            request.execute.return_value = {
                "id": id,
                "threadId": f"thread-{id}",
                "raw": _gmail_raw(
                    id,
                    subject=f"主题 {id}",
                    body=f"完整中文正文 {id}。",
                ),
            }
            return request

        messages.get.side_effect = get_message
        provider = RecordingFakeProvider()
        process_spy = Mock(wraps=process_email)
        stdout = io.StringIO()

        with (
            patch("agent.main.load_environment"),
            patch("agent.main.connect_gmail", return_value=service),
            patch("agent.main.bailian_extraction_provider", provider),
            patch("agent.main.process_email", process_spy),
            redirect_stdout(stdout),
        ):
            exit_code = cli_main(
                ["--recent", "--mailbox-address", "sales@example.com"]
            )

        output_text = stdout.getvalue()
        document = json.loads(output_text)
        self.assertEqual(exit_code, 0)
        self.assertNotIn("\\u", output_text)
        self.assertEqual(len(document), len(listed_ids))
        for result in document:
            self.assertEqual(tuple(result), EMAIL_SUBMISSION_FIELDS)
            self.assertEqual(len(result), 19)
            self.assertTrue(
                {"source", "mailbox_id", "message_id", "error"}.isdisjoint(result)
            )
        self.assertEqual(
            [result["gmail_message_id"] for result in document], listed_ids
        )
        self.assertEqual(
            [result["body_text"] for result in document],
            [f"完整中文正文 {message_id}。" for message_id in listed_ids],
        )
        users.getProfile.assert_not_called()
        messages.list.assert_called_once_with(userId="me", maxResults=5)
        self.assertEqual(
            messages.get.call_args_list,
            [
                call(userId="me", id=message_id, format="raw")
                for message_id in listed_ids
            ],
        )
        self.assertEqual(process_spy.call_count, len(listed_ids))
        self.assertEqual(
            [args.args[0]["gmail_message_id"] for args in process_spy.call_args_list],
            listed_ids,
        )
        self.assertTrue(all(
            args.args[1] == "sales@example.com"
            and args.args[2] is provider
            for args in process_spy.call_args_list
        ))
        self.assertEqual(len(provider.calls), len(listed_ids))

    def test_all_extraction_statuses_are_normal_exact_cli_submissions(self):
        """Completed, failed, and skipped states all keep exit code zero."""
        cases = (
            ("completed", RecordingFakeProvider(), None),
            (
                "failed",
                Mock(side_effect=RuntimeError("provider secret must stay hidden")),
                None,
            ),
            (
                "skipped_non_business",
                Mock(name="provider_must_not_run"),
                (("Precedence", "bulk"),),
            ),
        )

        for expected_status, provider, headers in cases:
            with self.subTest(status=expected_status):
                service = Mock(name=f"{expected_status}_service")
                messages = service.users.return_value.messages.return_value
                request = Mock(name=f"{expected_status}_request")
                request.execute.return_value = {
                    "id": f"{expected_status}-id",
                    "raw": _gmail_raw(
                        f"{expected_status}-id",
                        subject=f"状态 {expected_status}",
                        body=f"正文 {expected_status}",
                        headers=headers,
                    ),
                }
                messages.get.return_value = request
                stdout = io.StringIO()

                with (
                    patch("agent.main.load_environment"),
                    patch("agent.main.connect_gmail", return_value=service),
                    patch("agent.main.bailian_extraction_provider", provider),
                    redirect_stdout(stdout),
                ):
                    exit_code = cli_main(
                        [
                            "--message-id",
                            f"{expected_status}-id",
                            "--mailbox-address",
                            "sales@example.com",
                        ]
                    )

                output_text = stdout.getvalue()
                document = json.loads(output_text)
                self.assertEqual(exit_code, 0)
                self.assertEqual(tuple(document), EMAIL_SUBMISSION_FIELDS)
                self.assertEqual(len(document), 19)
                self.assertEqual(document["extract_status"], expected_status)
                self.assertNotIn("provider secret", output_text)
                self.assertTrue(
                    {"source", "mailbox_id", "message_id", "error"}.isdisjoint(
                        document
                    )
                )
                if expected_status == "skipped_non_business":
                    provider.assert_not_called()
                elif expected_status == "failed":
                    provider.assert_called_once_with(
                        f"状态 {expected_status}", f"正文 {expected_status}"
                    )
                else:
                    self.assertEqual(
                        provider.calls,
                        [(f"状态 {expected_status}", f"正文 {expected_status}")],
                    )

    def test_gmail_fetch_failure_returns_safe_error_without_processing(self):
        service = Mock(name="failed_gmail_service")
        users = service.users.return_value
        messages = users.messages.return_value
        failed_request = Mock(name="failed_message_request")
        failed_request.execute.side_effect = RuntimeError(
            "Authorization: Bearer secret-token; raw provider response"
        )
        messages.get.return_value = failed_request
        process_mock = Mock(name="process_email")
        provider = Mock(name="provider")
        stdout = io.StringIO()

        with (
            patch("agent.main.load_environment"),
            patch("agent.main.connect_gmail", return_value=service),
            patch("agent.main.bailian_extraction_provider", provider),
            patch("agent.main.process_email", process_mock),
            redirect_stdout(stdout),
        ):
            exit_code = cli_main(
                [
                    "--message-id",
                    "missing-id",
                    "--mailbox-address",
                    "sales@example.com",
                ]
            )

        output_text = stdout.getvalue()
        self.assertNotEqual(exit_code, 0)
        self.assertEqual(json.loads(output_text), self.SAFE_INPUT_ERROR)
        self.assertNotIn("secret-token", output_text)
        self.assertNotIn("raw provider response", output_text)
        users.getProfile.assert_not_called()
        messages.get.assert_called_once_with(
            userId="me", id="missing-id", format="raw"
        )
        process_mock.assert_not_called()
        provider.assert_not_called()

    def test_mime_parse_failure_returns_safe_error_without_processing(self):
        service = Mock(name="invalid_mime_service")
        users = service.users.return_value
        messages = users.messages.return_value
        message_request = Mock(name="invalid_mime_request")
        message_request.execute.return_value = {
            "id": "invalid-mime-id",
            "threadId": None,
            "raw": "not-valid-base64%",
        }
        messages.get.return_value = message_request
        process_mock = Mock(name="process_email")
        provider = Mock(name="provider")
        stdout = io.StringIO()

        with (
            patch("agent.main.load_environment"),
            patch("agent.main.connect_gmail", return_value=service),
            patch("agent.main.bailian_extraction_provider", provider),
            patch("agent.main.process_email", process_mock),
            redirect_stdout(stdout),
        ):
            exit_code = cli_main(
                [
                    "--message-id",
                    "invalid-mime-id",
                    "--mailbox-address",
                    "sales@example.com",
                ]
            )

        self.assertNotEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), self.SAFE_INPUT_ERROR)
        users.getProfile.assert_not_called()
        messages.get.assert_called_once_with(
            userId="me", id="invalid-mime-id", format="raw"
        )
        process_mock.assert_not_called()
        provider.assert_not_called()

    def test_cli_requires_exactly_one_gmail_selection(self):
        invalid_argv = (
            [],
            ["--message-id", "one-id", "--recent"],
        )

        for argv in invalid_argv:
            with self.subTest(argv=argv):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    patch("agent.main.connect_gmail") as connect,
                    patch("sys.stderr", stderr),
                    redirect_stdout(stdout),
                    self.assertRaises(SystemExit) as raised,
                ):
                    cli_main(argv)

                self.assertEqual(raised.exception.code, 2)
                self.assertEqual(stdout.getvalue(), "")
                connect.assert_not_called()

    def test_removed_local_cli_flags_are_rejected(self):
        removed_options = (
            ("--mailbox-id", "business-mailbox-7"),
            ("--source", "local"),
            ("--email-file", "mail.txt"),
            ("--from", "buyer@example.com"),
            ("--to", "sales@example.com"),
            ("--cc", "manager@example.com"),
            ("--subject", "本地主题"),
        )

        for option in removed_options:
            with self.subTest(option=option[0]):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    patch("agent.main.connect_gmail") as connect,
                    patch("sys.stderr", stderr),
                    redirect_stdout(stdout),
                    self.assertRaises(SystemExit) as raised,
                ):
                    cli_main(["--message-id", "target-id", *option])

                self.assertEqual(raised.exception.code, 2)
                self.assertEqual(stdout.getvalue(), "")
                connect.assert_not_called()


class CLIArgumentCompatibilityTests(unittest.TestCase):
    """验证 Task 3.7 保留的 CLI 参数表面，不触达任何运行时边界。"""

    def test_message_and_recent_remain_strictly_mutually_exclusive(self):
        cases = (
            [],
            ["--message-id", "one-id", "--recent"],
        )
        for argv in cases:
            with self.subTest(argv=argv):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    patch("agent.main.load_environment") as load_environment,
                    patch("agent.main.connect_gmail") as connect,
                    patch("sys.stderr", stderr),
                    redirect_stdout(stdout),
                    self.assertRaises(SystemExit) as raised,
                ):
                    cli_main(argv)

                self.assertEqual(raised.exception.code, 2)
                self.assertEqual(stdout.getvalue(), "")
                load_environment.assert_not_called()
                connect.assert_not_called()

    def test_mailbox_address_remains_optional_and_mailbox_id_is_unknown(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch("agent.main.load_environment") as load_environment,
            patch("agent.main.connect_gmail") as connect,
            patch("sys.stderr", stderr),
            redirect_stdout(stdout),
            self.assertRaises(SystemExit) as raised,
        ):
            cli_main(
                [
                    "--message-id",
                    "one-id",
                    "--mailbox-id",
                    "business-mailbox-7",
                ]
            )

        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(stdout.getvalue(), "")
        load_environment.assert_not_called()
        connect.assert_not_called()


class IntegrationTests(unittest.TestCase):
    @patch.dict("os.environ", BAILIAN_CONFIG, clear=True)
    @patch("agent.llm.bailian.requests.post")
    def test_llm_client_accepts_arbitrary_json_task(self, post):
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {
            "choices": [{"finish_reason": "stop", "message": {"content": '{"order_id": "A-1"}'}}]
        }
        result = generate_json("提取订单编号，返回 JSON。", "订单编号 A-1")
        self.assertEqual(json.loads(result), {"order_id": "A-1"})
        self.assertEqual(post.call_args.kwargs["json"]["messages"], [
            {"role": "system", "content": "提取订单编号，返回 JSON。"},
            {"role": "user", "content": "订单编号 A-1"},
        ])

    @patch("agent.tools.gmail.build")
    @patch("agent.tools.gmail.Credentials.from_authorized_user_file")
    @patch("pathlib.Path.exists", return_value=True)
    def test_gmail_reuses_token_from_agent_root(self, exists, load_token, build):
        load_token.return_value = Mock(valid=True)
        self.assertIs(connect_gmail(), build.return_value)
        load_token.assert_called_once_with(str(AGENT_DIR / "gmail_token.json"), SCOPES)


if __name__ == "__main__":
    unittest.main()
