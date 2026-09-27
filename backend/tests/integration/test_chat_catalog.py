"""Responsibility: Verify complete MCP business access through request-bound chat.
Implementation: Exercise real registry, PostgreSQL, Agent HTTP and Session decisions;
mock only asynchronous dispatch where an external service would otherwise run.
Relationships: Reuses synthetic ChatActionTests fixtures and actual tool/approval services.
Directory:
- ChatCatalogTests: Full catalog and generic business execution checks.
- ChatCatalogTests.setUp: Construct isolated employee and request fixtures.
- ChatCatalogTests.call: Submit a request-bound operation with an optional checkpoint.
- ChatCatalogTests.approve: Decide and reclaim one persisted operation.
- ChatCatalogTests.test_catalog_equals_mcp_across_all_pages: Check publication parity.
- ChatCatalogTests.test_product_create_read_update_archive: Verify actual CRUD and replay.
- ChatCatalogTests.test_native_confirmation_and_rejection: Preserve native proposal semantics.
- ChatCatalogTests.test_async_status_and_source_idempotency: Retain queued receipts and source keys.
- ChatCatalogTests.test_schema_change_and_business_permissions: Reject stale or unauthorized writes.
Variable index:
- None
"""

from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.response import Response

from apps.agent_tools import services as tools
from apps.agent_tools.models import ToolProposal
from apps.agent_tools.registry import build_registry
from apps.chat import action_contract, services
from apps.sales import models
from tests.integration import test_chat_actions as fixtures


