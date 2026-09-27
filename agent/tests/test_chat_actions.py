"""Responsibility: Verify independent proposal contracts alongside dynamic generic tool discovery.
Implementation: Exercise real Agent workflow and HTTP validation using queued model/transport fixtures.
Relationships: Reuses workspace/http fixtures; backend integration tests separately verify actual persistence and Session decisions.
Directory:
- tool: Construct one queued model tool decision.
- order_arguments: Provide explicit synthetic order changes and original revisions.
- email_arguments: Provide a frozen synthetic plaintext email.
- proposal: Construct a synthetic order or email proposal at a chosen status.
- receipt: Construct a canonical-shaped mocked tool receipt.
- order_read: Provide a current synthetic order with one line.
- connection_read: Provide an active synthetic Gmail account.
- ActionBackend: Group isolated fixture and contract checks.
- ActionBackend.get_chat_tools: Publish the fixture customer and proposal capabilities.
- ChatActionTests: Group isolated fixture and contract checks.
- ChatActionTests.test_missing_connection_get_reports_required_read_before_preparation: Verify missing connection get reports required read before preparation.
- ChatActionTests.backend: Build an isolated queued backend for a specified question.
- ChatActionTests.test_order_preparation_displays_frozen_changes_and_stops_before_execution: Verify order preparation displays frozen changes and stops before execution.
- ChatActionTests.test_email_preview_preserves_body_and_exposes_all_recipients_without_sending: Verify email preview preserves body and exposes all recipients without sending.
- ChatActionTests.test_unsupported_backend_does_not_call_proposal_endpoint: Verify unsupported backend does not call proposal endpoint.
- ChatActionTests.test_stale_or_unread_order_never_prepares: Verify stale or unread order never prepares.
- ChatActionTests.test_mismatched_order_read_is_rejected: Verify mismatched order read is rejected.
- ChatActionTests.test_preparation_cannot_report_execution_or_change_payload: Verify preparation cannot report execution or change payload.
- ChatActionTests.test_yes_in_conversation_only_reads_pending_proposal: Verify yes in conversation only reads pending proposal.
- ChatActionTests.test_status_reports_distinguish_execution_and_uncertainty: Verify status reports distinguish execution and uncertainty.
- ChatActionTests.test_success_without_confirmation_or_wrong_id_is_rejected: Verify success without confirmation or wrong id is rejected.
- ChatActionTests.test_lost_prepare_response_does_not_retry: Verify lost prepare response does not retry.
- ChatActionTests.test_order_conflict_allows_fresh_read_before_a_new_proposal: Verify order conflict allows fresh read before a new proposal.
- ChatActionTests.test_unread_or_foreign_line_cannot_be_prepared: Verify unread or foreign line cannot be prepared.
- ChatActionTests.test_email_requires_customer_and_active_account_read: Verify email requires customer and active account read.
- ChatActionTests.test_prepared_sender_must_match_selected_account: Verify prepared sender must match selected account.
- ChatActionTests.test_backend_totals_keep_currency_and_unknown_values: Verify backend totals keep currency and unknown values.
- ChatActionTests.test_unpublished_tools_fail_in_workflow_and_backend_errors_propagate: Verify unpublished tools fail in workflow and backend errors propagate.
- ChatActionTests.test_catalog_requires_confirmation_mode_and_contract: Verify catalog requires confirmation mode and contract.
- ChatActionTests.test_reject_implicit_approval_header_injection_and_float_prices: Verify reject implicit approval header injection and float prices.
- ChatActionTests.test_http_proposal_uses_employee_agent_identity_without_approval_fields: Verify http proposal uses employee agent identity without approval fields.
Variable index:
- ORDER_ID: Synthetic order UUID.
- LINE_ID: Synthetic order-line UUID.
- CONNECTION_ID: Synthetic Gmail connection UUID.
- PROPOSAL_ID: Synthetic independent proposal UUID.
"""

import copy
import json
import unittest

