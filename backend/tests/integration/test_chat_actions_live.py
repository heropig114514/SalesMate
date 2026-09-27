"""Responsibility: Verify the shipped Agent HTTP client and concurrent employee confirmations.
Implementation: A Django live server and independent PostgreSQL connections test real transport and locking; synthetic fixtures reuse the action integration setup.
Relationships: DjangoBackendClient calls actual chat endpoints; action_services serializes competing decisions; Gmail networking is never invoked here.
Directory:
- ChatActionLiveTests: Real HTTP and concurrency coverage.
- ChatActionLiveTests.setUp: Create committed synthetic business and chat fixtures.
- ChatActionLiveTests.test_agent_http_prepare_browser_decision_and_status: Verify Agent-to-backend proposal lifecycle through sockets.
- ChatActionLiveTests.test_agent_workflow_reports_preview_without_execution: Run the actual Agent tool loop and answer reporting against live HTTP.
- ChatActionLiveTests.test_competing_email_approvals_create_one_task: Race two real database transactions against the same proposal.
- ChatActionLiveTests.test_competing_email_approvals_create_one_task.approve: Approve using an independent database connection.
Variable index:
- None
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import requests
from django.conf import settings
from django.db import close_old_connections
from django.test import LiveServerTestCase, override_settings

from agent.clients.backend_api import DjangoBackendClient
from agent.clients.chat_actions import validate_action_receipt
from agent.workflows.chat import answer_workspace_request
from agent.tests.test_workspace_chat import QueueProvider
from apps.chat import tool_reads, action_services
from apps.chat.action_contract import PREPARE_ORDER, PREPARE_EMAIL, GET_ACTION
from apps.sales import models
from tests.integration import test_chat_actions as fixtures


# Function: Exercise committed transactions, real sockets and the actual Agent transport.
# Logic: LiveServerTestCase does not wrap each test in an invisible outer transaction, allowing concurrent connections to observe rows and locks.
# Constraints: Isolated test database only; no production service, model or mail transport.
@override_settings(LAB_OPEN_ACCESS=False, WORKSPACE_OWNER_ONLY=False, LOCAL_DEBUG_AUTO_LOGIN=False, ANALYSIS_PROVIDER="agent")
class ChatActionLiveTests(LiveServerTestCase):
    # Function: Reuse synthetic action fixtures without running their test methods.
    # Inputs: Isolated live-server database and inherited unittest cleanup stack.
    # Outputs: Committed employee, request, order, connection and browser Session fixtures.
    # Logic: Delegate fixture construction only to the existing integration setup.
    # Constraints: No database or HTTP behavior is mocked.
    def setUp(self):
        fixtures.ChatActionTests.setUp(self)

    # Function: Verify actual Agent discovery, reads, preparation and post-decision status.
    # Inputs: Live server, real employee Agent token and a real Session cookie generated in fixture setup.
    # Outputs: Contract-valid pending then succeeded receipts and a modified order.
    # Logic: Use requests sockets for both Agent and browser; acquire CSRF from the normal session endpoint before deciding.
    # Constraints: No mocked HTTP responses; browser authorization is never supplied to the model client.
    def test_agent_http_prepare_browser_decision_and_status(self):
        backend = DjangoBackendClient(self.live_server_url + "/api/v1/agent/", "chat-actions-test")
        self.addCleanup(backend.close)
        catalog = backend.get_chat_tools(str(self.request.pk))
        self.assertIn(PREPARE_ORDER, {row["name"] for row in catalog["tools"]})
        order = backend.read_chat_tool(str(self.request.pk), "orders.get", {"id": str(self.order.pk)})
        self.assertEqual(order["data"]["lines"][0]["revision"], self.line.revision)
        prepared = backend.read_chat_tool(str(self.request.pk), PREPARE_ORDER, self.order_args)
        validate_action_receipt(prepared["data"], PREPARE_ORDER, self.order_args)
        proposal_id = prepared["data"]["id"]
        with requests.Session() as browser:
            browser.cookies.set(settings.SESSION_COOKIE_NAME, self.browser.cookies[settings.SESSION_COOKIE_NAME].value)
            response = browser.get(self.live_server_url + "/api/v1/session/", timeout=10)
            self.assertEqual(response.status_code, 200)
            response = browser.post(self.live_server_url + f"/api/v1/sales/chat/action-proposals/{proposal_id}/decision/",
                json={"decision": "approve", "revision": 1}, headers={"X-CSRFToken": browser.cookies[settings.CSRF_COOKIE_NAME]}, timeout=10)
            self.assertEqual(response.status_code, 200, response.text)
        result = backend.read_chat_tool(str(self.request.pk), GET_ACTION, {"proposal_id": proposal_id})
        validate_action_receipt(result["data"], GET_ACTION, {"proposal_id": proposal_id})
        self.assertEqual(result["data"]["status"], "succeeded")
        self.order.refresh_from_db()
        self.assertEqual(self.order.notes, self.order_args["changes"]["notes"])

    # Function: Verify the unchanged Agent workflow can finish a proposal turn on this backend.
    # Inputs: Real Agent client and deterministic model outputs selecting read then prepare.
    # Outputs: Persisted completed answer, pending proposal and unchanged order.
    # Logic: Execute the actual model-tool loop, schema validation, context retrieval and six-field answer report over real sockets.
    # Constraints: Only model choices are simulated; no HTTP response, business write or employee confirmation is mocked.
    def test_agent_workflow_reports_preview_without_execution(self):
        backend = DjangoBackendClient(self.live_server_url + "/api/v1/agent/", "chat-actions-test")
        self.addCleanup(backend.close)
        provider = QueueProvider({"action": "tool", "name": "orders.get", "arguments": {"id": str(self.order.pk)}},
            {"action": "tool", "name": PREPARE_ORDER, "arguments": self.order_args})
        result = answer_workspace_request({"request_id": str(self.request.pk), "conversation_id": str(self.conversation.pk),
            "user_message_id": str(self.request.user_message_id), "question": "Update this order after my review", "recent_history": []},
            backend=backend, chat_provider=provider)
        self.assertEqual(result["status"], "completed", result)
        self.assertIn("Nothing has been changed or sent", result["assistant_text"])
        backend.report_answer(result)
        self.request.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.request.status, "completed")
        self.assertEqual(self.request.action_proposals.get().status, "pending_confirmation")
        self.assertEqual(self.order.notes, "")

    # Function: Verify concurrent confirmation cannot create duplicate drafts or sends.
    # Inputs: One committed email proposal and two independent database connections released together.
    # Outputs: Two consistent approved results, exactly one Draft and one ToolAction.
    # Logic: Actual employee/proposal row locks serialize the competing transactions.
    # Constraints: Bounded barriers and futures detect deadlock; no provider network call occurs.
    def test_competing_email_approvals_create_one_task(self):
        result = tool_reads.read_tool(self.user, {"request_id": str(self.request.pk), "name": PREPARE_EMAIL, "arguments": self.email_args})
        proposal_id = result["data"]["id"]
        barrier = Barrier(2)

        # Function: Submit one competing explicit approval on a fresh connection.
        # Inputs: Closure employee/proposal IDs and the bounded start barrier.
        # Outputs: Authoritative approved status.
        # Logic: Open a thread-local connection, wait for the competitor, execute the real service and always close it.
        # Constraints: Exceptions propagate to the test; no retries conceal serialization failures.
        def approve():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return action_services.decide(self.user, proposal_id, "approve", 1)["status"]
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(approve) for _ in range(2)]
            self.assertEqual([future.result(timeout=20) for future in futures], ["approved", "approved"])
        self.assertEqual(models.Draft.objects.count(), 1)
        self.assertEqual(models.ToolAction.objects.count(), 1)