# Function: Exercise full publication and generic writes without external side effects.
# Logic: Run actual handlers for product/team operations and real Session approval.
# Constraints: Test database only; fabricated model decisions do not prove live LLM behavior.
@override_settings(LAB_OPEN_ACCESS=False, LOCAL_DEBUG_AUTO_LOGIN=False, WORKSPACE_OWNER_ONLY=False, ANALYSIS_PROVIDER="agent")
class ChatCatalogTests(TestCase):
    # Function: Create isolated employees, credentials and a processing conversation.
    # Inputs: Test database and the unittest cleanup stack.
    # Outputs: Fixture state shared with the existing order/email regression suite.
    # Logic: Reuse fixture construction without inheriting its test cases.
    # Constraints: Synthetic credentials never contact Gmail.
    def setUp(self):
        fixtures.ChatActionTests.setUp(self)

    # Function: Invoke any published tool through the real chat endpoint.
    # Inputs: Tool `name`, exact `arguments`, and `write` indicating a checkpoint.
    # Outputs: Original DRF response for explicit status assertions.
    # Logic: Bind every operation to the processing request and preserve arguments.
    # Constraints: No retries or direct business writes occur in this helper.
    def call(self, name, arguments, write=False):
        payload = {"request_id": str(self.request.pk), "name": name, "arguments": arguments}
        if write:
            payload["continuation"] = {"next_turn": 1, "observations": [], "signatures": []}
        return self.agent.post("/api/v1/agent/chat/tool-reads/", payload, format="json")

    # Function: Approve a frozen operation and reclaim its authoritative receipt.
    # Inputs: HTTP `response` from proposal creation and optional approve/reject `decision`.
    # Outputs: Resumed receipt for approval, or None for rejection.
    # Logic: Repeat the same decision to verify it cannot duplicate business execution.
    # Constraints: Uses a real logged-in Session; no mutation arguments are resubmitted.
    def approve(self, response, decision="approve"):
        self.assertEqual(response.status_code, 202, response.data)
        path = f"/api/v1/sales/chat/requests/{self.request.pk}/approvals/{response.data['approval']['id']}/decision/"
        for _ in range(2):
            result = self.browser.post(path, {"decision": decision}, format="json")
            self.assertEqual(result.status_code, 200, result.data)
        if decision == "approve":
            return services.claim(self.user)["resume"]["tool_result"]
        return None

    # Function: Ensure no catalog page or registered business capability is hidden.
    # Inputs: Actual MCP catalog and all request-bound pages of size thirty.
    # Outputs: Exact schema/mode parity, unique names and only three extra proposal tools.
    # Logic: Compare whole entry dictionaries after aggregating every page.
    # Constraints: Publication does not replace per-record authorization at execution.
    def test_catalog_equals_mcp_across_all_pages(self):
        expected = {row["name"]: row for row in tools.catalog(self.user)}
        actual = {}
        page = 1
        while True:
            response = self.agent.get("/api/v1/agent/chat/tools/", {
                "request_id": str(self.request.pk), "page": page, "page_size": 30})
            self.assertEqual(response.status_code, 200)
            for row in response.data["tools"]:
                self.assertNotIn(row["name"], actual)
                actual[row["name"]] = row
            if len(actual) == response.data["count"]:
                break
            self.assertTrue(response.data["tools"])
            page += 1
        self.assertGreater(page, 1)
        self.assertEqual(set(actual), set(expected) | action_contract.ACTION_TOOLS)
        for name, entry in expected.items():
            self.assertEqual(actual[name], entry)

    # Function: Execute a complete product lifecycle through generic chat tools.
    # Inputs: Synthetic SKU, explicit amounts and returned record revisions.
    # Outputs: No premature creation, one created row, persisted update and archive.
    # Logic: Approve each mutation and read actual serialized records between changes.
    # Constraints: Uses real product handlers, not mocked CRUD or external providers.
    def test_product_create_read_update_archive(self):
        proposal = self.call("products.create", {"data": {"sku": "CHAT-CATALOG", "name": "Catalog test", "currency": "USD", "unit_price": "12.50"}}, True)
        self.assertFalse(models.Product.objects.filter(sku="CHAT-CATALOG").exists())
        receipt = self.approve(proposal)
        self.assertEqual(receipt["http_status"], 201)
        product = models.Product.objects.get(sku="CHAT-CATALOG")
        read = self.call("products.get", {"id": str(product.pk)})
        self.assertEqual(read.data["data"]["name"], "Catalog test")
        self.assertEqual(read.data["evidence_items"][0]["source_type"], "business_tool_result")
        receipt = self.approve(self.call("products.update", {"id": str(product.pk), "revision": product.revision, "data": {"name": "Changed"}}, True))
        product.refresh_from_db()
        self.assertEqual(product.name, "Changed")
        self.approve(self.call("products.archive", {"id": str(product.pk), "revision": product.revision, "archived": True}, True))
        product.refresh_from_db()
        self.assertTrue(product.archived)

    # Function: Reuse native confirmation for management operations and honor rejection.
    # Inputs: Actual teams.create confirm mode and two synthetic team names.
    # Outputs: Exactly one approved native proposal and no rejected team.
    # Logic: Approve the first frozen creation, then reject the second through Session HTTP.
    # Constraints: Does not change LAB_OPEN_ACCESS or native tool execution modes.
    def test_native_confirmation_and_rejection(self):
        self.assertEqual(build_registry()["teams.create"]["executionMode"], "confirm")
        result = self.approve(self.call("teams.create", {"data": {"name": "Approved chat team"}}, True))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(models.Team.objects.filter(name="Approved chat team").count(), 1)
        self.assertEqual(ToolProposal.objects.get(tool="teams.create").status, "approved")
        self.approve(self.call("teams.create", {"data": {"name": "Rejected chat team"}}, True), "reject")
        self.assertFalse(models.Team.objects.filter(name="Rejected chat team").exists())

    # Function: Preserve asynchronous statuses and source-scoped idempotency keys.
    # Inputs: A real published product schema annotated with source-key semantics for this test.
    # Outputs: Accepted/confirmation_required receipts without falsely reporting completion.
    # Logic: Mock external dispatch only; assert no UUID is supplied to source-key execution.
    # Constraints: This contract test does not claim graph inference or provider execution works.
    def test_async_status_and_source_idempotency(self):
        registry = build_registry()
        registry["products.create"]["idempotency_scope"] = "source_key"
        for state, data in (("accepted", {"job_id": "queued"}), ("confirmation_required", {"status": "pending_confirmation"})):
            with patch("apps.chat.approvals.build_registry", return_value=registry), patch("apps.agent_tools.services.build_registry", return_value=registry), patch("apps.agent_tools.services.execute", return_value=Response(data, status=202)) as handler:
                receipt = self.approve(self.call("products.create", {"data": {"sku": "ASYNC", "name": "Async", "currency": "USD", "unit_price": "1"}}, True))
                self.assertEqual(receipt["status"], state)
                self.assertEqual(len(handler.call_args.args), 3)
                self.assertEqual(handler.call_count, 1)

    # Function: Preserve schema and entity authorization after removing publication filters.
    # Inputs: Foreign product lookup and a pending team operation with changed schema.
    # Outputs: Actual 404 read denial and 409 approval conflict with no created team.
    # Logic: Exercise business ownership first, then mock only definition drift at decision time.
    # Constraints: Does not loosen production permissions or manufacture a successful receipt.
    def test_schema_change_and_business_permissions(self):
        foreign = models.Product.objects.create(owner=self.other, sku="FOREIGN", name="Foreign", currency="USD", unit_price="1")
        self.assertEqual(self.call("products.get", {"id": str(foreign.pk)}).status_code, 404)
        response = self.call("teams.create", {"data": {"name": "Stale schema"}}, True)
        registry = build_registry()
        registry["teams.create"]["inputSchema"] = {"type": "object"}
        with patch("apps.chat.approvals.build_registry", return_value=registry):
            result = self.browser.post(f"/api/v1/sales/chat/requests/{self.request.pk}/approvals/{response.data['approval']['id']}/decision/", {"decision": "approve"}, format="json")
        self.assertEqual(result.status_code, 409)
        self.assertFalse(models.Team.objects.filter(name="Stale schema").exists())
