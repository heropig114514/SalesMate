"""Responsibility: Validate explicit backend historical-L1 upgrades and the v7 direction contract.
Implementation: Use real isolated PostgreSQL and concurrent connections to validate row locks; inject failures inside transactions and provide model output through a fixed-fact substitute.
Relationships: extraction_upgrades, lineage, and serializers; does not read real mailboxes or rewrite local business data.
Directory:
- ExtractionUpgradeTests: Upgrade authorization, version, failure, and provenance regression tests.
- ExtractionUpgradeTests.setUp: Prepare a legacy business source and two employees.
- ExtractionUpgradeTests.test_preview_and_queue_are_owner_scoped: Validate read-only behavior, permission, optimistic locking, and idempotent queueing.
- ExtractionUpgradeTests.test_upgrade_keeps_history_and_uses_stored_body: Validate persisted original text, historical preservation, and analysis unblocking.
- ExtractionUpgradeTests.test_upgraded_unknown_intent_requires_review: Validate that re-extraction updates machine classification rather than retaining stale business judgement.
- ExtractionUpgradeTests.test_failure_requires_explicit_retry: Validate that failures do not retry automatically.
- ExtractionUpgradeTests.test_changed_review_rejects_inflight_result: Validate rejection of old results when a manual decision changes.
- ExtractionUpgradeTests.test_invalid_direction_rejected_at_submission_and_completion: Validate direction boundaries for writes and repair.
- ExtractionUpgradeTests.test_old_analysis_is_explicitly_blocked: Validate that the analysis entry for legacy facts gives an actionable error.
- ExtractionUpgradeTests.add_email: Construct new/old-version or non-business-email control pairs for one customer.
- ExtractionUpgradeTests.test_concurrent_queue_serializes_revision: Validate that concurrent real POST requests queue only once and reject stale versions.
- ExtractionUpgradeTests.test_concurrent_queue_serializes_revision.submit: Submit the same-version upgrade request through independent connections.
- ExtractionUpgradeTests.test_queue_rolls_back_partial_batch: Validate that a failure when queueing the second email rolls back the entire batch and failure-state change.
- ExtractionUpgradeTests.test_queue_rolls_back_partial_batch.fail_second: Inject a mid-batch exception after actual repair writes.
- ExtractionUpgradeTests.test_queue_rolls_back_revision_and_analysis: Validate that a post-scheduling failure rolls back jobs, company version, and analysis queue.
- ExtractionUpgradeTests.test_queue_rolls_back_revision_and_analysis.fail_after_schedule: Inject an exception after actual analysis-scheduling writes.
- ExtractionUpgradeTests.test_expired_lease_rejects_result_without_retry: Validate rejection when the lease equals the current time and that expiry cleanup marks failure without retrying.
- ExtractionUpgradeTests.test_replaced_source_rejects_result: Independently cover completion validation when the original extraction identity is replaced.
- ExtractionUpgradeTests.test_duplicate_completion_has_no_side_effects: Validate that repeated completion neither writes extraction again nor advances the version.
- ExtractionUpgradeTests.test_mixed_batch_blocks_until_all_repairs_complete: Validate that a mixed-version batch upgrades only targets and that partial failure blocks analysis.
- ExtractionUpgradeTests.test_latest_version_and_empty_batch_are_noops: Validate that empty batches and batches containing only current versions produce no writes.
- ExtractionUpgradeTests.test_upgrade_http_rejects_missing_version_and_payload: Validate HTTP boundaries for missing versions and extra parameters.
Variable index:
- None
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient
from rest_framework.exceptions import ValidationError

from apps.crm import ingestion, jobs, rules
from apps.crm.access import Conflict
from apps.crm.classification import review_email
from apps.crm.extraction_upgrades import queue_upgrades
from apps.crm.durable_models import ExtractionRepair
from apps.crm.lineage import claim_repair, complete_repair, request_repair, run_repair, schedule_analysis
from apps.crm.models import Email, Extraction, Mailbox


# Function: Test persistent boundaries and cross-account isolation for version upgrades.
# Logic: Each test constructs a legacy business source; every model call is replaced with verifiable fixed facts.
# Constraints: Real transaction tests use only the isolated test database; passing does not prove model purchasing-stage semantics are correct.
@override_settings(ANALYSIS_PROVIDER="agent")
class ExtractionUpgradeTests(TransactionTestCase):
    # Function: Prepare legacy facts, persisted body text, session identity, and a new-fact candidate.
    # Inputs: No external inputs; invoked by the test runner.
    # Outputs: Instance state stores owner, other, mailbox, email, source, facts, url, and client.
    # Logic: First ingest through the real v7 interface, then change the extraction to legacy v6 only in the test database to simulate pre-upgrade existing data.
    # Constraints: Does not loosen the production entry point to manufacture legacy data.
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="upgrade-owner")
        self.other = get_user_model().objects.create_user(username="upgrade-other")
        self.mailbox = Mailbox.objects.create(owner=self.owner, address="sales@upgrade.example")
        self.payload = rules.extract_email(self.mailbox, "buyer@client.example", "询价", "需求：设备\n数量：2 台", "old-one")
        ingestion.submit_emails(self.owner, [self.payload])
        self.email = Email.objects.get(pk=self.payload["dedupe_key"])
        self.source = self.email.extractions.get()
        self.facts = deepcopy(self.source.facts)
        self.source.prompt_version = "extract-v6"
        self.source.facts["intent_hint"] = "purchase_inquiry"
        self.source.save(update_fields=["prompt_version", "facts"])
        self.url = f"/api/v1/companies/{self.email.company_id}/extraction-upgrade/"
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    # Function: Verify read-only upgrade preview and account/version boundaries for queueing.
    # Inputs: Two session identities and current/legacy If-Match values.
    # Outputs: Unauthorized access returns 404, a stale version returns 409, valid queueing returns 202, and repeated explicit requests reuse the active job.
    # Logic: Compare repair-row count, company revision, and summary count.
    # Constraints: Does not start a Worker or invoke a model.
    def test_preview_and_queue_are_owner_scoped(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["incompatible_emails"], 1)
        self.assertFalse(self.email.repairs.exists())
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url, {}, format="json", HTTP_IF_MATCH=response["ETag"]).status_code, 404)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.post(self.url, {}, format="json", HTTP_IF_MATCH="0").status_code, 409)
        queued = self.client.post(self.url, {}, format="json", HTTP_IF_MATCH=response["ETag"])
        self.assertEqual(queued.status_code, 202, queued.data)
        self.assertEqual(queued.data["created"], 1)
        repeated = self.client.post(self.url, {}, format="json", HTTP_IF_MATCH=queued["ETag"])
        self.assertEqual(repeated.data["reused"], 1)
        self.assertEqual(self.email.repairs.count(), 1)
        self.assertEqual(jobs.claim(self.owner, 1, 120), [])

    # Function: Validate that upgrades use persisted bodies while preserving old provenance.
    # Inputs: Explicit queue and fixed v7 model candidate.
    # Outputs: Two extractions, one new and one old; old facts remain unchanged, original text and manual decisions remain unchanged, and an analysis job becomes claimable.
    # Logic: A real Worker unit reads the email body; the substitute provider replaces only the external model.
    # Constraints: Does not access Gmail, and the model substitute does not prove actual extraction quality.
    def test_upgrade_keeps_history_and_uses_stored_body(self):
        old_facts = deepcopy(self.source.facts)
        queue_upgrades(self.owner, self.email.company_id, self.email.company.revision)
        with patch("apps.crm.lineage.bailian_extraction_provider", return_value=self.facts) as model:
            self.assertTrue(run_repair(self.owner))
        model.assert_called_once_with(self.payload["subject"], self.payload["body_text"], direction="inbound")
        self.source.refresh_from_db()
        self.email.refresh_from_db()
        self.assertEqual(self.source.facts, old_facts)
        self.assertEqual(self.email.extractions.count(), 2)
        self.assertEqual(self.email.extractions.order_by("-pk").first().prompt_version, "extract-v7")
        self.assertEqual(self.email.review_status, "")
        self.assertEqual(self.email.payload["body_text"], self.payload["body_text"])
        self.assertEqual(self.client.get(self.url).data["incompatible_emails"], 0)
        self.assertEqual(len(jobs.claim(self.owner, 1, 120)), 1)

    # Function: Validate that a machine source with no purchase stage re-enters review after upgrading.
    # Inputs: Legacy automatic business source and valid v7 facts with null intent.
    # Outputs: The new extraction is retained, classification becomes needs_review, and no analysis job is claimable.
    # Logic: Update classification through the actual completion service; do not forge a manually confirmed business record.
    # Constraints: Does not discard old provenance or guess an unknown stage as the lowest stage.
    def test_upgraded_unknown_intent_requires_review(self):
        queue_upgrades(self.owner, self.email.company_id, self.email.company.revision)
        facts = deepcopy(self.facts)
        facts["intent_hint"], facts["intent_evidences"] = None, []
        complete_repair(claim_repair(self.owner), facts)
        self.email.refresh_from_db()
        self.assertEqual(self.email.business_classification, "needs_review")
        self.assertEqual(self.email.extractions.count(), 2)
        self.assertEqual(jobs.claim(self.owner, 1, 120), [])

    # Function: Validate that failure results persist and are requeued only by explicit POST.
    # Inputs: Model exception and a subsequent explicit upgrade request.
    # Outputs: One model call, failure summary, and blocked analysis; explicit requeueing then produces a new job.
    # Logic: Run the Worker unit twice; the second run cannot invoke the model again.
    # Constraints: Does not inject retries or fallback algorithms.
    def test_failure_requires_explicit_retry(self):
        queue_upgrades(self.owner, self.email.company_id, self.email.company.revision)
        with patch("apps.crm.lineage.bailian_extraction_provider", side_effect=RuntimeError("test failure")) as model:
            self.assertTrue(run_repair(self.owner))
            self.assertFalse(run_repair(self.owner))
        self.assertEqual(model.call_count, 1)
        preview = self.client.get(self.url)
        self.assertEqual(preview.data["repairs"]["failed"], 1)
        self.assertEqual(jobs.claim(self.owner, 1, 120), [])
        result = self.client.post(self.url, {}, format="json", HTTP_IF_MATCH=preview["ETag"])
        self.assertEqual(result.data["created"], 1)

    # Function: Validate that an in-progress upgrade cannot overwrite a later manual review decision.
    # Inputs: A claimed job followed by a manual confirmation of non-business status.
    # Outputs: The old result raises Conflict; the historical extraction remains the only copy.
    # Logic: Use the actual classification transaction to change review_revision and repair state.
    # Constraints: There are no real concurrent threads; the test verifies the same interleaved execution order.
    def test_changed_review_rejects_inflight_result(self):
        queue_upgrades(self.owner, self.email.company_id, self.email.company.revision)
        repair = claim_repair(self.owner)
        review_email(self.owner, self.email.pk, "confirmed_non_business", self.email.review_revision)
        with self.assertRaises(Conflict):
            complete_repair(repair, self.facts)
        self.assertEqual(self.email.extractions.count(), 1)

    # Function: Validate that outbound or unknown directions cannot provide a customer purchase stage.
    # Inputs: Outbound/unknown emails carrying a stage and a legacy business source frozen to outbound direction.
    # Outputs: Both ordinary ingestion and repair saving reject the invalid stage and create no extraction.
    # Logic: Cover serializer boundaries and Worker-result saving that bypasses HTTP.
    # Constraints: Does not silently convert an invalid stage to null.
    def test_invalid_direction_rejected_at_submission_and_completion(self):
        for direction in ("outbound", "unknown"):
            payload = deepcopy(self.payload)
            payload["direction"] = direction
            with self.assertRaises(ValidationError):
                ingestion.submit_emails(self.owner, [payload])
        self.email.direction = "outbound"
        self.email.save(update_fields=["direction"])
        queue_upgrades(self.owner, self.email.company_id, self.email.company.revision)
        repair = claim_repair(self.owner)
        with self.assertRaises(ValidationError):
            complete_repair(repair, self.facts)
        self.assertEqual(self.email.extractions.count(), 1)

    # Function: Validate that legacy facts do not directly enqueue an Agent analysis guaranteed to be incompatible.
    # Inputs: Customer-analysis POST for a customer containing a v6 source.
    # Outputs: 409 and an explicit upgrade-entry prompt.
    # Logic: Call through the actual session endpoint and preserve original data.
    # Constraints: Does not verify whether the frontend already displays that entry.
    def test_old_analysis_is_explicitly_blocked(self):
        response = self.client.post(f"/api/v1/companies/{self.email.company_id}/analyze/")
        self.assertEqual(response.status_code, 409)
        self.assertIn("extraction-upgrade", str(response.data))


    # Function: Construct version and classification control emails for one customer.
    # Inputs: `key` is the message identifier; `legacy` controls the legacy version in the test database, and `business` controls whether to retain the purchase stage.
    # Outputs: Persisted Email instance.
    # Logic: After actual ingestion, change only the legacy version or machine classification in the test database; body text and existing samples remain unchanged.
    # Constraints: Does not change versions accepted by the production interface; the returned object belongs to the same owner and customer as the original sample.
    def add_email(self, key, *, legacy=True, business=True):
        payload = rules.extract_email(self.mailbox, "buyer@client.example", "询价", "需求：设备\n数量：2 台", key)
        if not business:
            payload["facts"]["intent_hint"], payload["facts"]["intent_evidences"] = None, []
        ingestion.submit_emails(self.owner, [payload])
        email = Email.objects.get(pk=payload["dedupe_key"])
        if legacy:
            extraction = email.extractions.get()
            extraction.prompt_version = "extract-v6"
            extraction.save(update_fields=["prompt_version"])
        return email

    # Function: Validate that database row locking permits only one queueing operation when the same ETag is submitted concurrently.
    # Inputs: Two independent threads, connections, and session clients; a barrier synchronizes requests for the same customer version.
    # Outputs: One 202, one 409, a single repair, and one company-version advance.
    # Logic: Execute actual HTTP and PostgreSQL transactions without replacing row locks or permission checks.
    # Constraints: Does not claim exhaustive scheduling coverage; threads close connections on exit and the barrier has a timeout to avoid hanging tests.
    def test_concurrent_queue_serializes_revision(self):
        company = self.email.company
        revision, barrier = company.revision, Barrier(2)

        # Function: Submit a same-version customer upgrade through an independent connection.
        # Inputs: `index` is the concurrent-task number; read owner, revision, and barrier from the closure.
        # Outputs: HTTP status code.
        # Logic: First create a thread-local APIClient, then synchronize and call the actual POST.
        # Constraints: Each thread closes its own connection and does not share client state.
        def submit(index):
            try:
                client = APIClient()
                client.force_authenticate(get_user_model().objects.get(pk=self.owner.pk))
                barrier.wait(timeout=10)
                response = client.post(self.url, {}, format="json", HTTP_IF_MATCH=str(revision))
                return response.status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(submit, range(2)))
        self.assertEqual(sorted(statuses), [202, 409])
        company.refresh_from_db()
        self.assertEqual(company.revision, revision + 1)
        self.assertEqual(self.email.repairs.count(), 1)
        self.assertEqual(self.email.repairs.get().status, "pending")

    # Function: Validate that an entire transaction rolls back after partial writes while queueing multiple emails.
    # Inputs: Two legacy sources: the first already has a failed repair, and the second queueing operation raises a failure.
    # Outputs: Repair rows, failure state, company version, and analysis queue all return to their original values.
    # Logic: Simulate the failure point rather than the transaction: the first creation and old failed-state change both execute for real.
    # Constraints: Does not change transaction isolation level; injects only a repeatable test exception.
    def test_queue_rolls_back_partial_batch(self):
        second = self.add_email("old-two")
        failed = ExtractionRepair.objects.create(email=self.email, source=self.source,
            review_revision=self.email.review_revision, status="failed", error="original_failure")
        company = self.email.company
        company.refresh_from_db()
        revision = company.revision
        before_jobs = list(company.jobs.order_by("pk").values())
        calls = []

        # Function: Trigger failure of the second item after real queueing writes occur.
        # Inputs: `email` is the current email and `upgrade` is the mode explicitly passed by the caller.
        # Outputs: The first item returns the original service result and the second raises RuntimeError.
        # Logic: Call the original request_repair first so the test covers rollback after writes have occurred.
        # Constraints: Do not catch the exception; the enclosing real atomic block must perform rollback.
        def fail_second(email, *, upgrade):
            result = request_repair(email, upgrade=upgrade)
            calls.append(email.pk)
            if len(calls) == 2:
                raise RuntimeError("injected after second repair write")
            return result

        with patch("apps.crm.extraction_upgrades.request_repair", side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, "second repair"):
                queue_upgrades(self.owner, company.pk, revision)
        self.assertEqual(set(calls), {self.email.pk, second.pk})
        failed.refresh_from_db()
        company.refresh_from_db()
        self.assertEqual((failed.status, failed.error), ("failed", "original_failure"))
        self.assertEqual(ExtractionRepair.objects.count(), 1)
        self.assertEqual(company.revision, revision)
        self.assertEqual(list(company.jobs.order_by("pk").values()), before_jobs)

    # Function: Validate that a scheduling-tail failure also rolls back the whole upgrade transaction.
    # Inputs: One legacy source; raise an exception after actually updating the analysis queue.
    # Outputs: No new repair; company revision and original analysis records remain completely unchanged.
    # Logic: Cover the post-exception path after company version is saved and schedule_analysis has executed.
    # Constraints: Does not merely test precondition failure and does not alter business implementation.
    def test_queue_rolls_back_revision_and_analysis(self):
        company = self.email.company
        revision = company.revision
        before_jobs = list(company.jobs.order_by("pk").values())

        # Function: Inject a fault after actual analysis scheduling completes.
        # Inputs: `company` is the customer locked by the current transaction.
        # Outputs: Always raises RuntimeError.
        # Logic: Call the original scheduler first to create database changes, then let the outer atomic block roll them back.
        # Constraints: Does not swallow exceptions or simulate database-write results.
        def fail_after_schedule(company):
            schedule_analysis(company)
            raise RuntimeError("injected after analysis scheduling")

        with patch("apps.crm.extraction_upgrades.schedule_analysis", side_effect=fail_after_schedule):
            with self.assertRaisesRegex(RuntimeError, "analysis scheduling"):
                queue_upgrades(self.owner, company.pk, revision)
        company.refresh_from_db()
        self.assertEqual(company.revision, revision)
        self.assertFalse(self.email.repairs.exists())
        self.assertEqual(list(company.jobs.order_by("pk").values()), before_jobs)

    # Function: Validate that exact lease-boundary rejects late completion and explicitly marks expiry as failure.
    # Inputs: Claimed job with fixed current time equal to lease_until.
    # Outputs: Completion raises Conflict and creates no extraction; subsequent claiming marks it failed and creates no retry job automatically.
    # Logic: Fix only the clock and do not change the 600-second lease; check completion and expiry-cleanup branches separately.
    # Constraints: Does not actually wait for the lease and does not treat timeout as success or automatic renewal.
    def test_expired_lease_rejects_result_without_retry(self):
        queue_upgrades(self.owner, self.email.company_id, self.email.company.revision)
        repair = claim_repair(self.owner)
        company = self.email.company
        company.refresh_from_db()
        revision = company.revision
        with patch("apps.crm.lineage.timezone.now", return_value=repair.lease_until):
            with self.assertRaises(Conflict):
                complete_repair(repair, self.facts)
            self.assertIsNone(claim_repair(self.owner))
            self.assertIsNone(claim_repair(self.owner))
        repair.refresh_from_db()
        company.refresh_from_db()
        self.assertEqual((repair.status, repair.error), ("failed", "repair_lease_expired"))
        self.assertEqual(company.revision, revision)
        self.assertEqual(self.email.extractions.count(), 1)
        self.assertEqual(self.email.repairs.count(), 1)
        self.assertEqual(jobs.claim(self.owner, 1, 120), [])

    # Function: Independently validate that old model results cannot be submitted after source identity is replaced.
    # Inputs: A job with valid state and lease, followed by insertion of a new current extraction in the test database.
    # Outputs: Old completion raises Conflict, creates no third extraction, and leaves version and job state unchanged.
    # Logic: Change only source identity, excluding other rejection conditions that could mask this branch.
    # Constraints: Direct modeling is used only for fault injection and does not mean production permits bypassing ingestion transactions.
    def test_replaced_source_rejects_result(self):
        queue_upgrades(self.owner, self.email.company_id, self.email.company.revision)
        repair = claim_repair(self.owner)
        current = Extraction.objects.create(email=self.email, prompt_version="extract-v7", status="completed",
                                             facts=self.facts, error=None)
        company = self.email.company
        company.refresh_from_db()
        revision = company.revision
        with self.assertRaises(Conflict):
            complete_repair(repair, self.facts)
        repair.refresh_from_db()
        company.refresh_from_db()
        self.assertEqual(repair.status, "running")
        self.assertEqual(company.revision, revision)
        self.assertEqual(self.email.extractions.count(), 2)
        self.assertEqual(self.email.extractions.order_by("-pk").first().pk, current.pk)

    # Function: Validate that completing a successful job again cannot repeat side effects.
    # Inputs: The same claimed job and the same v7 facts are submitted for completion twice in succession.
    # Outputs: The second raises Conflict; there are exactly two old/new facts and version and analysis queue no longer change.
    # Logic: Capture database state after the first real submission succeeds, then compare every relevant write after rejection.
    # Constraints: Preserve the established repeated-completion failure semantics and do not turn rejection into implicit success.
    def test_duplicate_completion_has_no_side_effects(self):
        queue_upgrades(self.owner, self.email.company_id, self.email.company.revision)
        repair = claim_repair(self.owner)
        complete_repair(repair, self.facts)
        company = self.email.company
        company.refresh_from_db()
        revision = company.revision
        before_jobs = list(company.jobs.order_by("pk").values())
        with self.assertRaises(Conflict):
            complete_repair(repair, self.facts)
        company.refresh_from_db()
        repair.refresh_from_db()
        self.assertEqual(repair.status, "completed")
        self.assertEqual(company.revision, revision)
        self.assertEqual(self.email.extractions.count(), 2)
        self.assertEqual(list(company.jobs.order_by("pk").values()), before_jobs)

    # Function: Validate that a mixed old/new-version batch upgrades only legacy business sources and blocks analysis until all repairs finish.
    # Inputs: Two legacy business emails, one v7 email, and one email pending review; the model for the second legacy source fails once.
    # Outputs: Only the two legacy business emails are queued; analysis remains blocked on partial success/failure and becomes claimable only after an explicit retry completes.
    # Logic: Run actual Worker units with fixed success or exception at the model boundary, then verify that a completed batch performs no writes.
    # Constraints: Does not retry automatically, process unconfirmed review emails, or alter existing current-version data.
    def test_mixed_batch_blocks_until_all_repairs_complete(self):
        second = self.add_email("old-two")
        current = self.add_email("current", legacy=False)
        review = self.add_email("review", business=False)
        company = self.email.company
        company.refresh_from_db()
        queued = queue_upgrades(self.owner, company.pk, company.revision)
        self.assertEqual(queued["created"], 2)
        self.assertEqual(queued["incompatible_emails"], 2)
        self.assertEqual(set(ExtractionRepair.objects.values_list("email_id", flat=True)), {self.email.pk, second.pk})
        with patch("apps.crm.lineage.bailian_extraction_provider", return_value=self.facts):
            self.assertTrue(run_repair(self.owner))
        self.assertEqual(jobs.claim(self.owner, 1, 120), [])
        with patch("apps.crm.lineage.bailian_extraction_provider", side_effect=RuntimeError("one failed")):
            self.assertTrue(run_repair(self.owner))
        self.assertEqual(jobs.claim(self.owner, 1, 120), [])
        preview = self.client.get(self.url)
        self.assertEqual(preview.data["incompatible_emails"], 1)
        self.assertEqual(preview.data["repairs"], {"pending": 0, "running": 0, "failed": 1})
        retry = self.client.post(self.url, {}, format="json", HTTP_IF_MATCH=preview["ETag"])
        self.assertEqual((retry.data["created"], retry.data["reused"]), (1, 0))
        with patch("apps.crm.lineage.bailian_extraction_provider", return_value=self.facts):
            self.assertTrue(run_repair(self.owner))
        self.assertFalse(current.repairs.exists())
        self.assertFalse(review.repairs.exists())
        self.assertEqual(current.extractions.count(), 1)
        self.assertEqual(review.extractions.count(), 1)
        company.refresh_from_db()
        revision = company.revision
        before_jobs = list(company.jobs.order_by("pk").values())
        repair_count = ExtractionRepair.objects.count()
        noop = queue_upgrades(self.owner, company.pk, revision)
        self.assertEqual((noop["created"], noop["reused"], noop["revision"]), (0, 0, revision))
        self.assertEqual(noop["incompatible_emails"], 0)
        self.assertEqual(ExtractionRepair.objects.count(), repair_count)
        self.assertEqual(list(company.jobs.order_by("pk").values()), before_jobs)
        self.assertEqual(len(jobs.claim(self.owner, 1, 120)), 1)

    # Function: Validate that no version advances or queue is created when no sources need upgrading.
    # Inputs: An empty business set, then a set containing only current v7 business sources.
    # Outputs: Both cases have zero created/reused; version and original analysis records remain unchanged.
    # Logic: Exercise the empty-loop and current-version-skip branches separately and check the latest version count.
    # Constraints: Directly changing sample classification is used only to construct boundaries and does not simulate a production manual action.
    def test_latest_version_and_empty_batch_are_noops(self):
        company = self.email.company
        revision = company.revision
        before_jobs = list(company.jobs.order_by("pk").values())
        self.email.business_classification = "needs_review"
        self.email.save(update_fields=["business_classification"])
        empty = queue_upgrades(self.owner, company.pk, revision)
        self.assertEqual(empty["versions"], {})
        self.email.business_classification = "business"
        self.email.save(update_fields=["business_classification"])
        Extraction.objects.create(email=self.email, prompt_version="extract-v7", status="completed", facts=self.facts)
        current = queue_upgrades(self.owner, company.pk, revision)
        self.assertEqual(current["versions"], {"extract-v7": 1})
        for result in (empty, current):
            self.assertEqual((result["created"], result["reused"], result["revision"]), (0, 0, revision))
        company.refresh_from_db()
        self.assertEqual(company.revision, revision)
        self.assertFalse(self.email.repairs.exists())
        self.assertEqual(list(company.jobs.order_by("pk").values()), before_jobs)

    # Function: Validate that the upgrade API rejects missing versions and extra model/fact parameters.
    # Inputs: An empty request without If-Match, or a request carrying unsupported parameters.
    # Outputs: Missing version or extra parameters both return 400; repair and company revision remain unchanged.
    # Logic: Use real HTTP input boundaries and verify that rejection creates no writes.
    # Constraints: Does not change HTTP status-code contracts or loosen permissions.
    def test_upgrade_http_rejects_missing_version_and_payload(self):
        revision = self.email.company.revision
        self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 400)
        response = self.client.post(self.url, {"model": "override"}, format="json", HTTP_IF_MATCH=str(revision))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.email.repairs.exists())
        self.email.company.refresh_from_db()
        self.assertEqual(self.email.company.revision, revision)
