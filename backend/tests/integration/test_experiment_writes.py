"""Responsibility: Verifies transactions, permissions, idempotency, and subsequent cleanup for cross-account experiment maintenance.
Implementation: Real PostgreSQL fixtures and Session/Tool/Agent HTTP; all writes are limited to the isolated database.
Relationships: `experiment_writes`, `experiments`, `agent_tools`, `chat.tool_reads`, and `seed_kg_lab`.
Directory:
- ExperimentWriteTests: Maintenance permission integration verification.
- ExperimentWriteTests.setUp: Creates two accounts and a complete batch.
- ExperimentWriteTests.call: Maintains through the unified HTTP entry point.
- ExperimentWriteTests.row: Reads an exact shared row.
- ExperimentWriteTests.test_crud_replay_conflict_cleanup: Cross-account CRUD, idempotency, old fingerprints, and cleanup.
- ExperimentWriteTests.test_boundaries_and_references: Private rows, ownership, cross-batch relations, and deletion-reference protection.
- ExperimentWriteTests.test_update_all_allowed_models: Verifies valid maintenance and integrity for each model.
- ExperimentWriteTests.test_tool_scope_and_chat_mutation: Frozen credentials and request-bound Agent maintenance receipts.
- ExperimentWriteTests.test_cleanup_after_relation_edit: Remains cleanly removable after a relation is changed to a later-created record.
- ExperimentWriteConcurrencyTests: Concurrent-write verification using independent database connections.
- ExperimentWriteConcurrencyTests.test_same_fingerprint_has_one_winner: Only one operation with the same old fingerprint may succeed.
- ExperimentWriteConcurrencyTests.test_same_fingerprint_has_one_winner.write: Uses independent connections for competing writes.
Variable index:
- None
"""

import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from django.apps import apps
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.agent_tools.models import ToolCredential
from apps.chat import services
from apps.crm.models import AgentCredential
from apps.crm.access import Conflict
from apps.sales.experiment_writes import WRITE_MODELS, mutate
from apps.sales.experiments import APPROVED_BATCHES, load_batch, table_rows
from apps.sales.management.commands.seed_kg_lab import run_delete, verify_manifest
from apps.sales.models import Conversation, Product
from integrations.salesmate_tools.read_contract import EXPERIMENT_WRITE_TOOLS
from tests.integration.test_experiments import ExperimentTests


