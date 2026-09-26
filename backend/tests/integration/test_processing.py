"""Responsibility: Verify business invariants for durable processing, manual review, and company scheduling.
Implementation: Gmail test batches explicitly select at most 20 messages, not a runtime default. Use isolated PostgreSQL and synthetic mail with mocked Gmail reads, validating real transactions and HTTP permissions.
Relationships: processing, classification, jobs, and new progress APIs; no real mailbox or model calls.
Directory:
- ProcessingTests: Verify batch and classification services.
- ProcessingTests.setUp: Create independent employees and mailboxes.
- ProcessingTests.email: Save classifiable synthetic mail.
- ProcessingTests.test_nonbusiness_hidden_everywhere: Verify consistency across hiding, statistics, context, and analysis entry points.
- ProcessingTests.test_review_is_owned_versioned_and_preserves_source: Verify manual precedence, original text, and permissions.
- ProcessingTests.test_queue_is_durable_and_rejects_duplicate_clicks: Verify durable queuing and 202 responses.
- ProcessingTests.test_progress_partial_retry_and_expired_lease: Verify per-message counts, explicit retries, and stale-lease rejection.
- ProcessingTests.test_same_company_successor_waits_while_other_company_runs: Verify company mutual exclusion and cross-company claiming.
- ProcessingTests.test_tracked_gmail_read_failure_keeps_other_messages: Verify per-message read-failure isolation.
- ProcessingTests.test_backfill_preserves_manual_decisions: Verify historical backfill preserves manual decisions.
- ProcessingTests.test_progress_covers_companies_outside_visible_page: Verify overall profile progress is independent of pagination.
- ProcessingTests.test_expired_history_preserves_pending_ids: Verify expired cursors preserve old pending lists.
- ProcessingTests.test_review_hides_analysis_that_used_removed_email: Verify manual hiding stops display of contaminated profiles.
- ProcessingTests.test_saved_mailbox_view_includes_all_classifications: Verify mailbox-scoped original text, chronological ordering, and permissions.
- ProcessingTests.test_company_row_exposes_actual_email_sources: Verify source distinction between real mail and demonstration samples.
- WorkerPipelineTests: Verify cross-thread observer events and real business persistence.
- WorkerPipelineTests.test_worker_persists_stream_and_isolates_read_error: Mock Gmail/models while integrating temporary employee identities, Worker callbacks, and backend persistence.
Variable index:
- None
"""
from datetime import timedelta
from io import StringIO
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from agent.tools.gmail import read_messages
from apps.crm import ingestion, jobs, rules, selectors
from apps.crm.access import Conflict
from apps.crm.classification import review_email
from apps.crm.models import Email, EmailProcessingJob, GmailCredential, Mailbox, MailboxSyncRun
from apps.crm.processing import claim_run, expire_runs, finish_run, record_event, request_run, retry_run, run_data
from apps.crm.worker import run_sync


