"""Responsibility: Verify shared-scheduler fairness, employee HTTP isolation, and credential revocation.
Implementation: Gmail test batches explicitly select at most 20 messages, not a runtime default. Use isolated PostgreSQL and a local HTTP test server; mock mail/models while executing real permission/lease code.
Relationships: dispatch, worker, crm_worker, and AgentAuthentication; no real mailbox or LLM connections.
Directory:
- SharedWorkerTests: Shared Worker integration tests.
- SharedWorkerTests.setUp: Create two employees without service credentials and their mailboxes.
- SharedWorkerTests.test_round_robin_and_inactive_owner: Fair rotation excluding inactive employees.
- SharedWorkerTests.test_parallel_clients_are_scoped_and_revoked: Concurrent clients reject cross-employee access and revoke identities.
- SharedWorkerTests.test_exception_revokes_identity: Revoke identities on exceptional exit.
- SharedWorkerTests.test_shared_command_drains_two_owners: Real scheduling and HTTP synchronization handle two new employees.
- SharedWorkerTests.test_analysis_uses_selected_owner: Company-job claiming uses the selected employee identity.
- SharedWorkerTests.test_expired_sync_fails_without_retry: Shared scheduling explicitly terminates expired-lease batches.
- SharedWorkerTests.test_concurrent_claim_is_unique: Two executors cannot claim one batch twice.
- exercise_sync: Query batch-mailbox state through real local HTTP and return synthetic results.
- claim_in_thread: Claim one batch through an independent database connection.
Variable index:
- None
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connections
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone

from agent.clients.backend_api import BackendRequestError
from apps.crm import dispatch, worker
from apps.crm.models import AgentCredential, Company, GmailCredential, Job, Mailbox, MailboxSyncRun
from apps.crm.processing import claim_run, request_run


# Function: Mock mailbox reads while retaining real HTTP verification of current-batch access.
# Inputs: `run` is the claimed batch, `service` a placeholder, and `backend` an independent client.
# Outputs: Synthetic success summary.
# Logic: Request current mailbox state; an incorrect employee identity is rejected by HTTP.
# Constraints: No Google/LLM calls or synthetic mail writes.
def exercise_sync(run, service, backend):
    backend.get_sync_state(str(run.mailbox_id))
    return {"status": "completed"}


# Function: Attempt claiming in an independent thread and close its database connection.
# Inputs: `owner` identifies the target employee.
# Outputs: Batch primary key or None.
# Logic: Call real transactional claiming and release the thread connection in finally.
# Constraints: Isolated test database only; transaction locks are not mocked.
def claim_in_thread(owner):
    try:
        run = claim_run(owner)
        return run.pk if run else None
    finally:
        connections.close_all()


# Function: Verify shared scheduling and authentication boundaries end to end across employees.
# Logic: Use local LiveServer, preserving real authentication while replacing only external mail/models.
# Constraints: Passing does not establish real Gmail/QQ authorization; all data is synthetic and isolated.
@override_settings(ANALYSIS_PROVIDER="agent")
class SharedWorkerTests(LiveServerTestCase):
    # Function: Prepare two employees without preconfigured service tokens.
    # Inputs: Framework-created isolated database and localhost HTTP service.
    # Outputs: owners and mailboxes; temporarily override connection settings and restore automatically.
    # Logic: Set invalid legacy employee token/mailbox environment values to verify explicit task identity takes precedence without environment mutation.
    # Constraints: Connect only to localhost and use no real credentials.
    def setUp(self):
        self.owners = [get_user_model().objects.create_user(username=f"shared-{i}") for i in range(2)]
        self.mailboxes = [Mailbox.objects.create(owner=owner, address=f"shared-{owner.pk}@example.test") for owner in self.owners]
        for mailbox in self.mailboxes:
            GmailCredential.objects.create(mailbox=mailbox, credentials={"mock": True})
        environment = patch.dict(os.environ, {
            "SALESMATE_BACKEND_AGENT_URL": self.live_server_url + "/api/v1/agent/",
            "SALESMATE_AGENT_SERVICE_TOKEN": "unused-legacy-token",
            "SALESMATE_MAILBOX_ID": "unused-legacy-mailbox",
            "NO_PROXY": "localhost,127.0.0.1",
        })
        environment.start()
        self.addCleanup(environment.stop)

    # Function: Verify a continuously queued first employee does not prevent selection of others.
    # Inputs: Two employees each have a queued batch.
    # Outputs: Rotation is first/second/first; after deactivation only active employees are selected.
    # Logic: Keep batches queued to verify cursor selection independently of completion order. Each Gmail fixture explicitly selects 20 messages; employee-order/activity assertions remain unchanged.
    # Constraints: Do not claim work or change established concurrency limits.
    def test_round_robin_and_inactive_owner(self):
        for owner, mailbox in zip(self.owners, self.mailboxes):
            request_run(owner, mailbox.pk, sync_options={"max_messages": 20})
        first = dispatch.next_owner("sync")
        second = dispatch.next_owner("sync", first.pk)
        self.assertEqual([first.pk, second.pk], [owner.pk for owner in self.owners])
        self.assertEqual(dispatch.next_owner("sync", second.pk).pk, first.pk)
        first.is_active = False
        first.save(update_fields=["is_active"])
        self.assertEqual(dispatch.next_owner("sync", second.pk).pk, second.pk)

    # Function: Simultaneous task clients retain separate identities and become invalid afterward.
    # Inputs: Temporary credentials for two employees and real local HTTP requests.
    # Outputs: Own mailboxes are readable, cross-mailbox requests return 404, and old tokens return 401 after context exit; environment values remain unchanged.
    # Logic: Concurrent requests verify instance isolation through real AgentAuthentication.
    # Constraints: Never print credentials or use mocked authentication as permission evidence.
    def test_parallel_clients_are_scoped_and_revoked(self):
        with dispatch.scoped_backend(self.owners[0], str(self.mailboxes[0].pk)) as left, dispatch.scoped_backend(self.owners[1], str(self.mailboxes[1].pk)) as right:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(client.get_sync_state, str(mailbox.pk)) for client, mailbox in zip([left, right], self.mailboxes)]
                for future in futures:
                    self.assertIsInstance(future.result(), dict)
            for client, foreign in [(left, self.mailboxes[1]), (right, self.mailboxes[0])]:
                with self.assertRaises(BackendRequestError) as error:
                    client.get_sync_state(str(foreign.pk))
                self.assertEqual(error.exception.status_code, 404)
            self.assertEqual(AgentCredential.objects.count(), 2)
        self.assertFalse(AgentCredential.objects.exists())
        with self.assertRaises(BackendRequestError) as error:
            left.get_sync_state(str(self.mailboxes[0].pk))
        self.assertEqual(error.exception.status_code, 401)
        left.close()
        self.assertEqual(os.environ["SALESMATE_AGENT_SERVICE_TOKEN"], "unused-legacy-token")
        self.assertEqual(os.environ["SALESMATE_MAILBOX_ID"], "unused-legacy-mailbox")

    # Function: Execution failure leaves no valid temporary identity.
    # Inputs: A synthetic exception inside the temporary-identity context.
    # Outputs: The exception propagates and the credential is deleted.
    # Logic: Keep lifecycle implementation intact and verify revocation through actual database queries.
    # Constraints: No mailbox calls or retries.
    def test_exception_revokes_identity(self):
        with self.assertRaises(RuntimeError):
            with dispatch.scoped_backend(self.owners[0]) as backend:
                self.assertIsNone(backend.mailbox_id)
                raise RuntimeError("synthetic failure")
        self.assertFalse(AgentCredential.objects.exists())

    # Function: Reproduce new-employee queues and verify the shared command automatically handles both employees.
    # Inputs: Two employees unbound to old environment tokens and their queued batches.
    # Outputs: Both batches complete with start times; all temporary credentials are revoked.
    # Logic: Use real thread-pool scheduling/claiming and mock only Gmail networking/models, not HTTP employee authentication. Each employee explicitly selects 20 messages; the mocked Worker verifies shared scheduling and command exit.
    # Constraints: No sending or changes to default command concurrency/polling.
    def test_shared_command_drains_two_owners(self):
        runs = [request_run(owner, mailbox.pk, sync_options={"max_messages": 20}) for owner, mailbox in zip(self.owners, self.mailboxes)]
        with patch("apps.crm.worker.create_service_from_authorization", return_value=(object(), None)), patch("apps.crm.worker.sync_persisted", side_effect=exercise_sync):
            call_command("crm_worker", once=True)
        for run in runs:
            run.refresh_from_db()
            self.assertEqual(run.status, "completed")
            self.assertIsNotNone(run.started_at)
        self.assertFalse(AgentCredential.objects.exists())

    # Function: Verify profile discovery and HTTP claiming remain scoped to the selected employee.
    # Inputs: Each of two employees has one company and pending Job.
    # Outputs: The second employee's client claims only that employee's job; the first remains pending.
    # Logic: Replace LLM orchestration with actual client claim_jobs while executing the Worker's full identity lifecycle.
    # Constraints: No analysis-model calls; retain running leases for assertions without mocking permission filters.
    def test_analysis_uses_selected_owner(self):
        work = []
        for owner in self.owners:
            company = Company.objects.create(owner=owner, name=f"company-{owner.pk}", group_key=f"owner-{owner.pk}.test")
            work.append(Job.objects.create(company=company, trigger="test", revision=company.revision))
        self.assertEqual(dispatch.next_owner("analysis", self.owners[0].pk).pk, self.owners[1].pk)
        with patch("apps.crm.worker.process_jobs_once", side_effect=lambda backend, limit: backend.claim_jobs(limit)):
            self.assertTrue(worker.run_analysis(self.owners[1]))
        for job in work:
            job.refresh_from_db()
        self.assertEqual([job.status for job in work], ["pending", "running"])
        self.assertFalse(AgentCredential.objects.exists())

    # Function: Verify shared scheduling finds expired batches and fails them explicitly without rereading mail automatically.
    # Inputs: One running batch with an expired lease.
    # Outputs: failed state, worker_interrupted error, and no schedulable sync work.
    # Logic: Run actual scheduling/work units and assert the Gmail branch is not entered. Create a batch explicitly scoped to 20 messages, then simulate expiry without changing failure semantics.
    # Constraints: Retain existing explicit-retry semantics.
    def test_expired_sync_fails_without_retry(self):
        run = request_run(self.owners[0], self.mailboxes[0].pk, sync_options={"max_messages": 20})
        MailboxSyncRun.objects.filter(pk=run.pk).update(status="running", lease_until=timezone.now() - timedelta(seconds=1))
        owner = dispatch.next_owner("sync")
        with patch("apps.crm.worker.create_service_from_authorization") as gmail:
            self.assertFalse(worker.run_sync(owner))
        gmail.assert_not_called()
        run.refresh_from_db()
        self.assertEqual(run.error["code"], "worker_interrupted")
        self.assertEqual(run.status, "failed")
        self.assertIsNone(dispatch.next_owner("sync"))

    # Function: Verify shared multiprocess deployment retains real database exclusion.
    # Inputs: One queued batch for an employee, claimed competitively by two independent threads.
    # Outputs: Only one thread obtains the batch ID.
    # Logic: Real PostgreSQL row locks and state rechecks prevent duplicate claims. Both contend for one explicitly scoped 20-message batch; repeated queuing does not replace concurrent-claim testing.
    # Constraints: Do not mock database locks or claim unlimited-load validation.
    def test_concurrent_claim_is_unique(self):
        run = request_run(self.owners[0], self.mailboxes[0].pk, sync_options={"max_messages": 20})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim_in_thread, [self.owners[0], self.owners[0]]))
        self.assertCountEqual(results, [run.pk, None])