from agent.clients.backend_api import BackendContractError, BackendRequestError, DjangoBackendClient
from agent.clients.chat_actions import (
    PREPARE_ORDER, PREPARE_EMAIL, GET_ACTION, CONFIRMATION_CONTRACT,
    validate_action_arguments, validate_action_receipt,
)
from agent.tests.test_http_backend import _Response, _Session
from agent.tests.test_workspace_chat import (
    COMPANY_ID, SECOND_ID, SEARCH_READ_ID, ToolBackend, QueueProvider,
    conversation_request, context_item, detail_evidence, detail_result,
)
from agent.workflows.chat import process_chat_once, _workspace_catalog, ChatValidationError

ORDER_ID = "44444444-4444-4444-8444-444444444444"
LINE_ID = "55555555-5555-4555-8555-555555555555"
CONNECTION_ID = "66666666-6666-4666-8666-666666666666"
PROPOSAL_ID = "77777777-7777-4777-8777-777777777777"


# Function: Construct one queued model tool decision.
# Inputs: `name`, `arguments`.
# Outputs: The configured fixture value or client; exhausted transport queues raise AssertionError.
# Logic: Return the action/name/arguments envelope without executing it.
# Constraints: Mocks isolate network/model boundaries; passing assertions does not prove live service availability.
def tool(name, **arguments):
    return {"action": "tool", "name": name, "arguments": arguments}


# Function: Provide explicit synthetic order changes and original revisions.
# Inputs: Instance fixture state and queued synthetic responses.
# Outputs: The configured fixture value or client; exhausted transport queues raise AssertionError.
# Logic: Return fixed header and line edits with decimal-string amounts.
# Constraints: Mocks isolate network/model boundaries; passing assertions does not prove live service availability.
def order_arguments():
    return {"order_id": ORDER_ID, "revision": 4, "changes": {"notes": "Deliver in October"},
            "line_changes": [{"line_id": LINE_ID, "revision": 2, "changes": {"quantity": "5", "unit_price": "1200.00"}}]}


# Function: Provide a frozen synthetic plaintext email.
# Inputs: Instance fixture state and queued synthetic responses.
# Outputs: The configured fixture value or client; exhausted transport queues raise AssertionError.
# Logic: Return explicit customer/account IDs, recipients, subject and body.
# Constraints: Mocks isolate network/model boundaries; passing assertions does not prove live service availability.
def email_arguments():
    return {"company_id": COMPANY_ID, "connection_id": CONNECTION_ID,
            "to": ["buyer@example.com"], "cc": [], "bcc": ["archive@example.com"],
            "subject": "Delivery plan", "body_text": "Hello,\nWe propose delivery in October."}


# Function: Construct a synthetic order or email proposal at a chosen status.
# Inputs: `kind`, `status`.
# Outputs: The configured fixture value or client; exhausted transport queues raise AssertionError.
# Logic: Select the matching arguments and preview; model confirmed status explicitly for validation tests.
# Constraints: Mocks isolate network/model boundaries; passing assertions does not prove live service availability.
def proposal(kind="order_update", status="pending_confirmation"):
    return {
        "id": PROPOSAL_ID, "kind": kind, "status": status, "revision": 1,
        "expires_at": "2030-01-01T12:00:00Z", "confirmed_by_employee": status in {"approved", "running", "succeeded", "uncertain"},
        "arguments": order_arguments() if kind == "order_update" else email_arguments(),
        "preview": {
            "company_name": "Acme", "order_number": "SO-100",
            "changes": [{"field": "notes", "before": "", "after": "Deliver in October"},
                        {"field": f"lines.{LINE_ID}.quantity", "before": "2", "after": "5"},
                        {"field": f"lines.{LINE_ID}.unit_price", "before": "1000.00", "after": "1200.00"}],
        } if kind == "order_update" else {"company_name": "Acme", "from_address": "sales@example.com"},
    }


# Function: Construct a canonical-shaped mocked tool receipt.
# Inputs: `name`, `data`.
# Outputs: The configured fixture value or client; exhausted transport queues raise AssertionError.
# Logic: Bind the supplied tool and data to a fixed request/read ID and JSON evidence.
# Constraints: Mocks isolate network/model boundaries; passing assertions does not prove live service availability.
def receipt(name, data):
    return {
        "request_id": "request-1", "tool": name, "status": "completed", "http_status": 200,
        "read_id": SEARCH_READ_ID, "data": data,
        "evidence_items": [context_item(source_id=f"chat-tool:{SEARCH_READ_ID}:{name}",
                                       content=json.dumps(data, ensure_ascii=False))],
    }


