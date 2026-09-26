"""Responsibility: Validate vector isolation, execution modes, and manual migration diagnostics.
Implementation: Vector tests use the real PostgreSQL extension; message boundaries use a mocked broker and do not call real mail or a model.
Relationships: vectors.services、common.execution/tasks、check_release_migrations.
Directory:
- VectorTests: Actual vector read/write tests.
- VectorTests.setUp: Create an isolated employee.
- VectorTests.test_search_isolates_owner_model_and_dimension: Validate similarity and ownership isolation.
- VectorTests.test_update_and_dimension_change: Validate update and model-dimension contracts.
- VectorTests.test_invalid_vectors_and_inactive_owner: Validate invalid input and disabled employees.
- ExecutionTests: Task transport and manual-migration diagnostic tests.
- ExecutionTests.test_local_mode_never_contacts_broker: Local execution does not access the message service.
- ExecutionTests.test_remote_only_sends_identifiers: Remote execution sends only database identifiers.
- ExecutionTests.test_publish_failure_does_not_run_locally: Publication failure has no local fallback.
- ExecutionTests.test_sales_remote_waits_without_local_side_effect: Sales invokes the remote task only once.
- ExecutionTests.test_release_gate_rejects_destructive_operations: Reject destructive migrations.
Variable index:
- None
"""
from unittest.mock import Mock, patch
from django.contrib.auth import get_user_model
from django.db import migrations, models
from django.test import SimpleTestCase, TestCase, override_settings
from apps.vectors.services import put_document, search_documents, validate_vector
from apps.vectors.models import VectorDocument
from apps.crm.management.commands.check_release_migrations import compatible
from apps.crm.worker import run_sync
from common.execution import CeleryExecutor, execute_sales, work_executor


# Function: Validate retrieval and write boundaries using real vector SQL.
# Logic: Use the test database and synthetic text without depending on an embedding service.
# Constraints: Passing does not prove real business-embedding quality.
class VectorTests(TestCase):
    # Function: Create two mutually isolated employees.
    # Inputs: Test database.
    # Outputs: Stores `left` and `right` employee instances.
    # Logic: Does not create mailboxes or external credentials.
    # Constraints: The test framework rolls back transactions.
    def setUp(self):
        self.left = get_user_model().objects.create_user(username="vector-left")
        self.right = get_user_model().objects.create_user(username="vector-right")

    # Function: Verifies similarity ordering cannot read another employee's or model's data.
    # Inputs: Synthesizes two models and documents for two employees.
    # Outputs: Only current-employee neighbors of the same model appear, at expected distance.
    # Logic: Uses orthogonal vectors so real SQL cosine results are verifiable.
    # Constraints: Does not mock database or vector operations.
    def test_search_isolates_owner_model_and_dimension(self):
        for owner, model, source, vector in [(self.left, "m1", "near", [1, 0]), (self.left, "m1", "far", [0, 1]), (self.right, "m1", "secret", [1, 0]), (self.left, "m2", "other", [1, 0, 0])]:
            put_document(owner=owner, namespace="test", model=model, source=source, content=source, embedding=vector)
        result = search_documents(owner=self.left, namespace="test", model="m1", embedding=[1, 0])
        self.assertEqual([item["source"] for item in result], ["near", "far"])
        self.assertAlmostEqual(result[0]["distance"], 0)
        self.assertAlmostEqual(result[1]["distance"], 1)
        self.assertEqual(search_documents(owner=self.left, namespace="test", model="m1", embedding=[1, 0, 0]), [])

    # Function: Verifies document updates create no duplicates and dimension changes require an explicit model-version change.
    # Inputs: Two writes for the same source and model.
    # Outputs: One new text record and matching summary; dimension change fails.
    # Logic: Queries the real uniqueness constraint and saved result.
    # Constraints: Does not automatically migrate existing embeddings.
    def test_update_and_dimension_change(self):
        args = dict(owner=self.left, namespace="test", model="m1", source="one", embedding=[1, 0])
        first = put_document(**args, content="old")
        second = put_document(**args, content="new")
        self.assertEqual(first.pk, second.pk)
        self.assertNotEqual(first.content_hash, second.content_hash)
        self.assertEqual(VectorDocument.objects.count(), 1)
        with self.assertRaises(ValueError):
            put_document(**{**args, "embedding": [1, 0, 0]}, content="new")

    # Function: Rejects invalid vectors and access by deactivated employees.
    # Inputs: Zero, non-finite, oversized vectors and a deactivated employee.
    # Outputs: `ValueError` or user-not-found error.
    # Logic: Directly verifies input and service-layer permission checks.
    # Constraints: Does not turn failure into an empty search result.
    def test_invalid_vectors_and_inactive_owner(self):
        for vector in ([], [0, 0], [float("nan")], [float("inf")], [1e100]):
            with self.assertRaises(ValueError):
                validate_vector(vector)
        self.left.is_active = False
        self.left.save(update_fields=["is_active"])
        with self.assertRaises(get_user_model().DoesNotExist):
            search_documents(owner=self.left, namespace="test", model="m1", embedding=[1, 0])


