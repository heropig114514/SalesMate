"""Responsibility: Verify that temporary QQ disablement covers entry points, queues, and external execution while retaining historical data and Gmail.
Implementation: Gmail test batches explicitly select at most 20 messages rather than the runtime default; use isolated PostgreSQL, real services and HTTP views, and mocks to prove network calls do not occur.
Relationships: Covers `common.mail_features`, CRM scheduling, sales actions, and runtime capability APIs.
Directory:
- QQDisabledTests: QQ-default-disabled integration verification.
- QQDisabledTests.setUp: Prepare personal QQ/Gmail mailboxes and a client.
- QQDisabledTests.test_connections_fail_before_network: Reject connections before network access.
- QQDisabledTests.test_queue_pauses_qq_and_keeps_gmail: Reject new requests, pause existing queues, and keep Gmail claimable.
- QQDisabledTests.test_history_and_credentials_remain: Retain historical source data and credentials.
- QQDisabledTests.test_sales_actions_cannot_send_or_verify: Approval, preparation, execution, and verification cannot access QQ.
- QQDisabledTests.test_capabilities_and_restoration: Keep capability discovery consistent with restored configuration.
Variable index:
- None
"""
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from common.mail_features import QQMailDisabled
from apps.agent_tools.registry import build_registry
from apps.crm import ingestion, rules
from apps.crm.dispatch import next_owner
from apps.crm.models import Company, Email, GmailCredential, Mailbox, MailboxSyncRun, QQCredential
from apps.crm.processing import claim_run, request_run
from apps.crm.qq_connection import authorization_code
from apps.sales import actions
from apps.sales.models import ToolAction