# Function: Provide a current synthetic order with one line.
# Inputs: Instance fixture state and queued synthetic responses.
# Outputs: The configured fixture value or client; exhausted transport queues raise AssertionError.
# Logic: Wrap IDs, revision and decimal-string values in the shared receipt fixture.
# Constraints: Mocks isolate network/model boundaries; passing assertions does not prove live service availability.
def order_read():
    return receipt("orders.get", {"id": ORDER_ID, "revision": 4, "company": COMPANY_ID,
                                 "number": "SO-100", "notes": "", "lines": [
                                     {"id": LINE_ID, "revision": 2, "quantity": "2", "unit_price": "1000.00"}]})


# Function: Provide an active synthetic Gmail account.
# Inputs: Instance fixture state and queued synthetic responses.
# Outputs: The configured fixture value or client; exhausted transport queues raise AssertionError.
# Logic: Wrap public account metadata without real credentials in the shared receipt fixture.
# Constraints: Mocks isolate network/model boundaries; passing assertions does not prove live service availability.
def connection_read():
    return receipt("connections.get", {"id": CONNECTION_ID, "provider": "gmail", "account": "sales@example.com", "archived": False})


# Function: Group isolated proposal capability fixtures.
# Logic: Use queued data or mocks while exercising real client and validation code.
# Constraints: No deployed backend, model, employee decisions or external providers are contacted.
class ActionBackend(ToolBackend):
    # Function: Publish the fixture customer and proposal capabilities.
    # Inputs: `request_id`.
    # Outputs: The configured fixture value or client; exhausted transport queues raise AssertionError.
    # Logic: Extend the parent fixture catalog with exact proposal modes and confirmation contracts.
    # Constraints: Mocks isolate network/model boundaries; passing assertions does not prove live service availability.
    def get_chat_tools(self, request_id):
        result = super().get_chat_tools(request_id)
        for name, args in (
            ("orders.get", {"id": ORDER_ID}), ("connections.get", {"id": CONNECTION_ID}),
            (PREPARE_ORDER, order_arguments()), (PREPARE_EMAIL, email_arguments()),
            (GET_ACTION, {"proposal_id": PROPOSAL_ID}),
        ):
            result["tools"].append({
                "name": name, "description": name,
                "executionMode": "confirm" if name in {PREPARE_ORDER, PREPARE_EMAIL} else "read",
                "confirmationContract": CONFIRMATION_CONTRACT,
                "inputSchema": {"type": "object", "properties": {key: {} for key in args},
                                "required": list(args), "additionalProperties": False},
            })
        result["count"] = len(result["tools"])
        return result