# Function: Verifies experiment-business maintenance by every logged-in account.
# Logic: Real requests cover the shared write entry point and all writable models; no external Worker runs.
# Constraints: Uses isolated database and temporary attachments; does not prove production external-model planning quality.
class ExperimentWriteTests(TestCase):
    # Function: Creates real related test data with different ownership.
    # Inputs: Test database and temporary directory.
    # Outputs: `owner`, `reader`, `manifest`, `private`, `base`, and `client` instance state.
    # Logic: Reuses complete 44-table fixtures; the writing account differs from the ownership account.
    # Constraints: Each case rolls back its transaction; attachments are registered for cleanup by the baseline builder.
    def setUp(self):
        ExperimentTests.setUp(self)

    # Function: Submits a maintenance request with an idempotency key.
    # Inputs: `operation` is the action, `model` the model, `status` the expected status, `key` the optional idempotency key, and `args` business parameters.
    # Outputs: Raw response dictionary.
    # Logic: Authenticates with the current client; failed assertions include the response for diagnosis.
    # Constraints: Does not bypass schema or handlers and does not retry automatically.
    def call(self, operation, model, status=200, key=None, **args):
        response = self.client.post("/api/v1/agent-tools/call/", {"name": "experiments." + operation,
            "arguments": {"batch": APPROVED_BATCHES[0], "model": model, **args},
            "idempotency_key": str(key or uuid.uuid4())}, format="json")
        self.assertEqual(response.status_code, status, response.data)
        return response.data

    # Function: Reads the shared projection in the target table.
    # Inputs: `model` is the model and `pk` is an optional exact primary key.
    # Outputs: One fingerprinted row.
    # Logic: Calls the actual experiment-read endpoint.
    # Constraints: Applies only to records that already exist.
    def row(self, model, pk=None):
        response = self.client.get(self.base + model + "/", {"pk": pk} if pk else {})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data["results"][0]

    # Function: Verifies complete CRUD, idempotency, and removability.
    # Inputs: A new account and a batch owned by another account.
    # Outputs: Counts are restored, three operations are audited, ownership remains unchanged, old fingerprints are rejected, and cleanup preview succeeds.
    # Logic: Adds a product, replays the same idempotency key, reads across accounts, rejects the old version after modification, then deletes it.
    # Constraints: Does not delete existing test scenarios; operates only on the new product.
    def test_crud_replay_conflict_cleanup(self):
        key = uuid.uuid4()
        data = {"sku": "KGSEED-new-product", "name": "共享新增", "currency": "USD", "unit_price": "3.50"}
        first = self.call("create", "sales.Product", key=key, data=data)
        replay = self.call("create", "sales.Product", key=key, data=data)
        self.assertTrue(replay["replayed"])
        row = first["data"]["record"]
        self.assertEqual(row["owner"]["id"], self.owner.pk)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.row("sales.Product", row["pk"])["fields"]["name"], "共享新增")
        self.client.force_authenticate(self.reader)
        updated = self.call("update", "sales.Product", pk=row["pk"], expected=row["fingerprint"], data={"name": "已修改"})["data"]["record"]
        self.call("update", "sales.Product", 409, pk=row["pk"], expected=row["fingerprint"], data={"name": "过期提交"})
        self.call("delete", "sales.Product", pk=row["pk"], expected=updated["fingerprint"])
        self.assertFalse(Product.objects.filter(pk=row["pk"]).exists())
        manifest = load_batch(APPROVED_BATCHES[0]).changes
        self.assertEqual(len(manifest["mutations"]), 3)
        self.assertEqual(manifest["original_rows"], self.manifest["rows"])
        self.assertEqual(verify_manifest(manifest), manifest["table_counts"])
        self.assertEqual(run_delete(self.owner, APPROVED_BATCHES[0], False)["action"], "delete_preview")

    # Function: Verifies exact authorization and reference protection.
    # Inputs: A shared customer, private customer, and non-business safety model.
    # Outputs: Out-of-scope actions are rejected and manifest content is unchanged.
    # Logic: Rejects private primary keys, owner changes, foreign keys to private customers, identity models, and deletion of referenced customers.
    # Constraints: Errors do not disclose private bodies or trigger cascades.
    def test_boundaries_and_references(self):
        company = self.row("crm.Company")
        self.call("update", "crm.Company", 404, pk=str(self.private.pk), expected="0" * 64, data={"name": "越界"})
        self.call("update", "crm.Company", 400, pk=company["pk"], expected=company["fingerprint"], data={"owner_id": self.reader.pk})
        self.call("create", "crm.Contact", 400, data={"company_id": str(self.private.pk), "email": "test@example.invalid"})
        self.call("delete", "crm.Company", 409, pk=company["pk"], expected=company["fingerprint"])
        self.call("create", "accounts.User", 403, data={"username": "forbidden"})
        self.assertEqual(load_batch(APPROVED_BATCHES[0]).changes, self.manifest)
        self.client.force_authenticate(None)
        self.call("create", "sales.Product", 401, data={})

    # Function: Verifies every declared writable model can actually be saved.
    # Inputs: The first complete fictional record for each table.
    # Outputs: All 28 models succeed; fingerprints match the manifest and original ownership remains.
    # Logic: Empty-field updates verify original model constraints and version increments without mocks.
    # Constraints: Does not change business values; advances only maintenance audit and model version in the isolated database.
    def test_update_all_allowed_models(self):
        for model in sorted(WRITE_MODELS):
            with self.subTest(model=model):
                row = self.row(model)
                result = self.call("update", model, pk=row["pk"], expected=row["fingerprint"], data={})
                self.assertEqual(result["data"]["record"]["owner"], row["owner"])
        entry = load_batch(APPROVED_BATCHES[0])
        self.assertEqual(verify_manifest(entry.changes), entry.changes["table_counts"])

    # Function: Verifies the real maintenance chain for Tool scope and built-in Agent.
    # Inputs: A read-only token, subsequent explicit write authorization, and a chat request in progress.
    # Outputs: Old authorization is rejected; it succeeds after authorization, and Agent replay modifies only once and saves a stable receipt.
    # Logic: HTTP Tool authentication and Agent authentication run separately; model decisions are outside this case.
    # Constraints: Does not mistake a Tool token for an Agent token and does not call a real model.
    def test_tool_scope_and_chat_mutation(self):
        credential = ToolCredential.objects.create(owner=self.reader, name="write-test",
            digest=hashlib.sha256(b"write-test-token").hexdigest(), allowed_tools=["experiments.rows"],
            expires_at=timezone.now() + timedelta(hours=1))
        row = self.row("crm.Company")
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION="Tool write-test-token")
        args = {"pk": row["pk"], "expected": row["fingerprint"], "data": {"name": "由工具修改"}}
        self.call("update", "crm.Company", 403, **args)
        credential.allowed_tools = sorted(EXPERIMENT_WRITE_TOOLS)
        credential.save(update_fields=["allowed_tools"])
        row = self.call("update", "crm.Company", **args)["data"]["record"]
        conversation = Conversation.objects.create(owner=self.reader)
        request, _ = services.submit(self.reader, {"conversation_id": str(conversation.pk),
            "content": "请修改 KGSEED 虚构客户名称", "client_key": str(uuid.uuid4())})
        services.claim(self.reader)
        AgentCredential.objects.create(owner=self.reader, name="write-agent", digest=hashlib.sha256(b"write-agent-token").hexdigest())
        self.client.credentials(HTTP_AUTHORIZATION="Agent write-agent-token")
        payload = {"request_id": str(request.pk), "name": "experiments.update", "arguments": {
            "batch": APPROVED_BATCHES[0], "model": "crm.Company", "pk": row["pk"],
            "expected": row["fingerprint"], "data": {"name": "由聊天修改"}}}
        for index in range(2):
            response = self.client.post("/api/v1/agent/chat/tool-reads/", payload, format="json")
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data["replayed"], bool(index))
            self.assertEqual(response.data["evidence_items"][0]["source_type"], "experiment_mutation")
        self.assertEqual(len(load_batch(APPROVED_BATCHES[0]).changes["mutations"]), 2)

    # Function: Verifies batch-deletion order after editing a foreign key.
    # Inputs: An original quote line and a later-created product.
    # Outputs: The batch is completely deleted, manifest and attachments are cleaned up, and non-batch customers remain.
    # Logic: Changes the earlier-created line to reference the new product, triggering a dependency that the original reverse-creation order cannot handle.
    # Constraints: Performs real deletion only in the isolated database and does not modify the production manifest.
    def test_cleanup_after_relation_edit(self):
        product = self.call("create", "sales.Product", data={"sku": "cleanup-new", "name": "清理测试", "currency": "USD", "unit_price": "1.00"})["data"]["record"]
        line = self.row("sales.QuoteLine")
        self.call("update", "sales.QuoteLine", pk=line["pk"], expected=line["fingerprint"], data={"product_id": product["pk"]})
        result = run_delete(self.owner, APPROVED_BATCHES[0], True)
        self.assertEqual(result["action"], "deleted")
        self.assertTrue(type(self.private).objects.filter(pk=self.private.pk).exists())
        self.assertFalse(Product.objects.filter(pk=product["pk"]).exists())
        for row in self.manifest["rows"]:
            self.assertFalse(apps.get_model(row["model"]).objects.filter(pk=row["pk"]).exists())


