# Application support APIs for algorithms

The backend handles storage, permissions, versions, files, and business operations; callers handle scoring, matching, recommendations, news collection, and PDF text extraction. Profile helpers and the 14 new opportunity/context tools reuse existing HTTP, Python SDK, CLI, and stdio MCP without a second business implementation. See [development support](development-support.md) for relaxed integration mode/minimal submissions; version/authorization requirements below apply to production.

## Existing and added capabilities

| Data | Tool prefix | Supported operations |
| --- | --- | --- |
| Companies, contacts, emails, opportunities, tickets, quotes, orders | Existing `customers`, `contacts`, `emails`, `opportunities`, `tickets`, etc. | Retain existing tools; current catalogs define writable fields/confirmation requirements |
| Company profile, personal onboarding, seller capabilities | `company_profile`, `sales_setup`, `seller_profile` | `get/update` with explicit revision |
| Reference products, sales proposals | `setup_products`, `solutions` | `list/get/create/update/delete`, stable UUIDs, shared onboarding revision |
| Onboarding files | `setup_documents` | `list/get/upload/read/delete`, PDF/UTF-8 TXT; reference checks before deletion |
| Existing business attachments | `files.read` | Direct chunked Tool-token reads without Session downloads; existing tools remain |
| Global events, industry news | `world_events`, `world_news` | `list/get/create/update/archive`, archive/restore, region/time filters |
| Opportunity signals/scores | `opportunity_signals`, `opportunity_priorities` | `list/get/create/update/archive`, optional explanations, open JSON structures |
| Algorithm inputs/page aggregates | `seller_context.get`, `opportunity_context.get`, `priority_board.list`, `world_insights.get` | Unified profiles, opportunity context, latest priorities, map aggregation |

`setup_products` is the reference catalog; `products` is the transaction catalog. Reference `linked_product_id` may explicitly link the owner's unarchived transaction products without automatic creation, price synchronization, or matching scores. Proposals must link owned uploaded files. Full profile replacements retain item `id`; read current snapshots before edits. Legacy items without IDs receive deterministic UUIDs on read, persisted when the corresponding array is next saved; frontend edits retain IDs/links.

`seller_profile.update` retains existing capability fields/dependency updates without scoring changes. Other new tools do not call models, fetch external sites, or generate scores.

## Repeated calls after one authorization

Authenticated users inspect exact scopes through `GET /api/v1/agent-tools/permission-presets/`, then create credentials with Session + CSRF:

```json
{"name":"Algorithm data integration","preset":"data_management","expires_in_hours":720}
```

Send to `POST /api/v1/agent-tools/credentials/`. Alternatively use `read_only` or explicit existing `allowed_tools`; choose either `preset` or `allowed_tools`, not both. Presets freeze tool names at creation; deployments never expand old tokens automatically. Users must issue newly scoped credentials for newly introduced tools. Plaintext tokens appear once and enter `SALESMATE_TOOLS_TOKEN` through the host's private environment; expiry remains explicit at 1–720 hours.

`data_management` includes current read/write tools, with no per-call confirmation for ordinary profiles, catalogs, files, or event/news writes. It also includes existing business writes, so inspect its list first. External actions may only prepare drafts/pending actions; confirmation tools are excluded. Account isolation, granted team permissions, version conflicts, and external-service OAuth remain. Tool tokens cannot authorize themselves, approve proposals, or bypass actual sending/meeting confirmation.

After authorization, calls require only `Authorization: Tool <token>`, without browser Cookie/CSRF. Independent MCP reads the same catalog dynamically; built-in chat-worker read-only allowlists are not expanded. See [Agent business tools](agent-business-tools.md).

## Call examples

Use unified `POST /api/v1/agent-tools/call/`. Read versions first:

```json
{"name":"sales_setup.get","arguments":{}}
```

Update using receipt `data.revision`; revision=0 below applies only to unsaved accounts. Each new logical write gets a fresh UUID idempotency key. Identical keys/inputs reconcile existing operations without automatic retries.

```json
{
  "name":"setup_products.create",
  "arguments":{
    "revision":0,
    "data":{
      "name":"检测设备","category":"光学",
      "specifications":["精度 1 mm"],"scenarios":["产线检测"],
      "price_min":null,"price_max":null,"currency":"SGD",
      "document_id":null,"linked_product_id":null
    }
  },
  "idempotency_key":"eb6c53cf-6613-4412-b9ee-ebd8f2680c51"
}
```

