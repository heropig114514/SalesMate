"""Responsibility: Validate bounded Gmail synchronization, limiting before deduplication, and prohibition of implicit full synchronization.
Implementation: Mock the Gmail SDK to validate the default limit of 50 messages and explicit approval for excess; persistent tests use an isolated database and the real batch service. Agent exceptions retain their current English diagnostics.
Relationships: gmail_scope selects messages, sync_scope freezes conditions, and durable_sync/processing perform persistent synchronization.
Directory:
- GmailScopeLogicTests: Scope, pagination, and CLI-boundary tests that require no database.
- GmailScopeLogicTests.setUp: Create fixed scopes and SDK mocks.
- GmailScopeLogicTests.test_latest_limit_stops_before_older_page: Truncate to the newest message count and constrain request size.
- GmailScopeLogicTests.test_days_filter_and_combined_limit: Day-window query and intersection limits.
- GmailScopeLogicTests.test_duplicate_ids_do_not_expand_latest_selection: Duplicate IDs across pages do not reserve capacity twice.
- GmailScopeLogicTests.test_missing_or_invalid_scope_never_lists_mail: No scope or invalid window reads no mailbox.
- GmailScopeLogicTests.test_invalid_response_and_page_cycles_fail: Invalid responses and pagination loops fail explicitly.
- GmailScopeLogicTests.test_network_failure_does_not_retry: Network exceptions propagate without implicit retry.
- GmailScopeLogicTests.test_snapshot_is_second_precise_and_validated: Scope freezing and invalid-parameter rejection.
- GmailScopeLogicTests.test_cli_deduplicates_before_reading: A one-shot Agent deduplicates before reading bodies and does not read the old cursor.
- GmailScopeLogicTests.test_cli_rejects_unbounded_legacy_claim: A legacy claim with no scope does not trigger Gmail reads.
- GmailScopeLogicTests.test_days_only_stops_at_fifty: A days-only scope also stops pagination at 50 messages.
- GmailScopeLogicTests.test_large_sync_requires_specific_approval: Excess requests without approval do not read; approved requests remain bound by their explicit message count.
- GmailScopeLogicTests.test_snapshot_freezes_cap_and_approval: The backend freezes the default limit and approval flag.
- GmailScopeLogicTests.test_explicit_retry_cannot_bypass_approval: Explicit CLI retries enforce the same excess-approval limit.
- GmailScopePersistenceTests: Database-dependent entry and deduplication tests.
- GmailScopePersistenceTests.setUp: Create an employee, authorized mailbox, and browser.
- GmailScopePersistenceTests.test_http_requires_scope_and_isolates_owner: HTTP rejects an empty scope and validates ownership.
- GmailScopePersistenceTests.test_active_run_rejects_replacement: Repeated submission does not overwrite the frozen scope.
- GmailScopePersistenceTests.test_scoped_sync_skips_completed_failed_and_outside_pending: Limit first, then skip terminal states without draining backlog outside the scope.
- GmailScopePersistenceTests.test_legacy_unbounded_run_fails_without_listing: A legacy queued batch without scope rejects full synchronization.
- GmailScopePersistenceTests.test_large_sync_requires_approval_and_preserves_it_on_retry: Excess queueing and explicit retry retain the approved scope.
Variable index:
- None
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TransactionTestCase
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from agent.tools.gmail_scope import gmail_message_limit, scoped_message_pages
from agent.workflows.authorized_gmail_sync import sync_authorized_mailboxes_once
from agent.workflows.gmail_sync import sync_gmail
from apps.crm.durable_sync import sync_persisted
from apps.crm.models import GmailCredential, Mailbox, MailboxSyncRun, StoredMessage
from apps.crm.processing import claim_run, finish_run, request_run, retry_run
from apps.crm.sync_scope import SyncRequestSerializer, snapshot


# Function: Validate Gmail scope boundaries that do not depend on persistent storage.
# Logic: Mock SDK responses and check request parameters, read count, and error propagation.
# Constraints: Does not connect to Google, an LLM, or a database; mocked results do not prove real-account integration.
class GmailScopeLogicTests(SimpleTestCase):
    # Function: Prepare reproducible frozen windows and SDK state.
    # Inputs: No external arguments.
    # Outputs: options, service, listing, and execute test state.
    # Logic: Freeze the window to whole UTC seconds and mock only message-list boundaries.
    # Constraints: Days are unspecified and message count is explicitly 3, rather than the product default.
    def setUp(self):
        self.options = {"recent_days": None, "max_messages": 3, "since": None, "until": "2026-09-20T00:00:00+00:00"}
        self.service = Mock()
        self.listing = self.service.users.return_value.messages.return_value.list
        self.execute = self.listing.return_value.execute

    # Function: Validate that scanning older pages stops once the newest N messages reaches the limit.
    # Inputs: No external inputs; the first response contains 4 IDs and a next page.
    # Outputs: Return only 3 IDs with one list call.
    # Logic: Even when the SDK returns excess, truncate locally and keep query size no greater than remaining capacity.
    # Constraints: Does not read bodies or depend on estimated total mailbox size.
    def test_latest_limit_stops_before_older_page(self):
        self.execute.return_value = {"messages": [{"id": str(i)} for i in range(4)], "nextPageToken": "older"}
        self.assertEqual(list(scoped_message_pages(self.service, self.options)), [["0", "1", "2"]])
        self.assertEqual(self.listing.call_count, 1)
        self.assertEqual(self.listing.call_args.kwargs["maxResults"], 3)
        self.assertIn("{in:inbox in:sent}", self.listing.call_args.kwargs["q"])

    # Function: Validate that days filter on the Google side and dual limits use their intersection.
    # Inputs: No external inputs; a 7-day window and two SDK response pages.
    # Outputs: The query contains second-level bounds and the next page is limited to the remaining message count.
    # Logic: Traverse pages within the days-only window; dual limits stop on the third message.
    # Constraints: Use the frozen queueing time rather than execution-time current time.
    def test_days_filter_and_combined_limit(self):
        for limit in (None, 3):
            with self.subTest(limit=limit):
                self.listing.reset_mock()
                self.execute.side_effect = [{"messages": [{"id": "a"}, {"id": "b"}], "nextPageToken": "p2"}, {"messages": [{"id": "c"}]}]
                options = {**self.options, "recent_days": 7, "since": "2026-09-13T00:00:00+00:00", "max_messages": limit}
                self.assertEqual(list(scoped_message_pages(self.service, options)), [["a", "b"], ["c"]])
                since = int(datetime.fromisoformat(options["since"]).timestamp())
                until = int(datetime.fromisoformat(options["until"]).timestamp())
                self.assertEqual(self.listing.call_args.kwargs["q"], f"{{in:inbox in:sent}} before:{until} after:{since}")
                self.assertEqual(self.listing.call_args.kwargs["maxResults"], 1 if limit else 20)

    # Function: Validate that the same ID across pages is selected only once.
    # Inputs: No external inputs; two overlapping pages and the second reaches the limit.
    # Outputs: Select three unique IDs in total and do not request a third page.
    # Logic: API duplicate-ID deduplication is independent of business deduplication for already synchronized data.
    # Constraints: Do not subtract already synchronized records from the message limit to enlarge scan scope.
    def test_duplicate_ids_do_not_expand_latest_selection(self):
        self.execute.side_effect = [{"messages": [{"id": "a"}, {"id": "b"}], "nextPageToken": "p2"}, {"messages": [{"id": "b"}, {"id": "c"}], "nextPageToken": "p3"}]
        self.assertEqual(list(scoped_message_pages(self.service, self.options)), [["a", "b"], ["c"]])
        self.assertEqual(self.listing.call_count, 2)

    # Function: Validate that unbounded or invalid scope cannot initiate a list request.
    # Inputs: No external inputs; empty scope, non-positive integers, missing window, and a timezone-naive window.
    # Outputs: Every input raises ValueError and the SDK is not called.
    # Logic: Validate scope before any network call.
    # Constraints: Do not interpret a corrupted legacy batch as the default scope.
    def test_missing_or_invalid_scope_never_lists_mail(self):
        for options in ({}, None, {**self.options, "max_messages": 0}, {**self.options, "max_messages": True}, {**self.options, "max_messages": 1.5}, {**self.options, "until": None}, {**self.options, "recent_days": 7}, {**self.options, "until": "2026-09-20T00:00:00"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                list(scoped_message_pages(self.service, options))
        self.listing.assert_not_called()

    # Function: Validate explicit failure for invalid responses and pagination loops.
    # Inputs: No external inputs; invalid list structure, empty ID, invalid token, and cyclic pages.
    # Outputs: RuntimeError with the Agent's pagination-loop diagnostic; a cyclic page does not produce a second duplicate message batch.
    # Logic: Validate the response before yielding; pagination tokens must make progress.
    # Constraints: Do not silently treat it as completed synchronization.
    def test_invalid_response_and_page_cycles_fail(self):
        for response in (None, {"messages": {}}, {"messages": [{"id": ""}]}, {"nextPageToken": 12}):
            self.execute.return_value = response
            with self.subTest(response=response), self.assertRaises(RuntimeError):
                list(scoped_message_pages(self.service, self.options))
        self.execute.side_effect = [{"messages": [{"id": "a"}], "nextPageToken": "p2"}, {"messages": [{"id": "b"}], "nextPageToken": "p2"}]
        with self.assertRaisesRegex(RuntimeError, "pagination loop"):
            list(scoped_message_pages(self.service, self.options))

    # Function: Validate that Google network exceptions do not implicitly retry or fall back to full synchronization.
    # Inputs: No external inputs; mock the first request to raise a network error.
    # Outputs: The same error propagates and there is only one call.
    # Logic: The scope selector has no retry branch.
    # Constraints: Does not simulate real network recovery.
    def test_network_failure_does_not_retry(self):
        self.execute.side_effect = RuntimeError("network unavailable")
        with self.assertRaisesRegex(RuntimeError, "network unavailable"):
            list(scoped_message_pages(self.service, self.options))
        self.assertEqual(self.execute.call_count, 1)

    # Function: Validate API validation and second-level scope freezing.
    # Inputs: No external inputs; fixed queueing time with microseconds and valid/invalid limits.
    # Outputs: Exact seven-day window; empty values, invalid integers, and client time fields are rejected.
    # Logic: Execute actual serialization and snapshotting; QQ default precision remains unchanged.
    # Constraints: The clock is mocked and production defaults are not changed.
    def test_snapshot_is_second_precise_and_validated(self):
        now = datetime(2026, 9, 20, 12, 0, 0, 123456, tzinfo=timezone.utc)
        with patch("apps.crm.sync_scope.timezone.now", return_value=now):
            scope = snapshot({"recent_days": 7}, gmail=True)
            self.assertEqual(scope["until"], now.replace(microsecond=0).isoformat())
            self.assertEqual(scope["since"], (now.replace(microsecond=0) - timedelta(days=7)).isoformat())
            self.assertEqual(snapshot({"max_messages": 2})["until"], now.isoformat())
        for data in ({}, {"sync_options": {}}, {"sync_options": {"max_messages": -1}}, {"sync_options": {"recent_days": 1.5}}, {"sync_options": {"max_messages": 1, "until": "2026-09-20"}}):
            self.assertFalse(SyncRequestSerializer(data=data).is_valid(), data)
        with self.assertRaises(ValidationError):
            snapshot({"recent_days": 10**12})

    # Function: Validate that the debug Agent also deduplicates before reading bodies.
    # Inputs: No external inputs; within scope, one stored and one unknown message; legacy state also has pending and failed IDs outside scope.
    # Outputs: Read only the unknown ID, do not call old History selection logic; retain old failure but do not count it in this batch’s failure count.
    # Logic: Execute actual scope selection and lookup; mock empty original-text result to isolate L1.
    # Constraints: Does not claim that actual backend writes or model calls passed.
    def test_cli_deduplicates_before_reading(self):
        backend = Mock()
        backend.get_sync_state.return_value = {"cursor": "old", "scope": {"pending_message_ids": ["outside"], "failed_message_ids": ["old-failure"]}, "version": 0}
        backend.get_stored_email.side_effect = [{"saved": True}, None]
        self.execute.return_value = {"messages": [{"id": "done"}, {"id": "new"}]}
        authorization = {"mailbox_id": "mb1", "access_token": "mock", "mailbox_address": "sales@example.com", "sync_options": self.options}
        with patch("agent.workflows.gmail_sync.resolve_mailbox_address", return_value="sales@example.com"), patch("agent.workflows.gmail_sync.read_messages", return_value=[]) as reader, patch("agent.workflows.gmail_sync._read_email_batch") as legacy:
            result = sync_gmail(authorization, backend=backend, gmail_factory=lambda _: self.service)
        self.assertEqual(reader.call_args.args[1], ["new"])
        self.assertEqual(result["duplicate_count"], 1)
        self.assertEqual(result["failed_email_count"], 0)
        self.assertEqual(backend.save_sync_state.call_args.args[0]["scope"]["failed_message_ids"], ["old-failure"])
        legacy.assert_not_called()

    # Function: Validate that pre-upgrade queued CLI work without scope cannot trigger synchronization.
    # Inputs: No external inputs; claim response carries authorization but lacks scope and explicit IDs.
    # Outputs: Report failure and do not construct a Gmail client.
    # Logic: Reject legacy work before credential or network boundaries.
    # Constraints: Independent company-task simulation is empty and does not trigger real analysis.
    def test_cli_rejects_unbounded_legacy_claim(self):
        backend = Mock()
        backend.claim_mailbox_syncs.return_value = [{"mailbox_id": "mb1", "authorization": {"mock": True}}]
        with patch("agent.workflows.authorized_gmail_sync.create_service_from_authorization") as create, patch("agent.workflows.authorized_gmail_sync.process_jobs_once", return_value=[]):
            reports = sync_authorized_mailboxes_once(backend=backend)
        self.assertEqual(reports[0]["status"], "failed")
        create.assert_not_called()

    # Function: Validate that a days-only query does not scan beyond the default 50 messages.
    # Inputs: No external inputs; at least 80 messages in the window, SDK returns 20 per page and successor pages.
    # Outputs: Select only 50 messages; the final request has a capacity of 10 and does not access the fourth page.
    # Logic: Execute the actual selector and use excess-response mocks to verify local truncation.
    # Constraints: 50 is the user-specified product threshold; the established page size of 20 remains unchanged.
    def test_days_only_stops_at_fifty(self):
        self.execute.side_effect = [{"messages": [{"id": str(i)} for i in range(start, start + 20)], "nextPageToken": f"p{start}"} for start in (0, 20, 40, 60)]
        options = {**self.options, "max_messages": None, "recent_days": 7, "since": "2026-09-13T00:00:00+00:00"}
        pages = list(scoped_message_pages(self.service, options))
        self.assertEqual([len(page) for page in pages], [20, 20, 10])
        self.assertEqual(self.listing.call_count, 3)
        self.assertEqual(self.listing.call_args.kwargs["maxResults"], 10)

    # Function: Validate that approval permits only the selected explicit excess message count.
    # Inputs: No external inputs; 75 messages under unapproved, explicitly rejected, and approved conditions.
    # Outputs: Explicit approval-required diagnostic and no list request when unapproved; after approval select at most 75 messages.
    # Logic: Use real validation and pagination; approval cannot make scope unlimited.
    # Constraints: Google response mocks do not prove a real network latency limit.
    def test_large_sync_requires_specific_approval(self):
        for approval in ({}, {"allow_large_sync": False}):
            with self.assertRaisesRegex(ValueError, "approve the exact count"):
                list(scoped_message_pages(self.service, {**self.options, "max_messages": 75, **approval}))
        self.listing.assert_not_called()
        self.execute.side_effect = [{"messages": [{"id": str(i)} for i in range(start, start + 20)], "nextPageToken": f"p{start}"} for start in (0, 20, 40, 60)]
        pages = list(scoped_message_pages(self.service, {**self.options, "max_messages": 75, "allow_large_sync": True}))
        self.assertEqual(sum(map(len, pages)), 75)
        self.assertEqual(self.listing.call_args.kwargs["maxResults"], 15)
        for options in ({"allow_large_sync": True}, {"max_messages": 75, "allow_large_sync": "true"}):
            with self.assertRaises(ValueError):
                gmail_message_limit(options)

    # Function: Validate server default limit and approved persistent payload.
    # Inputs: No external inputs; days-only, 50 messages, 51 messages, and a request containing only the approval flag.
    # Outputs: Default/boundary is 50, an unapproved 51 is rejected, and an approved 51 retains both the limit and flag.
    # Logic: Execute actual snapshot and serialization; original QQ scope semantics remain unchanged.
    # Constraints: Even with approval, synchronization cannot run with no user scope.
    def test_snapshot_freezes_cap_and_approval(self):
        self.assertEqual(snapshot({"recent_days": 7}, gmail=True)["max_messages"], 50)
        self.assertEqual(snapshot({"max_messages": 50}, gmail=True)["max_messages"], 50)
        for approval in ({}, {"allow_large_sync": False}):
            with self.assertRaises(ValidationError):
                snapshot({"max_messages": 51, **approval}, gmail=True)
        approved = snapshot({"max_messages": 51, "allow_large_sync": True}, gmail=True)
        self.assertEqual((approved["max_messages"], approved["allow_large_sync"]), (51, True))
        self.assertFalse(SyncRequestSerializer(data={"sync_options": {"allow_large_sync": True}}).is_valid())
        self.assertIsNone(snapshot({"recent_days": 7})["max_messages"])

    # Function: Validate that explicit retry cannot bypass approval validation.
    # Inputs: No external inputs; 51 explicit message IDs, once without and once with current approval.
    # Outputs: Without approval it fails and reads no bodies; with approval it reads the explicit 51 IDs.
    # Logic: Execute the actual sync_gmail entry point, isolating model and bodies with empty responses.
    # Constraints: Retain original synchronization parameters and failure-report format; do not split or retry automatically.
    def test_explicit_retry_cannot_bypass_approval(self):
        backend = Mock()
        backend.get_sync_state.return_value = {"cursor": None, "scope": {}, "version": 0}
        ids = [str(i) for i in range(51)]
        authorization = {"mailbox_id": "mb1", "access_token": "mock", "mailbox_address": "sales@example.com", "sync_options": {"max_messages": 51}}
        with patch("agent.workflows.gmail_sync.resolve_mailbox_address", return_value="sales@example.com"), patch("agent.workflows.gmail_sync.read_messages", return_value=[]) as reader:
            failed = sync_gmail(authorization, backend=backend, gmail_factory=lambda _: self.service, message_ids=ids)
            self.assertEqual(failed["status"], "failed")
            reader.assert_not_called()
            authorization["sync_options"]["allow_large_sync"] = True
            result = sync_gmail(authorization, backend=backend, gmail_factory=lambda _: self.service, message_ids=ids)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(reader.call_args.args[1], ids)


# Function: Validate actual database queueing, permissions, and deduplication within scope.
# Logic: Use an isolated TransactionTestCase database with actual serialization and transactions; do not execute Gmail bodies or a model.
# Constraints: Requires an available configured database; mocks cannot replace persistent validation.
class GmailScopePersistenceTests(TransactionTestCase):
    # Function: Create an independent employee and authorized mailbox.
    # Inputs: No external arguments.
    # Outputs: owner, mailbox, and browser test state.
    # Logic: Credentials are used only to pass the local authorization-existence check.
    # Constraints: Does not generate real Google authorization.
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="gmail-scope")
        self.mailbox = Mailbox.objects.create(owner=self.owner, address="sales@scope.example")
        GmailCredential.objects.create(mailbox=self.mailbox, credentials={"mock": True})
        self.browser = APIClient()
        self.browser.force_authenticate(self.owner)

    # Function: Validate required HTTP scope and employee isolation.
    # Inputs: No external inputs; an empty scope and a valid scoped request from another employee.
    # Outputs: Respectively return 400 and 404; neither creates a batch.
    # Logic: Call the actual request-sync entry point.
    # Constraints: Does not trigger a Worker or network access.
    def test_http_requires_scope_and_isolates_owner(self):
        path = f"/api/v1/mailboxes/{self.mailbox.pk}/request-sync/"
        for data in ({}, {"sync_options": {}}, {"sync_options": {"recent_days": None, "max_messages": None}}):
            self.assertEqual(self.browser.post(path, data, format="json").status_code, 400)
        self.browser.force_authenticate(get_user_model().objects.create_user(username="scope-other"))
        self.assertEqual(self.browser.post(path, {"sync_options": {"max_messages": 3}}, format="json").status_code, 404)
        self.assertFalse(MailboxSyncRun.objects.exists())

    # Function: Validate that repeated clicks do not change an active batch’s scope.
    # Inputs: No external inputs; first select 7 days, then try selecting 500 messages.
    # Outputs: First response is 202, subsequent response is 409, and the database retains the unique original batch.
    # Logic: Real mailbox row locking protects queueing; verify frozen JSON is not replaced.
    # Constraints: test_shared_worker covers cross-connection concurrent-lock testing.
    def test_active_run_rejects_replacement(self):
        path = f"/api/v1/mailboxes/{self.mailbox.pk}/request-sync/"
        first = self.browser.post(path, {"sync_options": {"recent_days": 7}}, format="json")
        self.assertEqual(first.status_code, 202)
        self.assertEqual(self.browser.post(path, {"sync_options": {"max_messages": 500}}, format="json").status_code, 409)
        self.assertEqual(MailboxSyncRun.objects.get().sync_options, first.data["sync_options"])

    # Function: Validate that completed/failed emails in the latest scope do not backfill earlier emails.
    # Inputs: No external inputs; the newest three emails include completed, failed, and new, with backlog outside scope.
    # Outputs: Process only the new email; neither the next page nor pending work outside scope is processed.
    # Logic: Use the actual Gmail selector, real database deduplication and registration, and mocked original-text processing boundary.
    # Constraints: Failure requires explicit retry; an old prompt version also does not cause ordinary synchronization to re-extract.
    def test_scoped_sync_skips_completed_failed_and_outside_pending(self):
        for message_id, status in [("done", "completed"), ("failed", "failed"), ("outside", "pending")]:
            StoredMessage.objects.create(mailbox=self.mailbox, message_id=message_id, status=status, prompt_version="old")
        request_run(self.owner, self.mailbox.pk, sync_options={"max_messages": 3})
        run = claim_run(self.owner)
        service = Mock()
        listing = service.users.return_value.messages.return_value.list
        listing.return_value.execute.return_value = {"messages": [{"id": value} for value in ["done", "failed", "new"]], "nextPageToken": "older"}
        with patch("apps.crm.durable_sync.resolve_mailbox_address", return_value=self.mailbox.address), patch("apps.crm.durable_sync.process_page") as process:
            result = sync_persisted(run, service, Mock())
        self.assertEqual([record.message_id for record in process.call_args.args[3]], ["new"])
        self.assertEqual(list(run.email_jobs.values_list("gmail_message_id", flat=True)), ["new"])
        self.assertEqual(result["duplicate_count"], 2)
        self.assertEqual(listing.call_count, 1)

    # Function: Validate that a legacy queued task without scope does not restore full scanning.
    # Inputs: No external inputs; directly construct a pre-upgrade batch with empty scope.
    # Outputs: ValueError and no SDK message-list call.
    # Logic: After claiming a batch, it must use the same scope selector.
    # Constraints: Mock only profile identity confirmation; do not process historical backlog.
    def test_legacy_unbounded_run_fails_without_listing(self):
        MailboxSyncRun.objects.create(mailbox=self.mailbox)
        run = claim_run(self.owner)
        service = Mock()
        with patch("apps.crm.durable_sync.resolve_mailbox_address", return_value=self.mailbox.address), self.assertRaises(ValueError):
            sync_persisted(run, service, Mock())
        service.users.assert_not_called()

    # Function: Validate that excess HTTP requests carry current approval and retries retain the same approved scope.
    # Inputs: No external inputs; the same employee first submits unapproved 51 messages, then approved 51 messages.
    # Outputs: Rejected request is not queued; approved request returns 202, and failure retry does not enlarge approved scope.
    # Logic: Use actual entry point, transactions, and state transitions; mock batch failure to check snapshot inheritance.
    # Constraints: Does not connect to real Gmail or infer that a different reselected scope is approved.
    def test_large_sync_requires_approval_and_preserves_it_on_retry(self):
        path = f"/api/v1/mailboxes/{self.mailbox.pk}/request-sync/"
        self.assertEqual(self.browser.post(path, {"sync_options": {"max_messages": 51}}, format="json").status_code, 400)
        self.assertFalse(MailboxSyncRun.objects.exists())
        response = self.browser.post(path, {"sync_options": {"max_messages": 51, "allow_large_sync": True}}, format="json")
        self.assertEqual(response.status_code, 202)
        run = claim_run(self.owner)
        self.assertTrue(run.sync_options["allow_large_sync"])
        finish_run(run.pk, run.lease_token, {"status": "failed"})
        retried = retry_run(self.owner, run.pk)
        self.assertEqual(retried.sync_options, run.sync_options)
