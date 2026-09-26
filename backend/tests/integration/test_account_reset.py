"""Responsibility: Verify internal account-data clearing, retained login, account isolation, and concurrency and failure boundaries.
Implementation: Use real PostgreSQL and Session HTTP with synthetic data including pending chat approvals and temporary attachments; mock only file-deletion failure.
Relationships: Covers `accounts.reset`, `reset_locks`, `reset_middleware`, and foreign keys across modules.
Directory:
- AccountResetTests: Account-reset integration acceptance tests.
- AccountResetTests.setUp: Create two accounts and a temporary attachment root.
- AccountResetTests.seed: Create synthetic business data with dependencies, retry relationships, and credentials.
- AccountResetTests.reset: Call the clearing endpoint in a real logged-in session.
- AccountResetTests.test_scope_identity_files_sessions_and_tokens: Verify clearing scope, identity, attachments, sessions, and credentials.
- AccountResetTests.test_idempotency_does_not_delete_new_data: Verify that replaying an earlier operation does not affect new data.
- AccountResetTests.test_file_failure_can_resume: Verify that a file failure preserves isolation and can be explicitly resumed.
- AccountResetTests.test_busy_account_is_unchanged: Verify that running work blocks clearing without changing data.
- AccountResetTests.test_stale_write_rejected: Verify that an old page cannot write data back.
- AccountResetTests.test_cross_owner_reference_rolls_back: Verify that shared business references are not implicitly cascade-deleted.
- AccountResetTests.test_authentication_csrf_and_schema: Verify anonymous access, CSRF, and operation keys.
- AccountResetTests.test_shared_membership_is_removed_without_deleting_other_team: Verify that unlinking a membership does not delete another user's team.
- AccountResetTests.test_stale_sales_queue_is_cancelled: Verify that a cleared action does not make an external call.
- AccountResetTests.test_shared_reminders_do_not_repopulate: Verify that notifications are no longer generated for the user after the assignee association is removed.
Variable index:
- URL: Reset route for the currently logged-in account.
"""
import hashlib
from pathlib import Path
import tempfile
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import CompanyProfile, SalesSetup, SetupDocument
from apps.accounts.reset import scoped_records
from apps.accounts.reset_locks import account_lock
from apps.accounts.reset_models import AccountReset
from apps.agent_tools.models import ToolCredential, ToolCall, ToolProposal
from apps.chat.models import AnswerRequest, ChatApproval, Citation, ToolRead, KnowledgeEntry
from apps.crm import models as crm
from apps.sales import models as sales
from apps.vectors.models import VectorDocument

URL = "/api/v1/accounts/me/reset/"


