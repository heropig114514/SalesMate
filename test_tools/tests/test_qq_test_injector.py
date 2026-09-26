"""Responsibility: Offline-verify write gating and result semantics for the QQ test injector.
Implementation: Mock IMAP and authorization-code input while constructing real MIME; never access a mailbox or project database.
Relationships: Tests `qq_test_injector.py` and is run by the tool-packaging pipeline.
Directory:
- QQInjectorTests: Network-isolated behavior tests.
- QQInjectorTests.setUp: Construct a synthetic plan and mock IMAP.
- QQInjectorTests.test_dry_run_never_reads_credentials_or_connects: Preview makes no external call.
- QQInjectorTests.test_explicit_mode_required: Reject execution when write mode is absent.
- QQInjectorTests.test_rejects_bad_plan_before_connect: Validate allowlists and headers.
- QQInjectorTests.test_append_targets_only_own_inbox: Verify account, folder, and MIME.
- QQInjectorTests.test_partial_rejection_keeps_confirmed_count: Explicit rejection stops the batch.
- QQInjectorTests.test_timeout_stays_uncertain_without_retry: Interrupted writes are not retried.
- QQInjectorTests.test_login_failure_does_not_leak_secret: Authentication exceptions produce safe output.
- QQInjectorTests.test_logout_failure_does_not_change_success: Cleanup exception preserves success.
- QQInjectorTests.test_credentials_require_secure_input: Validate authorization format and no-echo constraints.
Variable index:
- ORIGINAL_READ_CODE: Retains the original authorization-reader function for independent input-protection verification.
"""
import contextlib
import getpass
import imaplib
import io
import json
import tempfile
import unittest
from datetime import datetime
from email import message_from_bytes, policy
from email.utils import parsedate_to_datetime
from pathlib import Path
from unittest.mock import patch

from test_tools import qq_test_injector as tool


