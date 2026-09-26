# Automatic graph linking for business schemas and external information

This feature connects PostgreSQL business graphs, incomplete schema records, emails, and natural-language observations. Structured business data remains authoritative. External input enters a separate observation layer without fabricated defaults, automatic business orders, transaction confirmation, permission changes, or sending.

## Implemented scope

- `GET /api/v1/graph/schema/` derives fields, types, nullability, foreign keys, and enums for 48 business model types from Django metadata. It covers profiles, companies, contacts, products, opportunities, tickets, quotes/lines, orders/lines, follow-ups, conversations, emails, analyses, knowledge, news/events, tool receipts, and related records. Identities, credentials, queues, and checkpoints are excluded; authorization/credential fields never enter model context.
- The original ten-table graph expands to 48 business sources plus `Episode`, totaling 49 capture tables. Original purchase/L1 rules remain; new `schema.<model>.<field>` relations/properties and observations use mapping `salesmate-kg-v2`.
- Structured input validates supplied fields only; omission/null remain unknown, retaining complete original JSON. Foreign keys may reference batch records/source IDs. Missing targets become placeholders in the same user's graph, linked after later source projection.
- Local Qwen3-4B-Instruct-2507 Q4_K_M extracts natural language and proposes links. Protocol v4 JSON Schema distinguishes relations/properties, restricts local references/predicates/nulls, then validates contiguous source excerpts, types, predicates, and target ownership. Independent entity resolution prefers valid IDs, otherwise unique exact same-type names; matching known neighbors can narrow multiple candidates. Remaining ambiguity preserves new entities. Raw model output and programmatic decisions are stored separately; programmatic linking is not model capability. Fail explicitly without output repair/model fallback.
- Model context includes existing labels, limited identity fields, and observed facts. Models may reuse entities; new `external.<business-model>` entities exist only in the graph. Links combine model suggestions/programmatic resolution, not verified identity merges.
- Observations retain occurrence/receipt times, inputs, model parameters, and prompt digests. Existing lineage APIs expose source versions/derivations/support paths. Distinct values remain with `needs_review`; latest statements never silently overwrite earlier values, and complete bitemporal reasoning is not claimed.
- `graph_ingest --emails --watch` automatically ingests new emails. Model failure exits without retries; existing sources, including retracted ones, do not call models again. Deletion, nonbusiness reclassification, direction changes, or body changes cause ordinary graph_worker to revoke stale email support. New bodies require new observations.

## Model and database startup

Run from SalesMate root. Initial migrations enqueue existing users for backfill. Model inference remains outside database transactions; graph recomputation remains per user.

```powershell
.venv/Scripts/python.exe backend/manage.py migrate --noinput
.venv/Scripts/python.exe backend/manage.py graph_worker --once

# Run the local model separately; the launcher verifies the approved Q4 artifact's SHA256.
.venv/Scripts/python.exe backend/tools/run_graph_model.py `
  --executable ../output/crmarena-cpu-q4-20260924/llama-bin/llama-server.exe `
  --model ../output/semantic-finetune-v2-20260925/results/semantic-ft-v2/Qwen3-4B-SalesMate-Semantic-v2-Q4_K_M.gguf `
  --variant semantic-v2 --profile baseline
