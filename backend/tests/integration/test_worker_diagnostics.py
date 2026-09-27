"""Responsibility: Verify that synchronization Worker failures are locatable without leaking sensitive exception text.
Implementation: Store real runs in isolated database; mock external authorization and execution failures to verify established terminal states and no-retry semantics.
Relationships: Covers crm.worker, processing, and dispatch; does not call real mailbox, Agent, or model.
Directory:
- WorkerDiagnosticsTests: Worker diagnostics regression tests.
- WorkerDiagnosticsTests.setUp: Create synthetic worker and mailbox.
- WorkerDiagnosticsTests.test_authorization_failure_records_stage_without_secrets: Retain terminal state and safely record authorization failure.
- WorkerDiagnosticsTests.test_invalid_grant_preserves_reauthorization_guidance: Expose a failed pre-discovery authorization separately from empty successful synchronization.
- WorkerDiagnosticsTests.test_sync_failure_records_execution_stage: Distinguish synchronization execution failure from authorization stage.
- WorkerDiagnosticsTests.test_report_failure_preserves_both_locations: Retain both error locations when failure reporting is rejected.
- WorkerDiagnosticsTests.test_error_chain_omits_messages: Exception chain excludes secrets and business body.
- WorkerDiagnosticsTests.test_idle_connection_loss_does_not_fail_completed_sync: Closing idle database connection does not misclassify completed synchronization.
- WorkerDiagnosticsTests.test_broken_connection_failure_revokes_identity: Reestablish cleanup connection after failure, revoke temporary identity, and report failure.
- close_idle_connection: Simulate database closing idle connection during long external call.
- fail_with_closed_connection: Simulate external-call failure after idle connection closure.
Variable index:
- None
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, OperationalError
from django.test import TransactionTestCase

from apps.crm import worker
from agent.tools.gmail import GmailReauthorizationRequired
from apps.crm.models import AgentCredential, GmailCredential, Mailbox
from apps.crm.processing import request_run


# Function: Simulate idle database connection closure at successful external-sync boundary.
# Inputs: `run`, `service`, and `backend` are original Worker call parameters used only to match replacement signature.
# Outputs: Completed result; underlying PostgreSQL connection is closed.
# Logic: Actually close the underlying connection for this test thread rather than mock ORM success.
# Constraints: Applies only to isolated test database; does not modify connection parameters or send mailbox requests.
def close_idle_connection(run, service, backend):
    connection.connection.close()
    return {"status": "completed"}


# Function: Simulate idle connection failure together with external authorization exception.
# Inputs: `credentials` is a fictional authorization placeholder.
# Outputs: None; closes underlying test-database connection then raises OperationalError.
# Logic: Expose whether identity cleanup and failure persistence reuse a closed connection.
# Constraints: Do not print credentials or stop server or other test connections.
def fail_with_closed_connection(credentials):
    connection.connection.close()
    raise OperationalError("synthetic closed connection")


# Function: Check real run state and safe logs.
# Logic: Execute original Worker and persistence, replacing external calls only; failure triggers no real network.
# Constraints: Test cannot prove production credentials work or replay historical business tasks.
class WorkerDiagnosticsTests(TransactionTestCase):
    # Function: Establish test mailbox and explicit synchronization scope.
    # Inputs: No external parameters; reads isolated test database.
    # Outputs: `owner`, `mailbox`, and `run` instances.
    # Logic: Create queued run through existing API; 20 messages is an established test-fixture condition.
    # Constraints: Do not change product defaults or use real credentials.
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="diagnostics")
        self.mailbox = Mailbox.objects.create(owner=self.owner, address="diagnostics@example.test")
        GmailCredential.objects.create(mailbox=self.mailbox, credentials={"mock": True})
        self.run = request_run(self.owner, self.mailbox.pk, sync_options={"max_messages": 20})

    # Function: Verify authorization failure remains explicit and marks the failure stage.
    # Inputs: No external parameters; inject exception containing fake token.
    # Outputs: Failed status, one authorization call, no remaining identity, and no token in logs.
    # Logic: Retain real claiming and finish_run, checking diagnostics do not change failure semantics.
    # Constraints: Prohibit actual mailbox or model calls.
    def test_authorization_failure_records_stage_without_secrets(self):
        with patch.object(worker, "create_service_from_authorization", side_effect=RuntimeError("secret-access-token")) as authorize, patch.object(worker, "sync_persisted") as sync:
            with self.assertLogs(worker.logger, level="ERROR") as logged:
                self.assertTrue(worker.run_sync(self.owner))
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, "failed")
        self.assertEqual(self.run.error["code"], "worker_sync_failed")
        authorize.assert_called_once()
        sync.assert_not_called()
        self.assertFalse(AgentCredential.objects.exists())
        output = "\n".join(logged.output)
        self.assertIn("stage=gmail_authorize", output)
        self.assertIn("worker.py:run_sync:", output)
        self.assertNotIn("secret-access-token", output)

    # Function: Persist actionable guidance when OAuth fails before any messages are discovered.
    # Inputs: Queued real test run and a mocked typed grant rejection containing a synthetic secret.
    # Outputs: Failed batch with zero jobs, safe reauthorization code/message, no sync calls or leaked secret.
    # Logic: Execute real worker, identity cleanup and finish_run; compare mailbox and batch error projections.
    # Constraints: No Google or model calls; no implicit retry, credential deletion, or reclassification as success.
    def test_invalid_grant_preserves_reauthorization_guidance(self):
        with patch.object(worker, "create_service_from_authorization", side_effect=GmailReauthorizationRequired("secret-refresh-token")) as authorize, patch.object(worker, "sync_persisted") as sync:
            with self.assertLogs(worker.logger, level="ERROR") as logged:
                self.assertTrue(worker.run_sync(self.owner))
        self.run.refresh_from_db()
        self.mailbox.refresh_from_db()
        self.assertEqual(self.run.status, "failed")
        self.assertEqual(self.run.email_jobs.count(), 0)
        self.assertEqual(self.run.error["code"], "gmail_reauthorization_required")
        self.assertIn("重新授权 Gmail 收信", self.run.error["message"])
        self.assertEqual(self.mailbox.sync_state["error"], self.run.error["message"])
        self.assertNotIn("secret-refresh-token", str(self.run.error) + str(logged.output))
        self.assertTrue(GmailCredential.objects.filter(mailbox=self.mailbox).exists())
        self.assertFalse(AgentCredential.objects.exists())
        authorize.assert_called_once()
        sync.assert_not_called()

    # Function: Distinguish execution error after authorization.
    # Inputs: No external parameters; mock successful authorization and synchronization failure.
    # Outputs: gmail_sync stage, failed terminal state, and one execution.
    # Logic: Execute complete identity lifecycle, replacing network boundary only.
    # Constraints: Do not add automatic retry after failure.
    def test_sync_failure_records_execution_stage(self):
        with patch.object(worker, "create_service_from_authorization", return_value=(object(), None)), patch.object(worker, "sync_persisted", side_effect=ValueError("private-email-body")) as sync:
            with self.assertLogs(worker.logger, level="ERROR") as logged:
                self.assertTrue(worker.run_sync(self.owner))
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, "failed")
        sync.assert_called_once()
        self.assertIn("stage=gmail_sync", "\n".join(logged.output))
        self.assertNotIn("private-email-body", "\n".join(logged.output))

    # Function: Retain locations for both original failure and report failure.
    # Inputs: No external parameters; mock authorization exception and failure-report exception.
    # Outputs: Two events, original stage and report_failure stage, without exception text.
    # Logic: Mock finish_run as unwritable; do not fabricate a persisted failed terminal state.
    # Constraints: This boundary relies on established lease-expiry handling and adds no retry.
    def test_report_failure_preserves_both_locations(self):
        with patch.object(worker, "create_service_from_authorization", side_effect=RuntimeError("secret-a")), patch.object(worker, "finish_run", side_effect=RuntimeError("secret-b")) as finish:
            with self.assertLogs(worker.logger, level="ERROR") as logged:
                self.assertTrue(worker.run_sync(self.owner))
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, "running")
        finish.assert_called_once()
        output = "\n".join(logged.output)
        self.assertIn("stage=gmail_authorize", output)
        self.assertIn("stage=report_failure", output)
        self.assertNotIn("secret-a", output)
        self.assertNotIn("secret-b", output)

    # Function: Ensure nested exceptions have locatable information without sensitive content.
    # Inputs: No external parameters; construct explicit cause chain.
    # Outputs: Two exception types and code location without either message body.
    # Logic: Directly test diagnostic extraction function rather than log-formatting mock.
    # Constraints: Do not read traceback local variables or source text.
    def test_error_chain_omits_messages(self):
        try:
            try:
                raise ValueError("secret-inner")
            except ValueError as cause:
                raise RuntimeError("secret-outer") from cause
        except RuntimeError as error:
            location = worker.error_location(error)
        self.assertIn("RuntimeError[", location)
        self.assertIn("ValueError[", location)
        self.assertIn("test_worker_diagnostics.py:test_error_chain_omits_messages:", location)
        self.assertNotIn("secret-", location)

    # Function: Verify completed external work is not incorrectly marked failed after old database connection expires.
    # Inputs: Successful result and closed idle PostgreSQL connection.
    # Outputs: Run completed, all temporary credentials revoked, and synchronization executes once.
    # Logic: Actually execute ending transaction and identity cleanup.
    # Constraints: Not a test of retrying synchronization or changing connection lifetime.
    def test_idle_connection_loss_does_not_fail_completed_sync(self):
        with patch.object(worker, "create_service_from_authorization", return_value=(object(), None)), patch.object(worker, "sync_persisted", side_effect=close_idle_connection) as sync:
            self.assertTrue(worker.run_sync(self.owner))
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, "completed")
        self.assertFalse(AgentCredential.objects.exists())
        sync.assert_called_once()

    # Function: Verify cleanup and failure report complete on a new connection after failure.
    # Inputs: Underlying connection closed before authorization exception.
    # Outputs: Run failed, no valid temporary credential, and authorization called once.
    # Logic: Retain real ORM and mock external-call error only.
    # Constraints: Do not present an still-unreachable database as recoverable.
    def test_broken_connection_failure_revokes_identity(self):
        with patch.object(worker, "create_service_from_authorization", side_effect=fail_with_closed_connection) as authorize:
            self.assertTrue(worker.run_sync(self.owner))
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, "failed")
        self.assertFalse(AgentCredential.objects.exists())
        authorize.assert_called_once()
