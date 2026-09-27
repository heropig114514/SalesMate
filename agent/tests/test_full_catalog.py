"""Responsibility: Verify dynamic tool discovery and arbitrary business receipts.
Implementation: Use queued HTTP responses and model decisions with real Agent code.
Relationships: Covers backend_api pagination and workflow catalog/result validation.
Directory:
- FullCatalogTests: Offline dynamic catalog contracts.
- FullCatalogTests.test_all_pages_and_inconsistent_catalog: Verify complete discovery.
- FullCatalogTests.test_generic_modes_checkpoint_and_resume: Follow generic writes through suspension.
- FullCatalogTests.test_generic_json_shapes_and_statuses: Preserve result data and pending status.
- FullCatalogTests.test_array_decisions_remain_invalid: Reject array-wrapped model calls without executing them.
- FullCatalogTests.test_effective_pagination_schema: Align model-visible pagination with unchanged Agent limits without mutating backend declarations.
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
from agent.workflows.chat import process_chat_once, _workspace_tool_result, _decode_json_object, _workspace_catalog, _workspace_arguments, ChatValidationError


# Function: Test discovery and workflow contracts independently of external services.
# Logic: Only transport/model responses are simulated; production parsers and workflow execute.
# Constraints: Passing these tests does not verify a deployed backend or real model.
class FullCatalogTests(unittest.TestCase):
    # Function: Prevent publishing backend page sizes that existing Agent validation rejects.
    # Inputs: Synthetic schemas with backend maxima of 100, 10, or unspecified, plus an unrestricted generic tool.
    # Outputs: Effective maxima never exceed the Agent's existing 20-row bound; original schemas remain identical.
    # Logic: Parse real catalog code, select the advertised maximum, and verify acceptance and rejection at the unchanged boundary.
    # Constraints: No model or backend service is invoked; no page-size parameter or fallback policy changes.
    def test_effective_pagination_schema(self):
        for maximum in (100, 10, None):
            page = {"type": "integer", "minimum": 1}
            if maximum is not None:
                page["maximum"] = maximum
            raw = {"contract_version": "chat-tools-v1", "request_id": "request-1", "tools": [
                {"name": name, "executionMode": "read", "inputSchema": {
                    "type": "object", "properties": {"page_size": dict(page)}, "required": [], "additionalProperties": False}}
                for name in ("customers.search", "experiments.rows", "orders.list", "connections.list", "future.list")
            ]}
            original = copy.deepcopy(raw)
            catalog = _workspace_catalog(raw, "request-1")
            self.assertEqual(raw, original)
            self.assertEqual(set(catalog), {entry["name"] for entry in raw["tools"]})
            for name in ("customers.search", "experiments.rows", "orders.list", "connections.list"):
                advertised = catalog[name]["inputSchema"]["properties"]["page_size"]["maximum"]
                self.assertEqual(advertised, min(maximum or 20, 20))
                self.assertEqual(_workspace_arguments(name, {"page_size": advertised})[1]["page_size"], advertised)
                with self.assertRaisesRegex(ChatValidationError, "Invalid search page size"):
                    _workspace_arguments(name, {"page_size": 21})
            self.assertEqual(catalog["future.list"]["inputSchema"]["properties"]["page_size"], page)

    # Function: Preserve strict single-object model output despite provider JSON arrays.
    # Inputs: A syntactically valid array containing one product-update decision.
    # Outputs: A redacted error naming the root type; no operation execution.
    # Logic: Exercise the production decoder without coercion, unwrapping or retry.
    # Constraints: The provider is not invoked and no model content enters the error message.
    def test_array_decisions_remain_invalid(self):
        with self.assertRaisesRegex(ChatValidationError, "received list"):
            _decode_json_object(json.dumps([tool("products.update", data={"unit_price": "15.75"})]))

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
