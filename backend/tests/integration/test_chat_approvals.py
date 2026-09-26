"""Responsibility: Verify chat writes cannot execute before an independent user decision.
Implementation: Exercise real PostgreSQL transactions, Session/Agent authentication, frozen versions, decision replay, cancellation, and competing browser decisions using synthetic fixtures.
Relationships: chat.approvals, tool_reads, services, and the existing experiment fixture builder; no external model, scheduler, or provider calls.
Directory:
- ChatApprovalTests: Approval enforcement and state-transition tests.
- ChatApprovalTests.setUp: Build synthetic data, a processing question, and separate clients.
- ChatApprovalTests.propose: Submit one concrete write through the Agent endpoint.
- ChatApprovalTests.decide: Send a browser decision to a frozen proposal.
- ChatApprovalTests.test_wait_blocks_execution_and_other_chat_work: Enforce suspension before mutation.
- ChatApprovalTests.test_approve_once_and_resume: Execute once and restore canonical continuation.
- ChatApprovalTests.test_reject_restores_chat_without_mutation: Cancel without changing data or previous messages.
- ChatApprovalTests.test_browser_identity_csrf_and_binding: Reject machine credentials, CSRF omissions, and wrong users/requests.
- ChatApprovalTests.test_expiry_and_changed_target: Reject obsolete approvals without implicit replanning.
- ChatApprovalTests.test_open_lab_still_requires_approval: Keep approval and version checks active in laboratory mode.
- ChatApprovalTests.test_failed_execution_rolls_back_decision: Retain pending state after business failure.
- ChatApprovalTests.test_unbound_tool_calls_remain_unchanged: Leave non-chat execution semantics unchanged.
- ChatApprovalTests.test_browser_crud_while_chat_waits: Keep traditional UI writes immediate during a pending chat approval.
- ChatApprovalTests.test_checkpoint_required_and_schema_changes: Fail closed for old Agents and changed contracts.
- ChatApprovalTests.test_delete_requires_approval: Keep a record until its explicit deletion is approved.
- ChatApprovalConcurrencyTests: Real transaction races for explicit decisions.
- ChatApprovalConcurrencyTests.test_competing_decisions: Two approving connections produce one mutation.
- ChatApprovalConcurrencyTests.test_competing_decisions.approve: Submit a decision on an independent connection.
Variable index:
- None
"""

import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace

from django.db import close_old_connections
from django.middleware.csrf import get_token
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.agent_tools import services as tool_services
from apps.chat import approvals, services, tool_reads
from apps.crm.models import AgentCredential
from apps.sales.experiments import APPROVED_BATCHES, load_batch, table_rows
from apps.sales.models import Conversation, Message, Product
from tests.integration.test_experiments import ExperimentTests