```

Explicitly set these in the Web/ingestion environment:

```powershell
$env:GRAPH_LLM_URL = 'http://127.0.0.1:8088/v1'
$env:GRAPH_LLM_MODEL = 'salesmate-graph'
```

The model binds loopback only, bypasses environment proxies/redirects, and sends no remote requests. Inference uses CPU, 8 threads, 16384 context, 1536 maximum output tokens, temperature=0, seed=2026, and 300-second single-request timeout. Context shifting is disabled; overflow/abnormal termination fails explicitly. Retired-experiment cleanup preserves these parameters and historical audit results.

The launcher verifies weight identity; responses retain aliases/parameters without treating aliases as independent remote weight verification. On 2026-09-25, user-requested cleanup retained only fine-tuned V2 Q4 deployment artifacts, defaulting the launcher to semantic-v2 and removing official-weight deployment branches. See [usage/deployment](semantic-graph-deployment.md) for quality limits.

## Input examples

Save UTF-8 JSON, e.g. `partial-input.json`. Observations may contain partial quote fields and an incomplete company in the same batch:

```json
{
  "source_key": "demo-quote-001",
  "observed_at": "2026-09-24T20:00:00+08:00",
  "records": [
    {"key": "customer", "schema": "crm.company", "fields": {"name": "Acme"}},
    {"key": "quote", "schema": "sales.quote", "fields": {"number": "Q-001", "company": {"record": "customer"}}}
  ]
}
```

A record may specify `source_id` matching its original schema-record ID. Foreign keys may directly supply target ID strings. Unknown targets create graph placeholders without cross-user reads. Without source_id, uniquely match supplied sku, number, email, group_key, source_key, company_name, name, or title against same-type candidates. Ambiguity retains independent observation entities; such matches are not human-verified identities.

Natural language uses the same envelope; choose exactly one of `text`/`records`:

```json
{
  "source_key": "meeting-001",
  "observed_at": "2026-09-24T20:10:00+08:00",
  "text": "Mira 是 Acme 的联系人。Acme 需要 Edge，预算为 800 SGD。"
}
```

```powershell
.venv/Scripts/python.exe backend/manage.py graph_ingest --owner 1 --input partial-input.json

# Process this account's persisted inbound business emails only; no mailbox fetch or sending.
.venv/Scripts/python.exe backend/manage.py graph_ingest --owner 1 --emails --watch
```

Identical source-key/content replays avoid model calls; changed content under the same key returns 409. Corrections need new keys. Explicitly retracting the old source before adding a replacement prevents corrections appearing as concurrent candidates; retraction retains history.

## HTTP APIs

Use existing identity/Session CSRF. owner derives from the authenticated user, never request input. Existing experiment switches remain without feature-specific permission expansion.

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/graph/schema/` | Input schema catalog |
| `POST /api/v1/graph/episodes/` | Accept JSON, automatically link/synchronize |
| `GET /api/v1/graph/episodes/` | Paginate owned sources |
| `GET /api/v1/graph/episodes/{id}/` | Source text, extraction, model audit, sync state |
| `POST /api/v1/graph/episodes/{id}/retract/` | Retract with an empty object |
| `GET /api/v1/graph/entities/` | Current entities including external.* |
| `GET /api/v1/graph/facts/?entity={id}` | Entity relations/observed properties |
| `GET /api/v1/graph/facts/{id}/lineage/` | Source versions/evidence |

Unready graphs/missing capture return 503; context changes/source-key conflicts 409; model/evidence failures 502 with stage/safe reason. Only `sync.current` indicates current readiness; observation persistence and graph publication are separate states.

## Boundaries and validation

This first version is not an autonomous business decision service. Existing source excerpts do not prove semantic correctness; models can omit entities, misalign, or misread negation. Exact-name linking can also be wrong. Build business-specific entity/relation annotations before deployment.

In an actual synthetic example, Qwen mapped being an Acme contact to `works_for`, exceeding strict employment meaning. Source validation passed, but the relation remains a model candidate. No separate semantic judge or human-review UI exists; not all graph relations are verified.

Natural language is limited to 12000 characters; structured input to 30 records/60000 characters; model output to 30 entities/60 facts. Total linking context is limited to 80000 characters, rejecting overflow without silent candidate removal; local model token limits also apply. Large graphs need hierarchical candidate retrieval/performance evaluation. Full per-user recomputation has no demonstrated large-scale performance. PDF/web parsing, proactive crawling, automatic general-chat calls, and graphical browsing are outside this delivery.

Database tests use real PostgreSQL with explicit model mocks. Real-model smoke tests use isolated `salesmate_semantic_smoke_` databases/synthetic data. Delivery reports record actual runs; mocks are not real-model accuracy evidence. See [usage/deployment](semantic-graph-deployment.md) for release checks/commits; historical smoke and current release validation are recorded separately.