# Function: Verifies concurrent-version boundaries for shared records.
# Logic: Two real connections submit the same fingerprint simultaneously, checking row locks and manifest reread after commit.
# Constraints: Does not mock locks or the database and does not access external services.
class ExperimentWriteConcurrencyTests(TransactionTestCase):
    # Function: Ensures two competing updates cannot overwrite each other.
    # Inputs: A complete test batch and two simultaneously started maintenance requests.
    # Outputs: Exactly one succeeds and one returns 409; audit appends once and manifest verification succeeds.
    # Logic: A barrier synchronizes request start; the service's owner and manifest locks ensure the latest fingerprint is read.
    # Constraints: Each thread uses an independent connection, waits at most ten seconds, and does not retry failures automatically.
    def test_same_fingerprint_has_one_winner(self):
        ExperimentTests.setUp(self)
        row = table_rows(load_batch(APPROVED_BATCHES[0]), "crm.Company")[0]
        barrier = Barrier(2)

        # Function: Submits one concurrent update.
        # Inputs: `name` is the new customer name; the closure reads the batch, reader, old fingerprint, and synchronization barrier.
        # Outputs: A 200 or 409 status integer.
        # Logic: Calls the real maintenance transaction in an independent connection and closes it in `finally`.
        # Constraints: Captures only expected version conflicts; unknown exceptions propagate to the test.
        def write(name):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                mutate(self.reader, "update", APPROVED_BATCHES[0], "crm.Company", {"name": name}, row["pk"], row["fingerprint"])
                return 200
            except Conflict:
                return 409
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(write, name) for name in ("并发甲", "并发乙")]
            self.assertEqual(sorted(future.result(timeout=10) for future in futures), [200, 409])
        manifest = load_batch(APPROVED_BATCHES[0]).changes
        self.assertEqual(len(manifest["mutations"]), 1)
        self.assertEqual(verify_manifest(manifest), manifest["table_counts"])
