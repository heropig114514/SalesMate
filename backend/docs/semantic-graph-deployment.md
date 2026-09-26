# Semantic graph: HTTP, MCP, and deployment on small-memory servers

Agent developers should first read the [unified handoff](semantic-agent-handoff.md) for tool selection, write effects, failures/idempotency, and current availability. This page provides configuration/deployment commands.

This version provides 48 business schemas, incomplete structured observations, text/email extraction, existing-entity linking, source retraction, and evidence lineage. PostgreSQL maintains graphs; LLMs propose candidates without modifying orders/permissions or sending mail. Fine-tuned V2 Q4 retains experimental quality limitations. Following the 2026-09-25 cleanup, launchers expose only semantic-v2; see [experiments](semantic-model-experiments.md).

## Initial baseline validation and optimization

The server loaded weights successfully; structured HTTP, source replay, and real stdio MCP passed end to end. Full-schema natural-language input failed after 301.34 seconds with 502 ReadTimeout. Logs showed 2048 input tokens taking 256.22 seconds (about 7.99 tokens/s); the request was cancelled without source persistence. Model health=ok does not establish natural-language API usability. The model service is running without boot enablement; experiment API boot startup is enabled. Persistent 8 GiB swap remains. The default 300-second threshold was not relaxed.

Those are initial baseline results. Local full-test mean latency for candidate prefix8 improved 62.47→18.31 seconds, but contract passes fell 8/12→7/12, failing adoption criteria; baseline remains default. See [inference optimization](semantic-inference-optimization.md) for temporary server validation, limits, and restoration.

## Inputs and results

Provide source_key, timezone-aware observed_at, and exactly one of text/records. source_key is unique per user. Identical key/text/time returns the same source without repeated inference; changed content under the same key returns 409. Corrections require a new source and explicit old-source retraction. Do not automatically retry timeouts with new keys.

```json
{
  "source_key": "email-20260925-001",
  "observed_at": "2026-09-25T10:00:00+08:00",
  "text": "SandboxAcme needs SandboxEdge. The budget is unknown."
}
```

Structured example:

```json
{
  "source_key": "import-20260925-001",
  "observed_at": "2026-09-25T10:00:00+08:00",
  "records": [
    {"key": "customer", "schema": "crm.company", "fields": {"name": "SandboxAcme"}},
    {"key": "quote", "schema": "sales.quote", "fields": {"number": "Q-DEMO", "company": {"record": "customer"}}}
  ]
}
```

Missing fields remain unknown without business defaults; structured ingestion calls no LLM. Text sends complete compact schemas, owned candidate entities, known observations, and source text. Results contain id, extraction.entities/facts, model_audit, sync. model_audit.raw_model_extraction retains raw suggestions; entity_resolution records programmatic decisions. Only sync.current=true denotes current graph readiness. Valid source excerpts do not establish human-verified semantics.

Email inputs may include subjects/plain-text bodies; graph_ingest --emails reads owned persisted inbound business emails. Attachment parsing, mailbox fetching, and sending are excluded.

## HTTP calls

Direct APIs: `/api/v1/graph/schema/`, `episodes/`, `episodes/{id}/`, `episodes/{id}/retract/`, `status/`, `entities/`, `facts/`, and `facts/{id}/lineage/`, using Session/CSRF. See [detailed APIs](semantic-graph.md).

Programs/MCP should use independent Tool authentication. Create limited credentials through authenticated `POST /api/v1/agent-tools/credentials/`, e.g.:

```json
{"name":"Semantic graph assistant","allowed_tools":["graph.schema","graph.status","graph.entities","graph.facts","graph.lineage","graph.episodes","graph.episode","graph.ingest","graph.retract"],"expires_in_hours":24}
```

Tokens appear once and stay outside Git. Call `POST /api/v1/agent-tools/call/` with `Authorization: Tool <token>`:

```json
{
  "name": "graph.ingest",
  "arguments": {
    "source_key": "email-20260925-001",
    "observed_at": "2026-09-25T10:00:00+08:00",
    "text": "SandboxAcme needs SandboxEdge. The budget is unknown."
  }
}
```