# Function: Validate the chat-specific gate against real database and HTTP behavior.
# Logic: Each case builds unrelated owner/reader identities and a frozen synthetic batch.
# Constraints: Transactional fixtures and test-only credentials; no production data or real LLM.
@override_settings(LAB_OPEN_ACCESS=False, WORKSPACE_OWNER_ONLY=False, LOCAL_DEBUG_AUTO_LOGIN=False)
class ChatApprovalTests(TestCase):
    # Function: Prepare a chat write with independent human and machine credentials.
    # Inputs: Test database and temporary file storage from ExperimentTests.
    # Outputs: Processing request, valid product arguments, checkpoint, Agent client, and browser Session client.
    # Logic: Bind approval to the question submitter and keep synthetic ownership distinct.
    # Constraints: Browser CSRF enforcement is tested separately; force_login creates a real session without a password flow.
    def setUp(self):
        ExperimentTests.setUp(self)
        self.conversation = Conversation.objects.create(owner=self.reader)
        self.request, _ = services.submit(self.reader, {"conversation_id": str(self.conversation.pk),
            "content": "新增虚构实验产品", "client_key": str(uuid.uuid4())})
        services.claim(self.reader)
        self.arguments = {"batch": APPROVED_BATCHES[0], "model": "sales.Product",
            "data": {"sku": "chat-approved-product", "name": "Reviewed product", "currency": "USD", "unit_price": "2.00"}}
        self.checkpoint = {"next_turn": 1, "observations": [], "signatures": []}
        AgentCredential.objects.create(owner=self.reader, name="approval-test", digest=hashlib.sha256(b"approval-test").hexdigest())
        self.agent = APIClient()
        self.agent.credentials(HTTP_AUTHORIZATION="Agent approval-test")
        self.browser = APIClient()
        self.browser.force_login(self.reader)

    # Function: Propose a mutation through the actual request-bound HTTP endpoint.
    # Inputs: Optional ``name`` and ``arguments`` select the exact pending write.
    # Outputs: Created approval ID; asserts 202 and no terminal answer.
    # Logic: Include the checkpoint required for durable continuation.
    # Constraints: Does not approve or call the business handler directly.
    def propose(self, name="experiments.create", arguments=None):
        response = self.agent.post("/api/v1/agent/chat/tool-reads/", {"request_id": str(self.request.pk),
            "name": name, "arguments": arguments or self.arguments, "continuation": self.checkpoint}, format="json")
        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(response.data["status"], "approval_required")
        return response.data["approval"]["id"]

    # Function: Send a concrete browser decision.
    # Inputs: ``approval_id`` and optional approve/reject ``decision``; browser Session supplies identity.
    # Outputs: Unmodified HTTP response for state/error assertions.
    # Logic: Submit only the decision, never replacement arguments or an approved flag.
    # Constraints: No retries; repeated calls in tests are deliberate idempotency checks.
    def decide(self, approval_id, decision="approve"):
        return self.browser.post(f"/api/v1/sales/chat/requests/{self.request.pk}/approvals/{approval_id}/decision/",
                                 {"decision": decision}, format="json")

    # Function: Check that a pending write suspends all further work in this request.
    # Inputs: Valid create request and checkpoint.
    # Outputs: No mutation, claim, read, answer, or second question is allowed while waiting.
    # Logic: Exercise the same endpoints used by Agent and browser.
    # Constraints: Read-only work before the approval remains valid; suspension begins atomically with proposal creation.
    def test_wait_blocks_execution_and_other_chat_work(self):
        self.propose()
        self.assertFalse(Product.objects.filter(sku="chat-approved-product").exists())
        self.assertEqual(self.request.tool_reads.count(), 0)
        self.assertIsNone(services.claim(self.reader))
        self.assertEqual(self.agent.post("/api/v1/agent/chat/tool-reads/", {"request_id": str(self.request.pk),
            "name": "experiments.catalog", "arguments": {}}, format="json").status_code, 409)
        self.assertEqual(self.agent.post("/api/v1/agent/chat/answers/", {"request_id": str(self.request.pk),
            "chat_prompt_version": "test", "assistant_text": "Done", "citations": [], "status": "completed", "error": None}, format="json").status_code, 409)
        self.assertEqual(self.browser.post("/api/v1/sales/chat/messages/", {"conversation_id": str(self.conversation.pk),
            "client_key": str(uuid.uuid4()), "content": "Another question"}, format="json").status_code, 409)

    # Function: Verify execution is atomic and repeated decisions do not repeat writes.
    # Inputs: One frozen product creation and two identical approve submissions.
    # Outputs: One product, one evidence receipt, and a claim containing the exact checkpoint and result.
    # Logic: Project a stale request after approval to verify consistent status, reclaim its checkpoint, then replay the decision after resumption.
    # Constraints: Tests backend continuation transport; the HTTP Agent round-trip test covers model continuation separately.
    def test_approve_once_and_resume(self):
        approval_id = self.propose()
        stale_request = services.request_for(self.reader, self.request.pk)
        self.assertEqual(self.decide(approval_id).status_code, 200)
        state = services.request_data(stale_request)
        self.assertEqual(state["status"], "pending")
        self.assertNotIn("approval", state)
        claim = services.claim(self.reader)
        self.assertEqual(claim["request_id"], str(self.request.pk))
        self.assertEqual(claim["resume"]["continuation"], self.checkpoint)
        self.assertEqual(claim["resume"]["tool_result"]["status"], "completed")
        self.assertEqual(self.decide(approval_id).status_code, 200)
        self.assertEqual(Product.objects.filter(sku="chat-approved-product").count(), 1)
        self.assertEqual(self.request.tool_reads.count(), 1)

    # Function: Reject without business mutation or deleting prior conversation context.
    # Inputs: Pending create request and explicit rejection.
    # Outputs: Cancelled task, preserved message, available next question, and no later approval.
    # Logic: Repeat rejection and attempt to reverse it, then submit new work.
    # Constraints: No automatic rollback of previously approved operations is implied.
    def test_reject_restores_chat_without_mutation(self):
        approval_id = self.propose()
        self.assertEqual(self.decide(approval_id, "reject").data["status"], "cancelled")
        self.assertEqual(self.decide(approval_id, "reject").status_code, 200)
        self.assertEqual(self.decide(approval_id).status_code, 409)
        self.assertFalse(Product.objects.filter(sku="chat-approved-product").exists())
        self.assertEqual(Message.objects.filter(conversation=self.conversation).count(), 1)
        response = self.browser.post("/api/v1/sales/chat/messages/", {"conversation_id": str(self.conversation.pk),
            "client_key": str(uuid.uuid4()), "content": "继续聊天"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)

    # Function: Verify approvals require the initiating browser user and CSRF.
    # Inputs: Agent-only client, missing-CSRF Session, another user, and an altered URL/payload.
    # Outputs: Unauthorized/rebound decisions fail; a Session with a matching CSRF token succeeds.
    # Logic: Exercise real authentication classes and closed request validation.
    # Constraints: No force_authenticate bypass is used for approval identities.
    def test_browser_identity_csrf_and_binding(self):
        approval_id = self.propose()
        url = f"/api/v1/sales/chat/requests/{self.request.pk}/approvals/{approval_id}/decision/"
        self.assertEqual(self.agent.post(url, {"decision": "approve"}, format="json").status_code, 403)
        csrf = APIClient(enforce_csrf_checks=True)
        csrf.force_login(self.reader)
        self.assertEqual(csrf.post(url, {"decision": "approve"}, format="json").status_code, 403)
        self.browser.force_login(self.owner)
        self.assertEqual(self.decide(approval_id).status_code, 404)
        self.browser.force_login(self.reader)
        self.assertEqual(self.decide(uuid.uuid4()).status_code, 404)
        self.assertEqual(self.browser.post(url, {"decision": "approve", "arguments": {}}, format="json").status_code, 400)
        self.assertFalse(Product.objects.filter(sku="chat-approved-product").exists())

        csrf_request = SimpleNamespace(META={})
        token = get_token(csrf_request)
        csrf.cookies["csrftoken"] = csrf_request.META["CSRF_COOKIE"]
        self.assertEqual(csrf.post(url, {"decision": "approve"}, format="json", HTTP_X_CSRFTOKEN=token).status_code, 200)

    # Function: Reject approvals whose deadline or reviewed target is obsolete.
    # Inputs: Existing experimental product followed by an independent valid write.
    # Outputs: Approval conflicts, no overwritten fields, and explicit rejection remains available.
    # Logic: Mutate the row through the existing non-chat service after proposal; also exercise expiration.
    # Constraints: No fingerprint is silently refreshed and no replacement proposal is generated.
    def test_expiry_and_changed_target(self):
        row = table_rows(load_batch(APPROVED_BATCHES[0]), "sales.Product")[0]
        arguments = {**self.arguments, "pk": row["pk"], "expected": row["fingerprint"], "data": {"name": "Pending"}}
        approval_id = self.propose("experiments.update", arguments)
        tool_services.invoke(self.reader, None, "experiments.update", {**arguments, "data": {"name": "Changed elsewhere"}}, str(uuid.uuid4()))
        self.assertEqual(self.decide(approval_id).status_code, 409)
        self.assertEqual(Product.objects.get(pk=row["pk"]).name, "Changed elsewhere")
        self.request.approvals.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.decide(approval_id).status_code, 409)
        self.assertEqual(self.decide(approval_id, "reject").status_code, 200)

    # Function: Keep the chat gate active even when ordinary laboratory writes are open.
    # Inputs: Laboratory setting, valid Agent request, and public selected-identity header.
    # Outputs: A real proposal is required and public laboratory identity cannot approve.
    # Logic: Enable laboratory mode only around the actual proposal and decision.
    # Constraints: The explicit laboratory override is a test of the gate, not a change to deployment configuration.
    def test_open_lab_still_requires_approval(self):
        with override_settings(LAB_OPEN_ACCESS=True):
            approval_id = self.propose()
            public = APIClient()
            response = public.post(f"/api/v1/sales/chat/requests/{self.request.pk}/approvals/{approval_id}/decision/",
                                   {"decision": "approve"}, format="json", HTTP_X_LAB_USER=self.reader.username)
            self.assertEqual(response.status_code, 403)
            self.assertFalse(Product.objects.filter(sku="chat-approved-product").exists())
            self.assertEqual(self.decide(approval_id).status_code, 200)

    # Function: Keep a failed business operation pending and unexecuted.
    # Inputs: A schema-valid proposal containing a business-forbidden field.
    # Outputs: 400, no evidence or mutation, pending approval, and successful explicit rejection.
    # Logic: Let the actual business validator fail inside the decision transaction.
    # Constraints: No mocked database or swallowed exception; the browser receives the actionable error.
    def test_failed_execution_rolls_back_decision(self):
        approval_id = self.propose(arguments={**self.arguments, "data": {"owner_id": self.owner.pk}})
        self.assertEqual(self.decide(approval_id).status_code, 400)
        self.assertEqual(self.request.approvals.get().status, "pending")
        self.assertEqual(self.request.tool_reads.count(), 0)
        self.assertEqual(self.decide(approval_id, "reject").status_code, 200)

    # Function: Verify ordinary non-chat handlers do not gain this approval requirement.
    # Inputs: Explicit existing Tool service call without a chat request.
    # Outputs: Completed product creation with no ChatApproval record.
    # Logic: Exercise the unchanged boundary used outside the conversation path.
    # Constraints: Does not claim all scheduled jobs were executed; their code paths are unchanged.
    def test_unbound_tool_calls_remain_unchanged(self):
        result = tool_services.invoke(self.reader, None, "experiments.create", self.arguments, str(uuid.uuid4()))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.request.approvals.count(), 0)

    # Function: Verify a waiting chat does not intercept ordinary UI maintenance.
    # Inputs: Pending chat creation and a logged-in browser using the existing Tool HTTP endpoint.
    # Outputs: Immediate create/update/delete results; the chat write stays pending and unexecuted.
    # Logic: Perform the same HTTP CRUD sequence used by the data-management UI with separate idempotency keys.
    # Constraints: Real database and Session authentication; only isolated synthetic records are changed.
    def test_browser_crud_while_chat_waits(self):
        self.propose()
        arguments = {**self.arguments, "data": {**self.arguments["data"], "sku": "ordinary-ui-product"}}
        for operation in ("create", "update", "delete"):
            response = self.browser.post("/api/v1/agent-tools/call/", {
                "name": "experiments." + operation, "arguments": arguments,
                "idempotency_key": str(uuid.uuid4()),
            }, format="json")
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data["status"], "completed")
            if operation != "delete":
                row = response.data["data"]["record"]
                arguments = {"batch": self.arguments["batch"], "model": "sales.Product",
                             "pk": row["pk"], "expected": row["fingerprint"]}
                if operation == "create":
                    arguments["data"] = {"name": "Changed through UI"}
        self.assertFalse(Product.objects.filter(sku__in=["ordinary-ui-product", "chat-approved-product"]).exists())
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, "awaiting_approval")
        self.assertEqual(self.request.approvals.count(), 1)
        self.assertEqual(self.request.approvals.get().status, "pending")

    # Function: Require resumable state and the same tool contract at approval time.
    # Inputs: Missing checkpoint and a subsequently changed stored schema.
    # Outputs: 400 before proposal creation, then 409 for a contract mismatch.
    # Logic: Legacy calls fail closed; modifying the test proposal's schema simulates a deployment contract change.
    # Constraints: No production schema changes or compatibility fallback.
    def test_checkpoint_required_and_schema_changes(self):
        response = self.agent.post("/api/v1/agent/chat/tool-reads/", {"request_id": str(self.request.pk),
            "name": "experiments.create", "arguments": self.arguments}, format="json")
        self.assertEqual(response.status_code, 400)
        approval_id = self.propose()
        self.request.approvals.update(schema={})
        self.assertEqual(self.decide(approval_id).status_code, 409)

    # Function: Guard deletion as well as creation and modification.
    # Inputs: A newly created synthetic product with no reverse references.
    # Outputs: The record exists while approval is pending and is removed once approved.
    # Logic: Use the ordinary service only for fixture creation, then the chat gate for deletion.
    # Constraints: No cascade or unrelated rows are deleted.
    def test_delete_requires_approval(self):
        created = tool_services.invoke(self.reader, None, "experiments.create", self.arguments, str(uuid.uuid4()))["data"]["record"]
        arguments = {"batch": APPROVED_BATCHES[0], "model": "sales.Product", "pk": created["pk"], "expected": created["fingerprint"]}
        approval_id = self.propose("experiments.delete", arguments)
        self.assertTrue(Product.objects.filter(pk=created["pk"]).exists())
        self.assertEqual(self.decide(approval_id).status_code, 200)
        self.assertFalse(Product.objects.filter(pk=created["pk"]).exists())