# Function: Verifies task-adapter and publication boundaries.
# Logic: Mocks message transport and checks result waiting, error propagation, and parameter allowlisting.
# Constraints: Real Redis message round trips are separately verified by CI and deployment `check_infrastructure`.
class ExecutionTests(SimpleTestCase):
    # Function: Verifies local mode preserves the original execution path.
    # Inputs: Explicit local configuration and side-effect-free function.
    # Outputs: Thread result; broker is never called.
    # Logic: Actually enters the thread executor.
    # Constraints: Does not require local Redis or Celery.
    @override_settings(TASK_EXECUTION_MODE="local")
    def test_local_mode_never_contacts_broker(self):
        with patch("common.tasks.execute.apply_async") as publish:
            with work_executor(1, "test") as executor:
                self.assertEqual(executor.submit(int, "7").result(), 7)
            publish.assert_not_called()

    # Function: Ensures remote messages contain neither employee objects nor authorization codes.
    # Inputs: Employee primary key and mocked Celery completion result.
    # Outputs: Strict `sync` type and integer primary key; result is cleared.
    # Logic: Checks one publication and emptied context.
    # Constraints: Does not trigger real synchronization.
    def test_remote_only_sends_identifiers(self):
        with patch("common.tasks.execute.apply_async") as publish:
            publish.return_value.get.return_value = True
            publish.return_value.ready.return_value = True
            with CeleryExecutor() as executor:
                self.assertTrue(executor.submit(run_sync, Mock(pk=6)).result())
            publish.assert_called_once_with(args=["sync", 6], queue="crm", retry=False)
            publish.return_value.forget.assert_called_once_with()

    # Function: Ensures infrastructure errors introduce no implicit fallback.
    # Inputs: Simulates broker publication failure.
    # Outputs: The original exception propagates.
    # Logic: Executor submission does not call the business function.
    # Constraints: Does not retry automatically.
    def test_publish_failure_does_not_run_locally(self):
        with patch("common.tasks.execute.apply_async", side_effect=ConnectionError("synthetic")) as publish:
            with self.assertRaises(ConnectionError), CeleryExecutor() as executor:
                executor.submit(run_sync, Mock(pk=6))
            self.assertEqual(publish.call_count, 1)

    # Function: Ensures sales tasks are not executed locally again after remote success.
    # Inputs: Celery mode, action identifier, and mocked result.
    # Outputs: One publication and zero local-function calls.
    # Logic: Clears state after waiting for remote result.
    # Constraints: Does not send email.
    @override_settings(TASK_EXECUTION_MODE="celery")
    def test_sales_remote_waits_without_local_side_effect(self):
        local = Mock()
        with patch("common.tasks.execute.apply_async") as publish:
            publish.return_value.ready.return_value = True
            execute_sales("action-id", local)
            publish.assert_called_once_with(args=["sales", "action-id"], queue="sales", retry=False)
            publish.return_value.get.assert_called_once_with()
        local.assert_not_called()

    # Function: Verifies manual migration diagnostics still identify destructive and arbitrary-code operations.
    # Inputs: Real Django migration operation instances.
    # Outputs: New tables and nullable fields pass; deletion, SQL, and non-null fields are rejected.
    # Logic: Directly invokes the optional diagnostic's conservative decision; does not establish that automatic deployment retains this gate.
    # Constraints: Does not execute supplied SQL.
    def test_release_gate_rejects_destructive_operations(self):
        self.assertTrue(compatible(migrations.CreateModel("NewTable", [])))
        self.assertTrue(compatible(migrations.AddField("item", "extra", models.TextField(null=True))))
        for operation in (migrations.DeleteModel("Item"), migrations.RunSQL("SELECT 1"), migrations.AddField("item", "extra", models.TextField())):
            self.assertFalse(compatible(operation))
