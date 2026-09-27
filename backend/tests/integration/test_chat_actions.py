"""Responsibility: Verify real database and HTTP contracts for employee-confirmed chat actions.
Implementation: Synthetic PostgreSQL fixtures exercise strict authentication, frozen previews, rollback, idempotency and the real Gmail MIME builder with only provider networking mocked.
Relationships: The actual Agent validator consumes backend receipts; existing experiment approvals remain separately covered.
Directory:
- ChatActionTests: Database and API tests for preparation and decisions.
- ChatActionTests.setUp: Create isolated employee, order, message and encrypted test connection.
- ChatActionTests.call: Invoke the actual Agent tool HTTP endpoint.
- ChatActionTests.prepare: Validate an actual preparation receipt against the Agent contract.
- ChatActionTests.decision: Submit an explicit browser decision.
- ChatActionTests.test_catalog_and_business_reads: Verify discovery and private read receipts.
- ChatActionTests.test_order_preview_no_write_and_atomic_confirmation: Check complete preview, revisions and idempotent execution.
- ChatActionTests.test_order_conflict_rolls_back_all_changes: Reject stale line revisions without partial header mutation.
- ChatActionTests.test_validation_and_frozen_orders: Reject illegal fields, duplicate lines, quantities and frozen orders.
- ChatActionTests.test_prepare_failure_rolls_back_proposal_and_receipt: Keep evidence and proposals atomic on persistence failure.
- ChatActionTests.test_execution_failure_rolls_back_all_business_writes: Roll back header changes when a later line write fails.
- ChatActionTests.test_execution_failure_rolls_back_all_business_writes.save_then_fail: Execute the real header save and fail at the line boundary.
- ChatActionTests.test_email_confirmation_worker_and_exact_mime: Send exact approved content through mocked provider transport once.
- ChatActionTests.test_email_uncertain_never_resends: Preserve uncertain outcome without duplicate sends.
- ChatActionTests.test_email_requires_sender_and_scope: Reject foreign, readonly and changed sender connections.
- ChatActionTests.test_email_ownership_and_actionable_errors_in_laboratory: Preserve visible records while distinguishing customer and connection email eligibility and error codes.
- ChatActionTests.test_decision_identity_csrf_and_version: Enforce genuine Session identity and displayed revision.
- ChatActionTests.test_cross_conversation_and_refresh_recovery: Recover proposals through browser and later-turn Agent reads.
- ChatActionTests.test_expiry_cancel_and_superseded_proposals: Refuse expired, cancelled and replaced approvals.
- ChatActionTests.test_laboratory_does_not_relax_scope_or_versions: Retain strict business rules in open mode.
- ChatActionTests.test_team_order_permission_is_rechecked: Respect genuine editor grants and revoke access before confirmation.
- ChatActionTests.test_review_capability_requires_actual_session_owner: Reject foreign conversation reads and require the actual owner Session for proposal review.
Variable index:
- None
"""

import base64
import copy
import hashlib
import uuid
from datetime import timedelta
from email import policy, message_from_bytes
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.middleware.csrf import get_token
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from agent.clients.chat_actions import validate_action_receipt, validate_action_arguments
from apps.chat import action_services
from apps.chat.action_contract import PREPARE_ORDER, PREPARE_EMAIL, GET_ACTION
from apps.chat.models import AnswerRequest, ActionProposal, ToolRead
from apps.crm.models import AgentCredential
from apps.sales import models, grouping, integrations, actions, services


