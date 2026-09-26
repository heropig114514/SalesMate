"""Responsibility: Verifies durable source recovery, incremental processing, and automatic lineage correction.
Implementation: The Gmail test batch explicitly selects at most 20 messages, rather than using a runtime default; uses isolated PostgreSQL, real business transactions, and mocked Gmail/LLM to check persisted state and model call counts.
Relationships: `durable_sync`, `lineage`, `classification`, and `results`; does not connect to a real mailbox or model.
Directory:
- DurableLineageTests: Cross-connection persistence and source-regression tests.
- DurableLineageTests.setUp: Creates an employee, mailbox, and mocked HTTP write adapter.
- DurableLineageTests.payload: Builds an email with source-text evidence.
- DurableLineageTests.submit: Sends mocked HTTP submissions into real business transactions.
- DurableLineageTests.start: Creates and claims an independent batch.
- DurableLineageTests.store: Saves a test business email.
- DurableLineageTests.fail_after_raw: After verifying that source text was submitted before the model boundary, simulates failure.
- DurableLineageTests.test_no_purchase_stage_requires_review: Covers emails with material updates but no buying stage.
- DurableLineageTests.test_new_purchase_stage_is_saved_as_business: A new-version buying-stage email can be saved as a business email.
- DurableLineageTests.test_new_mail_without_purchase_stage_waits_for_review: A new-version email without a buying stage enters review; it is rejected when stage and evidence are inconsistent.
- DurableLineageTests.test_old_l1_submission_is_rejected: Old extraction versions and intent values cannot enter the new email interface.
- DurableLineageTests.test_repair_preserves_source_and_unblocks_analysis: Manual re-extraction preserves history and unblocks profiling.
- DurableLineageTests.test_stale_repair_is_rejected: Rejects an old result when a manual decision changes while the model is returning.
- DurableLineageTests.test_failed_repair_requires_explicit_retry: Makes failure persistence visible and retries explicit.
- DurableLineageTests.test_lineage_recomputes_remaining_and_preserves_unrelated: Recomputes after source removal without affecting other companies.
- DurableLineageTests.test_restore_same_input_keeps_revision_history: Restoring identical input preserves an independent snapshot.
- DurableLineageTests.test_raw_survives_failure_and_incremental_reuses_results: Persists source text before the model; failed retries and later synchronization reuse the cache.
- DurableLineageTests.test_bounded_pages_retry_same_window_without_duplicate_reads: After bounded-pagination failure, retries the original window and does not read bodies after deduplication.
- DurableLineageTests.test_checkpoint_rolls_back_discovery_on_database_error: Rolls back messages and per-message progress atomically when registration fails.
- DurableLineageTests.test_old_cursor_protocol_fails_loudly: Does not disguise old-protocol cursor errors as success.
- DurableLineageTests.test_completed_l1_retries_submission_without_model: After submission failure, resubmits only persisted L1 output.
- DurableLineageTests.test_worker_dispatches_repair_without_sync_and_once_exits: Consumes repairs even without a sync batch; exits normally after one round on failure.
Variable index:
- None
"""
from copy import deepcopy
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from agent.workflows.analysis_input import build_analysis_input
from agent.workflows.gmail_sync import _get_sync_state, _save_sync_state
from apps.crm import ingestion, jobs, rules, selectors
from apps.crm.access import Conflict
from apps.crm.classification import review_data, review_email
from apps.crm.durable_models import ExtractionRepair, SnapshotInvalidation, SnapshotSource, StoredMessage
from apps.crm.durable_sync import checkpoint, sync_persisted
from apps.crm.lineage import claim_repair, complete_repair, run_repair
from apps.crm.models import Email, GmailCredential, Mailbox
from apps.crm.processing import claim_run, finish_run, request_run, retry_run
from apps.crm.results import cached_analysis