# Function: Accept internal-data reset while retaining login identity.
# Logic: Each case uses real commits and independent accounts; reset operates only on the isolated test database.
# Constraints: Do not call real email, model, or external services or operate on existing user attachments.
@override_settings(LOCAL_DEBUG_AUTO_LOGIN=False)
class AccountResetTests(TransactionTestCase):
    # Function: Establish a real logged-in session and two accounts.
    # Inputs: Implicit test-framework lifecycle.
    # Outputs: Instance state for `user`, `other`, `client`, `key`, and `root`.
    # Logic: A temporary directory limits attachment side effects; `force_login` preserves the normal SessionAuthentication path.
    # Constraints: Do not use `force_authenticate` to bypass middleware or CSRF identity parsing.
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="reset-owner", password="SyntheticPassword123", email="login@example.test")
        self.other = get_user_model().objects.create_user(username="reset-other", password="OtherPassword123")
        self.client = APIClient()
        self.client.force_login(self.user)
        self.key = str(uuid.uuid4())
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        config = override_settings(BASE_DIR=self.root)
        config.enable()
        self.addCleanup(config.disable)

    # Function: Create cross-module dependencies and actual attachments.
    # Inputs: `owner` is the test account owning the data.
    # Outputs: A company object; the database stores analysis, chat, sales, tool, and reference records.
    # Logic: Include PROTECT foreign keys, chat approvals and retry self-references, vectors, and binary documents to validate actual deletion dependencies.
    # Constraints: Credentials are synthetic digests and no external calls occur.
    def seed(self, owner):
        now = timezone.now()
        company = crm.Company.objects.create(owner=owner, group_key="customer.test")
        mailbox = crm.Mailbox.objects.create(owner=owner, address=f"u{owner.pk}@example.test", sync_state={"cached": True})
        crm.GmailCredential.objects.create(mailbox=mailbox, credentials={"synthetic": True})
        crm.AgentCredential.objects.create(owner=owner, digest=hashlib.sha256(str(owner.pk).encode()).hexdigest())
        contact = crm.Contact.objects.create(company=company, email="contact@example.test")
        email = crm.Email.objects.create(dedupe_key=str(mailbox.pk) + ":m1", mailbox=mailbox, company=company, contact=contact, payload={"body": "synthetic"}, sent_at=now, received_at=now, direction="inbound")
        extraction = crm.Extraction.objects.create(email=email, prompt_version="test", status="completed", facts={})
        snapshot = crm.AnalysisInput.objects.create(company=company, revision=0, input_version="test", payload={})
        analysis = crm.Analysis.objects.create(snapshot=snapshot, prompt_version="test", payload={}, provider="agent")
        crm.Score.objects.create(analysis=analysis, payload={}, score_version="test", value=10)
        crm.SnapshotSource.objects.create(snapshot=snapshot, email=email, extraction=extraction, review_revision=0)
        crm.SnapshotInvalidation.objects.create(snapshot=snapshot, reason="test")
        crm.ExtractionRepair.objects.create(email=email, source=extraction, review_revision=0)
        crm.Job.objects.create(company=company, trigger="test", revision=0)
        run = crm.MailboxSyncRun.objects.create(mailbox=mailbox)
        crm.EmailProcessingJob.objects.create(run=run, gmail_message_id="m1", dedupe_key=email.pk, company=company)
        crm.StoredMessage.objects.create(mailbox=mailbox, message_id="m1", raw={"body": "synthetic"})
        crm.SyncCheckpoint.objects.create(mailbox=mailbox, cursor="test")
        CompanyProfile.objects.create(owner=owner, company_name="Seller")
        SalesSetup.objects.create(owner=owner, personal={"name": "Synthetic"}, completed=True)
        SetupDocument.objects.create(owner=owner, name="test.txt", content_type="text/plain", content=b"synthetic")
        sales.SellerProfile.objects.create(owner=owner, profile={"test": True})
        sales.CompanySettings.objects.create(owner=owner, company=company, primary_contact=contact)
        product = sales.Product.objects.create(owner=owner, sku="sku", name="test", currency="USD", unit_price=1)
        quote = sales.Quote.objects.create(owner=owner, company=company, number="Q1", currency="USD")
        sales.QuoteLine.objects.create(owner=owner, quote=quote, product=product, quantity=1, unit_price=1)
        order = sales.SalesOrder.objects.create(owner=owner, company=company, number="O1", currency="USD", quote=quote)
        sales.OrderLine.objects.create(owner=owner, order=order, product=product, quantity=1, unit_price=1)
        follow = sales.FollowUp.objects.create(owner=owner, company=company, title="test", due_at=now)
        sales.Notification.objects.create(owner=owner, follow_up=follow, source_revision=0, title="test")
        conversation = sales.Conversation.objects.create(owner=owner)
        message = sales.Message.objects.create(owner=owner, conversation=conversation, content="test", client_key=uuid.uuid4())
        failed = AnswerRequest.objects.create(owner=owner, conversation=conversation, user_message=message, status="failed")
        request = AnswerRequest.objects.create(owner=owner, conversation=conversation, user_message=message, retry_of=failed)
        Citation.objects.create(request=request, position=0, source_id="test", source_type="test", title_or_label="test", content="test")
        ToolRead.objects.create(request=request, tool="test", arguments={}, result={}, evidence_items=[])
        ChatApproval.objects.create(request=request, tool="experiments.create", arguments={}, schema={}, continuation={}, expires_at=now + timezone.timedelta(hours=1))
        KnowledgeEntry.objects.create(owner=owner, source_key="test", version="1", title="test", content="test")
        sales.Draft.objects.create(owner=owner, conversation=conversation, kind="chat", content="test")
        credential = ToolCredential.objects.create(owner=owner, name="test", digest=hashlib.sha256(f"tool{owner.pk}".encode()).hexdigest(), allowed_tools=[], expires_at=now + timezone.timedelta(hours=1))
        ToolProposal.objects.create(owner=owner, credential=credential, tool="test", arguments={}, expires_at=credential.expires_at)
        ToolCall.objects.create(owner=owner, key=uuid.uuid4(), tool="test", input_hash="test", result={})
        VectorDocument.objects.create(owner=owner, namespace="test", source="test", model="test", dimensions=2, content="test", content_hash="test", embedding=[1, 0])
        key = f"{owner.pk}/test.txt"
        path = self.root / "private_uploads" / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic", encoding="utf-8")
        sales.Attachment.objects.create(owner=owner, company=company, name="test.txt", storage_key=key, content_type="text/plain", size=9, sha256="test")
        return company

    # Function: Call the account-reset API.
    # Inputs: `key` is an optional idempotency key and defaults to the current operation.
    # Outputs: An HTTP response.
    # Logic: Use a real logged-in cookie and pass no account or company ID.
    # Constraints: Cases whose test client does not enforce CSRF verify session identity only; a dedicated case covers CSRF.
    def reset(self, key=None):
        return self.client.post(URL, {}, format="json", HTTP_IDEMPOTENCY_KEY=key or self.key)

    # Function: Verify clearing of the user's business data while retaining other users' data and login identity.
    # Inputs: Two complete synthetic businesses and two current-account sessions.
    # Outputs: All user-owned queries are empty; other-account counts and password hash remain unchanged; the current login remains valid.
    # Logic: Check files, documents, vectors, credentials, OAuth cache in an old session, and HTTP cache response headers.
    # Constraints: Clear internal authorized records only and make no claim to revoke third-party platform accounts.
    def test_scope_identity_files_sessions_and_tokens(self):
        self.seed(self.user)
        self.seed(self.other)
        counts = {model._meta.label: query.count() for model, query in scoped_records(self.other)}
        second = APIClient()
        second.force_login(self.user)
        session = second.session
        session["oauth_state"] = "synthetic-sensitive-state"
        session.save()
        self.user.refresh_from_db()
        identity = get_user_model().objects.filter(pk=self.user.pk).values().get()
        response = self.reset()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["Clear-Site-Data"], '"cache"')
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(identity, get_user_model().objects.filter(pk=self.user.pk).values().get())
        for model, query in scoped_records(self.user):
            self.assertFalse(query.exists(), model._meta.label)
        self.assertEqual(counts, {model._meta.label: query.count() for model, query in scoped_records(self.other)})
        self.assertFalse((self.root / "private_uploads" / str(self.user.pk) / "test.txt").exists())
        self.assertTrue((self.root / "private_uploads" / str(self.other.pk) / "test.txt").exists())
        self.assertNotIn("oauth_state", second.session)
        self.assertIn("_auth_user_id", second.session)
        self.assertEqual(self.client.get("/api/v1/accounts/me/").status_code, 200)
        stale_agent = APIClient()
        stale_agent.credentials(HTTP_AUTHORIZATION="Agent " + str(self.user.pk))
        self.assertIn(stale_agent.post("/api/v1/agent/chat/requests/claim/", {}).status_code, [401, 403])

    # Function: Verify that network replay does not clear data newly created after reset.
    # Inputs: Two independent operation keys and a subsequently created company.
    # Outputs: The new company remains after replaying either old key and the data version remains unchanged.
    # Logic: Complete two clearing operations, then replay the first key to cover implementations that record only the latest key.
    # Constraints: Do not automatically retry HTTP requests.
    def test_idempotency_does_not_delete_new_data(self):
        self.assertEqual(self.reset().status_code, 200)
        self.assertEqual(self.reset(str(uuid.uuid4())).status_code, 200)
        company = crm.Company.objects.create(owner=self.user, group_key="new.test")
        response = self.reset()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["generation"], 2)
        self.assertTrue(crm.Company.objects.filter(pk=company.pk).exists())

    # Function: Verify that a cleared database with file failure is not falsely reported as successful.
    # Inputs: A synthetic attachment and one OSError.
    # Outputs: After 503, `cleaning` is true and normal business returns 409; after explicit retry, files are cleaned and the version does not increment again.
    # Logic: Inject failure only at the file boundary while using real database commits.
    # Constraints: Do not automatically convert failure to success or retry in the background.
    def test_file_failure_can_resume(self):
        self.seed(self.user)
        with patch("apps.accounts.reset.clean_files", side_effect=OSError("synthetic")):
            self.assertEqual(self.reset().status_code, 503)
        state = AccountReset.objects.get(owner=self.user)
        self.assertTrue(state.cleaning)
        self.assertTrue(state.pending_files)
        self.assertEqual(self.client.get("/api/v1/mailboxes/").status_code, 409)
        identity = self.client.get("/api/v1/accounts/me/")
        self.assertEqual(identity.status_code, 200)
        self.assertEqual(identity["X-Account-Reset-Status"], "cleaning")
        self.assertEqual(self.reset().status_code, 200)
        state.refresh_from_db()
        self.assertFalse(state.cleaning)
        self.assertEqual(state.generation, 1)
        self.assertEqual(state.pending_files, [])

    # Function: Verify real cross-connection exclusion between a shared-work lock and the clearing exclusive lock.
    # Inputs: Persistent business records for the user and another account.
    # Outputs: The user's work period returns 409 with unchanged data; another user's lock does not affect the user's clearing.
    # Logic: Use a real PostgreSQL advisory lock and do not mock lock behavior.
    # Constraints: Do not block waiting tasks or stop another account's work.
    def test_busy_account_is_unchanged(self):
        company = crm.Company.objects.create(owner=self.user, group_key="test")
        with account_lock(self.user.pk):
            self.assertEqual(self.reset().status_code, 409)
        self.assertTrue(crm.Company.objects.filter(pk=company.pk).exists())
        with account_lock(self.other.pk):
            self.assertEqual(self.reset().status_code, 200)

    # Function: Verify that write requests from an old page are isolated by version.
    # Inputs: An old request with data version zero.
    # Outputs: Returns 409 after clearing and provides the latest version in the response.
    # Logic: The complete HTTP middleware checks the version before entering the business view.
    # Constraints: Do not achieve rejection by adding business-content validation.
    def test_stale_write_rejected(self):
        self.assertEqual(self.reset().status_code, 200)
        response = self.client.patch("/api/v1/accounts/onboarding/", {"completed": True}, format="json", HTTP_X_ACCOUNT_DATA_VERSION="0")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "account_data_reset")
        self.assertEqual(response["X-Account-Data-Version"], "1")
        self.assertFalse(SalesSetup.objects.filter(owner=self.user).exists())

    # Function: Verify that business records owned by another user are neither deleted nor cascade-modified.
    # Inputs: Another user's ticket references the user's company.
    # Outputs: The whole transaction rolls back and both company and ticket remain.
    # Logic: Real database foreign keys find a shared reference absent from the deletion set.
    # Constraints: There is no fallback path that disables foreign keys or deletes another user's business data.
    def test_cross_owner_reference_rolls_back(self):
        company = self.seed(self.user)
        ticket = sales.Ticket.objects.create(owner=self.other, company=company, title="shared")
        self.assertEqual(self.reset().status_code, 409)
        self.assertTrue(crm.Company.objects.filter(pk=company.pk).exists())
        self.assertTrue(sales.Ticket.objects.filter(pk=ticket.pk).exists())
        self.assertTrue(sales.Attachment.objects.filter(owner=self.user).exists())
        self.assertEqual(AccountReset.objects.get(owner=self.user).generation, 0)

    # Function: Verify that only the account logged in to the current browser can initiate reset.
    # Inputs: An anonymous request, a session without CSRF, and an invalid operation key.
    # Outputs: Anonymous and missing-CSRF requests return 403; an invalid key returns 400.
    # Logic: Use real SessionAuthentication and Django CSRF protection.
    # Constraints: Do not request an extra password or require business fields.
    def test_authentication_csrf_and_schema(self):
        self.assertEqual(APIClient().post(URL, {}).status_code, 403)
        csrf_client = APIClient(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(URL, {}, HTTP_IDEMPOTENCY_KEY=self.key).status_code, 403)
        self.assertEqual(self.reset("invalid").status_code, 400)

    # Function: Verify that account clearing only removes membership in another user's team.
    # Inputs: A team owned by another user and the user's membership.
    # Outputs: The membership is deleted and the team remains.
    # Logic: Membership is an association record and does not cascade deletion to the team.
    # Constraints: Do not change other members' permissions.
    def test_shared_membership_is_removed_without_deleting_other_team(self):
        team = sales.Team.objects.create(owner=self.other, name="shared")
        membership = sales.Membership.objects.create(owner=self.other, team=team, user=self.user, role="viewer")
        self.assertEqual(self.reset().status_code, 200)
        self.assertFalse(sales.Membership.objects.filter(pk=membership.pk).exists())
        self.assertTrue(sales.Team.objects.filter(pk=team.pk).exists())

    # Function: Verify that a stale queue key after clearing neither executes an external action nor terminates the work loop.
    # Inputs: A synthetic action that was approved and then cleared.
    # Outputs: Returns `cancelled` and does not call the external provider.
    # Logic: Call the background entry point after real record deletion, covering the window where a queue is fetched before clearing.
    # Constraints: Do not execute a real network request.
    def test_stale_sales_queue_is_cancelled(self):
        from apps.sales.actions import run_action
        company = crm.Company.objects.create(owner=self.user, group_key="test")
        action = sales.ToolAction.objects.create(owner=self.user, company=company, tool="gmail.send", parameters={}, status="approved", idempotency_key=uuid.uuid4())
        self.assertEqual(self.reset().status_code, 200)
        with patch("apps.sales.actions.execute_provider") as provider:
            self.assertEqual(run_action(action.pk), "cancelled")
        provider.assert_not_called()

    # Function: Verify that a follow-up owned by another user does not write the user's notifications again after clearing.
    # Inputs: Another user's follow-up assigns the user and has an existing due notification.
    # Outputs: The follow-up remains, its assignee is removed, and the user's notifications remain empty.
    # Logic: Run the real reminder scan again after clearing to check account isolation and background reads and writes.
    # Constraints: Remove the association only and do not delete another user's follow-up content.
    def test_shared_reminders_do_not_repopulate(self):
        from apps.sales.services import notify_due
        company = crm.Company.objects.create(owner=self.other, group_key="test")
        follow = sales.FollowUp.objects.create(owner=self.other, company=company, assigned_to=self.user, title="shared", due_at=timezone.now())
        sales.Notification.objects.create(owner=self.user, follow_up=follow, source_revision=0, title="shared")
        self.assertEqual(self.reset().status_code, 200)
        follow.refresh_from_db()
        self.assertIsNone(follow.assigned_to)
        notify_due()
        self.assertFalse(sales.Notification.objects.filter(owner=self.user).exists())
