"""Responsibility: Verify Agent approval suspension, lost-response reconciliation, and unchanged continuation budgets.
Implementation: Replace only backend transport and model calls with explicit fixture boundaries; inspect checkpoints, final reports, and remaining tool turns.
Relationships: workflows.chat; real HTTP/PostgreSQL behavior is verified by backend approval integration tests.
Directory:
- ApprovalBackend: Minimal transport fixture for one write.
- ApprovalBackend.__init__: Initialize request, call capture, and lost-response switch.
- ApprovalBackend.get_chat_tools: Publish the fixture write schema.
- ApprovalBackend.read_chat_tool: Capture the checkpoint and return a pending approval or lost response.
- ApprovalBackend.get_chat_request_status: Reconcile persisted suspension.
- ApprovalWorkflowTests: Unit checks for approval control flow.
- ApprovalWorkflowTests.test_suspend_and_lost_response_do_not_report: Release the worker without a terminal answer.
- ApprovalWorkflowTests.test_resume_at_last_turn_does_not_reset_budget: Resume using canonical evidence and zero remaining tool turns.
- ApprovalWorkflowTests.test_invalid_checkpoint_fails_before_model: Reject an invalid checkpoint without model execution.
Variable index:
- None
"""

import copy
import json
import unittest
import uuid

from agent.clients.backend_api import BackendRequestError
from agent.tests.test_workspace_chat import InMemoryChatBackend, QueueProvider, conversation_request
from agent.workflows.chat import answer_workspace_request, process_chat_once


# Function: Model a transport boundary with a durable pending write.
# Logic: Provide one registered write and retain its request checkpoint for assertions.
# Constraints: This fixture does not prove backend authorization or real mutation behavior.
class ApprovalBackend(InMemoryChatBackend):
    # Function: Initialize an isolated pending-approval transport.
    # Inputs: Optional ``lost`` flag simulates a response lost after backend commit.
    # Outputs: Instance with tool_calls and inherited report capture.
    # Logic: Use an explicit synthetic-data question that may enter the chat write path.
    # Constraints: No real network or database calls.
    def __init__(self, lost=False):
        super().__init__(request=conversation_request(question="请新增虚构实验产品"))
        self.lost = lost
        self.tool_calls = []

    # Function: Publish one explicitly allowed maintenance operation.
    # Inputs: ``request_id`` binds the fixture catalog.
    # Outputs: Schema-valid chat-tools-v1 catalog.
    # Logic: Keep the parameter contract closed while allowing arbitrary product data fields.
    # Constraints: Publication alone is not approval.
    def get_chat_tools(self, request_id):
        return {"contract_version": "chat-tools-v1", "request_id": request_id, "count": 1, "page": 1, "page_size": 30,
                "tools": [{"name": "experiments.create", "description": "Create synthetic product", "executionMode": "write",
                           "inputSchema": {"type": "object", "properties": {"batch": {"type": "string"}, "model": {"type": "string"}, "data": {"type": "object"}},
                                           "required": ["batch", "model", "data"], "additionalProperties": False}}]}

    # Function: Capture a proposal without pretending to execute it.
    # Inputs: ``request_id``, ``name``, exact ``arguments``, and keyword-only ``continuation``.
    # Outputs: approval_required response or explicit transport failure.
    # Logic: Record the checkpoint first so the lost-response case represents a committed suspension.
    # Constraints: No automatic retry and no mutation evidence is generated.
    def read_chat_tool(self, request_id, name, arguments, *, continuation):
        self.tool_calls.append(copy.deepcopy(continuation))
        if self.lost:
            raise BackendRequestError(status_code=0, code="network", detail="response lost")
        return {"request_id": request_id, "tool": name, "status": "approval_required"}

    # Function: Return authoritative suspension after a lost response.
    # Inputs: ``request_id`` being reconciled.
    # Outputs: Request ID and awaiting_approval status.
    # Logic: Read fixture state without repeating the write.
    # Constraints: Reconciliation is an observation, not a retry or approval.
    def get_chat_request_status(self, request_id):
        return {"request_id": request_id, "status": "awaiting_approval"}