# Function: Verifies cross-stage database recovery and source-change invariants.
# Logic: `TransactionTestCase` lets real thread connections observe committed source text; Gmail and model boundaries are fully mocked.
# Constraints: Mock success does not prove real-service quality and does not modify local business data.
class DurableLineageTests(TransactionTestCase):
    # Function: Creates isolated identities and a write adapter.
    # Inputs: No external parameters; invoked by the test framework.
    # Outputs: `owner`, `mailbox`, and `backend` instance state.
    # Logic: The HTTP mock delegates to real ingestion and does not bypass business validation.
    # Constraints: Credentials are synthetic placeholders and cannot authorize network access.
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="durable-owner")
        self.mailbox = Mailbox.objects.create(owner=self.owner, address="sales@durable.example")
        GmailCredential.objects.create(mailbox=self.mailbox, credentials={"mock": True})
        self.backend = Mock()
        self.backend.submit_emails.side_effect = self.submit

    # Function: Generates verifiable facts and email source text.
    # Inputs: `key` is the message ID; `sender` is a synthetic sender address.
    # Outputs: Standard submission dictionary.
    # Logic: A test-fixed tagged body; factual evidence comes from that body.
    # Constraints: Does not use rules as a runtime fallback for model failure.
    def payload(self, key, sender="buyer@customer.example"):
        return rules.extract_email(self.mailbox, sender, "询价", "需求：设备\n数量：2 台", key)

    # Function: Mocks Agent HTTP write transport.
    # Inputs: `submissions` is the array produced by real L1 processing.
    # Outputs: Statistics from real persisted results.
    # Logic: Adds the mailbox identity and `gmail_real` source managed by the adapter, then calls the original transactional service.
    # Constraints: Binds only the test employee; source text and facts still pass serializer validation.
    def submit(self, submissions):
        result = ingestion.submit_emails(self.owner, [{**item, "mailbox_id": str(self.mailbox.pk), "source": "gmail_real"} for item in submissions])
        return {"created_count": sum(item["status"] == "created" for item in result), "updated_count": sum(item["status"] == "updated" for item in result), "duplicate_count": sum(item["status"] == "duplicate" for item in result), "affected_company_ids": [item["company_id"] for item in result]}

    # Function: Creates a claimed batch.
    # Inputs: No external parameters; reads the current test employee and mailbox.
    # Outputs: A running batch with a lease.
    # Logic: Uses real enqueue and claim services. The fixture explicitly selects at most 20 messages, then obtains the batch through real enqueue and claim services.
    # Constraints: The previous batch must have ended.
    def start(self):
        request_run(self.owner, self.mailbox.pk, sync_options={"max_messages": 20})
        return claim_run(self.owner)

    # Function: Saves a test email with explicit business facts.
    # Inputs: `key` is the message identifier; `sender` determines company grouping.
    # Outputs: An `Email` instance.
    # Logic: Calls real ingestion and classification logic.
    # Constraints: Does not generate a model profile.
    def store(self, key, sender="buyer@customer.example"):
        data = self.payload(key, sender)
        ingestion.submit_emails(self.owner, [data])
        return Email.objects.get(pk=data["dedupe_key"])

    # Function: Verifies that source text can be read through an independent thread connection before entering the model.
    # Inputs: `subject` and `body` are actual model inputs.
    # Outputs: Raises a simulated model exception.
    # Logic: First asserts that persisted body and input match and no business email exists, closes the database connection opened by the mock provider inside the thread, then fails.
    # Constraints: For the mock provider only; the real provider does not access Django or call a real model.
    def fail_after_raw(self, subject, body):
        try:
            raw = StoredMessage.objects.get().raw
            self.assertEqual((raw["subject"], raw["eligible_body_text"]), (subject, body))
            self.assertFalse(Email.objects.exists())
        finally:
            connections.close_all()
        raise RuntimeError("mock model failure after durable raw")

    # Function: Verifies that email without a buying stage enters review.
    # Inputs: No external parameters; builds an email with factual updates but no buying stage.
    # Outputs: `needs_review` with no profiling task.
    # Logic: Whether material updates exist does not affect manual review for stage-less email.
    # Constraints: Does not call a model or external mailbox.
    def test_no_purchase_stage_requires_review(self):
        data = self.payload("no-purchase-stage")
        data["facts"]["intent_hint"] = None
        data["facts"]["intent_evidences"] = []
        ingestion.submit_emails(self.owner, [data])
        email = Email.objects.get(pk=data["dedupe_key"])
        self.assertEqual(email.business_classification, "needs_review")
        self.assertFalse(email.company.jobs.exists())

    # Function: Verifies that a new buying stage passes real ingestion validation.
    # Inputs: An `extract-v7` synthetic email with locatable quantity evidence.
    # Outputs: The email is classified as business and extraction version remains `v7`.
    # Logic: Saves through `submit_emails`, then reads classification and `Extraction`.
    # Constraints: Does not call a model or external mailbox.
    def test_new_purchase_stage_is_saved_as_business(self):
        data = self.payload("purchase-stage")
        data["extract_prompt_version"] = "extract-v7"
        data["facts"]["intent_hint"] = "L3 Qualified"
        data["facts"]["intent_evidences"] = ["数量：2 台"]
        ingestion.submit_emails(self.owner, [data])
        email = Email.objects.get(pk=data["dedupe_key"])
        self.assertEqual(email.business_classification, "business")
        self.assertEqual(email.extractions.get().prompt_version, "extract-v7")

    # Function: Verifies review for stage-less email and the stage-evidence constraint.
    # Inputs: A v7 email with intent_hint null and a v7 stage email lacking evidence.
    # Outputs: The former enters review and no analysis queue; the latter is rejected.
    # Logic: Calls the real email-write service and reads classification and task state.
    # Constraints: Does not treat null directly as non-business and does not call a model.
    def test_new_mail_without_purchase_stage_waits_for_review(self):
        data = self.payload("no-stage")
        data["extract_prompt_version"] = "extract-v7"
        data["facts"]["intent_hint"] = None
        data["facts"]["intent_evidences"] = []
        ingestion.submit_emails(self.owner, [data])
        email = Email.objects.get(pk=data["dedupe_key"])
        self.assertEqual(email.business_classification, "needs_review")
        self.assertFalse(email.company.jobs.exists())

        invalid = self.payload("missing-stage-evidence")
        invalid["extract_prompt_version"] = "extract-v7"
        invalid["facts"]["intent_hint"] = "L3 Qualified"
        invalid["facts"]["intent_evidences"] = []
        with self.assertRaises(ValidationError):
            ingestion.submit_emails(self.owner, [invalid])

    # Function: Verifies that the new email interface rejects old L1 formats.
    # Inputs: Synthetic email with an old extraction version and old intent enumeration.
    # Outputs: Both inputs are rejected and not persisted.
    # Logic: Calls real ingestion validation without triggering a model or external mailbox.
    # Constraints: Historical migration files remain unchanged.
    def test_old_l1_submission_is_rejected(self):
        old_version = self.payload("old-version")
        old_version["extract_prompt_version"] = "extract-v6"
        with self.assertRaises(ValidationError):
            ingestion.submit_emails(self.owner, [old_version])

        old_intent = self.payload("old-intent")
        old_intent["facts"]["intent_hint"] = "purchase_inquiry"
        with self.assertRaises(ValidationError):
            ingestion.submit_emails(self.owner, [old_intent])
        self.assertFalse(Email.objects.exists())

    # Function: Verifies that misclassification repair redoes L1 before profiling.
    # Inputs: No external parameters; a rule-skipped synthetic email and mocked L1 facts.
    # Outputs: Preserves the old extraction, lets real Agent L2 consume new facts, and unblocks the company task.
    # Logic: Does not modify source text or prompt version; repair uses an independent generation and is validated through the Agent's real L2 entry point.
    # Constraints: Does not interpret mocked LLM output as proof that a real model was verified.
    def test_repair_preserves_source_and_unblocks_analysis(self):
        data = self.payload("skipped")
        facts = deepcopy(data["facts"])
        facts["intent_hint"] = "L1 Exploring"
        data.update(extract_status="skipped_non_business", facts=None, non_business_hint=True, non_business_reason="命中 no-reply 发件地址规则。")
        ingestion.submit_emails(self.owner, [data])
        email = Email.objects.get(pk=data["dedupe_key"])
        original = deepcopy(email.payload)
        source = email.extractions.get()
        review_email(self.owner, email.pk, "confirmed_business", email.review_revision)
        self.assertEqual(jobs.claim(self.owner, 1, 120), [])
        with patch("apps.crm.lineage.bailian_extraction_provider", return_value=facts) as model:
            self.assertTrue(run_repair(self.owner))
        email.refresh_from_db()
        self.assertEqual(email.payload, original)
        source.refresh_from_db()
        self.assertEqual(source.status, "skipped_non_business")
        self.assertEqual(email.extractions.count(), 2)
        self.assertEqual(selectors.latest_extraction(email).facts, facts)
        grouping, context = selectors.context_pair(email.company)
        backend = Mock()
        backend.get_company_grouping.return_value = grouping
        backend.get_company_context.return_value = context
        result = build_analysis_input(str(email.company_id), backend=backend, clock=timezone.now).to_dict()
        self.assertEqual(result.get("unparsed_message_count"), 0, result)
        model.assert_called_once_with(data["subject"], data["body_text"], direction="inbound")
        self.assertEqual(len(jobs.claim(self.owner, 1, 120)), 1)

    # Function: Rejects an old re-extraction result after a manual decision changes.
    # Inputs: No external parameters; a claimed task is confirmed non-business before it returns.
    # Outputs: The old result conflicts and does not add an extraction record.
    # Logic: Checks repair state, manual version, and source.
    # Constraints: Simulates timing interleaving and does not depend on thread scheduling speed.
    def test_stale_repair_is_rejected(self):
        data = self.payload("stale")
        facts = data["facts"]
        data.update(extract_status="failed", facts=None, extract_error="事实抽取失败。")
        ingestion.submit_emails(self.owner, [data])
        email = Email.objects.get(pk=data["dedupe_key"])
        review_email(self.owner, email.pk, "confirmed_business", email.review_revision)
        repair = claim_repair(self.owner)
        email.refresh_from_db()
        review_email(self.owner, email.pk, "confirmed_non_business", email.review_revision)
        with self.assertRaises(Conflict):
            complete_repair(repair, facts)
        self.assertEqual(email.extractions.count(), 1)

    # Function: Verifies that re-extraction failure is not silently retried.
    # Inputs: No external parameters; simulates one model exception.
    # Outputs: Failure is visible, a duplicate Worker does not call the model, and a new task is created only after business status is reconfirmed.
    # Logic: The failed task is retained; an explicit retry creates a successor.
    # Constraints: Does not change the original manual version or facts.
    def test_failed_repair_requires_explicit_retry(self):
        data = self.payload("retry-repair")
        data.update(extract_status="failed", facts=None, extract_error="事实抽取失败。")
        ingestion.submit_emails(self.owner, [data])
        email = Email.objects.get(pk=data["dedupe_key"])
        review_email(self.owner, email.pk, "confirmed_business", email.review_revision)
        with patch("apps.crm.lineage.bailian_extraction_provider", side_effect=RuntimeError("mock")) as model:
            self.assertTrue(run_repair(self.owner))
            self.assertFalse(run_repair(self.owner))
        self.assertEqual(model.call_count, 1)
        email.refresh_from_db()
        self.assertEqual(review_data(email)["repair_status"], "failed")
        review_email(self.owner, email.pk, "confirmed_business", email.review_revision)
        self.assertEqual(email.repairs.filter(status="pending").count(), 1)

    # Function: Verifies lineage invalidation affects only dependent companies and recomputes automatically.
    # Inputs: No external parameters; two emails for one company and a result for another company.
    # Outputs: Affected profile and score become unavailable, recomputation includes only remaining sources, and the other company's result remains valid.
    # Logic: Uses deterministic rules for real result persistence and does not mock lineage relationships.
    # Constraints: Historical snapshots and source edges must remain.
    def test_lineage_recomputes_remaining_and_preserves_unrelated(self):
        removed = self.store("remove")
        remaining = self.store("remain")
        unrelated = self.store("other", "buyer@other.example")
        rules.run_company(self.owner, removed.company_id)
        rules.run_company(self.owner, unrelated.company_id)
        before, _ = selectors.latest_result(removed.company)
        self.assertEqual(SnapshotSource.objects.filter(snapshot=before.snapshot).count(), 2)
        review_email(self.owner, removed.pk, "confirmed_non_business", removed.review_revision)
        removed.company.refresh_from_db()
        self.assertEqual(selectors.latest_result(removed.company), (None, None))
        self.assertFalse(cached_analysis(removed.company, before.snapshot.input_version)["hit"])
        self.assertIsNotNone(selectors.latest_result(unrelated.company)[0])
        self.assertTrue(SnapshotInvalidation.objects.filter(snapshot=before.snapshot).exists())
        rules.run_company(self.owner, removed.company_id)
        current, _ = selectors.latest_result(removed.company)
        self.assertEqual(current.snapshot.payload["member_dedupe_keys"], [remaining.pk])

    # Function: Verifies that restoring the same fact after revocation permits analysis again.
    # Inputs: No external parameters; a complete business-to-non-business-to-business cycle.
    # Outputs: Both revisions with the same `input_version` are retained and the new snapshot is valid.
    # Logic: Regresses the permanent conflict caused by the old company/input uniqueness constraint.
    # Constraints: The original extraction has completed; does not call LLM re-extraction.
    def test_restore_same_input_keeps_revision_history(self):
        email = self.store("restore")
        rules.run_company(self.owner, email.company_id)
        original, _ = selectors.latest_result(email.company)
        review_email(self.owner, email.pk, "confirmed_non_business", email.review_revision)
        email.refresh_from_db()
        review_email(self.owner, email.pk, "confirmed_business", email.review_revision)
        rules.run_company(self.owner, email.company_id)
        current, _ = selectors.latest_result(email.company)
        self.assertEqual(original.snapshot.input_version, current.snapshot.input_version)
        self.assertNotEqual(original.snapshot.pk, current.snapshot.pk)
        self.assertFalse(ExtractionRepair.objects.exists())

    # Function: Verifies that source text is saved before L1 and retry does not reread Gmail.
    # Inputs: No external parameters; one model failure, one explicit retry, and one empty incremental run.
    # Outputs: Source text remains, the first attempt fails, retry calls only the model, and later model and body calls are both zero.
    # Logic: Uses real L1 validation and submission, checking database contents after failure and boundary call counts.
    # Constraints: Gmail and Bailian are fully mocked; duplicate IDs within the range do not trigger body reads.
    def test_raw_survives_failure_and_incremental_reuses_results(self):
        raw = self.payload("raw-first")
        raw["facts"]["intent_hint"] = "L1 Exploring"
        raw["eligible_body_text"] = raw["body_text"]
        run = self.start()
        with patch("apps.crm.durable_sync.resolve_mailbox_address", return_value=self.mailbox.address), patch("apps.crm.durable_sync.scoped_message_pages", return_value=[["raw-first"]]), patch("apps.crm.durable_sync.read_email", return_value=raw), patch("apps.crm.durable_sync.bailian_extraction_provider", side_effect=self.fail_after_raw):
            result = sync_persisted(run, object(), self.backend)
        finish_run(run.pk, run.lease_token, result)
        self.assertEqual(StoredMessage.objects.get().raw, raw)
        self.assertEqual(StoredMessage.objects.get().status, "failed")
        retry_run(self.owner, run.pk)
        retry = claim_run(self.owner)
        with patch("apps.crm.durable_sync.resolve_mailbox_address", return_value=self.mailbox.address), patch("apps.crm.durable_sync.read_email") as reader, patch("apps.crm.durable_sync.bailian_extraction_provider", return_value=raw["facts"]) as model:
            result = sync_persisted(retry, object(), self.backend)
        finish_run(retry.pk, retry.lease_token, result)
        reader.assert_not_called()
        model.assert_called_once()
        self.assertEqual(StoredMessage.objects.get().status, "completed")
        next_run = self.start()
        with patch("apps.crm.durable_sync.resolve_mailbox_address", return_value=self.mailbox.address), patch("apps.crm.durable_sync.scoped_message_pages", return_value=[["raw-first"]]), patch("apps.crm.durable_sync.read_email") as reader, patch("apps.crm.durable_sync.bailian_extraction_provider") as model:
            result = sync_persisted(next_run, object(), self.backend)
        finish_run(next_run.pk, next_run.lease_token, result)
        reader.assert_not_called()
        model.assert_not_called()
        self.assertEqual(result["duplicate_count"], 1)

    # Function: Verifies that bounded pagination failure can retry the original window without rereading persisted email.
    # Inputs: No external parameters; explicitly selects 21 messages, persists all 21, and fails the first request for page two.
    # Outputs: Preserves the original frozen range, skips all 21 duplicates, and performs no body reads or extra registration.
    # Logic: Mocks Gmail SDK pagination boundaries while running real range selection, deduplication, and database batch services.
    # Constraints: Each page remains limited to 20 messages and does not access real Gmail.
    def test_bounded_pages_retry_same_window_without_duplicate_reads(self):
        for index in range(21):
            self.store(f"history-{index}")
        service = Mock()
        execute = service.users.return_value.messages.return_value.list.return_value.execute
        first_page = {"messages": [{"id": f"history-{i}"} for i in range(20)], "nextPageToken": "page-two"}
        execute.side_effect = [first_page, RuntimeError("mock page failure")]
        request_run(self.owner, self.mailbox.pk, sync_options={"max_messages": 21})
        run = claim_run(self.owner)
        with patch("apps.crm.durable_sync.resolve_mailbox_address", return_value=self.mailbox.address), patch("apps.crm.durable_sync.read_email") as reader:
            with self.assertRaises(RuntimeError):
                sync_persisted(run, service, self.backend)
        finish_run(run.pk, run.lease_token, {"status": "failed", "error": {"code": "page_failed"}})
        reader.assert_not_called()
        retry_run(self.owner, run.pk)
        resumed = claim_run(self.owner)
        self.assertEqual(resumed.sync_options, run.sync_options)
        execute.side_effect = [first_page, {"messages": [{"id": "history-20"}]}]
        with patch("apps.crm.durable_sync.resolve_mailbox_address", return_value=self.mailbox.address), patch("apps.crm.durable_sync.read_email") as reader:
            result = sync_persisted(resumed, service, self.backend)
        finish_run(resumed.pk, resumed.lease_token, result)
        reader.assert_not_called()
        self.assertEqual(result["duplicate_count"], 21)
        self.assertFalse(StoredMessage.objects.exists())
        self.assertEqual(service.users.return_value.messages.return_value.list.call_args.kwargs["maxResults"], 1)

    # Function: Verifies atomic rollback of messages and per-message tasks on database failure.
    # Inputs: No external parameters; simulates `Mailbox.save` raising after registration.
    # Outputs: Neither the new message nor the progress task is committed.
    # Logic: Injects a final database failure inside a real transaction.
    # Constraints: Mocks only the save boundary and does not alter transaction implementation.
    def test_checkpoint_rolls_back_discovery_on_database_error(self):
        run = self.start()
        with patch.object(Mailbox, "save", side_effect=RuntimeError("mock database failure")):
            with self.assertRaises(RuntimeError):
                checkpoint(run, ["not-committed"])
        self.assertFalse(StoredMessage.objects.exists())
        self.assertFalse(run.email_jobs.exists())

    # Function: Verifies that old CLI cursor read/write exceptions are reported upward.
    # Inputs: No external parameters; simulates read/write errors for a supported protocol.
    # Outputs: Raises an explicit exception and does not fall back to first synchronization or report write success.
    # Logic: Verifies the Agent boundary directly without accessing real services.
    # Constraints: Unsupported-protocol historical compatibility is outside this test.
    def test_old_cursor_protocol_fails_loudly(self):
        backend = Mock()
        backend.get_sync_state.side_effect = RuntimeError("mock")
        with self.assertRaises(RuntimeError):
            _get_sync_state(backend, str(self.mailbox.pk))
        backend.save_sync_state.side_effect = RuntimeError("mock")
        with self.assertRaises(RuntimeError):
            _save_sync_state(backend, str(self.mailbox.pk), {"version": 1}, "100", [], [])

    # Function: Verifies that a model is not called again when it completed but HTTP persistence failed.
    # Inputs: No external parameters; one successful L1 result, one HTTP exception, and an explicit retry.
    # Outputs: Cache retains complete facts; retry performs no Gmail read or LLM call and successfully saves the business email.
    # Logic: The first failure occurs after durable L1 and before business ingestion. Range selection returns the specified single ID, and retry reuses source text and completed L1.
    # Constraints: Mocks HTTP interruption and does not claim real external-service recovery was verified.
    def test_completed_l1_retries_submission_without_model(self):
        raw = self.payload("submission-retry")
        raw["facts"]["intent_hint"] = "L1 Exploring"
        run = self.start()
        self.backend.submit_emails.side_effect = RuntimeError("mock HTTP down")
        with patch("apps.crm.durable_sync.resolve_mailbox_address", return_value=self.mailbox.address), patch("apps.crm.durable_sync.scoped_message_pages", return_value=[["submission-retry"]]), patch("apps.crm.durable_sync.read_email", return_value=raw), patch("apps.crm.durable_sync.bailian_extraction_provider", return_value=raw["facts"]):
            result = sync_persisted(run, object(), self.backend)
        finish_run(run.pk, run.lease_token, result)
        self.assertFalse(Email.objects.exists())
        self.assertEqual(StoredMessage.objects.get().submission["facts"], raw["facts"])
        self.backend.submit_emails.side_effect = self.submit
        retry_run(self.owner, run.pk)
        retry = claim_run(self.owner)
        with patch("apps.crm.durable_sync.resolve_mailbox_address", return_value=self.mailbox.address), patch("apps.crm.durable_sync.read_email") as reader, patch("apps.crm.durable_sync.bailian_extraction_provider") as model:
            result = sync_persisted(retry, object(), self.backend)
        finish_run(retry.pk, retry.lease_token, result)
        reader.assert_not_called()
        model.assert_not_called()
        self.assertEqual(StoredMessage.objects.get().status, "completed")
        self.assertEqual(Email.objects.count(), 1)

    # Function: Verifies that an independent Worker schedules manual repair without requiring a sync click.
    # Inputs: No external parameters; only re-extraction and the profiling task blocked by it exist.
    # Outputs: The model is called once, failure persists, and `--once` is not indefinitely blocked by profiling.
    # Logic: Calls the real shared management command, discovers employees from the database, and replaces only the model boundary.
    # Constraints: The test explicitly enables Agent mode while disallowing real Gmail/LLM calls.
    @override_settings(ANALYSIS_PROVIDER="agent")
    def test_worker_dispatches_repair_without_sync_and_once_exits(self):
        data = self.payload("worker-repair")
        data.update(extract_status="failed", facts=None, extract_error="事实抽取失败。")
        ingestion.submit_emails(self.owner, [data])
        email = Email.objects.get(pk=data["dedupe_key"])
        review_email(self.owner, email.pk, "confirmed_business", email.review_revision)
        with patch("apps.crm.lineage.bailian_extraction_provider", side_effect=RuntimeError("mock model failure")) as model:
            call_command("crm_worker", once=True)
        model.assert_called_once()
        self.assertEqual(email.repairs.get().status, "failed")
