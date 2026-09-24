"""职责：验证客户端传输和真实 MCP stdio 握手。
实现：本机 HTTP fixture、真实 SDK 子进程和无外部账户的协议请求。
关联：client、cli、mcp_server；不代替 Django 权限集成测试。
目录：
- Handler：模拟业务 HTTP 协议。
- Handler.log_message：禁止访问日志污染输出。
- Handler.do_GET：模拟分页目录。
- Handler.do_POST：模拟调用和错误。
- Handler.reply：输出 JSON。
- ClientTests：HTTP 与命令行验证。
- ClientTests.setUp：启动测试 HTTP 服务。
- ClientTests.tearDown：释放服务和线程。
- ClientTests.test_cli_and_key_forwarding：验证文件参数及幂等键。
- ClientTests.test_transport_errors_and_redirects：验证不重试、不泄露 token。
- ClientTests.test_configuration_and_fixed_endpoints：拒绝不安全地址与未知接口。
- ClientTests.test_timeout_configuration_and_model_errors：核验显式长超时及模型错误透传。
- ProtocolTests：真实子进程 MCP 验证。
- ProtocolTests.test_anonymous_stdio：验证无令牌 MCP 与可省略幂等键。
- ProtocolTests.test_stdio_catalog_call_and_error：验证 SDK 握手、分页、写参数及错误标志。
变量索引：
- ROOT：项目根目录。
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


# 功能：模拟固定协议。
# 逻辑：模拟带合成授权的正式模式和无授权的实验模式。
# 约束：不访问 Django 或真实业务。
class Handler(BaseHTTPRequestHandler):
    # 功能：关闭测试访问日志。
    # 输入：`format`、`args`。
    # 输出：无。
    # 逻辑：fixture 请求由断言检查。
    # 约束：不影响被测服务日志。
    def log_message(self, format, *args):
        pass

    # 功能：模拟两页目录。
    # 输入：HTTP 路径及可选 Authorization。
    # 输出：一页工具描述。
    # 逻辑：读工具和写工具分两页。
    # 约束：页数仅用于协议测试。
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

    # 功能：记录调用。
    # 输入：JSON 信封。
    # 输出：completed 或测试错误。
    # 逻辑：回显不含 token 的载荷验证适配。
    # 约束：不模拟业务权限成功。
    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.reply(
            403 if payload["name"] == "denied" else 200,
            {"status": "completed", "echo": payload},
        )

    # 功能：输出固定响应。
    # 输入：`status`、`data`。
    # 输出：JSON 字节。
    # 逻辑：声明长度和编码。
    # 约束：仅测试 fixture 使用。
    def reply(self, status, data):
        content = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


# 功能：验证客户端错误和参数。
# 逻辑：本机 HTTP 服务与有限网络 Mock。
# 约束：不请求生产服务。
class ClientTests(unittest.TestCase):
    # 功能：启动本机 fixture。
    # 输入：无外部参数。
    # 输出：服务、线程和客户端实例状态。
    # 逻辑：操作系统分配空闲端口。
    # 约束：只监听 loopback。
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.client = ToolClient(self.url, "fixture-token")

    # 功能：释放 fixture。
    # 输入：实例服务与线程。
    # 输出：无。
    # 逻辑：停止监听并等待线程。
    # 约束：不会停止其他服务。
    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    # 功能：验证 CLI 文件参数与键。
    # 输入：临时 JSON 文件、合成环境。
    # 输出：stdout 可解析 JSON，原键被保留。
    # 逻辑：调用真实 CLI 主函数和 HTTP。
    # 约束：不修改业务。
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

    # 功能：验证网络失败策略。
    # 输入：重定向、超时、拒绝响应。
    # 输出：错误且无重试或 token 回显。
    # 逻辑：Mock 仅替代 requests 单次请求。
    # 约束：超时不会被解释为未执行。
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

    # 功能：验证连接配置边界。
    # 输入：非 TLS 外网、带凭证 URL、错误路径。
    # 输出：显式拒绝。
    # 逻辑：在发起网络前验证。
    # 约束：不提供任意 HTTP 代理能力。
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

    # 功能：验证 GPU 长推理可以显式配置超时且错误不会变为成功。
    # 输入：无外部参数；合成专用环境变量与503响应。
    # 输出：默认30秒保持，显式600秒传递，非法数值被拒绝，503保留业务错误代码。
    # 逻辑：仅网络响应由 Mock 提供，不连接模型服务。
    # 约束：不调整默认超时、不自动重试。
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
        response.json.return_value = {"error": {"code": "crmarena_unavailable", "detail": "Model not configured"}}
        with patch("integrations.salesmate_tools.client.requests.request", return_value=response) as request:
            with self.assertRaisesRegex(ToolError, "crmarena_unavailable"):
                self.client.call("crmarena.predict", {})
            request.assert_called_once()


# 功能：验证真实 MCP 进程。
# 逻辑：SDK 客户端通过 stdio 与服务子进程通信。
# 约束：HTTP 端使用 fixture，不能证明生产连接可用。
class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    # 功能：验证协议握手、目录和调用。
    # 输入：本机 HTTP fixture 和合成 token。
    # 输出：两页目录、独立写 Schema、结构化结果、isError。
    # 逻辑：启动真实 Python MCP 子进程；最终释放 HTTP 线程。
    # 约束：stdout 仅协议内容，客户端成功握手即检验未被日志污染。
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

    # 功能：验证匿名 MCP 真实握手及写入参数。
    # 输入：本机实验 HTTP fixture 和空令牌配置。
    # 输出：Schema 不要求幂等键，写调用得到结构化成功响应。
    # 逻辑：启动真实 stdio 子进程，通过 MCP SDK 完成目录和调用。
    # 约束：HTTP 业务由 fixture 模拟，后端权限另由集成测试验证。
    async def test_anonymous_stdio(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            params = StdioServerParameters(command=sys.executable,
                args=["-m", "integrations.salesmate_tools.mcp_server"], cwd=str(ROOT),
                env={**os.environ, "SALESMATE_TOOLS_URL": f"http://127.0.0.1:{server.server_port}",
                     "SALESMATE_TOOLS_TOKEN": "", "SALESMATE_TOOLS_USER": ""})
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
