"""职责：验证来源幂等图谱工具在MCP桥中的Schema及参数保持。
实现：模拟后端目录，不模拟数据库成功；协议端使用真实MCP类型。
关联：mcp_server.Bridge；真实stdio和HTTP数据库分别由其他集成验证覆盖。
目录：
- GraphBridgeTests：来源幂等的适配测试。
- GraphBridgeTests.test_source_key_is_not_uuid：Schema不插入传输UUID。
- GraphBridgeTests.test_call_preserves_source_payload：调用保持来源信封。
变量索引：
- 无
"""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from integrations.salesmate_tools.mcp_server import Bridge


# 功能：核验MCP不会破坏来源幂等契约。
# 逻辑：只替换HTTP客户端，运行实际桥方法。
# 约束：此测试不代表真实权重或数据库成功。
class GraphBridgeTests(unittest.IsolatedAsyncioTestCase):
    # 功能：保持source_key并省略idempotency_key。
    # 输入：模拟后端来源写入描述。
    # 输出：工具Schema和非只读注解断言。
    # 逻辑：与普通UUID写入使用同一目录函数。
    # 约束：不把graph.ingest标为只读。
    async def test_source_key_is_not_uuid(self):
        client = Mock()
        client.catalog.return_value = {"tools": [{"name": "graph.ingest", "description": "source write",
            "executionMode": "write", "idempotency_scope": "source_key", "idempotency_required": False,
            "inputSchema": {"type": "object", "properties": {"source_key": {"type": "string"}}, "required": ["source_key"]},
            "annotations": {"readOnlyHint": False, "idempotentHint": True}}], "page_size": 100, "count": 1}
        result = await Bridge(client).list_tools(None, None)
        self.assertNotIn("idempotency_key", result.tools[0].input_schema["properties"])
        self.assertFalse(result.tools[0].annotations.read_only_hint)

    # 功能：原样传递来源信封及审计结果。
    # 输入：合成来源键、文本和模拟HTTP回执。
    # 输出：客户端调用参数与structuredContent断言。
    # 逻辑：桥不改写来源键，不补传输UUID。
    # 约束：模拟回执不证明数据库已保存。
    async def test_call_preserves_source_payload(self):
        client = Mock()
        client.call.return_value = {"tool": "graph.ingest", "status": "completed", "data": {"id": "synthetic"}}
        payload = {"source_key": "mail-1", "text": "Sample"}
        result = await Bridge(client).call_tool(None, SimpleNamespace(name="graph.ingest", arguments=payload))
        client.call.assert_called_once_with("graph.ingest", payload, None)
        self.assertEqual(result.structured_content, client.call.return_value)
