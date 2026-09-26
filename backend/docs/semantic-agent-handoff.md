# Graph Model and Agent Tool Handoff

Updated 2026-09-25. The legacy model and research interfaces were removed at the user's request; the only retained model, remote recovery, and compatibility changes are recorded in [Model Retirement and Recovery](model-retirement.md). This document is the entry point for future Agent work. Runtime behavior is defined by the actual tool catalog and source code. The interfaces are packaged as an HTTP SDK and stdio MCP, but they are not registered in the existing workspace-chat Agent tool-selection flow. The presence of this document or an MCP configuration does not mean chat can construct graphs automatically.

## 1. Current Delivery and Operating Boundaries

- Baseline code: `semantic-graph-v0.2.0`, commit `6b7098ea91455b421c27876e524298a02fb2162d`. The code is on `feat/semantic-graph-mcp-v2`, [PR #1](https://github.com/heropig114514/SalesMate/pull/1); do not assume it has been merged into the default branch. Later documentation commits do not mean the server was synchronized or deployed.
- The isolated server runs fine-tuned V2 Q4_K_M weights with the `baseline` profile (16K context, one slot, two CPU threads). After the 2026-09-25 cleanup, the launcher accepts and defaults only to semantic-v2. Weight version and inference profile are distinct.
- Structured `records` input, queries, source replay, and retraction were verified end to end through real HTTP/MCP. This path does not call an LLM.
- Natural-language graph extraction remains experimental: fine-tuned Q4 produced structurally and evidentially valid output in 8/12 synthetic tests and fully matched facts in 6/12. This is not an accuracy claim for real mail.
- Full-schema text requests previously returned 502/ReadTimeout after about 301.34 seconds with baseline and 300.28 seconds with temporary prefix8. No failed source was written. prefix8 failed the local quality gate and baseline was restored.
- The diagnostic immediately before handoff found both services active and model health HTTP 200. A 13-input-token, 2-output-token “Reply with exactly: OK” probe returned OK in 44.301 seconds. This single short probe has no full schema or graph write and is not a production-latency commitment.
- The same memory snapshot showed 3.7 GiB RAM, about 466 MiB available, and about 4 GiB swap in use. A passing health check does not show that natural-language graph construction can finish within its limit.
- On a single Kaggle T4, 12 fine-tuned-Q4 full-schema requests averaged 6.045 seconds (4.08–8.14 seconds), with about 4,135 input tokens. This measures only model requests, excluding loading, business validation, database writes, and cross-network latency. Kaggle is a completed experiment notebook, not a deployed persistent model API.

## 2. Which Layer an Agent Should Call

| Goal | Entry point | Side effects and limits |
|---|---|---|
| Query entities, facts, and source evidence | `graph.entities/facts/lineage/episodes/episode` | Reads the graph for the current identity; an active fact is not necessarily human-confirmed. |
| Create observations and relations from text or mail | `graph.ingest` `text` branch | Calls the model, validates, resolves entities, saves evidence, and synchronizes the graph; it is a write tool. |
| Import incomplete business records | `graph.ingest` `records` branch | Does not call the model; creates schema observations and does not directly create a formal order. |
| Retract source support | `graph.retract` | Retains history and support from other sources; it does not delete all entities. |
| Generate candidates without saving | No public graph dry-run tool exists | Do not treat `graph.ingest` as a side-effect-free extraction API. |

The standalone model endpoint, `127.0.0.1:8088/v1/chat/completions`, generates text only; it does not authorize, validate evidence, or persist the graph. Business Agents must use backend tools. If pure extraction is needed, define, implement, and validate a dedicated interface; do not claim an existing dry run. The model is not trained online from input. The live graph is stored in PostgreSQL.

## 3. Integration Configuration and Authentication

See [Deployment Guide](semantic-graph-deployment.md) for the complete SSH tunnel, dependency installation, and credential-issuance procedure. The server API listens only on `127.0.0.1:8090`; after SSH forwarding, use `http://127.0.0.1:18090` locally. The production website address is not this sandbox's model-tool endpoint.

At the repository root, in a Python environment with `integrations/salesmate_tools/requirements.txt` installed, configure:

```text
SALESMATE_TOOLS_URL=http://127.0.0.1:18090
SALESMATE_TOOLS_TOKEN=<dedicated Tool credential for the current user>
SALESMATE_TOOLS_TIMEOUT=360
```

Inject tokens from a private environment or secret manager. Do not treat a placeholder as a real value or write it to Git, tool arguments, or logs. A `Tool` credential differs from the Gmail/chat Worker `Agent` credential. The isolated graph sandbox requires authentication; the unauthenticated laboratory mode described elsewhere does not apply. The credential determines identity, so an owner cannot be supplied to impersonate another user. Grant read-only Agents only query tools; graph construction and retraction each require appropriate write permission.

The SDK default timeout is 30 seconds. An explicit 360 seconds is only the client waiting limit; the backend model timeout remains 300 seconds. The SDK does not load the project `.env`; the calling process must receive these environment variables.

For MCP, configure the installed dependency environment's absolute Python path as `command`, `args` as `["-m", "integrations.salesmate_tools.mcp_server"]`, and `cwd` as the repository root, and pass the variables above to the child process. Clients without `cwd` support can set `PYTHONPATH` to the repository root. This is stdio MCP, not an HTTP MCP URL. Read every `tools/list` page and use only tools and input schemas present in the authorized catalog. MCP and Agent wait limits must cover the backend call.

## 4. Minimal Calls and Return Values

Discover the contract and read the schema before an authorized call. Run this Python SDK example from the repository root; the final call writes a synthetic observation and is not a read-only probe:

```python
from integrations.salesmate_tools.client import ToolClient

client = ToolClient.from_env()
spec = client.describe("graph.ingest")
schema_result = client.call("graph.schema", {})
status_result = client.call("graph.status", {})
result = client.call("graph.ingest", {
    "source_key": "handoff-demo-20260925-001",
    "observed_at": "2026-09-25T10:00:00+08:00",
    "records": [
        {"key": "customer", "schema": "crm.company",
         "fields": {"name": "SandboxAcme"}},
        {"key": "quote", "schema": "sales.quote",
         "fields": {"number": "Q-DEMO", "company": {"record": "customer"}}}
    ]
})
```

The natural-language branch uses the same tool, replacing `records` with `text`; both cannot be present. This argument example may time out on the current small server and makes no success guarantee:

```json
{
  "source_key": "email-demo-20260925-001",
  "observed_at": "2026-09-25T10:00:00+08:00",
  "text": "Acme needs Edge. Its budget is 800 SGD."
}
```

Mail input may combine subject, line breaks, and plain-text body into `text`; this feature does not fetch mail or parse attachments. Generate `source_key` from a stable external-source identifier and use the source's fixed observed time for `observed_at`; do not substitute the current time when replaying a source.

The HTTP equivalent is `POST /api/v1/agent-tools/call/`, header `Authorization: Tool <token>`, and body `{"name":"graph.ingest","arguments":{...}}`. The outer response is `{tool,status,http_status,data}`. The source result in `data` has fields such as `id`, `source_key`, `extraction`, `model_audit`, and `sync`. Check the tool status and `data.sync.current`; receiving JSON or successful MCP transport alone does not prove graph construction succeeded. Also check MCP `isError`.

Model output contains candidate entities and facts. `model_audit.raw_model_extraction` and entity-resolution decisions are stored separately, so programmatic matching gains cannot be counted as model accuracy. Source quotations support traceability but do not prove semantic correctness. After a fact query, call `graph.lineage` with the real `fact_id`; never invent IDs. See [Graph API](semantic-graph.md) and [Input and Output Notes](semantic-model-experiments.md) for field details.

## 5. Failures, Idempotency, and Agent Scheduling

| Status | Caller handling |
|---|---|
| 400 | Correct arguments from the real input schema/schema; do not remove mandatory evidence or fabricate missing information. |
| 401/403 | Check credential expiry, revocation, and tool scope; do not bypass this by switching to anonymous or another user's identity. |
| 404 | The resource does not exist or is not visible; do not infer another user's data. |
| 409 | Distinguish a source-key content conflict from graph changes during inference; retain the call, check status, and let business policy decide next action. |
| 502 | Model generation, timeout, or validation failed; report this truthfully and do not present candidates or empty output as success. |
| 503 | The graph is not ready; check status and maintenance processes before treating it as current. |
| Connection loss/client timeout | A write outcome may be unknown; page through `graph.episodes`, then inspect details by `episode_id`; do not automatically retry under a new key. |

For the same user, replaying identical input and observed time under the same `source_key` reuses the source without reinference. Changing content or time under that key returns 409. Corrections require a new source and explicit old-source retraction. `graph.ingest` and `graph.retract` use source-level idempotency and do not accept the ordinary tool UUID `idempotency_key`. Source pagination is not a source-key search API; process every relevant page.

The backend is currently synchronous and the model has one slot. No asynchronous job submission, job polling, or concurrent-throughput guarantee is implemented. Do not reuse email L1's four-way concurrency directly. A future Agent integration must explicitly design serial scheduling/job waiting, user status communication, and write authorization; this handoff does not alter existing workflows. Do not automatically retry, change models, truncate schema, raise timeouts, or repair model answers to manufacture success. Future experiment-condition changes remain subject to project change-control rules.

## 6. Code Locations, Verification, and Follow-up Work

| Path | Responsibility |
|---|---|
| `backend/apps/agent_tools/graph.py` | Nine-tool catalog, input contract, and routing |
| `integrations/salesmate_tools/client.py`, `mcp_server.py` | Standalone HTTP SDK and stdio MCP |
| `backend/apps/knowledge_graph/semantic_contract.py`, `semantic_provider.py` | Prompt, JSON-output constraints, and model requests |
| `backend/apps/knowledge_graph/episodes.py`, `entity_resolution.py`, `episode_projection.py` | Source transaction, entity association, and projection |
| `backend/apps/knowledge_graph/business_schema.py` | Business-schema catalog |
| `backend/tools/run_graph_model.py`, `backend/apps/knowledge_graph/inference_profiles.py` | Weight validation and inference profile |
| `backend/tools/smoke_graph_api.py`, `integrations/salesmate_tools/smoke_graph.py` | Real HTTP/MCP sandbox verification, including writes |

Historical verification: 46 database/contract regression tests passed and the real structured HTTP/MCP loop passed. Model-generation quality and server text timeouts are in [Optimization Record](semantic-inference-optimization.md). Do not interpret mock tests, structured smoke tests, or a passing health check as acceptance of real text-to-graph construction.

Handoff order: confirm checkout and target environment → configure minimum Tool permissions → discover tools and read schema/status → validate structured writes, queries, lineage, and retraction in a dedicated sandbox → independently validate text quality and latency → then implement explicit tool registration and execution in the chat Agent. Graph tools are not yet automatic tools in existing chat; SDK/MCP can be integrated independently without changing the L1–L4 path.

See [Deployment Guide](semantic-graph-deployment.md) for reproducibility, weight location and SHA256, service lifecycle, private credentials, and SSH access. Fixed evaluation conditions and performance results are in [Fine-tuning Notes](semantic-model-experiments.md) and [Inference Optimization](semantic-inference-optimization.md). Legacy models and research interfaces were retired at the user's request; [Model Retirement and Recovery](model-retirement.md) records recovery entry points and current artifacts. Training/evaluation conditions and server runtime configuration were not changed by this local cleanup.
