"""Responsibility: Verifies QQ integration, durable synchronization, permissions, and coexistence with Gmail.
Implementation: The Gmail test batch explicitly selects at most 20 messages rather than using a runtime default; explicitly enables QQ capability to cover recovered behavior; uses isolated PostgreSQL transactions and HTTP serializers, mocking IMAP/LLM boundaries.
Relationships: `qq_views`, `qq_connection`, `qq_sync`, and `worker`; does not read real mailboxes or call real models.
Directory:
- QQMailTests: QQ cross-module regression tests.
- QQMailTests.setUp: Creates isolated employees, encryption keys, and mocked HTTP and date-query boundaries.
- QQMailTests.http_write: Sends HTTP transport into real ingestion.
- QQMailTests.start: Claims a QQ batch.
- QQMailTests.raw: Builds a standard email with source-text evidence.
- QQMailTests.test_connect_encrypts_and_never_returns_code: Covers authorization validation, encryption, and response redaction.
- QQMailTests.test_invalid_input_or_vault_does_not_connect: Invalid input and a missing key make no network calls.
- QQMailTests.test_authentication_failure_is_safe_and_atomic: Failure is not persisted and does not disclose raw errors.
- QQMailTests.test_disconnect_is_owned_and_preserves_gmail: Employee isolation and Gmail coexistence.
- QQMailTests.test_legacy_gmail_claim_skips_qq: The old CLI does not claim QQ queues.
- QQMailTests.test_worker_persists_qq_and_deduplicates_incremental: Temporary employee identity, Worker, source text, L1, and incremental deduplication.
- QQMailTests.test_failure_requires_explicit_retry: Per-message failure isolation and explicit retry.
- QQMailTests.test_checkpoint_rolls_back_on_generation_change: A UIDVALIDITY change does not advance the cursor.
- QQMailTests.test_session_csrf_is_required: Session/CSRF boundaries for connection writes.
- QQMailTests.test_scope_is_required_and_validated_on_every_request: Scope selection, input, and ownership boundaries for every request.
- QQMailTests.test_limits_apply_before_bodies_across_folders: Global message count, date boundaries, and no missed mail when the range expands.
- QQMailTests.test_retry_keeps_original_window: Retry freezes time and old batches reject unbounded scans.
- QQMailTests.test_single_limits_and_empty_window: Per-item limits and no matching email.
Variable index:
- TEST_KEY: Fixed Fernet key used only to isolate test ciphertext.
"""
import base64
import json
from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from agent.clients.backend_api import DjangoBackendClient
from agent.tools import qq_mail
from apps.crm import gmail_oauth, ingestion, qq_connection, qq_sync, rules
from apps.crm.access import Conflict
from apps.crm.models import Email, GmailCredential, Mailbox, MailboxSyncRun, QQCredential, QQSyncCheckpoint, StoredMessage
from apps.crm.processing import claim_run, finish_run, request_run, retry_run
from apps.crm.access import InvalidState
from apps.crm.worker import run_sync
from apps.sales.integrations import vault

TEST_KEY = base64.urlsafe_b64encode(b"q" * 32).decode("ascii")