# Function: Verify boundaries between offline preview and explicit IMAP writing.
# Logic: Standard-library mocks take over network and authorization while other code executes normally.
# Constraints: Test success does not prove that QQ permits an actual APPEND and does not read a real authorization code.
class QQInjectorTests(unittest.TestCase):
    # Function: Generate a two-message scenario and replace transport and authorization reading.
    # Inputs: No external parameters; uses fixed synthetic address and text only.
    # Outputs: Instance state for `address`, `messages`, `factory`, `client`, and `code`.
    # Logic: Register cleanup for every patch and make the default mocked service explicitly accept commands.
    # Constraints: An accidental write path cannot connect to a real service.
    def setUp(self):
        self.address = "tester@qq.com"
        self.messages = tool.build_test_messages(self.address, [{"from": "测试客户 <buyer@example.com>", "subject": "采购咨询", "body": "第一行\n第二行"}] * 2, "test-run")
        network = patch.object(tool.imaplib, "IMAP4_SSL")
        self.factory = network.start()
        self.addCleanup(network.stop)
        self.client = self.factory.return_value
        self.client.login.return_value = self.client.select.return_value = self.client.append.return_value = ("OK", [b"ok"])
        secret = patch.object(tool, "read_authorization_code", return_value="abcdefghijklmnop")
        self.code = secret.start()
        self.addCleanup(secret.stop)

    # Function: Ensure that the default QQ sample can be previewed completely offline.
    # Inputs: `--dry-run` and the packaged template.
    # Outputs: Successful JSON with neither authorization reading nor network access.
    # Logic: Capture stdout to check explicit mode and nonempty count.
    # Constraints: Do not treat preview success as evidence of server connectivity.
    def test_dry_run_never_reads_credentials_or_connects(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(tool.main(["--dry-run"]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "dry_run")
        self.assertGreater(result["message_count"], 0)
        self.factory.assert_not_called()
        self.code.assert_not_called()

    # Function: Prevent implicit mailbox writes when arguments are omitted.
    # Inputs: Empty arguments and both mutually exclusive modes.
    # Outputs: `argparse` exits 2 and no credential is read.
    # Logic: Verify the mode group's required and mutual-exclusion contract.
    # Constraints: Do not change behavior of the original Gmail tool.
    def test_explicit_mode_required(self):
        for args in ([], ["--dry-run", "--apply"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                tool.main(args)
            self.assertEqual(caught.exception.code, 2)
        self.factory.assert_not_called()
        self.code.assert_not_called()

    # Function: Ensure invalid addresses, header injection, and extra fields fail before connection.
    # Inputs: Multiple invalid plans in temporary JSON.
    # Outputs: `--apply` returns failure with no network or authorization reading.
    # Logic: Use the real CLI entry point to verify that the entire plan is validated first.
    # Constraints: Temporary files contain synthetic text only and are cleaned automatically.
    def test_rejects_bad_plan_before_connect(self):
        message = {"from": "buyer@example.com", "subject": "Test", "body": "Body"}
        base = {"mailbox_address": self.address, "messages": [message]}
        cases = [{**base, "mailbox_address": "x@gmail.com"}, {**base, "authorization_code": "hidden"}, {**base, "messages": [{**message, "subject": "x\r\nBcc: x@example.com"}]}, {**base, "messages": [{**message, "to": "other@qq.com"}]}, {**base, "messages": []}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            for document in cases:
                path.write_text(json.dumps(document), encoding="utf-8")
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(tool.main(["--apply", "--messages-file", str(path)]), 1)
        self.factory.assert_not_called()
        self.code.assert_not_called()

    # Function: Verify the sole account, fixed INBOX, and exact Chinese MIME fixture.
    # Inputs: Two synthetic messages and mocked successful APPEND calls.
    # Outputs: Two confirmations, correct subject and body, past Date, and writes without seen flags.
    # Logic: Inspect actual submitted bytes, certificate validation, and account binding.
    # Constraints: Do not send SMTP or delete or EXPUNGE any message.
    def test_append_targets_only_own_inbox(self):
        result = tool.inject_messages(self.address, self.messages, "test-run")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["inserted_count"], 2)
        self.assertEqual(self.factory.call_args.args, ("imap.qq.com", 993))
        self.assertTrue(self.factory.call_args.kwargs["ssl_context"].check_hostname)
        self.client.login.assert_called_once_with(self.address, "abcdefghijklmnop")
        self.client.select.assert_called_once_with("INBOX", readonly=True)
        for call in self.client.append.call_args_list:
            folder, flags, date, raw = call.args
            self.assertEqual(folder, "INBOX")
            self.assertIsNone(flags)
            self.assertTrue(date.startswith('"'))
            self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
            message = message_from_bytes(raw, policy=policy.default)
            self.assertEqual(message["To"], self.address)
            self.assertIn("[SalesMate测试:QQ:test-run]", str(message["Subject"]))
            self.assertEqual(message.get_content().replace("\r\n", "\n").strip(), "第一行\n第二行")
            self.assertLess(parsedate_to_datetime(message["Date"]), datetime.now().astimezone())
        self.client.expunge.assert_not_called()
        self.client.close.assert_not_called()

    # Function: Preserve the exact count of prior successful messages after explicit rejection.
    # Inputs: First APPEND returns OK and second returns NO.
    # Outputs: `failed`, one confirmed message, and two total calls.
    # Logic: Stop the current batch without deleting the first message or retrying the second.
    # Constraints: Mock protocol responses and do not establish real QQ quota behavior.
    def test_partial_rejection_keeps_confirmed_count(self):
        self.client.append.side_effect = [("OK", [b"ok"]), ("NO", [b"private-service-detail"])]
        result = tool.inject_messages(self.address, self.messages, "test-run")
        self.assertEqual((result["status"], result["inserted_count"]), ("failed", 1))
        self.assertEqual(self.client.append.call_count, 2)
        self.assertNotIn("private-service-detail", str(result))

    # Function: Indicate that a submission interruption may already have written data.
    # Inputs: The first APPEND succeeds and the second times out.
    # Outputs: `uncertain` and the second Message-ID, with no automatic retry.
    # Logic: The confirmed count includes only messages explicitly accepted by the server.
    # Constraints: An unknown result does not mean no write occurred.
    def test_timeout_stays_uncertain_without_retry(self):
        self.client.append.side_effect = [("OK", [b"ok"]), TimeoutError("private")]
        result = tool.inject_messages(self.address, self.messages, "test-run")
        self.assertEqual((result["status"], result["inserted_count"]), ("uncertain", 1))
        self.assertEqual(result["uncertain_message_id"], self.messages[1]["Message-ID"])
        self.assertEqual(self.client.append.call_count, 2)

    # Function: Ensure authentication failure does not expose authorization codes or server exception text.
    # Inputs: A mocked login error carrying a test secret.
    # Outputs: `failed`; neither logs nor result contains the test secret, and APPEND is not called.
    # Logic: Record controlled stage and exception type only.
    # Constraints: Do not use a real account or secret.
    def test_login_failure_does_not_leak_secret(self):
        self.client.login.side_effect = imaplib.IMAP4.error("abcdefghijklmnop private")
        with self.assertLogs(tool.logger, level="ERROR") as logs:
            result = tool.inject_messages(self.address, self.messages, "test-run")
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("abcdefghijklmnop", str(result) + str(logs.output))
        self.client.append.assert_not_called()

    # Function: Ensure cleanup interruption after confirmed writes does not create a retryable failure.
    # Inputs: Every APPEND returns OK and LOGOUT is interrupted.
    # Outputs: `completed` and shutdown still executes.
    # Logic: Transport cleanup is independent from evidence of writes.
    # Constraints: Do not reconnect automatically.
    def test_logout_failure_does_not_change_success(self):
        self.client.logout.side_effect = imaplib.IMAP4.abort("disconnected")
        result = tool.inject_messages(self.address, self.messages, "test-run")
        self.assertEqual(result["status"], "completed")
        self.client.shutdown.assert_called_once()

    # Function: Validate environment authorization codes and reject terminal echo fallback.
    # Inputs: Invalid environment values, a valid synthetic value, and a mocked getpass warning dependency.
    # Outputs: Invalid input raises, valid input returns, and an unsafe terminal explicitly stops.
    # Logic: Call the original function independently without the authorization mock from `setUp`.
    # Constraints: Every environment value exists only inside a patch scope.
    def test_credentials_require_secure_input(self):
        with patch.dict(tool.os.environ, {"QQ_TEST_AUTHORIZATION_CODE": "bad"}):
            with self.assertRaises(ValueError):
                ORIGINAL_READ_CODE()
        with patch.dict(tool.os.environ, {"QQ_TEST_AUTHORIZATION_CODE": "abcdefghijklmnop"}):
            self.assertEqual(ORIGINAL_READ_CODE(), "abcdefghijklmnop")
        with patch.dict(tool.os.environ, {}, clear=True), patch.object(tool.getpass, "getpass", side_effect=getpass.GetPassWarning("no tty")):
            with self.assertRaises(getpass.GetPassWarning):
                ORIGINAL_READ_CODE()


ORIGINAL_READ_CODE = tool.read_authorization_code

if __name__ == "__main__":
    unittest.main()