# Function: Verify proposal behavior with production-like auth and isolated synthetic records.
# Logic: Real PostgreSQL and DRF routes remain active; tests mock only external calls or explicitly injected failure boundaries.
# Constraints: No real model, Gmail account or recipient is used; successful mocks do not prove provider delivery.
@override_settings(LAB_OPEN_ACCESS=False, WORKSPACE_OWNER_ONLY=False, LOCAL_DEBUG_AUTO_LOGIN=False, ANALYSIS_PROVIDER="agent")
class ChatActionTests(TestCase):
    # Function: Build independent authenticated chat and business fixtures.
    # Inputs: Isolated test database and a freshly generated test-only encryption key.
    # Outputs: Instance employee, foreign employee, order/line, request, connection, clients and exact arguments.
    # Logic: Create a processing request without invoking a model; encrypt synthetic granted scopes for actual preparation validation.
    # Constraints: No development credentials or existing business data are read.
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="chat-actions")
        self.other = get_user_model().objects.create_user(username="other-actions")
        self.company = grouping.create_company(self.user, "Action customer")
        self.order = models.SalesOrder.objects.create(owner=self.user, company=self.company, number="SO-1", currency="USD")
        self.line = models.OrderLine.objects.create(owner=self.user, order=self.order, description="Item", quantity="2", unit_price="1000", discount="0")
        self.conversation = models.Conversation.objects.create(owner=self.user)
        message = models.Message.objects.create(owner=self.user, conversation=self.conversation, role="user", content="Change order", client_key=uuid.uuid4())
        self.request = AnswerRequest.objects.create(owner=self.user, requested_by=self.user, conversation=self.conversation, user_message=message, status="processing")
        self.order_args = {"order_id": str(self.order.pk), "revision": self.order.revision, "changes": {"notes": " October "},
            "line_changes": [{"line_id": str(self.line.pk), "revision": self.line.revision, "changes": {"quantity": "5", "unit_price": "1200.00"}}]}
        self.vault = override_settings(SALESMATE_VAULT_KEY=Fernet.generate_key().decode())
        self.vault.enable()
        self.addCleanup(self.vault.disable)
        self.connection = models.Connection.objects.create(owner=self.user, provider="gmail", account="sales@example.com",
            encrypted_credentials=integrations.encrypt_credentials({"scopes": integrations.SCOPES["gmail"]}))
        self.email_args = {"company_id": str(self.company.pk), "connection_id": str(self.connection.pk),
            "to": ["buyer@example.com"], "cc": ["copy@example.com"], "bcc": ["archive@example.com"],
            "subject": " Delivery plan ", "body_text": "Hello,\nDelivery in October.  \n"}
        AgentCredential.objects.create(owner=self.user, name="chat-actions", digest=hashlib.sha256(b"chat-actions-test").hexdigest())
        self.agent = APIClient()
        self.agent.credentials(HTTP_AUTHORIZATION="Agent chat-actions-test")
        self.browser = APIClient()
        self.browser.force_login(self.user)

    # Function: Invoke an actual request-bound tool endpoint.
    # Inputs: Tool `name` and JSON `args`; instance stores the processing request and Agent credential.
    # Outputs: DRF response including its HTTP status.
    # Logic: Preserve the Agent's three-field envelope.
    # Constraints: Does not bypass authentication or catch backend exceptions.
    def call(self, name, args):
        return self.agent.post("/api/v1/agent/chat/tool-reads/", {"request_id": str(self.request.pk), "name": name, "arguments": args}, format="json")

    # Function: Prepare and validate a real backend receipt against the shipped Agent.
    # Inputs: Optional preparation `name` and `args`; default is the order fixture.
    # Outputs: Proposal UUID string.
    # Logic: Run both Agent validators over the actual HTTP response.
    # Constraints: A mocked backend receipt cannot satisfy this test.
    def prepare(self, name=PREPARE_ORDER, args=None):
        args = self.order_args if args is None else args
        response = self.call(name, args)
        self.assertEqual(response.status_code, 201, response.data)
        validate_action_arguments(name, args)
        validate_action_receipt(response.data["data"], name, args)
        return response.data["data"]["id"]

    # Function: Submit one browser decision against displayed proposal revision one.
    # Inputs: `proposal_id`, optional `decision`, and optional `revision`.
    # Outputs: Actual Session-authenticated HTTP response.
    # Logic: Send only closed decision fields.
    # Constraints: No model-generated authorization or replacement arguments.
    def decision(self, proposal_id, decision="approve", revision=1):
        return self.browser.post(f"/api/v1/sales/chat/action-proposals/{proposal_id}/decision/", {"decision": decision, "revision": revision}, format="json")

    # Function: Separate laboratory browsing from genuine employee review capability.
    # Inputs: Owned and foreign conversations, real Session, anonymous client and explicit lab identity header.
    # Outputs: Foreign and anonymous browsing rejects; an owning Session receives review capability.
    # Logic: Query real conversation serialization in open mode and assert it does not trust request.user impersonation.
    # Constraints: No private proposal content or decision permission is exposed to anonymous or foreign viewers.
    @override_settings(LAB_OPEN_ACCESS=True)
    def test_review_capability_requires_actual_session_owner(self):
        url = f"/api/v1/sales/records/conversations/{self.conversation.pk}/"
        self.assertTrue(self.browser.get(url).data["can_review_chat_actions"])
        self.browser.force_login(self.other)
        self.assertEqual(self.browser.get(url, HTTP_X_LAB_USER=self.user.username).status_code, 404)
        anonymous = APIClient()
        response = anonymous.get(url, HTTP_X_LAB_USER=self.user.username)
        self.assertEqual(response.status_code, 403)

    # Function: Verify discoverability, exact modes, schemas and private business reads.
    # Inputs: Real processing request and an unrelated employee connection.
    # Outputs: Published proposal modes, complete order lines and credential-free owner-scoped connection data under normal access.
    # Logic: Generic order lists accept omitted company just like MCP; Exercise discovery and pagination through HTTP, then inspect registered evidence.
    # Constraints: Existing experiment tools are not removed.
    def test_catalog_and_business_reads(self):
        response = self.agent.get("/api/v1/agent/chat/tools/", {"request_id": str(self.request.pk), "page_size": 100})
        entries = {row["name"]: row for row in response.data["tools"]}
        self.assertEqual(entries[PREPARE_ORDER]["executionMode"], "confirm")
        self.assertEqual(entries[GET_ACTION]["confirmationContract"], "chat-actions-v1")
        self.assertIn("experiments.create", entries)
        order = self.call("orders.get", {"id": str(self.order.pk)})
        self.assertEqual(order.status_code, 200, order.data)
        self.assertEqual(order.data["data"]["lines"][0]["revision"], self.line.revision)
        models.Connection.objects.create(owner=self.other, provider="gmail", account="other@example.com")
        page = self.call("connections.list", {"page_size": 1})
        self.assertEqual(page.data["data"]["count"], 1)
        self.assertNotIn("encrypted_credentials", page.data["data"]["results"][0])
        self.assertEqual(self.call("orders.list", {}).status_code, 200)
        self.assertEqual(self.call("orders.list", {"company": str(self.company.pk)}).data["data"]["count"], 1)

    # Function: Verify preview-only preparation followed by one complete business commit.
    # Inputs: Header and existing-line changes, repeated preparation and repeated approval.
    # Outputs: No preparation writes, exact total preview, preserved text, increased versions and a single decision execution.
    # Logic: Compare real rows before preparation, after approval and after replay.
    # Constraints: Agent receipt validation is real; no external provider is involved.
    def test_order_preview_no_write_and_atomic_confirmation(self):
        proposal_id = self.prepare()
        replay = self.call(PREPARE_ORDER, self.order_args)
        self.assertEqual(replay.data["data"]["id"], proposal_id)
        self.assertEqual(ToolRead.objects.count(), 1)
        self.assertEqual(replay.data["data"]["preview"]["total_after"], "6000.00")
        self.order.refresh_from_db()
        self.assertEqual(self.order.notes, "")
        self.assertFalse(models.Draft.objects.exists())
        response = self.decision(proposal_id)
        self.assertEqual(response.status_code, 200, response.data)
        validate_action_receipt(response.data, GET_ACTION, {"proposal_id": proposal_id})
        self.assertEqual(response.data["status"], "succeeded")
        self.order.refresh_from_db()
        self.line.refresh_from_db()
        self.assertEqual(self.order.notes, " October ")
        self.assertEqual(self.line.quantity, 5)
        version = self.order.revision
        self.assertEqual(self.decision(proposal_id).status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.revision, version)
        self.assertEqual(self.call(PREPARE_ORDER, self.order_args).status_code, 409)

    # Function: Reject a stale line even when the parent version was not advanced.
    # Inputs: Prepared header/line update followed by an independent line revision change.
    # Outputs: 409 and conflicted status without any header or line content writes.
    # Logic: Change only a line revision to isolate the explicit line-version check.
    # Constraints: The direct fixture mutation intentionally simulates a conflicting writer; it is not production update logic.
    def test_order_conflict_rolls_back_all_changes(self):
        proposal_id = self.prepare()
        models.OrderLine.objects.filter(pk=self.line.pk).update(revision=self.line.revision + 1)
        self.assertEqual(self.decision(proposal_id).status_code, 409)
        self.order.refresh_from_db()
        self.line.refresh_from_db()
        self.assertEqual(self.order.notes, "")
        self.assertEqual(self.line.quantity, 2)
        self.assertEqual(ActionProposal.objects.get(pk=proposal_id).status, "conflicted")

    # Function: Preserve existing order constraints and strict input boundaries.
    # Inputs: Unsupported fields, duplicate/foreign lines, zero quantity and a confirmed order.
    # Outputs: Explicit tool errors and no proposals or business changes.
    # Logic: Exercise schema, membership and business validation separately.
    # Constraints: Never alter order state or currency rules to make preparation succeed.
    def test_validation_and_frozen_orders(self):
        invalid = copy.deepcopy(self.order_args)
        invalid["changes"]["status"] = "confirmed"
        self.assertEqual(self.call(PREPARE_ORDER, invalid).status_code, 400)
        invalid = copy.deepcopy(self.order_args)
        invalid["line_changes"] *= 2
        self.assertEqual(self.call(PREPARE_ORDER, invalid).status_code, 400)
        invalid = copy.deepcopy(self.order_args)
        invalid["line_changes"][0]["line_id"] = str(uuid.uuid4())
        self.assertEqual(self.call(PREPARE_ORDER, invalid).status_code, 400)
        invalid = copy.deepcopy(self.order_args)
        invalid["line_changes"][0]["changes"]["quantity"] = "0"
        self.assertEqual(self.call(PREPARE_ORDER, invalid).status_code, 400)
        invalid["line_changes"][0]["changes"]["quantity"] = "5\n"
        self.assertEqual(self.call(PREPARE_ORDER, invalid).status_code, 400)
        models.SalesOrder.objects.filter(pk=self.order.pk).update(status="confirmed")
        self.assertEqual(self.call(PREPARE_ORDER, self.order_args).status_code, 409)
        self.assertFalse(ActionProposal.objects.exists())

    # Function: Verify proposal persistence cannot survive a missing canonical receipt.
    # Inputs: Valid preparation and injected ToolRead persistence failure.
    # Outputs: HTTP 500, no proposal and no business mutation.
    # Logic: Raise at the final persistence boundary inside the actual transaction.
    # Constraints: Only the failing save is mocked; transaction rollback is real PostgreSQL behavior.
    def test_prepare_failure_rolls_back_proposal_and_receipt(self):
        with patch.object(ToolRead, "save", side_effect=RuntimeError("injected receipt failure")):
            self.assertEqual(self.call(PREPARE_ORDER, self.order_args).status_code, 500)
        self.assertFalse(ActionProposal.objects.exists())

    # Function: Roll back an earlier successful header update when a later line fails.
    # Inputs: Prepared header/line operation and injected failure on the second business save.
    # Outputs: Original header/line values and a still-unconfirmed proposal.
    # Logic: Let the first real save execute, then raise a business validation error on the second.
    # Constraints: The assertion verifies actual database rollback, not mocked success.
    def test_execution_failure_rolls_back_all_business_writes(self):
        proposal_id = self.prepare()
        original = services.save_record

        # Function: Inject failure only after the header was actually saved.
        # Inputs: Business `serializer`, employee `actor`, and original `expected` revision.
        # Outputs: Real saved header or explicit validation exception for a line.
        # Logic: Delegate header persistence to the captured original service and fail at the later line boundary.
        # Constraints: No mocked successful mutation; the surrounding PostgreSQL transaction must undo the real header change.
        def save_then_fail(serializer, actor, expected):
            if isinstance(serializer.instance, models.SalesOrder):
                return original(serializer, actor, expected)
            raise ValidationError("line failed")

        with patch("apps.chat.action_services.services.save_record", side_effect=save_then_fail):
            self.assertEqual(self.decision(proposal_id).status_code, 400)
        self.order.refresh_from_db()
        self.assertEqual(self.order.notes, "")
        self.assertIsNone(ActionProposal.objects.get(pk=proposal_id).decided_by_id)

    # Function: Verify approval-only materialization and complete Gmail MIME payload.
    # Inputs: Plaintext proposal with To/Cc/Bcc, whitespace and mocked provider SDK.
    # Outputs: One approved draft/action, exactly one send and provider-accepted state with exact recipients and body.
    # Logic: Execute the real action worker and MIME builder after HTTP confirmation.
    # Constraints: Credential acquisition and Gmail transport are mocked; no real mail is sent.
    def test_email_confirmation_worker_and_exact_mime(self):
        proposal_id = self.prepare(PREPARE_EMAIL, self.email_args)
        self.assertFalse(models.Draft.objects.exists())
        self.assertFalse(models.ToolAction.objects.exists())
        response = self.decision(proposal_id)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "approved")
        self.assertEqual(self.decision(proposal_id).status_code, 200)
        self.assertEqual(models.Draft.objects.count(), 1)
        self.assertEqual(models.ToolAction.objects.count(), 1)
        action = models.ToolAction.objects.get()
        api = Mock()
        sender = api.users.return_value.messages.return_value.send
        sender.return_value.execute.return_value = {"id": "synthetic-sent"}
        with patch("apps.sales.actions.credentials_for", return_value=Mock()), patch("apps.sales.actions.build", return_value=api):
            self.assertEqual(actions.run_action(action.pk), "succeeded")
            self.assertEqual(actions.run_action(action.pk), "succeeded")
        sender.assert_called_once()
        message = message_from_bytes(base64.urlsafe_b64decode(sender.call_args.kwargs["body"]["raw"]), policy=policy.default)
        self.assertEqual(message["To"], "buyer@example.com")
        self.assertEqual(message["Cc"], "copy@example.com")
        self.assertEqual(message["Bcc"], "archive@example.com")
        self.assertEqual(message.get_content(), self.email_args["body_text"])
        data = self.call(GET_ACTION, {"proposal_id": proposal_id}).data["data"]
        validate_action_receipt(data, GET_ACTION, {"proposal_id": proposal_id})
        self.assertEqual(data["status"], "succeeded")

    # Function: Preserve an uncertain external result without replaying the send.
    # Inputs: Approved proposal and transport exception after send entry.
    # Outputs: Uncertain state and one provider invocation across repeated worker calls.
    # Logic: Use existing worker uncertainty classification and read through the proposal interface.
    # Constraints: No fallback transport or automatic retry.
    def test_email_uncertain_never_resends(self):
        proposal_id = self.prepare(PREPARE_EMAIL, self.email_args)
        self.assertEqual(self.decision(proposal_id).status_code, 200)
        action = models.ToolAction.objects.get()
        with patch("apps.sales.actions.credentials_for", return_value=Mock()), patch("apps.sales.actions.execute_provider", side_effect=TimeoutError) as send:
            self.assertEqual(actions.run_action(action.pk), "uncertain")
            self.assertEqual(actions.run_action(action.pk), "uncertain")
        send.assert_called_once()
        self.assertEqual(self.call(GET_ACTION, {"proposal_id": proposal_id}).data["data"]["status"], "uncertain")

    # Function: Reject credentials that do not authorize the reviewed sender and action.
    # Inputs: Foreign mailbox, readonly scope and an account changed after preparation.
    # Outputs: Preparation errors or confirmation conflict with no Draft/send task.
    # Logic: Mutate synthetic ownership/scopes/account; assert precise scoped errors before checking the unchanged confirmation conflict.
    # Constraints: Genuine OAuth tokens are never loaded or printed.
    def test_email_requires_sender_and_scope(self):
        self.assertEqual(self.call(PREPARE_EMAIL, {**self.email_args, "subject": "Header\n"}).status_code, 400)
        self.connection.owner = self.other
        self.connection.save()
        response = self.call(PREPARE_EMAIL, self.email_args)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["error"]["code"], "email_connection_unavailable")
        self.connection.owner = self.user
        self.connection.encrypted_credentials = integrations.encrypt_credentials({"scopes": [integrations.SCOPES["gmail"][1]]})
        self.connection.save()
        response = self.call(PREPARE_EMAIL, self.email_args)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "email_authorization_invalid")
        self.connection.encrypted_credentials = integrations.encrypt_credentials({"scopes": integrations.SCOPES["gmail"]})
        self.connection.save()
        proposal_id = self.prepare(PREPARE_EMAIL, self.email_args)
        self.connection.account = "changed@example.com"
        self.connection.save()
        self.assertEqual(self.decision(proposal_id).status_code, 409)
        self.assertFalse(models.Draft.objects.exists())

    # Function: Distinguish laboratory discovery from actual email ownership before and during preparation.
    # Inputs: Real owned fixtures plus a second employee's customer and Gmail metadata in laboratory mode.
    # Outputs: Foreign records remain hidden, owned records retain ownership annotations, and rejected preparation creates no proposal or send.
    # Logic: Exercise actual HTTP list/detail reads and customer/connection rejection paths, including a nonexistent UUID with the same non-leaking error.
    # Constraints: Only synthetic encrypted credentials are used; no provider or model call occurs.
    @override_settings(LAB_OPEN_ACCESS=True)
    def test_email_ownership_and_actionable_errors_in_laboratory(self):
        foreign = models.Connection.objects.create(owner=self.other, provider="gmail", account="foreign@example.com")
        customer = grouping.create_company(self.other, "Other email customer")
        page = self.call("connections.list", {"page_size": 20}).data["data"]
        ownership = {row["id"]: row["email_preparation_owned"] for row in page["results"]}
        self.assertEqual(ownership, {str(self.connection.pk): True})
        detail = self.call("connections.get", {"id": str(foreign.pk)})
        self.assertEqual(detail.status_code, 404)
        page = self.call("customers.search", {"page_size": 20}).data["data"]
        ownership = {row["id"]: row["email_preparation_owned"] for row in page["results"]}
        self.assertTrue(ownership[str(self.company.pk)])
        self.assertNotIn(str(customer.pk), ownership)
        self.assertTrue(self.call("customers.context", {"company_id": str(self.company.pk)}).data["data"]["email_preparation_owned"])
        self.assertEqual(self.call("customers.context", {"company_id": str(customer.pk)}).status_code, 404)
        for target in (foreign.pk, uuid.uuid4()):
            response = self.call(PREPARE_EMAIL, {**self.email_args, "connection_id": str(target)})
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.data["error"]["code"], "email_connection_unavailable")
            self.assertEqual(response.data["error"]["scope"], "tool")
            self.assertIn("新建聊天会话", response.data["error"]["detail"])
        response = self.call(PREPARE_EMAIL, {**self.email_args, "company_id": str(customer.pk)})
        self.assertEqual(response.data["error"]["code"], "email_customer_unavailable")
        self.assertFalse(ActionProposal.objects.exists())
        self.assertFalse(models.Draft.objects.exists())
        self.assertFalse(models.ToolAction.objects.exists())

    # Function: Bind approval to a real employee Session, CSRF token and proposal version.
    # Inputs: Agent-only, foreign Session, missing-CSRF and stale-revision submissions.
    # Outputs: Rejections until a matching CSRF-protected Session explicitly approves.
    # Logic: Exercise actual authentication middleware and closed decision schema.
    # Constraints: force_login establishes a Session; force_authenticate is never used for approval.
    def test_decision_identity_csrf_and_version(self):
        proposal_id = self.prepare()
        url = f"/api/v1/sales/chat/action-proposals/{proposal_id}/decision/"
        payload = {"decision": "approve", "revision": 1}
        self.assertEqual(self.agent.post(url, payload, format="json").status_code, 403)
        self.browser.force_login(self.other)
        self.assertEqual(self.decision(proposal_id).status_code, 404)
        self.browser.force_login(self.user)
        self.assertEqual(self.decision(proposal_id, revision=2).status_code, 409)
        self.assertEqual(self.browser.post(url, {**payload, "approved": True}, format="json").status_code, 400)
        csrf = APIClient(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        self.assertEqual(csrf.post(url, payload, format="json").status_code, 403)
        request = SimpleNamespace(META={})
        token = get_token(request)
        csrf.cookies["csrftoken"] = request.META["CSRF_COOKIE"]
        self.assertEqual(csrf.post(url, payload, format="json", HTTP_X_CSRFTOKEN=token).status_code, 200)

    # Function: Recover persisted proposals without parsing assistant messages.
    # Inputs: A prepared proposal, a later request in another conversation, and the browser list endpoint.
    # Outputs: Restorable card data and denied cross-conversation Agent read.
    # Logic: Complete the originating request, bind a fresh processing request, and query by stable ID.
    # Constraints: Sharing an employee does not grant cross-conversation Agent visibility.
    def test_cross_conversation_and_refresh_recovery(self):
        proposal_id = self.prepare()
        page = self.browser.get("/api/v1/sales/chat/action-proposals/", {"conversation_id": str(self.conversation.pk)})
        self.assertEqual(page.status_code, 200, page.data)
        self.assertEqual(page.data["results"][0]["id"], proposal_id)
        self.request.status = "completed"
        self.request.save()
        other_conversation = models.Conversation.objects.create(owner=self.user)
        message = models.Message.objects.create(owner=self.user, conversation=other_conversation, role="user", content="Status", client_key=uuid.uuid4())
        self.request = AnswerRequest.objects.create(owner=self.user, conversation=other_conversation, user_message=message, status="processing")
        self.assertEqual(self.call(GET_ACTION, {"proposal_id": proposal_id}).status_code, 404)
        message.conversation = self.conversation
        message.save()
        self.request.conversation = self.conversation
        self.request.save()
        self.assertEqual(self.call(GET_ACTION, {"proposal_id": proposal_id}).status_code, 200)

    # Function: Ensure obsolete proposals cannot retain approval authority.
    # Inputs: Expired proposal, changed email content and explicit cancellation.
    # Outputs: Expiry/conflict, explicit supersession audit and cancelled replacements without sends.
    # Logic: Create new frozen content instead of editing the old proposal, then test both IDs.
    # Constraints: No automatic execution on replay or cancellation of an already approved send.
    def test_expiry_cancel_and_superseded_proposals(self):
        proposal_id = self.prepare()
        ActionProposal.objects.filter(pk=proposal_id).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.decision(proposal_id).status_code, 409)
        first = self.prepare(PREPARE_EMAIL, self.email_args)
        second = self.prepare(PREPARE_EMAIL, {**self.email_args, "subject": "Replacement"})
        self.assertEqual(self.decision(first).status_code, 409)
        self.assertEqual(self.decision(second, "cancel").status_code, 200)
        self.assertEqual(self.decision(second, "cancel").status_code, 200)
        self.assertEqual(self.decision(second).status_code, 409)
        self.assertFalse(models.ToolAction.objects.exists())

    # Function: Verify laboratory configuration cannot disable business proposal authorization.
    # Inputs: Open-mode override, foreign connection and changed order version.
    # Outputs: Hidden foreign connection metadata and 409 rejection for stale independent proposal approval.
    # Logic: Generic reads and independent proposals both enforce private ownership and versions. Enable laboratory mode only for this explicit boundary test.
    # Constraints: Does not change runtime defaults or existing experiment-tool behavior.
    def test_laboratory_does_not_relax_scope_or_versions(self):
        with override_settings(LAB_OPEN_ACCESS=True):
            foreign = models.Connection.objects.create(owner=self.other, provider="gmail", account="other@example.com")
            self.assertEqual(self.call("connections.get", {"id": str(foreign.pk)}).status_code, 404)
            proposal_id = self.prepare()
            models.SalesOrder.objects.filter(pk=self.order.pk).update(revision=self.order.revision + 1)
            self.assertEqual(self.decision(proposal_id).status_code, 409)

    # Function: Preserve collaboration grants while rejecting permission revoked after review.
    # Inputs: Another employee's order shared through a real team editor grant.
    # Outputs: Successful preparation followed by 404 on confirmation after revocation; no mutation.
    # Logic: Move only synthetic fixture ownership, create matching membership/grant, and archive the grant before approval.
    # Constraints: No laboratory bypass and no implicit ownership transfer by the proposal.
    def test_team_order_permission_is_rechecked(self):
        self.company.owner = self.other
        self.company.save()
        models.SalesOrder.objects.filter(pk=self.order.pk).update(owner=self.other)
        models.OrderLine.objects.filter(pk=self.line.pk).update(owner=self.other)
        team = models.Team.objects.create(owner=self.other, name="Shared sales")
        models.Membership.objects.create(owner=self.other, team=team, user=self.user, role="editor")
        grant = models.CompanyGrant.objects.create(owner=self.other, company=self.company, team=team, role="editor")
        proposal_id = self.prepare()
        grant.archived = True
        grant.save()
        self.assertEqual(self.decision(proposal_id).status_code, 404)
        self.order.refresh_from_db()
        self.assertEqual(self.order.notes, "")
