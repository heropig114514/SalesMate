"""Responsibility: Verify chat customer registration without bypassing browser approval.
Implementation: Actual PostgreSQL, DRF authentication and business handlers with isolated
fixtures; test creation, rejection, replay, conflicts and laboratory ownership boundaries.
Relationships: ChatActionTests supplies synthetic fixtures; approvals/tool_reads execute
the real workflow. Live tests separately run the Agent client through HTTP sockets.
Directory:
- ChatCustomerTests: Customer approval and authorization regression checks.
- ChatCustomerTests.setUp: Build isolated users and a processing private chat.
- ChatCustomerTests.propose: Request a frozen name-only customer creation.
- ChatCustomerTests.decide: Submit a real Session decision.
- ChatCustomerTests.test_create_once_and_canonical_resume: Verify atomic creation and receipt.
- ChatCustomerTests.test_reject_without_customer_or_email: Verify cancellation has no writes.
- ChatCustomerTests.test_duplicate_name_rechecked_at_approval: Reject a competing exact name.
- ChatCustomerTests.test_invalid_inputs_and_missing_checkpoint: Reject malformed proposals.
- ChatCustomerTests.test_lab_borrowed_request_cannot_create: Preserve private owner binding.
- ChatCustomerTests.test_session_csrf_and_initiator_required: Reject unauthenticated decisions.
- ChatCustomerTests.test_creation_rollback_on_evidence_failure: Keep approval and CRM atomic.
Variable index:
- None
"""

from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.chat import services
from apps.crm.models import Company
from apps.sales import grouping, models
from tests.integration import test_chat_actions as fixtures