The outer result is `{tool,status,http_status,data}`; data contains business responses. Source writes use source_key/episode_id idempotency, reject idempotency_key, and create no ordinary ToolCall receipts; sources/lineage provide durable audit. Other tools retain UUID idempotency.

| Tool | Parameters | Behavior |
|---|---|---|
| `graph.schema` | `{}` | Complete input catalog |
| `graph.status` | `{}` | Synchronization state |
| `graph.entities` | Optional kind, q, page, page_size | Owned entities |
| `graph.facts` | Optional entity, predicate, page, page_size | Owned relations/properties |
| `graph.lineage` | fact_id; optional page, page_size | Evidence/history |
| `graph.episodes` | Optional page, page_size | Source summaries |
| `graph.episode` | episode_id | Source text, candidates, audit |
| `graph.ingest` | Input envelope above | Persist observations, link, synchronize |
| `graph.retract` | episode_id | Revoke support, retain history |

401/403 indicate authentication/authorization failure; 400 contract errors; 404 includes other users' resources; 409 source conflicts/context changes during inference; 502 model/evidence failures; 503 unready graphs. After timeout reconcile with graph.episodes/graph.episode; no automatic retries.

## MCP configuration

Create a separate SDK environment at repository root:

```powershell
python -m venv .tools-venv
.tools-venv/Scripts/python.exe -m pip install -r integrations/salesmate_tools/requirements.txt
$env:SALESMATE_TOOLS_URL = 'http://127.0.0.1:18090'
$env:SALESMATE_TOOLS_TOKEN = (Get-Content 'private-credentials.json' -Raw | ConvertFrom-Json).token
$env:SALESMATE_TOOLS_TIMEOUT = '360'
.tools-venv/Scripts/python.exe -m integrations.salesmate_tools.mcp_server
```

This is stdio MCP, not HTTP MCP. Set client command to absolute SDK Python, args to `["-m","integrations.salesmate_tools.mcp_server"]`, working directory to repository root, and pass dedicated environment variables. If working-directory configuration is unavailable, set PYTHONPATH to the root. Never publish raw tokens. SALESMATE_TOOLS_TIMEOUT defaults to 30 seconds; explicitly lengthen clients for model requests without changing backend's 300-second model timeout.

Read-only grants expose corresponding tools only; nine graph grants confer no other business access. MCP catalogs paginate; source-write schemas add no transport UUID. SDK waits should cover backend waits; no automatic connection/write retries.

## Local model and artifacts

