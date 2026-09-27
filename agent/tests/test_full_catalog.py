"""Responsibility: Verify dynamic tool discovery and arbitrary business receipts.
Implementation: Use queued HTTP responses and model decisions with real Agent code.
Relationships: Covers backend_api pagination and workflow catalog/result validation.
Directory:
- FullCatalogTests: Offline dynamic catalog contracts.
- FullCatalogTests.test_all_pages_and_inconsistent_catalog: Verify complete discovery.
- FullCatalogTests.test_generic_modes_checkpoint_and_resume: Follow generic writes through suspension.
- FullCatalogTests.test_generic_json_shapes_and_statuses: Preserve result data and pending status.
Variable index:
- None
"""

import copy
import json
import unittest

from agent.clients.backend_api import BackendContractError, DjangoBackendClient
from agent.tests.test_http_backend import _Response, _Session
from agent.tests.test_workspace_chat import ToolBackend, QueueProvider, conversation_request
from agent.tests.test_chat_actions import receipt, tool
from agent.workflows.chat import process_chat_once, _workspace_tool_result


# Function: Test discovery and workflow contracts independently of external services.
# Logic: Only transport/model responses are simulated; production parsers and workflow execute.
# Constraints: Passing these tests does not verify a deployed backend or real model.
class FullCatalogTests(unittest.TestCase):
    # Function: Require all pages and reject a partial or changing directory.
    # Inputs: Thirty-one synthetic tool entries and malformed second-page variants.
    # Outputs: Complete unique list or explicit contract failure without retries.
    # Logic: Request pages one and two and compare emitted query parameters.
    # Constraints: Page size remains thirty; no static tool-name assumptions.
    def test_all_pages_and_inconsistent_catalog(self):
        first = {"contract_version": "chat-tools-v1", "request_id": "request-1", "count": 31, "page": 1, "page_size": 30,
                 "tools": [{"name": f"dynamic.{i}"} for i in range(30)]}
        second = {**first, "page": 2, "tools": [{"name": "dynamic.last"}]}
        session = _Session(_Response(first), _Response(second))
        backend = DjangoBackendClient("http://backend.test/api/v1/agent/", "token", session=session)
        self.assertEqual(len(backend.get_chat_tools("request-1")["tools"]), 31)
        self.assertIn("page=1&page_size=30", session.calls[0][1])
        self.assertIn("page=2&page_size=30", session.calls[1][1])
        for changed in ({"tools": []}, {"count": 32}, {"tools": [{"name": "dynamic.0"}]}, {"page": 3}):
            session = _Session(_Response(first), _Response({**second, **changed}))
            backend = DjangoBackendClient("http://backend.test/api/v1/agent/", "token", session=session)
            with self.assertRaises(BackendContractError):
                backend.get_chat_tools("request-1")

    # Function: Recognize previously unknown generic write and confirm capabilities.
    # Inputs: Live-style catalog entries and queued pending/accepted receipts.
    # Outputs: Checkpoint suspension followed by receipt-only resume without replay.
    # Logic: Publish a synthetic future capability and inspect the first model prompt and resumed observations.
    # Constraints: Backend persistence is simulated here and independently covered by integration tests.
    def test_generic_modes_checkpoint_and_resume(self):
        for mode in ("write", "confirm"):
            backend = ToolBackend(request=conversation_request(), replies=[])
            catalog = backend.get_chat_tools("request-1")
            catalog["tools"].append({"name": "future.create", "executionMode": mode, "description": "Create a future resource",
                "inputSchema": {"type": "object", "properties": {"data": {"type": "object"}}, "required": ["data"], "additionalProperties": False}})
            backend.get_chat_tools = lambda request_id: copy.deepcopy(catalog)
            pending = {"request_id": "request-1", "tool": "future.create", "status": "approval_required"}
            from unittest.mock import Mock
            backend.read_chat_tool = Mock(return_value=pending)
            provider = QueueProvider(tool("future.create", data={"name": "Requested"}))
            result = process_chat_once(backend=backend, chat_provider=provider)
            self.assertEqual(result["status"], "awaiting_approval")
            checkpoint = backend.read_chat_tool.call_args.kwargs["continuation"]
            self.assertIn("future.create", provider.calls[0][0][-1]["content"])
            returned = receipt("future.create", {"job_id": "pending-job"})
            returned.update(status="accepted", http_status=202)
            backend.request = {**conversation_request(), "resume": {"continuation": checkpoint, "tool_result": returned,
                "arguments": {"data": {"name": "Requested"}}, "evidence_items": returned["evidence_items"]}}
            backend.read_chat_tool.reset_mock()
            provider = QueueProvider({"action": "answer", "assistant_text": "The operation is queued.", "citations": []})
            result = process_chat_once(backend=backend, chat_provider=provider)
            self.assertEqual(result["status"], "completed", result)
            backend.read_chat_tool.assert_not_called()
            prompt = json.loads(provider.calls[0][0][-1]["content"])
            self.assertEqual(prompt["tool_results"][-1]["status"], "accepted")

    # Function: Preserve generic JSON values and distinguish queued/pending receipts.
    # Inputs: Object, array, scalar and null data with successful business statuses.
    # Outputs: Exact data/status in summaries and stable canonical evidence.
    # Logic: Run the same parser for every shape without assuming customer-context fields.
    # Constraints: Does not infer external execution from receipt acceptance.
    def test_generic_json_shapes_and_statuses(self):
        for data in ({"id": "business-id"}, [1, 2], "text", None):
            for status in ("completed", "accepted", "confirmation_required"):
                result = receipt("future.operation", data)
                result.update(status=status, http_status=202 if status != "completed" else 200)
                summary, evidence = _workspace_tool_result(result, "request-1", "future.operation")
                self.assertEqual(summary["data"], data)
                self.assertEqual(summary["status"], status)
                self.assertEqual(evidence, result["evidence_items"])
