"""Responsibility: Expose currently authorized business MCP tools over stdio.
Implementation: The MCP SDK forwards paginated catalogs/results; ordinary writes add UUIDs while source-idempotent graph tools preserve original schemas; HTTP runs in threads.
Relationships: client connects to the backend, the sole authority for business permissions, confirmation, idempotency, and schemas.
Directory:
- Bridge: Dynamic tool-protocol bridge.
- Bridge.__init__: Bind the client.
- Bridge.list_tools: Publish paginated tools and write-idempotency parameters.
- Bridge.call_tool: Forward calls and distinguish errors.
- create_server: Construct a low-level MCP Server.
- main: Run the stdio service.
Variable index:
- None
"""

import copy
import json
import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from .client import ToolClient, ToolError


# Function: Adapt business and MCP protocols.
# Logic: Preserve separate names for each business tool.
# Constraints: No general function execution or privilege escalation.
class Bridge:
    # Function: Store the client.
    # Inputs: `client`.
    # Outputs: An instance.
    # Logic: No network access.
    # Constraints: Never write credentials to stdout.
    def __init__(self, client):
        self.client = client

    # Function: Return authorized tools.
    # Inputs: Protocol `context` and optional cursor `params`.
    # Outputs: MCP ListToolsResult.
    # Logic: Use 100 entries per page; ordinary write schemas add idempotency_key, while source-idempotent tools omit transport keys and retain server-declared requirements.
    # Constraints: Read-only tools reject keys; catalogs never publish Session-only confirmation/authorization endpoints.
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
            if spec["executionMode"] != "read" and not spec.get("idempotency_scope"):
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

    # Function: Execute one MCP call.
    # Inputs: `context` and the name/arguments in `params`.
    # Outputs: Text JSON and structuredContent; protocol-call failures set isError.
    # Logic: Separate transport idempotency keys; the backend validates permissions/arguments again.
    # Constraints: Pending confirmation is not business execution; no automatic retries.
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


# Function: Create a testable server.
# Inputs: `client`.
# Outputs: Server.
# Logic: Register two fixed callbacks.
# Constraints: No network listener or remote MCP authentication implementation.
def create_server(client):
    bridge = Bridge(client)
    return Server(
        "salesmate-business-tools",
        version="1.0.0",
        on_list_tools=bridge.list_tools,
        on_call_tool=bridge.call_tool,
    )


# Function: Start stdio transport.
# Inputs: Dedicated environment configuration and stdin.
# Outputs: MCP messages on stdout.
# Logic: The official SDK manages sessions.
# Constraints: Never print logs to stdout or automatically inject tools into the current chat Agent.
async def main():
    server = create_server(ToolClient.from_env())
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


if __name__ == "__main__":
    anyio.run(main)
