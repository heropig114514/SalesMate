"""Responsibility: Verify a real stdio MCP round trip to deployed graph HTTP endpoints.
Implementation: Dedicated sandbox credentials and real SDK subprocesses exercise discovery, structured writes, replay, queries, and withdrawal.
Relationships: mcp_server and backend graph tools; smoke_graph_api separately verifies the actual natural-language model.
Directory:
- verify: Execute one real MCP synthetic-observation round trip.
- main: Read explicit sandbox configuration and save verification results.
Variable index:
- None
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


# Function: Connect to MCP through a real subprocess and invoke the deployed backend.
# Inputs: `url` is a loopback HTTP or HTTPS service root; `credential` is a dedicated authorization dictionary.
# Outputs: Schema, source replay, withdrawal, and tool-catalog verification results.
# Logic: Put raw tokens only in the subprocess environment and use randomly unique source keys; no natural-language model calls.
# Constraints: Create and withdraw one synthetic observation while retaining audit records; failures propagate without automatic retries.
async def verify(url, credential):
    params = StdioServerParameters(command=sys.executable,
        args=["-m", "integrations.salesmate_tools.mcp_server"], cwd=str(Path(__file__).resolve().parents[2]),
        env={**os.environ, "SALESMATE_TOOLS_URL": url, "SALESMATE_TOOLS_TOKEN": credential["token"],
             "SALESMATE_TOOLS_TIMEOUT": "360"})
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


# Function: Run sandbox MCP verification and record results.
# Inputs: No function parameters; CLI url, credential-file, and output are required.
# Outputs: A new JSON evidence file and a credential-free summary.
# Logic: Reject existing evidence paths and non-sandbox usernames before starting an SDK session.
# Constraints: Never commit credential files; failed verification cannot emit a passed result.
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
