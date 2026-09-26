"""Responsibility: Verifies QQ sending connection, SMTP payload, and action-state boundaries.
Implementation: Explicitly enables QQ capability, uses an isolated database, mocks SMTP/IMAP network, and executes real encryption, approval, and state writes.
Relationships: `sales.qq_connection`, `qq_smtp`, and `actions`; does not use development mailbox credentials.
Directory:
- QQSendTests: QQ sending integration tests.
- QQSendTests.setUp: Prepares a synthetic connection and email draft.
- QQSendTests.prepare: Creates a pending-confirmation action.
- QQSendTests.test_connection_only_authenticates: Connection is encrypted and no mail is sent.
- QQSendTests.test_connection_rejects_unapproved_fields_and_credentials: Input and authentication-failure boundaries.
- QQSendTests.test_connection_permissions_and_pending_actions: Identity isolation and credential-replacement limits.
- QQSendTests.test_session_write_requires_csrf: A real session forbids connection writes without CSRF.
- QQSendTests.test_confirmation_frozen_mime_and_single_submission: Approval, frozen content, and one submission only.
- QQSendTests.test_refused_recipient_sends_no_body: Any rejected recipient prevents body submission.
- QQSendTests.test_transport_outcomes_are_not_retried: Distinguishes failure from uncertainty and forbids retry.
- QQSendTests.test_quit_failure_preserves_acceptance: Cleanup failure does not erase an accepted result.
- QQSendTests.test_changed_connection_blocks_submission: Checks the frozen connection version before execution.
- QQSendTests.test_recipient_validation_and_owner_scope: Recipient-address and account-permission validation.
- QQSendTests.test_verification_reads_matching_sent_copy_only: Reconciliation is read-only and requires a unique matching copy.
Variable index:
- None
"""
import smtplib
import uuid
from email import message_from_bytes, policy
from email.message import EmailMessage
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.crm.access import InvalidState
from apps.sales import actions, grouping, integrations, models, qq_smtp


