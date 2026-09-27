"""Responsibility: Verify client transport and actual MCP stdio handshakes.
Implementation: Local HTTP fixtures, real SDK subprocesses, and protocol requests without external accounts.
Relationships: client, cli, mcp_server; does not replace Django authorization integration tests.
Directory:
- Handler: Mock the business HTTP protocol.
- Handler.log_message: Prevent access logs from polluting output.
- Handler.do_GET: Mock a paginated catalog.
- Handler.do_POST: Mock calls and errors.
- Handler.reply: Output JSON.
- ClientTests: HTTP and command-line checks.
- ClientTests.setUp: Start the test HTTP service.
- ClientTests.tearDown: Release the service/thread.
- ClientTests.test_cli_and_key_forwarding: Verify file arguments and idempotency keys.
- ClientTests.test_transport_errors_and_redirects: Verify no retries or token leakage.
- ClientTests.test_configuration_and_fixed_endpoints: Reject unsafe addresses and unknown endpoints.
- ClientTests.test_timeout_configuration_and_model_errors: Verify explicit long timeouts and forwarded model errors.
- ProtocolTests: Actual subprocess MCP checks.
- ProtocolTests.test_anonymous_stdio: Verify tokenless protocol fixtures and optional idempotency keys against a mock server.
- ProtocolTests.test_stdio_catalog_call_and_error: Verify SDK handshakes, pagination, write arguments, and error flags.
Variable index:
- ROOT: Repository root.
"""

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch
import requests
from mcp import Client, StdioServerParameters
from integrations.salesmate_tools.client import ToolClient, ToolError
from integrations.salesmate_tools.cli import main

ROOT = Path(__file__).resolve().parents[3]


# Function: Mock a fixed protocol.
# Logic: Model production mode with synthetic authorization and experiment mode without authorization.
# Constraints: No Django or real business access.
class Handler(BaseHTTPRequestHandler):
    # Function: Disable test access logs.
    # Inputs: `format` and `args`.
    # Outputs: None.
    # Logic: Assertions check fixture requests.
    # Constraints: Do not affect logs of the tested service.
    def log_message(self, format, *args):
        pass

    # Function: Mock two catalog pages.
    # Inputs: HTTP path and optional Authorization.
    # Outputs: One tool-description page.
    # Logic: Place read/write tools on separate pages.
    # Constraints: Page counts serve protocol testing only.
    def do_GET(self):
        if self.headers.get("Authorization") not in (None, "Tool fixture-token"):
            return self.reply(401, {})
        second = "page=2" in self.path
        spec = {
            "name": "customers.create" if second else "customers.search",
            "description": "fixture",
            "inputSchema": {
                "type": "object",
                "properties": {"name": {"type": "string"}} if second else {},
                "required": ["name"] if second else [],
                "additionalProperties": False,
            },
            "executionMode": "write" if second else "read",
            "idempotency_required": bool(self.headers.get("Authorization")),
            "annotations": {"readOnlyHint": not second},
        }
        self.reply(
            200,
            {"tools": [spec], "page": 2 if second else 1, "page_size": 1, "count": 2},
        )

    # Function: Record calls.
    # Inputs: A JSON envelope.
    # Outputs: completed or a test error.
    # Logic: Echo token-free payloads to verify adaptation.
    # Constraints: Do not simulate successful business authorization.
    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.reply(
            403 if payload["name"] == "denied" else 200,
            {"status": "completed", "echo": payload},
        )

    # Function: Emit a fixed response.
    # Inputs: `status` and `data`.
    # Outputs: JSON bytes.
    # Logic: Declare length and encoding.
    # Constraints: Test fixtures only.
    def reply(self, status, data):
        content = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