# Function: Exercises real business boundaries from QQ connection through background processing.
# Logic: Explicitly enables QQ, test database and account isolation; mocks external IMAP, model, and HTTP.
# Constraints: Passing does not establish real QQ authorization or deployment.
@override_settings(QQ_MAIL_ENABLED=True, ANALYSIS_PROVIDER="agent", SALESMATE_VAULT_KEY=TEST_KEY, LOCAL_DEBUG_AUTO_LOGIN=False)
class QQMailTests(TransactionTestCase):
    # Function: Prepares independent users, QQ connections, and HTTP clients.
    # Inputs: No external arguments; invoked by the test framework.
    # Outputs: Test instance state; no external network.
    # Logic: Encrypts a fictional authorization code with the test key; network methods of a real `DjangoBackendClient` connect to ingestion.
    # Constraints: Does not use workspace accounts or keys.
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="qq-owner", password="test-password")
        self.other = get_user_model().objects.create_user(username="qq-other", password="test-password")
        self.mailbox = Mailbox.objects.create(owner=self.owner, address="demo@qq.com")
        QQCredential.objects.create(mailbox=self.mailbox, encrypted_code=vault().encrypt(b"abcdefghijklmnop").decode("ascii"))
        self.browser = APIClient()
        self.browser.force_authenticate(self.owner)
        self.backend = DjangoBackendClient("http://test.invalid/api/v1/agent/", "test-token", mailbox_id=str(self.mailbox.pk))
        self.backend._request = Mock(side_effect=self.http_write)
        dates = patch.object(qq_mail, "message_dates", side_effect=lambda client, uids: {uid: timezone.now() - timedelta(days=1, seconds=uid) for uid in uids})
        dates.start()
        self.addCleanup(dates.stop)

    # Function: Performs real persistence within the network boundary.
    # Inputs: `method` is the HTTP method; `path` is the endpoint path; `kwargs` contains the JSON payload.
    # Outputs: Real business response and empty-header dictionary.
    # Logic: Strictly permits only `emails/` writes and validates QQ source and identity.
    # Constraints: Does not access any HTTP server.
    def http_write(self, method, path, **kwargs):
        self.assertEqual((method, path), ("POST", "emails/"))
        self.assertTrue(all(item["source"] == "qq_real" for item in kwargs["json"]))
        return ingestion.submit_emails(self.owner, kwargs["json"]), {}

    # Function: Creates and claims one QQ synchronization.
    # Inputs: No external parameters; uses the current test mailbox.
    # Outputs: Durable batch with a lease.
    # Logic: Reuses actual `request_run` and `claim_run`.
    # Constraints: Does not bypass permission or active-batch constraints.
    def start(self):
        request_run(self.owner, self.mailbox.pk, sync_options={"recent_days": 7, "max_messages": 20})
        return claim_run(self.owner)

    # Function: Builds fictional source text for real L1 consumption.
    # Inputs: `value` is the QQ message ID.
    # Outputs: Standard source text and factual fields used as mocked model output.
    # Logic: Rule fixtures produce locatable evidence; the model boundary returns those fixed facts.
    # Constraints: Rules are test-only and do not add a runtime model fallback.
    def raw(self, value):
        document = rules.extract_email(self.mailbox, "buyer@customer.example", "设备询价", "需求：设备\n数量：2 台", value)
        document["extract_prompt_version"] = "extract-v7"
        document["facts"]["intent_hint"] = "L1 Exploring"
        document["eligible_body_text"] = document["body_text"]
        document["headers"] = {}
        return document

    # Function: Verifies ciphertext persists after connection and the response contains no authorization code.
    # Inputs: No external parameters; simulates successful login and folder validation, explicitly selecting at most 20 messages from the last 7 days.
    # Outputs: 202, a new batch, security flags, and decryptable ciphertext.
    # Logic: Uses real API, serialization, and queue services; network is replaced with mocks.
    # Constraints: Does not claim a fictional account is actually valid.
    def test_connect_encrypts_and_never_returns_code(self):
        with patch.object(qq_mail, "connect", return_value=Mock()), patch.object(qq_mail, "folders", return_value=["INBOX", "Sent Messages"]), patch.object(qq_mail, "select_folder", return_value=10), patch.object(qq_mail, "disconnect"):
            response = self.browser.post("/api/v1/mailboxes/qq-connect/", {"address": "DEMO@qq.com", "authorization_code": "abcdefghijklmnop", "sync_options": {"recent_days": 7, "max_messages": 20}}, format="json")
        self.assertEqual(response.status_code, 202, response.data)
        self.assertTrue(response.data["qq_authorized"])
        self.assertFalse(response.data["gmail_authorized"])
        self.assertNotIn("abcdefghijklmnop", json.dumps(response.data))
        credential = QQCredential.objects.get(mailbox=self.mailbox)
        self.assertNotEqual(credential.encrypted_code, "abcdefghijklmnop")
        self.assertEqual(qq_connection.authorization_code(credential), "abcdefghijklmnop")
        self.assertEqual(MailboxSyncRun.objects.get().status, "queued")
        self.assertNotIn("encrypted_code", json.dumps(self.browser.get("/api/v1/mailboxes/").data))

    # Function: Verifies invalid input and missing keys make no external connection.
    # Inputs: No external parameters; unknown fields, non-QQ addresses, short passwords, and an empty vault.
    # Outputs: 400 or 409, with zero network calls.
    # Logic: Strict API validation and encryption configuration precede networking.
    # Constraints: Does not generate a replacement key or accept arbitrary servers.
    def test_invalid_input_or_vault_does_not_connect(self):
        with patch.object(qq_mail, "connect") as connect:
            for data in [{"address": "a@gmail.com", "authorization_code": "abcdefghijklmnop", "sync_options": {"recent_days": 7, "max_messages": 20}}, {"address": "a@qq.com", "authorization_code": "password"}, {"address": "a@qq.com", "authorization_code": "abcdefghijklmnop", "host": "127.0.0.1"}]:
                self.assertEqual(self.browser.post("/api/v1/mailboxes/qq-connect/", data, format="json").status_code, 400)
            with override_settings(SALESMATE_VAULT_KEY=""):
                self.assertEqual(self.browser.post("/api/v1/mailboxes/qq-connect/", {"address": "a@qq.com", "authorization_code": "abcdefghijklmnop", "sync_options": {"recent_days": 7, "max_messages": 20}}, format="json").status_code, 409)
            connect.assert_not_called()

    # Function: Verifies authentication failure is safe and leaves no half-completed connection.
    # Inputs: No external parameters; simulates a controlled transport authentication error.
    # Outputs: 409, and no new mailbox, ciphertext, or batch is created.
    # Logic: Connection validation occurs before database writes.
    # Constraints: Does not present failure as connected.
    def test_authentication_failure_is_safe_and_atomic(self):
        with patch.object(qq_mail, "connect", side_effect=qq_mail.QQMailError("QQ 认证失败")):
            response = self.browser.post("/api/v1/mailboxes/qq-connect/", {"address": "new@qq.com", "authorization_code": "abcdefghijklmnop", "sync_options": {"recent_days": 7, "max_messages": 20}}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertFalse(Mailbox.objects.filter(address="new@qq.com").exists())
        self.assertFalse(MailboxSyncRun.objects.exists())

    # Function: Verifies deletion isolation while preserving Gmail and history.
    # Inputs: No external parameters; another employee, an active batch, and an independent Gmail connection.
    # Outputs: Cross-employee access is 404, active state is 409, removal is allowed after completion, and Gmail remains.
    # Logic: Covers ownership, active state, and final deletion in order.
    # Constraints: Does not delete the mailbox subject or synchronization history.
    def test_disconnect_is_owned_and_preserves_gmail(self):
        gmail = Mailbox.objects.create(owner=self.owner, address="demo@gmail.com")
        GmailCredential.objects.create(mailbox=gmail, credentials={"token": "test"})
        self.browser.force_authenticate(self.other)
        self.assertEqual(self.browser.delete(f"/api/v1/mailboxes/{self.mailbox.pk}/qq-authorization/").status_code, 404)
        self.browser.force_authenticate(self.owner)
        run = self.start()
        self.assertEqual(self.browser.delete(f"/api/v1/mailboxes/{self.mailbox.pk}/qq-authorization/").status_code, 409)
        finish_run(run.pk, run.lease_token, {"status": "completed"})
        response = self.browser.delete(f"/api/v1/mailboxes/{self.mailbox.pk}/qq-authorization/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["qq_authorized"])
        self.assertTrue(GmailCredential.objects.filter(mailbox=gmail).exists())
        self.assertTrue(MailboxSyncRun.objects.filter(pk=run.pk).exists())

    # Function: Prevents the old Gmail CLI from consuming QQ tasks.
    # Inputs: No external parameters; a QQ queue positioned before Gmail.
    # Outputs: Claim results contain Gmail only; QQ remains queued.
    # Logic: Checks provider filtering through the original public claim function. The Gmail fixture explicitly supplies a 20-message range, and the old claim endpoint still cannot receive a QQ batch.
    # Constraints: The Google-credential response contains no QQ ciphertext.
    def test_legacy_gmail_claim_skips_qq(self):
        qq_run = request_run(self.owner, self.mailbox.pk, sync_options={"recent_days": 7, "max_messages": 20})
        gmail = Mailbox.objects.create(owner=self.owner, address="demo@gmail.com")
        GmailCredential.objects.create(mailbox=gmail, credentials={"token": "test"})
        request_run(self.owner, gmail.pk, sync_options={"max_messages": 20})
        claims = gmail_oauth.claim_mailbox_syncs(self.owner, 10)
        self.assertEqual([item["mailbox_id"] for item in claims], [str(gmail.pk)])
        qq_run.refresh_from_db()
        self.assertEqual(qq_run.status, "queued")

    # Function: Verifies the Worker selects the QQ path and incrementally deduplicates.
    # Inputs: No external parameters; simulates two folder UIDs and one evidence-bearing email.
    # Outputs: Real source/email/extraction persistence; the second synchronization makes no duplicate LLM call.
    # Logic: Replaces only the temporary-identity HTTP client and external boundaries; Worker, payload, and transactions run real code.
    # Constraints: Confirms no Gmail SDK call and does not trigger a real L3 model.
    def test_worker_persists_qq_and_deduplicates_incremental(self):
        value = qq_mail.message_id("INBOX", 10, 1)
        raw = self.raw(value)
        request_run(self.owner, self.mailbox.pk, sync_options={"recent_days": 7, "max_messages": 20})
        with patch("apps.crm.dispatch.django_backend_from_environment", return_value=self.backend), patch("apps.crm.worker.create_service_from_authorization") as gmail, patch.object(qq_mail, "connect", return_value=Mock()), patch.object(qq_mail, "disconnect"), patch.object(qq_mail, "folders", return_value=["INBOX", "Sent Messages"]), patch.object(qq_mail, "select_folder", return_value=10), patch.object(qq_mail, "list_uids", side_effect=lambda client, after, since=None: [1] if after == 0 else []), patch.object(qq_mail, "read_email", return_value=raw) as read, patch("apps.crm.durable_sync.bailian_extraction_provider", return_value=json.dumps(raw["facts"])) as model:
            # Sent Messages remains empty for this test; INBOX contains one email initially.
            with patch.object(qq_mail, "list_uids", side_effect=[[1], []]):
                self.assertTrue(run_sync(self.owner))
            self.assertEqual(Email.objects.get().payload["source"], "qq_real")
            self.assertTrue(StoredMessage.objects.get().raw)
            self.assertEqual(MailboxSyncRun.objects.get().status, "completed")
            self.assertEqual(model.call_count, 1)
            request_run(self.owner, self.mailbox.pk, sync_options={"recent_days": 7, "max_messages": 20})
            with patch.object(qq_mail, "list_uids", return_value=[]):
                self.assertTrue(run_sync(self.owner))
            self.assertEqual(model.call_count, 1)
            self.assertEqual(read.call_count, 1)
            self.assertEqual(Email.objects.count(), 1)
            gmail.assert_not_called()

    # Function: Verifies per-email failure is retained and retried only after explicit user action.
    # Inputs: No external parameters; one email reads successfully and one temporarily fails.
    # Outputs: `partial`; ordinary synchronization does not retry failure, while explicit retry completes.
    # Logic: Real processing tables record state and later resume with the same ID.
    # Constraints: The successful email does not call the model twice.
    def test_failure_requires_explicit_retry(self):
        good, bad = [qq_mail.message_id("INBOX", 10, uid) for uid in [1, 2]]
        raw = self.raw(good)
        run = self.start()
        with patch.object(qq_mail, "folders", return_value=["INBOX", "Sent Messages"]), patch.object(qq_mail, "select_folder", return_value=10), patch("apps.crm.durable_sync.bailian_extraction_provider", return_value=json.dumps(raw["facts"])) as model:
            with patch.object(qq_mail, "list_uids", side_effect=[[1, 2], []]), patch.object(qq_mail, "read_email", side_effect=[raw, qq_mail.QQMailError("测试读取失败")]):
                result = qq_sync.sync_persisted(run, Mock(), self.backend)
            finish_run(run.pk, run.lease_token, result)
            run.refresh_from_db()
            self.assertEqual(run.status, "partial")
            next_run = self.start()
            with patch.object(qq_mail, "list_uids", return_value=[]), patch.object(qq_mail, "read_email") as read:
                finish_run(next_run.pk, next_run.lease_token, qq_sync.sync_persisted(next_run, Mock(), self.backend))
                read.assert_not_called()
            retry_run(self.owner, run.pk)
            retry = claim_run(self.owner)
            self.assertEqual(retry.message_ids, [bad])
            with patch.object(qq_mail, "read_email", return_value=self.raw(bad)):
                finish_run(retry.pk, retry.lease_token, qq_sync.sync_persisted(retry, Mock(), self.backend))
            self.assertEqual(Email.objects.count(), 2)
            self.assertEqual(model.call_count, 2)

    # Function: Verifies generation changes do not corrupt the original checkpoint.
    # Inputs: No external parameters; after saving a generation, attempts to submit a different generation.
    # Outputs: Conflict, original cursor unchanged, and no new message registered.
    # Logic: Real transaction rollback verification.
    # Constraints: Does not automatically switch to a full rescan.
    def test_checkpoint_rolls_back_on_generation_change(self):
        run = self.start()
        qq_sync.checkpoint(run, "INBOX", 10, [1])
        with self.assertRaises(Conflict):
            qq_sync.checkpoint(run, "INBOX", 11, [2])
        self.assertEqual(QQSyncCheckpoint.objects.get().folders["INBOX"], {"uidvalidity": 10, "last_uid": 1})
        self.assertEqual(StoredMessage.objects.count(), 1)

    # Function: Verifies browsers must provide Session and CSRF.
    # Inputs: No external parameters; unauthenticated and authenticated-without-CSRF clients.
    # Outputs: Both requests are rejected and make no IMAP call.
    # Logic: Uses real `SessionAuthentication`, not `force_authenticate`.
    # Constraints: Does not use authentication shortcuts from API tests to prove CSRF validity.
    def test_session_csrf_is_required(self):
        browser = APIClient(enforce_csrf_checks=True)
        data = {"address": "demo@qq.com", "authorization_code": "abcdefghijklmnop", "sync_options": {"recent_days": 7, "max_messages": 20}}
        with patch.object(qq_mail, "connect") as connect:
            self.assertEqual(browser.post("/api/v1/mailboxes/qq-connect/", data, format="json").status_code, 403)
            browser.force_login(self.owner)
            self.assertEqual(browser.post("/api/v1/mailboxes/qq-connect/", data, format="json").status_code, 403)
            connect.assert_not_called()

    # Function: Verifies every QQ request explicitly selects a scope.
    # Inputs: No external parameters; empty scope, invalid values, unauthorized accounts, and one valid dual-limit request.
    # Outputs: Invalid input is 400, unauthorized is 404, valid is 202, and active batches reject replacement.
    # Logic: Runs real HTTP validation and database enqueueing, checks window persistence, and rejects empty requests just as Gmail does.
    # Constraints: Does not access real IMAP; omitting scope does not reuse the prior selection.
    def test_scope_is_required_and_validated_on_every_request(self):
        url = f"/api/v1/mailboxes/{self.mailbox.pk}/request-sync/"
        for data in [{}, {"sync_options": {}}, {"sync_options": {"recent_days": None, "max_messages": None}},
                     {"sync_options": {"recent_days": 0}}, {"sync_options": {"max_messages": -1}},
                     {"sync_options": {"max_messages": 1.5}}, {"sync_options": {"recent_days": 10**12}},
                     {"sync_options": {"max_messages": 1, "until": "2026-01-01"}}]:
            with self.subTest(data=data):
                self.assertEqual(self.browser.post(url, data, format="json").status_code, 400)
        self.assertFalse(MailboxSyncRun.objects.exists())
        self.browser.force_authenticate(self.other)
        self.assertEqual(self.browser.post(url, {"sync_options": {"max_messages": 1}}, format="json").status_code, 404)
        self.browser.force_authenticate(self.owner)
        response = self.browser.post(url, {"sync_options": {"recent_days": 7, "max_messages": 2}}, format="json")
        self.assertEqual(response.status_code, 202, response.data)
        run = MailboxSyncRun.objects.get()
        self.assertEqual(response.data["sync_options"], run.sync_options)
        self.assertEqual(run.sync_options["max_messages"], 2)
        self.assertEqual(self.browser.post(url, {"sync_options": {"max_messages": 5}}, format="json").status_code, 409)
        run.status = "completed"
        run.save(update_fields=["status"])
        self.assertEqual(self.browser.post(url, {}, format="json").status_code, 400)
        gmail = Mailbox.objects.create(owner=self.owner, address="demo@gmail.com")
        GmailCredential.objects.create(mailbox=gmail, credentials={"token": "test"})
        self.assertEqual(self.browser.post(f"/api/v1/mailboxes/{gmail.pk}/request-sync/", {}, format="json").status_code, 400)

    # Function: Verifies scope filtering precedes source-text reads and model calls, covering every target folder.
    # Inputs: No external parameters; simulates new and old mail across folders, boundary timestamps, future dates, and pending mail outside scope.
    # Outputs: Dual limits select at most two messages; expanding later days finds an older low-UID email, while out-of-scope source text is not read.
    # Logic: Runs real synchronization and L1/ingestion flows, mocking only IMAP and model; completed records do not consume later message capacity. Frozen time is injected by shared `sync_scope`; QQ preserves its time precision and deduplicates before limiting.
    # Constraints: Does not treat page size as batch size or use maximum UID to skip unselected messages.
    def test_limits_apply_before_bodies_across_folders(self):
        now = timezone.now()
        inbox_dates = {1: now - timedelta(days=8), 2: now - timedelta(days=2), 3: now - timedelta(days=7), 4: now + timedelta(seconds=1)}
        sent_dates = {1: now - timedelta(days=1)}
        old = qq_mail.message_id("INBOX", 10, 1)
        StoredMessage.objects.create(mailbox=self.mailbox, message_id=old)
        facts = json.dumps(self.raw(old)["facts"])
        with patch("apps.crm.sync_scope.timezone.now", return_value=now):
            request_run(self.owner, self.mailbox.pk, sync_options={"recent_days": 7, "max_messages": 2})
        run = claim_run(self.owner)
        with patch.object(qq_mail, "folders", return_value=["INBOX", "Sent Messages"]), patch.object(qq_mail, "select_folder", return_value=10), patch.object(qq_mail, "read_email", side_effect=lambda client, value: self.raw(value)) as read, patch("apps.crm.durable_sync.bailian_extraction_provider", return_value=facts) as model:
            with patch.object(qq_mail, "list_uids", side_effect=[[1, 2, 3, 4], [1]]), patch.object(qq_mail, "message_dates", side_effect=[inbox_dates, sent_dates]):
                finish_run(run.pk, run.lease_token, qq_sync.sync_persisted(run, Mock(), self.backend))
            self.assertEqual([call.args[1] for call in read.call_args_list], [qq_mail.message_id("Sent Messages", 10, 1), qq_mail.message_id("INBOX", 10, 2)])
            self.assertEqual(model.call_count, 2)
            self.assertFalse(StoredMessage.objects.get(message_id=old).raw)
            request_run(self.owner, self.mailbox.pk, sync_options={"recent_days": 30, "max_messages": 2})
            next_run = claim_run(self.owner)
            with patch.object(qq_mail, "list_uids", side_effect=[[1, 2, 3], [1]]), patch.object(qq_mail, "message_dates", side_effect=[{key: value for key, value in inbox_dates.items() if key != 4}, sent_dates]):
                finish_run(next_run.pk, next_run.lease_token, qq_sync.sync_persisted(next_run, Mock(), self.backend))
            self.assertEqual([call.args[1] for call in read.call_args_list[2:]], [qq_mail.message_id("INBOX", 10, 3), old])
            self.assertEqual(Email.objects.count(), 4)
            self.assertEqual(model.call_count, 4)

    # Function: Verifies retry does not expand the originally selected time window.
    # Inputs: No external parameters; a failed batch with no per-message task and an old empty-scope batch.
    # Outputs: New retry preserves the original snapshot; old unscoped scanning is rejected.
    # Logic: Uses real `retry_run` and persisted scope comparison to prevent retry time from advancing.
    # Constraints: Existing retry tests with explicit failed IDs continue to cover per-email recovery.
    def test_retry_keeps_original_window(self):
        run = self.start()
        finish_run(run.pk, run.lease_token, {"status": "failed"})
        retried = retry_run(self.owner, run.pk)
        self.assertEqual(retried.sync_options, run.sync_options)
        retried.status, retried.sync_options = "failed", {}
        retried.save(update_fields=["status", "sync_options"])
        with self.assertRaises(InvalidState):
            retry_run(self.owner, retried.pk)

    # Function: Verifies semantics of supplying only days or only count, plus empty results.
    # Inputs: No external parameters; fixed window boundary and mocked metadata for two historical emails.
    # Outputs: Days-only retains the boundary, count-only retains newest items, and a no-match batch performs zero body reads.
    # Logic: Real scope selection first compares exact dates, then filters by global count. Shared `sync_scope` supplies frozen time while QQ per-item limits and empty-window boundaries retain established semantics.
    # Constraints: Mocked IMAP metadata does not prove real external-service verification.
    def test_single_limits_and_empty_window(self):
        now = timezone.now()
        for options, dates, expected in [({"recent_days": 7}, {1: now - timedelta(days=7), 2: now - timedelta(days=7, seconds=1)}, [1]),
                                         ({"max_messages": 1}, {1: now - timedelta(days=70), 2: now - timedelta(days=60)}, [2]),
                                         ({"recent_days": 1}, {1: now - timedelta(days=2)}, [])]:
            with patch("apps.crm.sync_scope.timezone.now", return_value=now):
                request_run(self.owner, self.mailbox.pk, sync_options=options)
            run = claim_run(self.owner)
            with patch.object(qq_mail, "folders", return_value=["INBOX", "Sent Messages"]), patch.object(qq_mail, "select_folder", return_value=10), patch.object(qq_mail, "list_uids", side_effect=[list(dates), []]), patch.object(qq_mail, "message_dates", return_value=dates), patch.object(qq_mail, "read_email") as read:
                selected = qq_sync.select_messages(run, Mock())
                self.assertEqual([item[3] for item in selected], expected)
                read.assert_not_called()
            finish_run(run.pk, run.lease_token, {"status": "completed"})