# Function: Verify approval-aware Agent orchestration independently of the LLM.
# Logic: Use deterministic provider outputs and explicit canonical receipt fixtures.
# Constraints: Mocked transport tests complement, and do not replace, real backend integration tests.
class ApprovalWorkflowTests(unittest.TestCase):
    # Function: Ensure pending approval never produces a terminal report or consumes more model calls.
    # Inputs: A successful proposal and a response-loss variant.
    # Outputs: One model call, one proposal, correct checkpoint, and no final report in either case.
    # Logic: Invoke the same process_chat_once used by the shared Worker.
    # Constraints: Both variants retain the original six-call tool budget.
    def test_suspend_and_lost_response_do_not_report(self):
        for lost in (False, True):
            with self.subTest(lost=lost):
                backend = ApprovalBackend(lost=lost)
                provider = QueueProvider({"action": "tool", "name": "experiments.create", "arguments": {"batch": "KGSEED_20260921_01", "model": "sales.Product", "data": {"name": "Approved only"}}})
                result = process_chat_once(backend=backend, chat_provider=provider)
                self.assertEqual(result["status"], "awaiting_approval")
                self.assertEqual(len(provider.calls), 1)
                self.assertEqual(len(backend.tool_calls), 1)
                self.assertEqual(backend.tool_calls[0]["next_turn"], 1)
                self.assertEqual(backend.report_calls, [])

    # Function: Resume the last permitted turn without replaying any write.
    # Inputs: Five previous observations and one canonical approved write receipt.
    # Outputs: Zero remaining_reads, one final model call, and no new tool call.
    # Logic: Restore all prior observations and append the approved receipt before asking for the final answer.
    # Constraints: The receipt fixture is explicitly synthetic and does not assert a real external mutation.
    def test_resume_at_last_turn_does_not_reset_budget(self):
        backend = ApprovalBackend()
        read_id = str(uuid.uuid4())
        evidence = {"source_id": f"chat-tool:{read_id}:mutation", "source_type": "experiment_mutation", "title_or_label": "Test receipt", "content": "Synthetic mutation completed"}
        backend.request["resume"] = {
            "continuation": {"next_turn": 6, "observations": [{"tool": "experiments.rows", "status": "completed"}] * 5, "signatures": []},
            "arguments": {}, "evidence_items": [evidence],
            "tool_result": {"request_id": "request-1", "tool": "experiments.create", "status": "completed", "http_status": 200,
                            "read_id": read_id, "evidence_items": [evidence],
                            "data": {"batch": "KGSEED_20260921_01", "model": "sales.Product", "pk": "test", "operation": "create", "synthetic": True, "audit": {}}},
        }
        provider = QueueProvider({"action": "answer", "assistant_text": "已完成。", "citations": []})
        result = process_chat_once(backend=backend, chat_provider=provider)
        self.assertEqual(result["status"], "completed")
        prompt = json.loads(provider.calls[0][0][-1]["content"])
        self.assertEqual(prompt["remaining_reads"], 0)
        self.assertEqual(len(prompt["tool_results"]), 6)
        self.assertEqual(backend.tool_calls, [])
        self.assertEqual(len(backend.report_calls), 1)

    # Function: Reject an invalid resume position before consuming a model call.
    # Inputs: Backend claim with an out-of-budget checkpoint.
    # Outputs: Failed local result and no provider calls.
    # Logic: Validate restored state at the workflow boundary.
    # Constraints: The direct workflow test avoids the separately tested lost-response reconciliation fixture.
    def test_invalid_checkpoint_fails_before_model(self):
        backend = ApprovalBackend()
        backend.request["resume"] = {"continuation": {"next_turn": 7, "observations": [], "signatures": []},
                                     "arguments": {}, "evidence_items": [], "tool_result": {}}
        provider = QueueProvider()
        result = answer_workspace_request(backend.request, backend=backend, chat_provider=provider)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(provider.calls, [])