Use official llama.cpp **b11146 / 7fe450e19**, with Linux CPU binaries from the [official release](https://github.com/ggml-org/llama.cpp/releases/tag/b11146). Ubuntu requires libgomp1. Install backend/requirements/base.txt and agent/requirements.txt for Python; keep MCP dependencies separate from Web.

Fine-tuned `Qwen3-4B-SalesMate-Semantic-v2-Q4_K_M.gguf` is 2,497,278,784 bytes, SHA256:

```text
cadbc0a850ad31c712b4441a39775a8969ecdf9c0a762730c2a9a1c4344efd09
```

GGUF/LoRA stay outside Git. Weights remain in [private Kaggle output](https://www.kaggle.com/code/heropig/salesmate-semantic-qlora-v2), local experiments, and the independent server model directory. Verify downloads against the digest.

```bash
python backend/tools/run_graph_model.py \
  --executable /absolute/path/llama-server \
  --model /absolute/path/Qwen3-4B-SalesMate-Semantic-v2-Q4_K_M.gguf \
  --variant semantic-v2 --threads 2 --port 8088
```

Default variant is semantic-v2; only the retained fine-tuned Q4 hash is accepted. Default threads=8; this 2-vCPU deployment explicitly uses 2, a separate validation not directly comparable to 8-thread desktop benchmarks. Default --profile baseline uses 16384 context, one slot, F16 KV, and no prompt cache. All profiles retain 1536 output tokens, temperature=0, seed=2026, and disabled automatic fit/context shifting. Failures never switch weights/precision.

Set GRAPH_LLM_URL=http://127.0.0.1:8088/v1 and GRAPH_LLM_MODEL=salesmate-graph. Model ports bind loopback only; backend enforces Tool authentication.

Explicitly align launcher --profile and API GRAPH_LLM_PROFILE; both default to baseline. See [optimization](semantic-inference-optimization.md) for configurations, quality gates, cold/warm results, and deployment validation. Inference profile and weight version are separate; KV quantization is not weight requantization.

## Server layout and access

The server has 2 vCPU/3.7 GiB RAM; the original site remains running. Experiments use `/opt/salesmate-semantic/`, system user salesmate-graph, and independent PostgreSQL database/role salesmate_graph_sandbox. No graph migrations touched production business databases or replaced production default models.

- app/: this Git source version; venv/: independent backend environment.
- models/: verified GGUF; llama-b11146/: official Linux CPU binaries.
- runtime.env: root/dedicated-group-readable private configuration.
- private/client.json: 0600 sandbox credentials granting nine graph tools for 720 hours. Production accounts still use Session authorization APIs.
- salesmate-graph-model.service: model at 127.0.0.1:8088.
- salesmate-graph-api.service: experiment backend at 127.0.0.1:8090.

Use the SSH key listed in server_info.txt and keep the tunnel terminal running:

```powershell
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 `
  -i 'LightsailDefaultKey-ap-southeast-1.pem' `
  -L 18090:127.0.0.1:8090 ubuntu@47.131.232.143
```

HTTP/MCP connect to http://127.0.0.1:18090. Expired credentials fail explicitly; administrators explicitly create new sandbox users/grants or use production user authorization APIs. No automatic renewal.

Persistent `/swapfile-salesmate-graph` is 8 GiB, mode 0600, registered in /etc/fstab. Swap reduces OOM risk but replaces neither RAM nor latency guarantees. Inspect:

```bash
free -h
swapon --show
sudo systemctl status salesmate-graph-model salesmate-graph-api
sudo systemctl show salesmate-graph-model -p MemoryCurrent -p MemorySwapCurrent
sudo journalctl -u salesmate-graph-model -u salesmate-graph-api --since '10 minutes ago'
curl -fsS http://127.0.0.1:8088/health
```

Both templates in deploy/lightsail/ use Restart=no and low priority; diagnose failures before explicit restart. Stop model memory usage with `sudo systemctl stop salesmate-graph-model`; structured APIs remain usable while text fails explicitly. Stop the backend with `sudo systemctl stop salesmate-graph-api`. Check free memory before swapoff; never force it under memory pressure.

## Reproducible validation

HTTP smoke permits only graph-sandbox- identities, creates synthetic data, retains results, and never cleans/retries automatically. Output directories must not exist:

```bash
python backend/tools/smoke_graph_api.py --url http://127.0.0.1:8090 \
  --credential-file /opt/salesmate-semantic/private/client.json \
  --output /opt/salesmate-semantic/private/http-smoke-new
```

Real MCP validation in the SDK environment checks discovery, writes, replay, queries, and retraction without additional model calls:

```powershell
.tools-venv/Scripts/python.exe -m integrations.salesmate_tools.smoke_graph `
  --url http://127.0.0.1:18090 --credential-file 'private-credentials.json' `
  --output 'mcp-verification-new.json'
```

Database tests require real PostgreSQL/pgvector. Test roles may create databases but typically not extensions; administrators preinstall vector in dedicated test databases used with --keepdb. Production authorization tests require LAB_OPEN_ACCESS=False and LOCAL_DEBUG_AUTO_LOGIN=False, avoiding inherited open-mode settings. Report mocked model tests separately from real inference.

Actual server speeds, success/failure boundaries, and final checks are in adjacent semantic-release-verification.json. One successful synthetic example does not overturn known Q4 limitations in negation, contact/employment, or direction.