# Function: Group isolated transport and workflow regression assertions.
# Logic: Use queued data or mocks while exercising real client and validation code.
# Constraints: No deployed backend, model, employee decisions or external providers are contacted.
class ChatActionTests(unittest.TestCase):
    # Function: Verify missing connection get reports required read before preparation.
    # Inputs: Instance fixture state and queued synthetic responses.
    # Outputs: None; unittest assertions raise on contract violations.
    # Logic: Arrange isolated responses, invoke the real client or workflow, and assert the named output/error and call-boundary behavior.
    # Constraints: Mocks isolate network/model boundaries; passing assertions does not prove live service availability.
    def test_missing_connection_get_reports_required_read_before_preparation(self):
        args = email_arguments()
        customer = detail_result(COMPANY_ID, "Acme", detail_evidence(COMPANY_ID, "Acme", "Contact buyer@example.com"))
        backend = self.backend([customer, connection_read(), receipt(PREPARE_EMAIL, proposal('email_send'))])
        provider = QueueProvider(
            tool('customers.context', company_id=COMPANY_ID),
            tool(PREPARE_EMAIL, **args),
            tool('connections.get', id=CONNECTION_ID),
            tool(PREPARE_EMAIL, **args),
        )
        result = process_chat_once(backend=backend, chat_provider=provider)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual([row[1] for row in backend.tool_calls], ['customers.context', 'connections.get', PREPARE_EMAIL])
        correction_prompt = provider.calls[2][0][-1]['content']
        self.assertIn('connections.get', correction_prompt)
        self.assertIn('discovery only', correction_prompt)
        self.assertIn('Nothing has been changed or sent', result['assistant_text'])

    # Function: Build an isolated queued backend for a specified question.
    # Inputs: `replies`, `question`.
    # Outputs: The configured fixture value or client; exhausted transport queues raise AssertionError.
    # Logic: Construct ActionBackend with the supplied replies and a synthetic conversation request.
    # Constraints: Mocks isolate network/model boundaries; passing assertions does not prove live service availability.
    def backend(self, replies, question="Change order SO-100 to five units at 1200 each and note delivery in October."):
        return ActionBackend(request=conversation_request(question=question), replies=replies)

    # Function: Verify order preparation displays frozen changes and stops before execution.
    # Inputs: Instance fixture state and queued synthetic responses.
    # Outputs: None; unittest assertions raise on contract violations.
    # Logic: Arrange isolated responses, invoke the real client or workflow, and assert the named output/error and call-boundary behavior.
    # Constraints: Mocks isolate network/model boundaries; passing assertions does not prove live service availability.
    def test_order_preparation_displays_frozen_changes_and_stops_before_execution(self):
        backend = self.backend([order_read(), receipt(PREPARE_ORDER, proposal())])
        provider = QueueProvider(tool("orders.get", id=ORDER_ID), tool(PREPARE_ORDER, **order_arguments()))
        result = process_chat_once(backend=backend, chat_provider=provider)
        self.assertEqual(result["status"], "completed")
        self.assertIn("Nothing has been changed or sent", result["assistant_text"])
        self.assertIn("2 → 5", result["assistant_text"])
        self.assertIn("1000.00 → 1200.00", result["assistant_text"])
        self.assertEqual([row[1] for row in backend.tool_calls], ["orders.get", PREPARE_ORDER])
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(set(result), {"request_id", "chat_prompt_version", "assistant_text", "citations", "status", "error"})

    # Function: Verify email preview preserves body and exposes all recipients without sending.
    # Inputs: Instance fixture state and queued synthetic responses.
    # Outputs: None; unittest assertions raise on contract violations.
    # Logic: Arrange isolated responses, invoke the real client or workflow, and assert the named output/error and call-boundary behavior.
    # Constraints: Mocks isolate network/model boundaries; passing assertions does not prove live service availability.
    def test_email_preview_preserves_body_and_exposes_all_recipients_without_sending(self):
        args = email_arguments()
        args["body_text"] = "您好！\n原文报价：每台 1200 元。"
        args["subject"] = "原文报价"
        data = proposal("email_send")
        data["arguments"] = args
        backend = self.backend([
            detail_result(COMPANY_ID, "Acme", detail_evidence(COMPANY_ID, "Acme", "Buyer: buyer@example.com")),
            connection_read(), receipt(PREPARE_EMAIL, data)], "Draft an email to Acme for my approval.")
        result = process_chat_once(backend=backend, chat_provider=QueueProvider(
            tool("customers.context", company_id=COMPANY_ID), tool("connections.get", id=CONNECTION_ID), tool(PREPARE_EMAIL, **args)))
        self.assertEqual(result["status"], "completed")
        for expected in (args["body_text"], args["subject"], "From: sales@example.com", "Bcc: archive@example.com", "explicitly confirm"):
            self.assertIn(expected, result["assistant_text"])
        self.assertEqual(len(backend.tool_calls), 3)

    # Function: Verify unsupported backend does not call proposal endpoint.
    # Inputs: Instance fixture state and queued synthetic responses.
    # Outputs: None; unittest assertions raise on contract violations.
    # Logic: Arrange isolated responses, invoke the real client or workflow, and assert the named output/error and call-boundary behavior.
    # Constraints: Mocks isolate network/model boundaries; passing assertions does not prove live service availability.
    def test_unsupported_backend_does_not_call_proposal_endpoint(self):
        backend = ToolBackend(request=conversation_request(), replies=[])
        result = process_chat_once(backend=backend, chat_provider=QueueProvider(tool(PREPARE_ORDER, **order_arguments())))
        self.assertEqual(result["status"], "completed")
        self.assertIn("not available", result["assistant_text"])
        self.assertEqual(backend.tool_calls, [])

    # Function: Verify stale or unread order never prepares.
    # Inputs: Instance fixture state and queued synthetic responses.
    # Outputs: None; unittest assertions raise on contract violations.
    # Logic: Arrange isolated responses, invoke the real client or workflow, and assert the named output/error and call-boundary behavior.
    # Constraints: Mocks isolate network/model boundaries; passing assertions does not prove live service availability.
    def test_stale_or_unread_order_never_prepares(self):
        for replies, read_first, revision in (([], False, 4), ([order_read()], True, 3)):
            with self.subTest(read_first=read_first):
                args = order_arguments()
                args["revision"] = revision
                backend = self.backend(replies)
                calls = [tool("orders.get", id=ORDER_ID)] if read_first else []
                calls += [tool(PREPARE_ORDER, **args), {"action": "answer", "assistant_text": "The order needs to be read again before preparing changes.", "citations": []}]
                result = process_chat_once(backend=backend, chat_provider=QueueProvider(*calls))
                self.assertEqual(result["status"], "completed")
                self.assertNotIn(PREPARE_ORDER, [row[1] for row in backend.tool_calls])

    # Function: Verify mismatched order read is rejected.
    # Inputs: Instance fixture state and queued synthetic responses.
    # Outputs: None; unittest assertions raise on contract violations.
    # Logic: Arrange isolated responses, invoke the real client or workflow, and assert the named output/error and call-boundary behavior.
    # Constraints: Mocks isolate network/model boundaries; passing assertions does not prove live service availability.
    def test_mismatched_order_read_is_rejected(self):
        response = order_read()
        response["data"]["id"] = SECOND_ID
        backend = self.backend([response])
        result = process_chat_once(backend=backend, chat_provider=QueueProvider(tool("orders.get", id=ORDER_ID)))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(backend.tool_calls), 1)

    # Function: Verify preparation cannot report execution or change payload.
    # Inputs: Instance fixture state and queued synthetic responses.
    # Outputs: None; unittest assertions raise on contract violations.
    # Logic: Arrange isolated responses, invoke the real client or workflow, and assert the named output/error and call-boundary behavior.
    # Constraints: Mocks isolate network/model boundaries; passing assertions does not prove live service availability.
    def test_preparation_cannot_report_execution_or_change_payload(self):
        for bad in ("succeeded", "wrong_payload", "wrong_preview", "already_confirmed"):
            with self.subTest(bad=bad):
                data = proposal()
                if bad == "succeeded":
                    data["status"] = "succeeded"
                    data["confirmed_by_employee"] = True
                elif bad == "wrong_payload":
                    data["arguments"]["changes"]["notes"] = "Different"
                elif bad == "wrong_preview":
                    data["preview"]["changes"][0]["after"] = "Different"
                else:
                    data["confirmed_by_employee"] = True
                backend = self.backend([order_read(), receipt(PREPARE_ORDER, data)])
                result = process_chat_once(backend=backend, chat_provider=QueueProvider(tool("orders.get", id=ORDER_ID), tool(PREPARE_ORDER, **order_arguments())))
                self.assertEqual(result["status"], "failed")

    # Function: Verify yes in conversation only reads pending proposal.
    # Inputs: Instance fixture state and queued synthetic responses.
    # Outputs: None; unittest assertions raise on contract violations.
    # Logic: Arrange isolated responses, invoke the real client or workflow, and assert the named output/error and call-boundary behavior.
    # Constraints: Mocks isolate network/model boundaries; passing assertions does not prove live service availability.
    def test_yes_in_conversation_only_reads_pending_proposal(self):
        backend = self.backend([receipt(GET_ACTION, proposal())], "Yes, confirm that order change.")
        result = process_chat_once(backend=backend, chat_provider=QueueProvider(tool(GET_ACTION, proposal_id=PROPOSAL_ID)))
        self.assertIn("explicitly confirm", result["assistant_text"])
        self.assertEqual([row[1] for row in backend.tool_calls], [GET_ACTION])

    # Function: Verify status reports distinguish execution and uncertainty.
    # Inputs: Instance fixture state and queued synthetic responses.
    # Outputs: None; unittest assertions raise on contract violations.
    # Logic: Arrange isolated responses, invoke the real client or workflow, and assert the named output/error and call-boundary behavior.
    # Constraints: Mocks isolate network/model boundaries; passing assertions does not prove live service availability.
    def test_status_reports_distinguish_execution_and_uncertainty(self):
        for status, text in (("approved", "not complete"), ("running", "not been confirmed"),
                             ("succeeded", "provider accepted"), ("uncertain", "Do not repeat"),
                             ("conflicted", "record changed"), ("expired", "expired"),
                             ("cancelled", "cancelled"), ("failed", "failed")):
            with self.subTest(status=status):
                backend = self.backend([receipt(GET_ACTION, proposal("email_send", status))])
                result = process_chat_once(backend=backend, chat_provider=QueueProvider(tool(GET_ACTION, proposal_id=PROPOSAL_ID)))
                self.assertEqual(result["status"], "completed")
                self.assertIn(text, result["assistant_text"])
                self.assertEqual(len(backend.tool_calls), 1)

    # Function: Verify success without confirmation or wrong id is rejected.
    # Inputs: Instance fixture state and queued synthetic responses.
    # Outputs: None; unittest assertions raise on contract violations.
    # Logic: Arrange isolated responses, invoke the real client or workflow, and assert the named output/error and call-boundary behavior.
    # Constraints: Mocks isolate network/model boundaries; passing assertions does not prove live service availability.
    def test_success_without_confirmation_or_wrong_id_is_rejected(self):
        for field, value in (("confirmed_by_employee", False), ("id", SECOND_ID)):
            data = proposal(status="succeeded")
            data[field] = value
            with self.assertRaises(ValueError):
                validate_action_receipt(data, GET_ACTION, {"proposal_id": PROPOSAL_ID})

    # Function: Verify lost prepare response does not retry.
    # Inputs: Instance fixture state and queued synthetic responses.
    # Outputs: None; unittest assertions raise on contract violations.
    # Logic: Arrange isolated responses, invoke the real client or workflow, and assert the named output/error and call-boundary behavior.
    # Constraints: Mocks isolate network/model boundaries; passing assertions does not prove live service availability.
    def test_lost_prepare_response_does_not_retry(self):
        backend = self.backend([order_read(), BackendRequestError(0, "network_error", "timeout")])
        result = process_chat_once(backend=backend, chat_provider=QueueProvider(tool("orders.get", id=ORDER_ID), tool(PREPARE_ORDER, **order_arguments())))
        self.assertIn("pending actions", result["assistant_text"])
        self.assertEqual([row[1] for row in backend.tool_calls], ["orders.get", PREPARE_ORDER])

    # Function: Verify order conflict allows fresh read before a new proposal.
    # Inputs: Instance fixture state and queued synthetic responses.
    # Outputs: None; unittest assertions raise on contract violations.
    # Logic: Arrange isolated responses, invoke the real client or workflow, and assert the named output/error and call-boundary behavior.
    # Constraints: Mocks isolate network/model boundaries; passing assertions does not prove live service availability.
    def test_order_conflict_allows_fresh_read_before_a_new_proposal(self):
        updated = order_read()
        updated["data"]["revision"] = 5
        args = order_arguments()
        args["revision"] = 5
        data = proposal()
        data["arguments"] = args
        backend = self.backend([order_read(), BackendRequestError(409, "conflict", "changed", scope="tool"),
                                updated, receipt(PREPARE_ORDER, data)])
        result = process_chat_once(backend=backend, chat_provider=QueueProvider(
            tool("orders.get", id=ORDER_ID), tool(PREPARE_ORDER, **order_arguments()),
            tool("orders.get", id=ORDER_ID), tool(PREPARE_ORDER, **args)))
        self.assertEqual(result["status"], "completed")
        self.assertIn("prepared for your review", result["assistant_text"])
        self.assertEqual(len(backend.tool_calls), 4)

    # Function: Verify unread or foreign line cannot be prepared.
    # Inputs: Instance fixture state and queued synthetic responses.
    # Outputs: None; unittest assertions raise on contract violations.
    # Logic: Arrange isolated responses, invoke the real client or workflow, and assert the named output/error and call-boundary behavior.
    # Constraints: Mocks isolate network/model boundaries; passing assertions does not prove live service availability.
    def test_unread_or_foreign_line_cannot_be_prepared(self):
        args = order_arguments()
        args["line_changes"][0]["line_id"] = SECOND_ID
        backend = self.backend([order_read()])
        result = process_chat_once(backend=backend, chat_provider=QueueProvider(
            tool("orders.get", id=ORDER_ID), tool(PREPARE_ORDER, **args),
            {"action": "answer", "assistant_text": "The requested line is not in this order.", "citations": []}))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(backend.tool_calls), 1)

    # Function: Verify email requires customer and active account read.
    # Inputs: Instance fixture state and queued synthetic responses.
    # Outputs: None; unittest assertions raise on contract violations.
    # Logic: Arrange isolated responses, invoke the real client or workflow, and assert the named output/error and call-boundary behavior.
    # Constraints: Mocks isolate network/model boundaries; passing assertions does not prove live service availability.
    def test_email_requires_customer_and_active_account_read(self):
        for reply in (None, connection_read()):
            with self.subTest(has_account=reply is not None):
                replies, outputs = [], []
                if reply is not None:
                    replies.append(reply)
                    outputs.append(tool("connections.get", id=CONNECTION_ID))
                backend = self.backend(replies)
                result = process_chat_once(backend=backend, chat_provider=QueueProvider(
                    *outputs, tool(PREPARE_EMAIL, **email_arguments()),
                    {"action": "answer", "assistant_text": "I need the customer and sending account before preparation.", "citations": []}))
                self.assertEqual(result["status"], "completed")
                self.assertNotIn(PREPARE_EMAIL, [row[1] for row in backend.tool_calls])

    # Function: Verify prepared sender must match selected account.
    # Inputs: Instance fixture state and queued synthetic responses.
    # Outputs: None; unittest assertions raise on contract violations.
    # Logic: Arrange isolated responses, invoke the real client or workflow, and assert the named output/error and call-boundary behavior.
    # Constraints: Mocks isolate network/model boundaries; passing assertions does not prove live service availability.
    def test_prepared_sender_must_match_selected_account(self):
        data = proposal("email_send")
        data["preview"]["from_address"] = "someone-else@example.com"
        backend = self.backend([
            detail_result(COMPANY_ID, "Acme", detail_evidence(COMPANY_ID, "Acme", "Customer")),
            connection_read(), receipt(PREPARE_EMAIL, data)])
        result = process_chat_once(backend=backend, chat_provider=QueueProvider(
            tool("customers.context", company_id=COMPANY_ID), tool("connections.get", id=CONNECTION_ID),
            tool(PREPARE_EMAIL, **email_arguments())))
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("prepared for your review", result["assistant_text"])

    # Function: Verify backend totals keep currency and unknown values.
    # Inputs: Instance fixture state and queued synthetic responses.
    # Outputs: None; unittest assertions raise on contract violations.
    # Logic: Arrange isolated responses, invoke the real client or workflow, and assert the named output/error and call-boundary behavior.
    # Constraints: Mocks isolate network/model boundaries; passing assertions does not prove live service availability.
    def test_backend_totals_keep_currency_and_unknown_values(self):
        data = proposal()
        data["preview"].update(currency="USD", total_before=None, total_after="6000.00")
        backend = self.backend([order_read(), receipt(PREPARE_ORDER, data)])
        result = process_chat_once(backend=backend, chat_provider=QueueProvider(
            tool("orders.get", id=ORDER_ID), tool(PREPARE_ORDER, **order_arguments())))
        self.assertEqual(result["status"], "completed")
        self.assertIn("Total: unknown USD → 6000.00 USD", result["assistant_text"])

    # Function: Verify unpublished tools fail in workflow and backend errors propagate.
    # Inputs: Instance fixture state and queued synthetic responses.
    # Outputs: None; unittest assertions raise on contract violations.
    # Logic: Arrange isolated responses, invoke the real client or workflow, and assert the named output/error and call-boundary behavior.
    # Constraints: Mocks isolate network/model boundaries; passing assertions does not prove live service availability.
    def test_unpublished_tools_fail_in_workflow_and_backend_errors_propagate(self):
        for name in ("orders.update", "order_lines.update", "drafts.create", "actions.prepare_gmail", "chat_actions.approve", "experiments.update"):
            with self.subTest(name=name):
                session = _Session(_Response({"error": {"code": "invalid", "detail": "Unpublished or invalid call", "scope": "tool"}}, status=400))
                client = DjangoBackendClient("http://backend.test/api/v1/agent/", "test-token", session=session)
                with self.assertRaises(BackendRequestError):
                    client.read_chat_tool("request-1", name, {})
                self.assertEqual(len(session.calls), 1)
                backend = self.backend([])
                result = process_chat_once(backend=backend, chat_provider=QueueProvider(tool(name)))
                self.assertEqual(result["status"], "failed")
                self.assertEqual(backend.tool_calls, [])

    # Function: Verify catalog requires confirmation mode and contract.
    # Inputs: Instance fixture state and queued synthetic responses.
    # Outputs: None; unittest assertions raise on contract violations.
    # Logic: Arrange isolated responses, invoke the real client or workflow, and assert the named output/error and call-boundary behavior.
    # Constraints: Mocks isolate network/model boundaries; passing assertions does not prove live service availability.
    def test_catalog_requires_confirmation_mode_and_contract(self):
        for field, value in (("executionMode", "write"), ("confirmationContract", None)):
            data = self.backend([]).get_chat_tools("request-1")
            entry = next(item for item in data["tools"] if item["name"] == PREPARE_ORDER)
            entry[field] = value
            with self.assertRaises(ChatValidationError):
                _workspace_catalog(data, "request-1")

    # Function: Verify reject implicit approval header injection and float prices.
    # Inputs: Instance fixture state and queued synthetic responses.
    # Outputs: None; unittest assertions raise on contract violations.
    # Logic: Arrange isolated responses, invoke the real client or workflow, and assert the named output/error and call-boundary behavior.
    # Constraints: Mocks isolate network/model boundaries; passing assertions does not prove live service availability.
    def test_reject_implicit_approval_header_injection_and_float_prices(self):
        for name, args in ((PREPARE_EMAIL, {**email_arguments(), "approved": True}),
                           (PREPARE_EMAIL, {**email_arguments(), "subject": "Hello\r\nBcc: unknown@example.com"}),
                           (PREPARE_EMAIL, {**email_arguments(), "to": ["a@example.com\nb@example.com"]}),
                           (PREPARE_ORDER, {**order_arguments(), "changes": {"status": "confirmed"}})):
            with self.assertRaises(ValueError):
                validate_action_arguments(name, args)
        args = order_arguments()
        args["line_changes"][0]["changes"]["unit_price"] = 1200.0
        with self.assertRaises(ValueError):
            validate_action_arguments(PREPARE_ORDER, args)

    # Function: Verify http proposal uses employee agent identity without approval fields.
    # Inputs: Instance fixture state and queued synthetic responses.
    # Outputs: None; unittest assertions raise on contract violations.
    # Logic: Arrange isolated responses, invoke the real client or workflow, and assert the named output/error and call-boundary behavior.
    # Constraints: Mocks isolate network/model boundaries; passing assertions does not prove live service availability.
    def test_http_proposal_uses_employee_agent_identity_without_approval_fields(self):
        session = _Session(_Response(receipt(PREPARE_ORDER, proposal())))
        client = DjangoBackendClient("http://backend.test/api/v1/agent/", "test-token", session=session)
        client.read_chat_tool("request-1", PREPARE_ORDER, order_arguments())
        method, url, kwargs = session.calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/chat/tool-reads/"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Agent test-token")
        self.assertEqual(kwargs["json"], {"request_id": "request-1", "name": PREPARE_ORDER, "arguments": order_arguments()})


if __name__ == "__main__":
    unittest.main()
