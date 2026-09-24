"""职责：验证真实stdio MCP到已部署图谱HTTP接口的闭环。
实现：使用专用沙盒凭证，真实SDK子进程完成发现、结构化写入、重放、查询与撤回。
关联：mcp_server与后端graph工具；自然语言真实模型另由smoke_graph_api验证。
目录：
- verify：执行一次真实MCP合成观察闭环。
- main：读取显式沙盒配置并保存核验结果。
变量索引：
- 无
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import uuid
import anyio
from mcp import Client, StdioServerParameters


# 功能：真实子进程连接MCP并调用部署后端。
# 输入：`url` 回环HTTP或HTTPS服务根；`credential` 专用授权字典。
# 输出：Schema、来源重放、撤回和工具清单核验结果。
# 逻辑：原始token只放子进程环境，来源使用随机唯一键；不调用自然语言模型。
# 约束：创建并撤回一份合成观察，保留审计；任何失败直接抛出，不自动重试。
async def verify(url, credential):
    params = StdioServerParameters(command=sys.executable,
        args=["-m", "integrations.salesmate_tools.mcp_server"], cwd=str(Path(__file__).resolve().parents[2]),
        env={**os.environ, "SALESMATE_TOOLS_URL": url, "SALESMATE_TOOLS_TOKEN": credential["token"],
             "SALESMATE_TOOLS_TIMEOUT": "360", "SALESMATE_TOOLS_USER": ""})
    async with Client(params) as client:
        catalog = await client.list_tools()
        names = {tool.name for tool in catalog.tools}
        assert len(names) == 9 and all(name.startswith("graph.") for name in names)
        spec = next(tool for tool in catalog.tools if tool.name == "graph.ingest")
        assert "idempotency_key" not in spec.input_schema["properties"]
        schema = await client.call_tool("graph.schema", {})
        assert not schema.is_error and len(schema.structured_content["data"]["schemas"]) == 48
        payload = {"source_key": "mcp-smoke-" + uuid.uuid4().hex, "observed_at": datetime.now(timezone.utc).isoformat(),
                   "records": [{"key": "company", "schema": "crm.company", "fields": {"name": "MCP Synthetic Company"}}]}
        first = await client.call_tool("graph.ingest", payload)
        assert not first.is_error, "MCP structured ingestion failed"
        episode = first.structured_content["data"]["id"]
        replay = await client.call_tool("graph.ingest", payload)
        assert not replay.is_error and replay.structured_content["data"]["id"] == episode
        detail = await client.call_tool("graph.episode", {"episode_id": episode})
        assert not detail.is_error and detail.structured_content["data"]["sync"]["current"]
        retract = await client.call_tool("graph.retract", {"episode_id": episode})
        assert not retract.is_error and retract.structured_content["data"]["retracted"]
        return {"passed": True, "transport": "real stdio MCP to real HTTP backend", "tools": sorted(names),
                "schema_count": 48, "source_key": payload["source_key"], "episode_id": episode,
                "source_replay_same_id": True, "retracted": True, "model_called": False}


# 功能：运行沙盒MCP验证并记录结果。
# 输入：无函数参数；CLI url、credential-file、output必填。
# 输出：新JSON结果文件和不含凭证的摘要。
# 逻辑：拒绝覆盖证据文件和非沙盒用户名，再启动SDK会话。
# 约束：不要把凭证文件提交Git；验证失败不生成passed结果。
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--credential-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Refusing to overwrite previous verification")
    credential = json.loads(args.credential_file.read_text(encoding="utf-8-sig"))
    if not credential["username"].startswith("graph-sandbox-"):
        raise ValueError("Only a dedicated graph-sandbox- identity is allowed")
    result = anyio.run(verify, args.url, credential)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