# Function: Covers state and payload contracts for independent QQ sending.
# Logic: Explicitly enables QQ and replaces only network transport; other business and database operations use real implementations.
# Constraints: Does not read real authorization codes or send mail; mock success does not establish external availability.
@override_settings(QQ_MAIL_ENABLED=True)
class QQSendTests(TestCase):
    # Function: Creates synthetic test data and a fixed successful SMTP response.
    # Inputs: No external parameters; uses a one-time test key.
    # Outputs: `user`, `company`, `connection`, `draft`, `client`, and `smtp` instance state.
    # Logic: Each case builds an isolated database; all SMTP construction is replaced by mocks.
    # Constraints: Patches and settings are cleaned after tests.
    def setUp(self):
        setting = override_settings(SALESMATE_VAULT_KEY=Fernet.generate_key().decode())
        setting.enable()
        self.addCleanup(setting.disable)
        self.user = get_user_model().objects.create_user(username="qq-send-test")
        self.company = grouping.create_company(self.user, "QQ 发信测试客户")
        self.connection = models.Connection.objects.create(owner=self.user, provider="qq", account="sender@qq.com", encrypted_credentials=integrations.encrypt_credentials({"authorization_code": "abcdefghijklmnop"}))
        conversation = models.Conversation.objects.create(owner=self.user, company=self.company)
        self.draft = models.Draft.objects.create(owner=self.user, conversation=conversation, kind="email", subject="报价确认", content="第一行\n第二行", recipients=["buyer@example.com", "buyer2@example.com"])
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        patcher = patch("apps.sales.qq_smtp.smtplib.SMTP_SSL")
        self.factory = patcher.start()
        self.addCleanup(patcher.stop)
        self.smtp = self.factory.return_value
        self.smtp.ehlo.return_value = (250, b"hello")
        self.smtp.mail.return_value = (250, b"ok")
        self.smtp.rcpt.return_value = (250, b"ok")
        self.smtp.data.return_value = (250, b"accepted")

    # Function: Creates a pending-confirmation action for the current draft.
    # Inputs: Reads employee, customer, connection, and draft from the current test instance.
    # Outputs: ToolAction.
    # Logic: Uses the formal action-creation service, freezing data without approval.
    # Constraints: Does not execute SMTP.
    def prepare(self):
        return actions.create_action(self.user, {"company": str(self.company.pk), "tool": "qq.send", "parameters": {"connection_id": str(self.connection.pk), "draft_id": str(self.draft.pk)}, "idempotency_key": str(uuid.uuid4())})

    # Function: Verifies the connection performs TLS authentication only and saves ciphertext.
    # Inputs: POST with a synthetic account authorization code.
    # Outputs: 201, no credential echo, and no sending action.
    # Logic: Checks fixed host, certificate validation, and that SMTP envelope and body were not called.
    # Constraints: SMTP is mocked and does not prove a real account can authenticate.
    def test_connection_only_authenticates(self):
        response = self.client.post("/api/v1/sales/connections/qq/", {"address": "new@qq.com", "authorization_code": "abcdefghijklmnop"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotIn("abcdefghijklmnop", str(response.data))
        self.assertNotIn("encrypted_credentials", response.data)
        connection = models.Connection.objects.get(pk=response.data["id"])
        self.assertNotIn("abcdefghijklmnop", connection.encrypted_credentials)
        self.assertEqual(integrations.credentials_for(connection), {"authorization_code": "abcdefghijklmnop"})
        self.assertEqual(self.factory.call_args.args, ("smtp.qq.com", 465))
        self.assertTrue(self.factory.call_args.kwargs["context"].check_hostname)
        self.smtp.login.assert_called_once_with("new@qq.com", "abcdefghijklmnop")
        self.smtp.mail.assert_not_called()
        self.smtp.data.assert_not_called()
        self.assertFalse(models.ToolAction.objects.exists())

    # Function: Verifies input allowlist and persistence boundaries for authentication rejection.
    # Inputs: Extra server field, invalid address, incorrect authorization code, and simulated authentication error.
    # Outputs: Formatting error is 400; authentication failure is 409; no connection or server secret is added or echoed.
    # Logic: Fails separately in serialization and SMTP authentication.
    # Constraints: Makes no real login attempt.
    def test_connection_rejects_unapproved_fields_and_credentials(self):
        data = {"address": "new@qq.com", "authorization_code": "abcdefghijklmnop"}
        for change in ({"host": "example.com"}, {"address": "x@example.com"}, {"authorization_code": "wrong"}):
            with self.subTest(change=change):
                response = self.client.post("/api/v1/sales/connections/qq/", {**data, **change}, format="json")
                self.assertEqual(response.status_code, 400)
        self.factory.assert_not_called()
        self.smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"private-server-detail")
        response = self.client.post("/api/v1/sales/connections/qq/", data, format="json")
        self.assertEqual(response.status_code, 409, response.data)
        self.assertNotIn("private-server-detail", str(response.data))
        self.assertEqual(models.Connection.objects.count(), 1)

    # Function: Verifies login permission and that pending actions prevent authorization replacement.
    # Inputs: Unauthenticated access, a related pending action, and a connection request by another employee.
    # Outputs: Anonymous access is rejected, same-employee replacement conflicts, and another employee saves independently.
    # Logic: Uses the real API and checks owner and original connection version.
    # Constraints: Mocks authentication only and does not send.
    def test_connection_permissions_and_pending_actions(self):
        data = {"address": "sender@qq.com", "authorization_code": "abcdefghijklmnop"}
        self.client.force_authenticate(None)
        self.assertIn(self.client.post("/api/v1/sales/connections/qq/", data, format="json").status_code, (401, 403))
        self.factory.assert_not_called()
        self.client.force_authenticate(self.user)
        self.prepare()
        self.assertEqual(self.client.post("/api/v1/sales/connections/qq/", data, format="json").status_code, 409)
        other = get_user_model().objects.create_user(username="another-sender")
        self.client.force_authenticate(other)
        response = self.client.post("/api/v1/sales/connections/qq/", data, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(models.Connection.objects.get(pk=response.data["id"]).owner, other)
        self.smtp.data.assert_not_called()

    # Function: Verifies QQ sending connection inherits Session CSRF protection.
    # Inputs: A logged-in real session without a CSRF token.
    # Outputs: 403; SMTP is not called.
    # Logic: Enables `APIClient` CSRF enforcement and does not use `force_authenticate`.
    # Constraints: The test does not submit accounts externally.
    def test_session_write_requires_csrf(self):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.post("/api/v1/sales/connections/qq/", {"address": "sender@qq.com", "authorization_code": "abcdefghijklmnop"}, format="json")
        self.assertEqual(response.status_code, 403)
        self.factory.assert_not_called()

    # Function: Verifies QQ draft freezing, approval gate, and one submission after success.
    # Inputs: Edits a draft after preparation, then explicitly approves the original action.
    # Outputs: Old Chinese content, complete recipients, and stable Message-ID are submitted once.
    # Logic: Parses real MIME bytes and checks SMTP envelope and CRLF.
    # Constraints: Transport is replaced and does not establish delivery.
    def test_confirmation_frozen_mime_and_single_submission(self):
        action = self.prepare()
        self.assertEqual(actions.run_action(action.pk), "pending_confirmation")
        self.factory.assert_not_called()
        self.draft.content = "后来修改的正文"
        self.draft.save()
        action = actions.decide_action(action, self.user, action.revision, "approved")
        self.assertEqual(actions.run_action(action.pk), "succeeded")
        self.assertEqual(actions.run_action(action.pk), "succeeded")
        self.smtp.data.assert_called_once()
        raw = self.smtp.data.call_args.args[0]
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        message = message_from_bytes(raw, policy=policy.default)
        self.assertEqual(message["Subject"], "报价确认")
        self.assertEqual(message["From"], "sender@qq.com")
        self.assertEqual(message["Message-ID"], f"<{action.pk}@salesmate.local>")
        self.assertEqual(message.get_content().replace("\r\n", "\n").strip(), "第一行\n第二行")
        self.assertEqual([call.args[0] for call in self.smtp.rcpt.call_args_list], self.draft.recipients)
        action.refresh_from_db()
        self.assertEqual(action.result["submission_status"], "smtp_accepted")

    # Function: Avoids sending body content to approved recipients when partial recipient rejection occurs.
    # Inputs: The second RCPT returns 550.
    # Outputs: Action is `failed`; DATA is never called.
    # Logic: Allows submission only after all recipients are accepted; failure closes the session.
    # Constraints: Does not automatically remove rejected addresses or resend.
    def test_refused_recipient_sends_no_body(self):
        action = self.prepare()
        actions.decide_action(action, self.user, action.revision, "approved")
        self.smtp.rcpt.side_effect = [(250, b"ok"), (550, b"no")]
        self.assertEqual(actions.run_action(action.pk), "failed")
        self.smtp.data.assert_not_called()
        self.smtp.quit.assert_called_once()

    # Function: Distinguishes explicit rejection, pre-submission failure, and body-submission interruption.
    # Inputs: Authentication failure, DATA 550, DATA timeout, and DATA connection interruption.
    # Outputs: The first two are `failed`, the latter two are `uncertain`, and repeated execution adds no submission.
    # Logic: Creates an approved action for each case and injects only the corresponding network exception.
    # Constraints: An unknown result cannot automatically be treated as unsent.
    def test_transport_outcomes_are_not_retried(self):
        for phase, error, expected in [("login", smtplib.SMTPAuthenticationError(535, b"no"), "failed"), ("data", smtplib.SMTPDataError(550, b"no"), "failed"), ("data", TimeoutError(), "uncertain"), ("data", smtplib.SMTPServerDisconnected(), "uncertain")]:
            with self.subTest(phase=phase, error=type(error).__name__):
                self.smtp.reset_mock()
                self.smtp.login.side_effect = self.smtp.data.side_effect = None
                getattr(self.smtp, phase).side_effect = error
                action = self.prepare()
                actions.decide_action(action, self.user, action.revision, "approved")
                self.assertEqual(actions.run_action(action.pk), expected)
                count = self.smtp.data.call_count
                self.assertEqual(actions.run_action(action.pk), expected)
                self.assertEqual(self.smtp.data.call_count, count)

    # Function: Verifies QUIT interruption after DATA acceptance still preserves success.
    # Inputs: Simulates QUIT disconnection after a success response.
    # Outputs: Action is `succeeded`; underlying close still executes.
    # Logic: Cleanup is independent of submission outcome.
    # Constraints: Does not resend because cleanup fails.
    def test_quit_failure_preserves_acceptance(self):
        action = self.prepare()
        actions.decide_action(action, self.user, action.revision, "approved")
        self.smtp.quit.side_effect = smtplib.SMTPServerDisconnected()
        self.assertEqual(actions.run_action(action.pk), "succeeded")
        self.smtp.close.assert_called_once()

    # Function: Verifies execution is blocked after the frozen connection changes.
    # Inputs: Connection version increments for an approved action.
    # Outputs: `failed` and no external connection.
    # Logic: Pre-execution identity-version validation rejects the old snapshot.
    # Constraints: Does not implicitly switch to the new connection.
    def test_changed_connection_blocks_submission(self):
        action = self.prepare()
        actions.decide_action(action, self.user, action.revision, "approved")
        self.connection.revision += 1
        self.connection.save()
        self.assertEqual(actions.run_action(action.pk), "failed")
        self.factory.assert_not_called()

    # Function: Verifies recipient format, duplicate addresses, provider, and employee ownership.
    # Inputs: Corrupt draft addresses and another person's connection or a Gmail connection.
    # Outputs: 400 or 404; no action is created and SMTP is not connected.
    # Logic: Uses real API parameter validation and exception translation.
    # Constraints: Does not test real-address deliverability.
    def test_recipient_validation_and_owner_scope(self):
        data = {"company": str(self.company.pk), "tool": "qq.send", "parameters": {"connection_id": str(self.connection.pk), "draft_id": str(self.draft.pk)}, "idempotency_key": str(uuid.uuid4())}
        for recipients in (["invalid"], ["x@example.com\r\nBCC: a@example.com"], ["x@example.com"] * 2, ["中文@example.com"]):
            self.draft.recipients = recipients
            self.draft.save()
            self.assertEqual(self.client.post("/api/v1/sales/records/actions/", data, format="json").status_code, 400)
        self.connection.provider = "gmail"
        self.connection.save()
        self.assertEqual(self.client.post("/api/v1/sales/records/actions/", data, format="json").status_code, 404)
        self.connection.provider = "qq"
        self.connection.owner = get_user_model().objects.create_user(username="other-owner")
        self.connection.save()
        self.assertEqual(self.client.post("/api/v1/sales/records/actions/", data, format="json").status_code, 404)
        self.factory.assert_not_called()

    # Function: Verifies uncertain actions reconcile read-only with exactly one complete matching sent header.
    # Inputs: Empty search, wrong subject, and correct copy, including server-added sender display name.
    # Outputs: The first two remain `uncertain`; a match becomes `succeeded`; SMTP is never connected.
    # Logic: Mocks IMAP UID query and BODY.PEEK return while executing real reconciliation and state updates.
    # Constraints: Does not APPEND, resend, or treat not-found as send failure.
    def test_verification_reads_matching_sent_copy_only(self):
        action = self.prepare()
        action.status = "uncertain"
        action.save()
        client = Mock()
        with patch("apps.sales.qq_smtp.qq_mail.connect", return_value=client), patch("apps.sales.qq_smtp.qq_mail.folders", return_value=("INBOX", "Sent Messages")), patch("apps.sales.qq_smtp.qq_mail.select_folder", return_value=123), patch("apps.sales.qq_smtp.qq_mail.disconnect"):
            client.uid.return_value = ("OK", [b""])
            with self.assertRaises(InvalidState):
                actions.verify_action(action, self.user, action.revision)
            for subject in ("wrong", action.parameters["subject"]):
                header = EmailMessage(policy=policy.SMTP)
                header["Message-ID"] = f"<{action.pk}@salesmate.local>"
                header["From"] = "Sender <sender@qq.com>"
                header["To"] = ", ".join(action.parameters["to"])
                header["Subject"] = subject
                client.uid.side_effect = [("OK", [b"42"]), ("OK", [(b"7 (UID 42 BODY[HEADER.FIELDS] {100}", header.as_bytes())])]
                if subject == "wrong":
                    with self.assertRaises(InvalidState):
                        actions.verify_action(action, self.user, action.revision)
                    action.refresh_from_db()
                    self.assertEqual(action.status, "uncertain")
                else:
                    result = actions.verify_action(action, self.user, action.revision)
                    self.assertEqual(result.status, "succeeded")
                    self.assertEqual(result.result["submission_status"], "confirmed_in_sent")
            self.assertIn("BODY.PEEK", client.uid.call_args.args[2])
            client.append.assert_not_called()
        self.factory.assert_not_called()