Item operations return `data.item`/`data.setup_revision`; lists return pagination and `setup_revision`. Subsequent update/delete uses the latest version. Full profile updates retain complete-replacement semantics for nested personal objects and products/solutions arrays; item-level update alone merges specified item fields.

Uploads accept `name`/`content_base64`, with at most 5 MiB original bytes. Read using:

```json
{
  "name":"setup_documents.read",
  "arguments":{"id":"<actual-file-UUID>","format":"text","offset":0,"limit":16000}
}
```

`text` supports UTF-8 TXT only, with character offsets/limits capped at 16000 per call. `base64` supports binary byte offsets/limits capped at 262144. Responses include `sha256`, `total`, `unit`, and `next_offset`; callers handle subsequent chunks, with null at the end. PDF returns original bytes only, not extracted text. `files.read` uses identical parameters and verifies attachment size/SHA-256. Deleting onboarding files still referenced by products/proposals returns 409; remove references explicitly first.

## Global-event/news data APIs

Browser Session APIs also expose `/api/v1/sales/records/world-events/` and `/api/v1/sales/records/world-news/`. Tool identities use unified call endpoints and cannot replace Sessions on business routes. Servers maintain ID, owner, revision, archival state, and timestamps; updates/archive require the read revision. Event APIs do not synthesize transaction amounts.

Event fields: title, event_type (exhibition/sales), country (two uppercase letters), city, latitude/longitude, starts_at/ends_at, nullable registration_deadline, source_url, description, onsite, suggested_actions, opportunity_ids, data_source. Opportunity links require authorized unarchived distinct UUIDs; experiment mode allows cross-account links. This validates references on save; later opportunity changes do not automatically remove historical links. Callers explicitly supply on-site information/advice; servers only store it.

In production/personal isolation, all authenticated employees share event/news reads, including manual/Agent sources; updates/archive remain owner-only, and Tools require allowlisted authorization. Every read filters opportunity_ids by viewer permissions; map amounts/company data retain original isolation. Public text fields are not private notes.

Optional time_precision=date|datetime defaults to datetime. Read-only starts_on/ends_on provide inclusive dates for date precision, otherwise null. date starts_at/ends_at use consistent UTC-midnight/noon boundaries with an exclusive end date. Backend compatibility recognizes existing explicit Agent placeholders without requiring payload changes; see [global insights](world-news.md).

Deduplicate nonempty Agent news by source URL and Agent events by URL/start time across accounts; archival retains uniqueness. Duplicates/races return 409 without overwriting; manual records are exempt.

News fields: title, category (regulation/industry/competition/price), industry, country, published_at, source_url, summary, content, data_source. Experiment mode or data_source=synthetic permits empty sources; supplied URLs must use credential-free HTTPS, without server fetch/reachability checks. Timestamps require timezones. Content is plain text, never executed HTML.

Lists support page/page_size, q, archived, country, from/to; events additionally event_type and news category. from/to are timezone-aware ISO timestamps with inclusive lower/exclusive upper bounds. Events sort by start ascending, news by publication descending. Unknown filters fail. Default pagination is 30, maximum 100; no implicit complete fetch.

Global-insight pages read databases and priorities display stored results. Fictional content requires explicit initialization/provenance; backends/pages do not independently collect, score, or recommend. Deploy independent Agent collection schedules separately; see [collection operations](world-insights-operations.md). No SSE/WebSocket.

## Operations and validation

Deploy with `python backend/manage.py migrate`. `sales.0007_world_insights` creates event/news tables; `sales.0008_development_support` adds provenance, permits empty sources, and creates signal/score tables. Existing deployment scripts include migrate. Ordinary profile/file tools reuse tables without new third-party keys; fictional data requires separate explicit initialization.

`tests.integration.test_support_tools` covers real HTTP + PostgreSQL CRUD, account isolation, idempotency, version conflicts, file references/chunks, and presets without real external data sources. `browser_onboarding.cjs` uses mocked APIs/real Chrome to verify preserved links during edits. Existing SDK MCP stdio tests use local HTTP fixtures; protocol success does not verify production accounts/external sending.

## Public-news sales leads

`world_news.create/update` supports thirteen optional public lead/source-amount fields; `list/get` and REST return them identically. See the [news lead contract](world-news-signals.md) for exact amounts, evidence combinations, nulls, and display semantics. Tool catalogs publish actual schemas; legacy payloads remain valid without automatic CRM creation/linking.