# Function: Verify client errors and arguments.
# Logic: Local HTTP service and limited network mocks.
# Constraints: No production requests.
class ClientTests(unittest.TestCase):
    # Function: Start a local fixture.
    # Inputs: No external arguments.
    # Outputs: Service, thread, and client instance state.
    # Logic: Let the operating system allocate a free port.
    # Constraints: Listen on loopback only.
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.client = ToolClient(self.url, "fixture-token")

    # Function: Release the fixture.
    # Inputs: Instance service/thread.
    # Outputs: None.
    # Logic: Stop listening and join the thread.
    # Constraints: Never stop other services.
    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    # Function: Verify CLI file arguments and keys.
    # Inputs: Temporary JSON files and a synthetic environment.
    # Outputs: Parseable stdout JSON with original keys retained.
    # Logic: Call the actual CLI main function and HTTP transport.
    # Constraints: No business changes.
    def test_cli_and_key_forwarding(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "arguments.json"
            source.write_text('{"name":"客户"}', encoding="utf-8")
            output = io.StringIO()
            with patch.dict(
                os.environ,
                {
                    "SALESMATE_TOOLS_URL": self.url,
                    "SALESMATE_TOOLS_TOKEN": "fixture-token",
                },
            ), contextlib.redirect_stdout(output):
                code = main(
                    [
                        "call",
                        "customers.create",
                        "--arguments-file",
                        str(source),
                        "--idempotency-key",
                        "00000000-0000-4000-8000-000000000001",
                    ]
                )
            self.assertEqual(code, 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["echo"]["arguments"], {"name": "客户"})
            self.assertEqual(
                result["echo"]["idempotency_key"],
                "00000000-0000-4000-8000-000000000001",
            )
        self.assertEqual(
            self.client.describe("customers.create")["executionMode"], "write"
        )

    # Function: Verify network failure policy.
    # Inputs: Redirects, timeouts, and rejection responses.
    # Outputs: Errors without retries or token echoing.
    # Logic: Mock only individual requests calls.
    # Constraints: A timeout must not imply nonexecution.
    def test_transport_errors_and_redirects(self):
        with patch(
            "integrations.salesmate_tools.client.requests.request",
            return_value=Mock(status_code=302),
        ) as request:
            with self.assertRaises(ToolError):
                self.client.catalog()
            request.assert_called_once()
            self.assertFalse(request.call_args.kwargs["allow_redirects"])
        with patch(
            "integrations.salesmate_tools.client.requests.request",
            side_effect=requests.Timeout("fixture-token"),
        ) as request:
            with self.assertRaises(ToolError) as error:
                self.client.call("customers.create", {})
            self.assertNotIn("fixture-token", str(error.exception))
            request.assert_called_once()
        with self.assertRaisesRegex(ToolError, "403"):
            self.client.call("denied", {})

    # Function: Verify connection configuration boundaries.
    # Inputs: Non-TLS external addresses, credential-bearing URLs, and invalid paths.
    # Outputs: Explicit rejection.
    # Logic: Validate before network access.
    # Constraints: Do not provide arbitrary HTTP proxy capabilities.
    def test_configuration_and_fixed_endpoints(self):
        for url in (
            "http://example.com",
            "https://secret@example.com",
            "https://example.com/?token=x",
            "https://example.com/path",
        ):
            with self.assertRaises(ToolError):
                ToolClient(url, "token")
        with self.assertRaises(ToolError):
            self.client.request("POST", "credentials/")

    # Function: Verify explicit long-request timeouts and that graph-unready errors cannot become successes.
    # Inputs: No external arguments; synthetic dedicated environment variables and 503 responses.
    # Outputs: Assert the 30-second default, forwarded 600-second override, rejected invalid values, and retained 503 business codes.
    # Logic: Mock network responses only; no model-service connection.
    # Constraints: Do not change timeout defaults or retry automatically.
    def test_timeout_configuration_and_model_errors(self):
        with patch.dict(os.environ, {"SALESMATE_TOOLS_URL": "http://127.0.0.1"}, clear=True):
            self.assertEqual(ToolClient.from_env().timeout, 30)
            os.environ["SALESMATE_TOOLS_TIMEOUT"] = "600"
            self.assertEqual(ToolClient.from_env().timeout, 600)
            for value in ["nan", "inf", "0", "3601", "bad"]:
                os.environ["SALESMATE_TOOLS_TIMEOUT"] = value
                with self.assertRaises(ToolError):
                    ToolClient.from_env()
        response = Mock(status_code=503)
        response.json.return_value = {"error": {"code": "graph_unavailable", "detail": "Model not configured"}}
        with patch("integrations.salesmate_tools.client.requests.request", return_value=response) as request:
            with self.assertRaisesRegex(ToolError, "graph_unavailable"):
                self.client.call("graph.status", {})
            request.assert_called_once()


# Function: Verify actual MCP processes.
# Logic: SDK clients communicate with server subprocesses through stdio.
# Constraints: HTTP uses fixtures and cannot establish production connectivity.
class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    # Function: Verify protocol handshakes, catalogs, and calls.
    # Inputs: Local HTTP fixture and synthetic token.
    # Outputs: Two catalog pages, independent write schemas, structured results, and isError assertions.
    # Logic: Start a real Python MCP subprocess and finally release the HTTP thread.
    # Constraints: stdout contains protocol content only; successful handshakes check that logs have not polluted it.
    async def test_stdio_catalog_call_and_error(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "integrations.salesmate_tools.mcp_server"],
                cwd=str(ROOT),
                env={
                    **os.environ,
                    "SALESMATE_TOOLS_URL": f"http://127.0.0.1:{server.server_port}",
                    "SALESMATE_TOOLS_TOKEN": "fixture-token",
                },
            )
            async with Client(params) as client:
                first = await client.list_tools()
                self.assertEqual(first.next_cursor, "2")
                self.assertNotIn(
                    "idempotency_key", first.tools[0].input_schema["properties"]
                )
                second = await client.list_tools(cursor=first.next_cursor)
                self.assertIn(
                    "idempotency_key", second.tools[0].input_schema["required"]
                )
                self.assertIsNone(second.next_cursor)
                result = await client.call_tool(
                    "customers.create",
                    {
                        "name": "new",
                        "idempotency_key": "00000000-0000-4000-8000-000000000001",
                    },
                )
                self.assertFalse(result.is_error)
                self.assertEqual(
                    result.structured_content["echo"]["arguments"], {"name": "new"}
                )
                self.assertEqual(
                    result.structured_content["echo"]["idempotency_key"],
                    "00000000-0000-4000-8000-000000000001",
                )
                denied = await client.call_tool("denied", {})
                self.assertTrue(denied.is_error)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    # Function: Verify tokenless MCP handshakes and write arguments against an isolated mock server.
    # Inputs: Local experiment HTTP fixture and empty-token configuration.
    # Outputs: Schemas omit mandatory idempotency keys; writes receive structured success responses.
    # Logic: Start actual stdio subprocesses and use the MCP SDK for discovery/calls.
    # Constraints: Fixtures mock HTTP business behavior; integration tests separately verify backend permissions.
    async def test_anonymous_stdio(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            params = StdioServerParameters(command=sys.executable,
                args=["-m", "integrations.salesmate_tools.mcp_server"], cwd=str(ROOT),
                env={**os.environ, "SALESMATE_TOOLS_URL": f"http://127.0.0.1:{server.server_port}",
                     "SALESMATE_TOOLS_TOKEN": ""})
            async with Client(params) as client:
                first = await client.list_tools()
                second = await client.list_tools(cursor=first.next_cursor)
                self.assertNotIn("idempotency_key", second.tools[0].input_schema["required"])
                result = await client.call_tool("customers.create", {"name": "Anonymous"})
                self.assertFalse(result.is_error)
                self.assertEqual(result.structured_content["echo"]["arguments"], {"name": "Anonymous"})
                self.assertNotIn("idempotency_key", result.structured_content["echo"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
