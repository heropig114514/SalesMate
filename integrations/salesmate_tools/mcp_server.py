"""职责：通过 stdio 发布当前授权的业务 MCP 工具。
实现：MCP SDK 低层回调透传分页目录、Schema 和调用结果；HTTP 在线程中运行。
关联：client 连接后端；后端是权限、确认、幂等及 Schema 的唯一业务来源。
目录：
- Bridge：动态工具协议桥。
- Bridge.__init__：绑定客户端。
- Bridge.list_tools：发布分页工具及写入幂等参数。
- Bridge.call_tool：转发调用并区分错误。
- create_server：构造低层 MCP Server。
- main：运行 stdio 服务。
变量索引：
- 无
"""

import copy
import json
import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from .client import ToolClient, ToolError


# 功能：适配业务与 MCP 协议。
# 逻辑：每个业务工具保留独立名称。
# 约束：不提供通用函数执行或权限提升。
class Bridge:
    # 功能：保存客户端。
    # 输入：`client`。
    # 输出：实例。
    # 逻辑：无网络访问。
    # 约束：凭证不写 stdout。
    def __init__(self, client):
        self.client = client

    # 功能：返回授权工具。
    # 输入：`context` 协议上下文、`params` 可选游标。
    # 输出：MCP ListToolsResult。
    # 逻辑：每页 100，写 Schema 增加 idempotency_key；是否必填由服务端当前模式声明。
    # 约束：只读工具不接受 key，目录不发布 Session-only 确认或授权接口。
    async def list_tools(self, context, params):
        cursor = params.cursor if params else None
        try:
            page = int(cursor) if cursor else 1
            if page < 1:
                raise ValueError
        except ValueError:
            raise ToolError("无效工具目录游标。") from None
        catalog = await anyio.to_thread.run_sync(lambda: self.client.catalog(page=page))
        result = []
        for spec in catalog["tools"]:
            schema = copy.deepcopy(spec["inputSchema"])
            if spec["executionMode"] != "read":
                schema["properties"]["idempotency_key"] = {
                    "type": "string",
                    "format": "uuid",
                    "description": "调用者为一次逻辑变更生成 UUID；结果未知时复用原值与原参数。",
                }
                if spec.get("idempotency_required", True):
                    schema["required"].append("idempotency_key")
            result.append(
                types.Tool(
                    name=spec["name"],
                    description=spec["description"],
                    input_schema=schema,
                    annotations=types.ToolAnnotations.model_validate(
                        spec["annotations"]
                    ),
                )
            )
        return types.ListToolsResult(
            tools=result,
            next_cursor=(
                str(page + 1)
                if page * catalog["page_size"] < catalog["count"]
                else None
            ),
        )

    # 功能：执行一个 MCP 调用。
    # 输入：`context`、`params` 的名称及参数。
    # 输出：文本 JSON 和 structuredContent；协议调用失败为 isError。
    # 逻辑：拆分传输幂等键，后端再次验证权限和参数。
    # 约束：待确认回执不等同业务执行，不自动重试。
    async def call_tool(self, context, params):
        arguments = dict(params.arguments or {})
        key = arguments.pop("idempotency_key", None)
        try:
            result = await anyio.to_thread.run_sync(
                lambda: self.client.call(params.name, arguments, key)
            )
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text", text=json.dumps(result, ensure_ascii=False)
                    )
                ],
                structured_content=result,
            )
        except ToolError as error:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=str(error))], is_error=True
            )


# 功能：创建可测试服务。
# 输入：`client`。
# 输出：Server。
# 逻辑：注册两个固定回调。
# 约束：无网络监听、无远程 MCP 认证实现。
def create_server(client):
    bridge = Bridge(client)
    return Server(
        "salesmate-business-tools",
        version="1.0.0",
        on_list_tools=bridge.list_tools,
        on_call_tool=bridge.call_tool,
    )


# 功能：启动 stdio。
# 输入：专用环境配置和 stdin。
# 输出：stdout MCP 消息。
# 逻辑：官方 SDK 管理会话。
# 约束：不向 stdout 打印日志、不自动注入当前聊天 Agent。
async def main():
    server = create_server(ToolClient.from_env())
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


if __name__ == "__main__":
    anyio.run(main)