# Function: Validate durable-processing and review contracts.
# Logic: Use an isolated test database and HTTP authenticated as synthetic employees.
# Constraints: External authorizations are placeholders, never evidence of valid real authorization.
@override_settings(ANALYSIS_PROVIDER="agent")
class ProcessingTests(TestCase):
    # Function: Create two employees and authorized mailboxes.
    # Inputs: No external arguments; invoked by the test framework.
    # Outputs: Initialize owner, other, mailbox, and browser.
    # Logic: Save users separately to test cross-employee rejection.
    # Constraints: Do not reuse local business accounts or mailboxes.
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="processing-owner")
        self.other = get_user_model().objects.create_user(username="processing-other")
        self.mailbox = Mailbox.objects.create(owner=self.owner, address="sales@processing.example")
        GmailCredential.objects.create(mailbox=self.mailbox, credentials={"mock": True})
        self.browser = APIClient()
        self.browser.force_authenticate(self.owner)

    # Function: Save synthetic mail with explicit classification.
    # Inputs: `key` is the message ID, `kind` is business/non_business/needs_review, and `sender` is a synthetic contact.
    # Outputs: Database Email.
    # Logic: Use existing rule fixtures to construct valid facts, then set classification signals for the test premise.
    # Constraints: No LLM call; signals verify software mappings, not model classification quality.
    def email(self, key, kind="business", sender="buyer@customer.example"):
        payload = rules.extract_email(self.mailbox, sender, "询价", "需求：设备\n数量：5 台", key)
        if kind == "non_business":
            payload.update(extract_status="skipped_non_business", facts=None, non_business_hint=True, non_business_reason="automated_sender")
        elif kind == "needs_review":
            payload["facts"].update(intent_hint=None, intent_evidences=[], has_substantive_update=False)
        ingestion.submit_emails(self.owner, [payload])
        return Email.objects.get(pk=payload["dedupe_key"])

    # Function: Verify non-business mail is consistently hidden from queries and analysis.
    # Inputs: No external arguments; create a normal company and a non-business-only company.
    # Outputs: Assertions for lists, statistics, member keys, and analysis entry points.
    # Logic: Mixed companies project business mail only; non-business-only companies cannot be analyzed.
    # Constraints: Keep original mail; do not satisfy assertions by deleting data.
    def test_nonbusiness_hidden_everywhere(self):
        normal = self.email("business")
        hidden = self.email("hidden", "non_business")
        only_hidden = self.email("only-hidden", "non_business", "news@newsletter.example")
        response = self.browser.get("/api/v1/companies/")
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["stats"]["new_emails_today"], 1)
        grouping, context = selectors.context_pair(normal.company)
        self.assertEqual(grouping["member_dedupe_keys"], [normal.pk])
        self.assertEqual(len(context["emails"]), 1)
        self.assertTrue(Email.objects.filter(pk=hidden.pk).exists())
        self.assertEqual(self.browser.post(f"/api/v1/companies/{only_hidden.company_id}/analyze/").status_code, 409)

    # Function: Verify mailbox-original endpoints neither omit mail due to classification nor mix other mailboxes.
    # Inputs: No external arguments; three classifications in one mailbox, another mailbox's mail, and another employee's session.
    # Outputs: saved returns all three classifications, original all semantics remain unchanged, mailboxes stay isolated, and unauthorized access returns 404.
    # Logic: Real HTTP/database sorting checks original text, dates, sources, and unchanged classifications.
    # Constraints: Use synthetic material without IMAP/models or automatic mail confirmation.
    def test_saved_mailbox_view_includes_all_classifications(self):
        items = [self.email("saved-business"), self.email("saved-hidden", "non_business"), self.email("saved-review", "needs_review")]
        original = {item.pk: item.business_classification for item in items}
        latest = timezone.now()
        for index, item in enumerate(items):
            item.received_at = latest - timedelta(days=index)
            item.save(update_fields=["received_at"])
        another = Mailbox.objects.create(owner=self.owner, address="another@processing.example")
        ingestion.submit_emails(self.owner, [rules.extract_email(another, "other@elsewhere.example", "其他邮箱", "需求：设备", "other-mailbox")])
        path = f"/api/v1/mailboxes/{self.mailbox.pk}/email-reviews/"
        response = self.browser.get(path + "?status=saved")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 3)
        self.assertEqual([row["email_id"] for row in response.data["results"]], [item.pk for item in items])
        for item, row in zip(items, response.data["results"]):
            self.assertEqual(row["source"], item.payload["source"])
            self.assertEqual(row["body_text"], item.payload["body_text"])
            self.assertEqual(row["received_at"], item.received_at.isoformat())
        self.assertEqual(self.browser.get(path + "?status=all").data["count"], 2)
        self.assertEqual(dict(Email.objects.filter(pk__in=original).values_list("pk", "business_classification")), original)
        self.browser.force_authenticate(self.other)
        self.assertEqual(self.browser.get(path + "?status=saved").status_code, 404)

    # Function: Verify customer-summary source labels derive from actual business mail.
    # Inputs: No external arguments; two synthetic fixtures with different sources in one company.
    # Outputs: The source set contains both entries without inferring from model provider or customer name.
    # Logic: Save standard fixtures, explicitly simulate mixed-source history, and read real company_row.
    # Constraints: Projection-only verification; the test's qq_real label does not verify real QQ authorization.
    def test_company_row_exposes_actual_email_sources(self):
        sample = self.email("sample-source")
        qq = self.email("qq-source")
        qq.payload = {**qq.payload, "source": "qq_real"}
        qq.save(update_fields=["payload"])
        row = selectors.company_row(sample.company)
        self.assertEqual(row["email_sources"], sorted({sample.payload["source"], "qq_real"}))

    # Function: Verify manual-review permissions, revisions, and source preservation.
    # Inputs: No external arguments; create synthetic mail pending review.
    # Outputs: Unauthorized access returns 404, stale versions 409, manual decisions persist, and original text is unchanged.
    # Logic: Confirm first, then resubmit identical original text; machine classification cannot overwrite manual decisions.
    # Constraints: Do not change extracted facts or depend on real external services.
    def test_review_is_owned_versioned_and_preserves_source(self):
        email = self.email("review", "needs_review")
        original = dict(email.payload)
        self.assertEqual(self.browser.get("/api/v1/email-reviews/").data["count"], 1)
        other = APIClient()
        other.force_authenticate(self.other)
        path = f"/api/v1/email-reviews/{email.pk}/"
        self.assertEqual(other.patch(path, {"review_status": "confirmed_business"}, format="json", HTTP_IF_MATCH=str(email.review_revision)).status_code, 404)
        response = self.browser.patch(path, {"review_status": "confirmed_business"}, format="json", HTTP_IF_MATCH=str(email.review_revision))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.browser.patch(path, {"review_status": "confirmed_non_business"}, format="json", HTTP_IF_MATCH=str(email.review_revision)).status_code, 409)
        email.refresh_from_db()
        ingestion.submit_emails(self.owner, [selectors.email_data(email)])
        email.refresh_from_db()
        self.assertEqual(email.classification_source, "human")
        self.assertEqual(email.business_classification, "business")
        self.assertEqual(email.payload, original)
        self.assertTrue(email.company.jobs.filter(status="pending").exists())

    # Function: Verify repeated sync clicks reject new batches.
    # Inputs: No external arguments; two sync requests for the same mailbox.
    # Outputs: First HTTP 202, duplicate HTTP 409, and one queued record.
    # Logic: Requery database state instead of checking process-local variables.
    # Constraints: Do not start Workers or treat queued work as completed synchronization.
    def test_queue_is_durable_and_rejects_duplicate_clicks(self):
        path = f"/api/v1/mailboxes/{self.mailbox.pk}/request-sync/"
        first, second = [self.browser.post(path, {"sync_options": {"max_messages": 20}}, format="json") for _ in range(2)]
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(MailboxSyncRun.objects.get().status, "queued")
        other = APIClient()
        other.force_authenticate(self.other)
        self.assertEqual(other.get(f"/api/v1/mailbox-sync-runs/{first.data['run_id']}/").status_code, 404)

    # Function: Verify per-message counts, partial completion, explicit retry, and lease invalidation.
    # Inputs: No external arguments; one completed event and one read-failure event.
    # Outputs: partial state, correct failed scope, and rejection of old executors.
    # Logic: Repeated discovery does not duplicate counts; retries contain failed IDs only and tasks survive expiry. The initial batch explicitly selects 20 messages; successors retry only simulated failed IDs.
    # Constraints: Simulate stage events without claiming real Gmail reads.
    def test_progress_partial_retry_and_expired_lease(self):
        request_run(self.owner, self.mailbox.pk, sync_options={"max_messages": 20})
        run = claim_run(self.owner)
        for _index in range(2):
            record_event(run.pk, run.lease_token, "discovered", {"message_ids": ["good", "bad"]})
        record_event(run.pk, run.lease_token, "completed", {"gmail_message_id": "good"})
        record_event(run.pk, run.lease_token, "failed", {"gmail_message_id": "bad", "stage": "fetching", "code": "gmail_read_failed"})
        result = finish_run(run.pk, run.lease_token, {"status": "completed"})
        self.assertEqual((result["status"], result["total_count"], result["completed_count"], result["failed_count"]), ("partial", 2, 1, 1))
        with self.assertRaises(Conflict):
            record_event(run.pk, run.lease_token, "fetching", {"gmail_message_id": "bad"})
        retry = retry_run(self.owner, run.pk)
        self.assertEqual(retry.message_ids, ["bad"])
        active = claim_run(self.owner)
        MailboxSyncRun.objects.filter(pk=active.pk).update(lease_until=timezone.now() - timedelta(seconds=1))
        self.assertEqual(expire_runs(self.owner), 1)
        self.assertEqual(MailboxSyncRun.objects.get(pk=active.pk).status, "failed")

    # Function: Verify other companies can be claimed while same-company successors wait.
    # Inputs: No external arguments; mail for two companies and an old running revision.
    # Outputs: The second claim includes only the other company's task.
    # Logic: New mail during execution creates a same-company successor; atomic backend claiming excludes that company.
    # Constraints: Use real database state; thread-pool size does not substitute for mutual-exclusion checks.
    def test_same_company_successor_waits_while_other_company_runs(self):
        first = self.email("first")
        claimed = jobs.claim(self.owner, 1, 120)
        self.assertEqual(claimed[0]["company_id"], str(first.company_id))
        self.email("successor")
        other = self.email("other", sender="buyer@other-customer.example")
        next_jobs = jobs.claim(self.owner, 3, 120)
        self.assertEqual([item["company_id"] for item in next_jobs], [str(other.company_id)])
        self.assertEqual(first.company.jobs.filter(status="pending").count(), 1)

    # Function: Verify isolation of individual Gmail read failures in observer mode.
    # Inputs: No external arguments; the SDK read mock raises on the second message.
    # Outputs: First and third messages succeed; the second has a failure event.
    # Logic: Use the new callback mode and verify the real loop continues.
    # Constraints: Mock the SDK; real authorization/networking are not verified.
    def test_tracked_gmail_read_failure_keeps_other_messages(self):
        events = []
        with patch("agent.tools.gmail.read_email", side_effect=[{"id": "one"}, RuntimeError("mock"), {"id": "three"}]) as reader:
            result = read_messages(object(), ["one", "bad", "three"], progress=lambda stage, data: events.append((stage, data)))
        self.assertEqual(reader.call_count, 3)
        self.assertEqual(result, [{"id": "one"}, {"id": "three"}])
        self.assertEqual([data["gmail_message_id"] for stage, data in events if stage == "failed"], ["bad"])

    # Function: Verify historical backfill preserves manual decisions.
    # Inputs: No external arguments; manually confirm a rule-hidden message.
    # Outputs: After backfill, manual business classification and original text remain.
    # Logic: Explicitly apply backfill and check manual precedence.
    # Constraints: The command accesses only the isolated test database.
    def test_backfill_preserves_manual_decisions(self):
        email = self.email("manual", "non_business")
        review_email(self.owner, email.pk, "confirmed_business", email.review_revision)
        call_command("classify_emails", apply=True, stdout=StringIO())
        email.refresh_from_db()
        self.assertEqual((email.business_classification, email.classification_source), ("business", "human"))

    # Function: Verify batch profile statistics include every related company.
    # Inputs: No external arguments; create 21 companies exceeding one page and completed mail tasks.
    # Outputs: Overall progress reports 21 pending profiles.
    # Logic: Progress queries batch relationships without frontend company-pagination arguments. Queuing explicitly uses a 20-message test scope; 21-company progress comes from manual task fixtures verifying pagination-independent statistics.
    # Constraints: No model calls; completed mail-task states are simulated test premises.
    def test_progress_covers_companies_outside_visible_page(self):
        run = request_run(self.owner, self.mailbox.pk, sync_options={"max_messages": 20})
        for index in range(21):
            email = self.email(f"page-{index}", sender=f"buyer@company-{index}.example")
            EmailProcessingJob.objects.create(run=run, gmail_message_id=f"page-{index}", dedupe_key=email.pk, company=email.company, status="completed", stage="completed")
        self.assertEqual(run_data(run)["analysis_pending_count"], 21)


    # Function: Verify cursor expiry preserves persisted failed and pending IDs.
    # Inputs: No external arguments; History 404, two old IDs, and one newly scanned ID.
    # Outputs: Old IDs are read first; new IDs beyond the per-round limit remain pending.
    # Logic: Mock only Gmail query results and execute actual recovery-selection logic.
    # Constraints: Preserve per-round limits; mocked historyId is not a real mailbox cursor.
    def test_expired_history_preserves_pending_ids(self):
        from agent.tools.gmail import GmailHistoryExpiredError
        from agent.workflows.gmail_sync import _read_email_batch
        state = {"cursor": "expired", "scope": {"pending_message_ids": ["old-pending"], "failed_message_ids": ["old-failed"]}}
        with (
            patch("agent.workflows.gmail_sync.list_history_message_ids", side_effect=GmailHistoryExpiredError("mock")),
            patch("agent.workflows.gmail_sync.get_profile_history_id", return_value="200"),
            patch("agent.workflows.gmail_sync.list_sync_message_ids", return_value=["recent"]),
            patch("agent.workflows.gmail_sync.read_messages", return_value=[]) as reader,
        ):
            _emails, cursor, pending, mode = _read_email_batch(object(), 2, state)
        self.assertEqual(reader.call_args.args[1], ["old-pending", "old-failed"])
        self.assertEqual((cursor, pending, mode), ("200", ["recent"], "recovered"))

    # Function: Verify non-business decisions hide old profiles dependent on that mail.
    # Inputs: No external arguments; first generate a deterministic test profile from one business message.
    # Outputs: After manual hiding, latest_result is empty while historical analysis records remain.
    # Logic: Preserve history and prevent display through member-key visibility checks.
    # Constraints: Rules are offline fixtures only; no model calls or non-business recomputation.
    def test_review_hides_analysis_that_used_removed_email(self):
        email = self.email("analysis-source")
        rules.run_company(self.owner, email.company_id)
        self.assertIsNotNone(selectors.latest_result(email.company)[0])
        review_email(self.owner, email.pk, "confirmed_non_business", email.review_revision)
        email.company.refresh_from_db()
        self.assertEqual(selectors.latest_result(email.company), (None, None))
        self.assertTrue(email.company.inputs.filter(analyses__isnull=False).exists())


