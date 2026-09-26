"""Responsibility: Verify schema and argument preservation for source-idempotent graph tools in the MCP bridge.
Implementation: Mock the backend catalog, not database success; use actual MCP protocol types.
Relationships: mcp_server.Bridge; separate integration checks cover real stdio and HTTP/database behavior.
Directory:
- GraphBridgeTests: Source-idempotency adapter tests.
- GraphBridgeTests.test_source_key_is_not_uuid: Ensure schemas do not insert transport UUIDs.
- GraphBridgeTests.test_call_preserves_source_payload: Preserve source envelopes during calls.
Variable index:
- None
"""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from integrations.salesmate_tools.mcp_server import Bridge


# Function: Verify that MCP preserves the source-idempotency contract.
# Logic: Replace the HTTP client only and execute actual bridge methods.
# Constraints: These tests do not establish real model-weight or database success.
class GraphBridgeTests(unittest.IsolatedAsyncioTestCase):
    # Function: Preserve source_key and omit idempotency_key.
    # Inputs: A mocked backend source-write descriptor.
    # Outputs: Tool schema and non-read-only annotation assertions.
    # Logic: Use the same catalog method as ordinary UUID writes.
    # Constraints: Never label graph.ingest read-only.
    async def test_source_key_is_not_uuid(self):
        client = Mock()
        client.catalog.return_value = {"tools": [{"name": "graph.ingest", "description": "source write",
            "executionMode": "write", "idempotency_scope": "source_key", "idempotency_required": False,
            "inputSchema": {"type": "object", "properties": {"source_key": {"type": "string"}}, "required": ["source_key"]},
            "annotations": {"readOnlyHint": False, "idempotentHint": True}}], "page_size": 100, "count": 1}
        result = await Bridge(client).list_tools(None, None)
        self.assertNotIn("idempotency_key", result.tools[0].input_schema["properties"])
        self.assertFalse(result.tools[0].annotations.read_only_hint)

    # Function: Forward source envelopes and audit results unchanged.
    # Inputs: Synthetic source keys/text and mocked HTTP receipts.
    # Outputs: Client argument and structuredContent assertions.
    # Logic: Do not rewrite source keys or add transport UUIDs.
    # Constraints: Mock receipts do not prove database persistence.
    async def test_call_preserves_source_payload(self):
        client = Mock()
        client.call.return_value = {"tool": "graph.ingest", "status": "completed", "data": {"id": "synthetic"}}
        payload = {"source_key": "mail-1", "text": "Sample"}
        result = await Bridge(client).call_tool(None, SimpleNamespace(name="graph.ingest", arguments=payload))
        client.call.assert_called_once_with("graph.ingest", payload, None)
        self.assertEqual(result.structured_content, client.call.return_value)