# Function: Exercise private registration with real business and approval services.
# Logic: Reuse only fixture construction; assert persisted state at every decision boundary.
# Constraints: No model or external Gmail calls; database changes stay in the test database.
@override_settings(LAB_OPEN_ACCESS=False, WORKSPACE_OWNER_ONLY=False, LOCAL_DEBUG_AUTO_LOGIN=False, ANALYSIS_PROVIDER="agent")
class ChatCustomerTests(TestCase):
    # Function: Create isolated owners, browser Session and Agent credential.
    # Inputs: Test database and unittest cleanup stack.
    # Outputs: Fixture attributes plus a canonical continuation and explicit company name.
    # Logic: Reuse order/email fixture setup without inheriting its test methods.
    # Constraints: Synthetic Gmail credentials never reach a provider.
    def setUp(self):
        fixtures.ChatActionTests.setUp(self)
        self.name = "New Heropig customer"
        self.checkpoint = {"next_turn": 1, "observations": [], "signatures": []}

    # Function: Prepare customer creation through the actual Agent route.
    # Inputs: Optional `arguments` override the explicit name-only input.
    # Outputs: DRF response for success or rejection assertions.
    # Logic: Bind parameters and continuation to this processing request.
    # Constraints: Never call the creation handler or approve directly.
    def propose(self, arguments=None):
        return self.agent.post("/api/v1/agent/chat/tool-reads/", {
            "request_id": str(self.request.pk), "name": "customers.create",
            "arguments": {"name": self.name} if arguments is None else arguments,
            "continuation": self.checkpoint,
        }, format="json")

    # Function: Submit an explicit approval or rejection.
    # Inputs: `approval_id` identifies frozen content; `decision` is approve or reject.
    # Outputs: Real Session-authenticated DRF response.
    # Logic: Send only the decision to the request-bound endpoint.
    # Constraints: No argument replacement or Agent authorization is used.
    def decide(self, approval_id, decision="approve"):
        return self.browser.post(
            f"/api/v1/sales/chat/requests/{self.request.pk}/approvals/{approval_id}/decision/",
            {"decision": decision}, format="json")

    # Function: Verify one approval creates one real customer and resumes exactly once.
    # Inputs: Valid private request and repeated explicit approval decisions.
    # Outputs: No early writes; one owned manual company, canonical 201 receipt and preserved budget.
    # Logic: Propose, approve twice, then claim the resumed task and compare authoritative evidence.
    # Constraints: Customer confirmation never creates a business draft or send action.
    def test_create_once_and_canonical_resume(self):
        response = self.propose()
        self.assertEqual(response.status_code, 202, response.data)
        self.assertFalse(Company.objects.filter(name=self.name).exists())
        approval = response.data["approval"]["id"]
        self.assertEqual(self.decide(approval).status_code, 200)
        self.assertEqual(self.decide(approval).status_code, 200)
        company = Company.objects.get(name=self.name, owner=self.user)
        self.assertEqual(company.domains, [])
        self.assertEqual(company.contacts.count(), 0)
        resumed = services.claim(self.user)["resume"]
        self.assertEqual(resumed["continuation"], self.checkpoint)
        self.assertEqual(resumed["tool_result"]["http_status"], 201)
        self.assertEqual(resumed["tool_result"]["data"]["id"], str(company.pk))
        self.assertEqual(resumed["evidence_items"][0]["source_type"], "customer_creation")
        self.assertEqual(self.request.tool_reads.count(), 1)
        self.assertFalse(models.Draft.objects.exists())
        self.assertFalse(models.ToolAction.objects.exists())

    # Function: Reject creation without leaving a customer or email behind.
    # Inputs: Pending proposal and explicit rejection.
    # Outputs: Cancelled request and zero matching customers, drafts or actions.
    # Logic: Decide through the normal browser route and inspect persistent state.
    # Constraints: Earlier unrelated fixture companies remain intact.
    def test_reject_without_customer_or_email(self):
        approval = self.propose().data["approval"]["id"]
        self.assertEqual(self.decide(approval, "reject").data["status"], "cancelled")
        self.assertFalse(Company.objects.filter(name=self.name).exists())
        self.assertFalse(models.Draft.objects.exists())
        self.assertFalse(models.ToolAction.objects.exists())

    # Function: Refuse exact-name duplication before and after employee review.
    # Inputs: A pending name followed by independent traditional-directory creation.
    # Outputs: 409 with unchanged pending approval and exactly one company.
    # Logic: Create via the existing grouping service while approval waits, then confirm.
    # Constraints: No implicit merge, substitution or unauthorized automatic retry.
    def test_duplicate_name_rechecked_at_approval(self):
        approval = self.propose().data["approval"]["id"]
        grouping.create_company(self.user, self.name.upper())
        self.assertEqual(self.decide(approval).status_code, 409)
        self.assertEqual(self.request.approvals.get().status, "pending")
        self.assertEqual(Company.objects.filter(owner=self.user, name__iexact=self.name).count(), 1)
        self.assertEqual(self.decide(approval, "reject").status_code, 200)
        self.request.status = "processing"
        self.request.save(update_fields=["status"])
        self.assertEqual(self.propose().status_code, 409)

    # Function: Reject blank, guessed, oversized or nonresumable creation inputs.
    # Inputs: Invalid argument dictionaries and a request without continuation.
    # Outputs: 400 responses and no pending approval/customer.
    # Logic: Exercise schema and name boundaries through Agent HTTP.
    # Constraints: Existing input limits and required fields remain unchanged.
    def test_invalid_inputs_and_missing_checkpoint(self):
        for args in ({}, {"name": " "}, {"name": "x" * 241}, {"name": self.name, "owner_id": self.other.pk}):
            self.assertEqual(self.propose(args).status_code, 400)
        response = self.agent.post("/api/v1/agent/chat/tool-reads/", {
            "request_id": str(self.request.pk), "name": "customers.create", "arguments": {"name": self.name},
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.request.approvals.exists())

    # Function: Disallow real customer creation in another employee's laboratory conversation.
    # Inputs: Open laboratory mode and a request submitted by a different employee.
    # Outputs: Full catalog publication with a 404 for private creation using a borrowed request.
    # Logic: Change submitter only to exercise strict private binding independent of lab access.
    # Constraints: Public experiment tools retain their established shared permissions.
    @override_settings(LAB_OPEN_ACCESS=True)
    def test_lab_borrowed_request_cannot_create(self):
        self.request.requested_by = self.other
        self.request.save(update_fields=["requested_by"])
        response = self.agent.get("/api/v1/agent/chat/tools/", {"request_id": str(self.request.pk), "page_size": 100})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn("customers.create", {tool["name"] for tool in response.data["tools"]})
        self.assertEqual(self.propose().status_code, 404)

    # Function: Require real initiating Session authentication and CSRF even in open lab mode.
    # Inputs: Pending creation and anonymous, Agent, wrong-user and missing-CSRF clients.
    # Outputs: Rejections and no company creation.
    # Logic: Call the actual decision endpoint with each disallowed identity; foreign requests return 404 without revealing an existing approval.
    # Constraints: Production SessionAuthentication remains active; no force_authenticate bypass.
    @override_settings(LAB_OPEN_ACCESS=True)
    def test_session_csrf_and_initiator_required(self):
        approval = self.propose().data["approval"]["id"]
        path = f"/api/v1/sales/chat/requests/{self.request.pk}/approvals/{approval}/decision/"
        self.assertEqual(APIClient().post(path, {"decision": "approve"}, format="json").status_code, 403)
        self.assertEqual(self.agent.post(path, {"decision": "approve"}, format="json").status_code, 403)
        csrf = APIClient(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        self.assertEqual(csrf.post(path, {"decision": "approve"}, format="json").status_code, 403)
        self.browser.force_login(self.other)
        self.assertEqual(self.decide(approval).status_code, 404)
        self.assertFalse(Company.objects.filter(name=self.name).exists())

    # Function: Roll back customer creation if canonical evidence cannot be saved.
    # Inputs: Valid approved operation with an injected ToolRead persistence failure.
    # Outputs: Exception, no new company, and an unchanged pending approval.
    # Logic: Fail after business execution inside the real approval transaction.
    # Constraints: Only persistence failure is injected; no successful mocked business handler.
    def test_creation_rollback_on_evidence_failure(self):
        approval = self.propose().data["approval"]["id"]
        with patch("apps.chat.models.ToolRead.save", side_effect=RuntimeError("test evidence failure")):
            with self.assertRaises(RuntimeError):
                self.decide(approval)
        self.assertFalse(Company.objects.filter(name=self.name).exists())
        self.assertEqual(self.request.approvals.get().status, "pending")