# Function: Verify real persistence relationships between Worker and multithreaded L1.
# Logic: Use a committable isolated transactional database so extraction threads see batches; mock network/models.
# Constraints: No real Gmail or HTTP; verify integration between protocol callbacks and database implementation.
class WorkerPipelineTests(TransactionTestCase):
    # Function: Integrate batch claiming, per-message reads, concurrent extraction, and durable progress.
    # Inputs: No external arguments; two valid synthetic emails and one read-failure message.
    # Outputs: Two business messages save, the batch becomes partial, failed IDs persist, and company analysis queues.
    # Logic: Replace Gmail, LLM, and the temporary-identity HTTP client while executing checkpoints and real ingestion transactions. Simulate three selected messages within the explicit 20-message scope; read-error isolation and ingestion remain real.
    # Constraints: Model outputs are synthetic fixtures; passing does not verify real mailbox authorization or model quality.
    def test_worker_persists_stream_and_isolates_read_error(self):
        owner = get_user_model().objects.create_user(username="worker-integration")
        mailbox = Mailbox.objects.create(owner=owner, address="worker@processing.example")
        GmailCredential.objects.create(mailbox=mailbox, credentials={"mock": True})
        run = request_run(owner, mailbox.pk, sync_options={"max_messages": 20})
        payloads = {key: rules.extract_email(mailbox, "buyer@pipeline.example", "询价", "需求：设备\n数量：2 台", key) for key in ["one", "three"]}
        client = Mock()
        client.get_sync_state.return_value = {"mailbox_id": str(mailbox.pk), "cursor": None, "scope": {}, "version": 0}
        client.get_stored_email.return_value = None
        client.submit_emails.side_effect = lambda submissions: {"created_count": len(ingestion.submit_emails(owner, submissions)), "updated_count": 0, "duplicate_count": 0, "affected_company_ids": []}
        with (
            patch("apps.crm.dispatch.django_backend_from_environment", return_value=client),
            patch("apps.crm.worker.create_service_from_authorization", return_value=(object(), None)),
            patch("apps.crm.durable_sync.resolve_mailbox_address", return_value=mailbox.address),
            patch("apps.crm.durable_sync.scoped_message_pages", return_value=[["one", "bad", "three"]]),
            patch("apps.crm.durable_sync.read_email", side_effect=[{"gmail_message_id": "one"}, RuntimeError("simulated"), {"gmail_message_id": "three"}]),
            patch("agent.workflows.gmail_sync.process_email", side_effect=lambda email, *_args: payloads[email["gmail_message_id"]]),
        ):
            self.assertTrue(run_sync(owner))
        run.refresh_from_db()
        self.assertEqual(run.status, "partial")
        self.assertEqual(Email.objects.filter(mailbox=mailbox).count(), 2)
        data = run_data(run)
        self.assertEqual((data["completed_count"], data["failed_count"], data["total_count"]), (2, 1, 3))
        self.assertEqual(data["email_errors"][0]["gmail_message_id"], "bad")
        self.assertEqual(data["analysis_pending_count"], 1)