# Function: Verify every external boundary of disabled configuration.
# Logic: Keep QQ disabled and use an independent database transaction per case.
# Constraints: Do not connect to real mailboxes, delete business data, or verify third-party authorization validity.
@override_settings(QQ_MAIL_ENABLED=False, ANALYSIS_PROVIDER="agent")
class QQDisabledTests(TestCase):
    # Function: Establish a personal mailbox and logged-in client.
    # Inputs: No external parameters; uses an isolated test database.
    # Outputs: Instance state for `user`, `qq`, `credential`, and `client`.
    # Logic: QQ ciphertext is intentionally undecryptable to ensure disablement is checked before decryption.
    # Constraints: Do not use real mailbox credentials.
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="qq-disabled")
        self.qq = Mailbox.objects.create(owner=self.user, address="fixture@qq.com")
        self.credential = QQCredential.objects.create(mailbox=self.qq, encrypted_code="not-a-secret")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    # Function: Verify that connection entry points do not access the network.
    # Inputs: Synthetic QQ address, authorization code, and synchronization range.
    # Outputs: Both entry points return 503 and authorization-record count remains unchanged.
    # Logic: Mock IMAP/SMTP connections and assert they are not called.
    # Constraints: Cover authenticated requests only; existing tests cover anonymous permissions.
    def test_connections_fail_before_network(self):
        with patch("apps.crm.qq_connection.qq_mail.connect") as imap, patch("apps.sales.qq_smtp.smtplib.SMTP_SSL") as smtp:
            data = {"address": "fixture@qq.com", "authorization_code": "abcdefghijklmnop"}
            response = self.client.post("/api/v1/mailboxes/qq-connect/", {**data, "sync_options": {"max_messages": 1}}, format="json")
            self.assertEqual(response.status_code, 503)
            response = self.client.post("/api/v1/sales/connections/qq/", data, format="json")
            self.assertEqual(response.status_code, 503)
            imap.assert_not_called()
            smtp.assert_not_called()
        with self.assertRaises(QQMailDisabled):
            authorization_code(self.credential)
        self.assertEqual(QQCredential.objects.count(), 1)

    # Function: Verify that QQ queues pause without blocking Gmail.
    # Inputs: An earlier queued QQ record and a later Gmail record for the same account.
    # Outputs: The original QQ queue remains unchanged; the scheduler selects the user only when Gmail exists.
    # Logic: First verify QQ-only has no pending work, then add Gmail and claim it; the Gmail batch explicitly supplies a 20-message range and the QQ switch does not change claimability.
    # Constraints: Do not execute any mailbox network request.
    def test_queue_pauses_qq_and_keeps_gmail(self):
        pending = MailboxSyncRun.objects.create(mailbox=self.qq)
        with self.assertRaises(QQMailDisabled):
            request_run(self.user, self.qq.pk, sync_options={"max_messages": 1})
        self.assertIsNone(claim_run(self.user))
        self.assertIsNone(next_owner("sync"))
        gmail = Mailbox.objects.create(owner=self.user, address="fixture@gmail.com")
        GmailCredential.objects.create(mailbox=gmail, credentials={})
        gmail_run = request_run(self.user, gmail.pk, sync_options={"max_messages": 20})
        self.assertEqual(next_owner("sync").pk, self.user.pk)
        self.assertEqual(claim_run(self.user).pk, gmail_run.pk)
        pending.refresh_from_db()
        self.assertEqual(pending.status, "queued")

    # Function: Verify that history remains queryable.
    # Inputs: A synthetic QQ-source message that has been ingested.
    # Outputs: Saved-message API returns 200 and includes the message; mailbox and credential remain.
    # Logic: Prepare history through the production ingestion service without running synchronization.
    # Constraints: The original text is synthetic test data and does not represent real QQ collection.
    def test_history_and_credentials_remain(self):
        payload = rules.extract_email(self.qq, "buyer@example.com", "采购询价", "需求：采购设备")
        payload["source"] = "qq_real"
        ingestion.submit_emails(self.user, [payload])
        response = self.client.get(f"/api/v1/mailboxes/{self.qq.pk}/email-reviews/?status=saved")
        self.assertEqual(response.status_code, 200)
        self.assertIn(payload["dedupe_key"], str(response.data))
        self.assertEqual(Email.objects.count(), 1)
        self.assertTrue(QQCredential.objects.filter(pk=self.credential.pk).exists())

    # Function: Verify that existing send actions cannot bypass the switch.
    # Inputs: Synthetic QQ pending-confirmation, approved, and uncertain actions.
    # Outputs: Preparation, approval, and verification reject; approved execution is marked failed with no external call.
    # Logic: Construct a frozen action and check `execute_provider` is not called, then verify the user can still cancel an old proposal.
    # Constraints: Do not change Gmail action approval or failure semantics.
    def test_sales_actions_cannot_send_or_verify(self):
        company = Company.objects.create(owner=self.user, group_key="manual:qq-disabled")
        action = ToolAction.objects.create(owner=self.user, company=company, tool="qq.send", parameters={}, idempotency_key=uuid.uuid4())
        with self.assertRaises(QQMailDisabled):
            actions.validate_parameters(self.user, company, "qq.send", {})
        with self.assertRaises(QQMailDisabled):
            actions.decide_action(action, self.user, action.revision, "approved")
        actions.decide_action(action, self.user, action.revision, "cancelled")
        action.status = "approved"
        action.save(update_fields=["status"])
        with patch("apps.sales.actions.execute_provider") as provider:
            self.assertEqual(actions.run_action(action.pk), "failed")
            provider.assert_not_called()
        action.status = "uncertain"
        action.save(update_fields=["status"])
        with self.assertRaises(QQMailDisabled):
            actions.verify_action(action, self.user, action.revision)

    # Function: Verify client capabilities and restoration behavior.
    # Inputs: Disabled configuration and explicitly enabled local settings.
    # Outputs: Runtime and tool registry agree; after enablement, the original QQ queue is claimable.
    # Logic: Do not delete tasks; restoration changes availability only.
    # Constraints: Does not prove real SMTP/IMAP reachability.
    def test_capabilities_and_restoration(self):
        self.assertFalse(self.client.get("/api/v1/demo/runtime/").data["qq_enabled"])
        self.assertNotIn("actions.prepare_qq", build_registry())
        pending = MailboxSyncRun.objects.create(mailbox=self.qq)
        with override_settings(QQ_MAIL_ENABLED=True):
            self.assertIn("actions.prepare_qq", build_registry())
            self.assertEqual(claim_run(self.user).pk, pending.pk)