# Function: Verify independent browser submissions serialize in PostgreSQL.
# Logic: Use committed fixture state and one connection per thread.
# Constraints: TransactionTestCase isolation is required; no mocked locks or external services.
@override_settings(LAB_OPEN_ACCESS=False, WORKSPACE_OWNER_ONLY=False, LOCAL_DEBUG_AUTO_LOGIN=False)
class ChatApprovalConcurrencyTests(TransactionTestCase):
    # Function: Execute one mutation when two sessions approve concurrently.
    # Inputs: Synthetic fixture and a two-party barrier.
    # Outputs: Both calls return the same request state, with one product and evidence receipt.
    # Logic: Race the real decision service on separate connections and wait for row-lock serialization.
    # Constraints: No implicit retry; all connections close even when an assertion fails.
    def test_competing_decisions(self):
        ChatApprovalTests.setUp(self)
        payload = {"request_id": str(self.request.pk), "name": "experiments.create",
                   "arguments": self.arguments, "continuation": self.checkpoint}
        approval_id = tool_reads.read_tool(self.reader, payload)["approval"]["id"]
        barrier = Barrier(2)

        # Function: Approve on a fresh database connection.
        # Inputs: Closure's reader, request, approval, and barrier; no external arguments.
        # Outputs: Authoritative pending request status after execution/replay.
        # Logic: Synchronize starts then call the real locked transaction.
        # Constraints: Closes the thread connection and propagates unexpected failures.
        def approve():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return approvals.decide(self.reader, self.request.pk, approval_id, "approve").status
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(approve) for _ in range(2)]
            self.assertEqual([future.result(timeout=20) for future in futures], ["pending", "pending"])
        self.assertEqual(Product.objects.filter(sku="chat-approved-product").count(), 1)
        self.assertEqual(self.request.tool_reads.count(), 1)
