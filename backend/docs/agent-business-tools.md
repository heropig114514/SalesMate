# Agent business-tool integration

Graph capabilities comprise nine `graph.*` tools for structured/text input, queries, source lineage, and retraction. Existing credentials do not gain permissions automatically; integration and performance boundaries are documented in the [Agent graph handoff](semantic-agent-handoff.md).

The algorithm integration server currently enables [public laboratory mode](laboratory-access.md): all business data, including non-KGSEED data, can be read and written across accounts without login, and Tool/MCP calls require no token. The authorization constraints below apply when that switch is disabled. See that document for retained external-action and secret boundaries.

The catalog provides business tools, separate user delegation, an HTTP SDK, a CLI, and a stdio MCP server, reusing existing permissions, serializers, and transactions. Its size is given by `catalog.count` and depends on authorization and the QQ switch; `actions.prepare_qq` is omitted while QQ is paused. See [software support interfaces](software-support-tools.md) for profiles, files, event news, and permission presets. These interfaces support Agent developers; the current chat workflow remains read-only Q&A without an automatic tool-selection/execution loop.

## File responsibilities

```text
backend/apps/agent_tools/
  registry.py          # Tool names, purposes, execution modes, and fixed routes
  schemas.py           # Input schemas from actual serializers; strict preflight validation
  authentication.py    # Users, token expiry/revocation, and tool allowlists
  dispatch.py          # Fixed adapters to CRM, sales, and chat handlers
  support.py           # Profile catalogs, onboarding files, and chunked attachment reads
  presets.py           # Explicit permission snapshots for the current tool scope
  services.py          # Idempotent receipts, frozen proposals, independent confirmation, permission rechecks
  models.py            # ToolCredential, ToolCall, ToolProposal
  views.py / urls.py   # Tool HTTP endpoints and session-only authorization/confirmation
  migrations/          # New tables; existing business tables are unchanged
backend/tests/integration/test_agent_tools.py
integrations/salesmate_tools/
  client.py            # HTTP SDK independent of Django
  cli.py               # list / describe / call
  mcp_server.py        # Dynamic schemas, paginated catalog, independently named MCP tools
  requirements.txt     # Independent client dependencies
  tests/test_clients.py # HTTP, CLI, and real MCP stdio handshake
```

Implement new permissions and rules in the business module first, then register the tool, bind dispatch, and test tool boundaries. Do not duplicate database business logic in MCP, CLI, or prompts.

## Coverage

| Domain | Prefixes / representative tools | Capabilities |
| --- | --- | --- |
| Customers | `customers.search/create/context/register/analyze`, `customer_settings.*` | Directory, registration, private context, sourced CRM profiles, analysis jobs, settings |
| Contacts | `contacts.save`, `contact_profiles.*` | Email/name and supplemental profiles; contact IDs are integers |
| Grouping | `aliases.*`, `customers.merge/move` | Manual aliases, customer merging, selected-email moves; propose a plan first |
| Collaboration | `teams.*`, `memberships.*`, `grants.*`, `people.find` | Teams, memberships, customer grants, exact username lookup |
| Sales | `products.*`, `tickets.*`, `opportunities.*` | Products, tickets, opportunities |
| Documents | `quotes.*`, `quote_lines.*`, `orders.*`, `order_lines.*` | Document/line reads and writes, amounts, revisions; confirm status changes first |
| Follow-up | `follow_ups.*`, `notifications.list/get/read` | Plans, assignments, reminders, read state |
| Assistant | `conversations.*`, `messages.list/get`, `drafts.*` | Conversations, history, drafts; no fabricated assistant answers |
| External actions | `actions.prepare_gmail/prepare_qq/prepare_calendar`, `actions.list/get` | Freeze email/meeting contents for human confirmation |
| Mail | `mailboxes.list/sync/sync_status/retry`, `emails.list/review` | Sync progress, saved emails, reviews; confirm writes first |
| Calendar | `calendar.events/freebusy` | Events and availability for an explicit connection/calendar/timezone-aware window |
| Evidence | `knowledge.search/get`, `files.list/get/archive/download_link` | Imported-knowledge keyword search, source versions, attachment metadata, session download links |
| Status | `sales.overview/audit`, `connections.list/get`, `proposals.get` | Overview, audit, secret-free connection metadata, the caller's proposal results |
| Profiles | `company_profile.*`, `sales_setup.*`, `seller_profile.*`, `setup_products.*`, `solutions.*` | Profile reads/writes, individual product/solution management, explicit transaction links |
| File contents | `setup_documents.*`, `files.read` | Private uploads, chunked reads, reference checks, deletion; no session download needed |
| Event news | `world_events.*`, `world_news.*` | Private fact CRUD, archive/restore, region/time filters; no generated scores |

Ordinary writable resources expose list/get/create/update/archive; unsupported operations are not registered. Messages, actions, attachments, notifications, and connections do not expose ordinary create/update. Use live `inputSchema` for exact fields. The token-filtered catalog defaults to 30 entries, with a maximum of 100; handle pagination. Business lists do not traverse all pages automatically. Select tool categories and authorization subsets for the task instead of placing every tool in every model context.

## Execution rules

- `read`: queries reject idempotency keys; calendar queries access external APIs.
- `write`: ordinary writes, such as follow-ups and drafts, require a UUID `idempotency_key`. Graph exceptions `graph.ingest`/`graph.retract` expose `idempotency_scope=source_key/episode_id`, use source-level idempotency, and reject transport UUIDs. They create no ToolCall; Episode and graph lineage retain their audit records.
- `confirm`: saves a frozen proposal valid for 24 hours without executing business operations; users review and approve separately.

Writes deduplicate by user plus idempotency key, sharing a logical operation across credentials. Replaying the same tool and input returns the historical receipt; different contents under the same key return 409. JSON key order does not affect deduplication. A receipt is not the latest business-entity snapshot.

`status=completed` means the operation completed; `accepted` means background work was accepted; `confirmation_required` means confirmation is pending. External-action preparation returns `data.status=pending_confirmation` and the original ToolAction; generic plans return `proposal`. Their confirmation routes differ.

Before updating, get the entity, then supply `id`, integer `revision`, and `data`. Use strings for Decimal amounts/quantities. Updates cannot write read-only fields. Quote-line writes advance the parent revision, so reread the parent before subsequent operations. QQ synchronization requires explicit `sync_options.recent_days` or `max_messages`; Gmail rejects QQ scope options. No default scope is filled automatically.

## HTTP and authorization

Apply new migrations against an explicitly configured development database with `python backend/manage.py migrate`. Tools use `Authorization: Tool <token>`, independently of Worker `Agent` credentials; they are not interchangeable. Identity comes only from the authorized user, and inputs cannot impersonate an owner. Existing team sharing and personal mail/draft/attachment isolation remain effective.

All routes below use the `/api/v1/agent-tools/` prefix:

| Method / path | Identity | Purpose |
| --- | --- | --- |
| `GET catalog/?category=follow_ups&page=1&page_size=100` | Tool or Session | Names, descriptions, schemas, execution modes, annotations |
| `GET permission-presets/` | Tool or Session | One-time permission presets and tool-name snapshot explanations |
| `POST call/` | Tool or Session | One structured call |
| `GET/POST credentials/` | Session; writes require CSRF | List/create the caller's authorizations |
| `DELETE credentials/{id}/` | Session + CSRF | Revoke the caller's credential |
| `GET proposals/`, `GET proposals/{id}/` | Session | Pending proposal list/details |
| `POST proposals/{id}/decision/` | Session + CSRF | `{"decision":"approve"}` or `cancel` |

Example credential request:

```json
{"name":"跟进助手","allowed_tools":["customers.search","follow_ups.list","follow_ups.get","follow_ups.create","follow_ups.update","proposals.get"],"expires_in_hours":24}
```

Explicit expiry must be 1–720 hours; wildcards are unsupported. The raw token is returned once, only its digest is stored, and responses prohibit caching. Keep tokens out of chat, tool arguments, logs, and Git. Authorization and approval have no MCP/CLI tools.

Example call; replace UUIDs with a real customer and this logical operation's identifier:

```json
{
  "name": "follow_ups.create",
  "arguments": {
    "data": {
      "company": "11111111-1111-4111-8111-111111111111",
      "title": "确认设备规格",
      "due_at": "2026-10-01T10:00:00+08:00"
    }
  },
  "idempotency_key": "22222222-2222-4222-8222-222222222222"
}
```

Errors retain their original HTTP 400/401/403/404/409 status. A network interruption can occur after commit; clients do not retry automatically. Reuse the original key and inputs when checking a call, and do not assume it was never executed. Business failure rolls back the current receipt.

## MCP, CLI, and Python

Install the SDK independently to avoid changing Web/Worker dependencies. Run from the repository root:

```powershell
python -m venv .tools-venv
.tools-venv/Scripts/python.exe -m pip install -r integrations/salesmate_tools/requirements.txt
$env:SALESMATE_TOOLS_URL = "http://127.0.0.1:8000"
# Inject SALESMATE_TOOLS_TOKEN through the host's private environment, outside the repository.
.tools-venv/Scripts/python.exe -m integrations.salesmate_tools.cli list --category follow_ups
.tools-venv/Scripts/python.exe -m integrations.salesmate_tools.cli describe follow_ups.create
.tools-venv/Scripts/python.exe -m integrations.salesmate_tools.cli call follow_ups.create --arguments-file follow-up.json --idempotency-key 22222222-2222-4222-8222-222222222222
```

`follow-up.json` contains only the example's `arguments` object. CLI stdout is JSON; errors go to stderr with a nonzero exit status. It does not execute shell strings. Use the HTTPS service root URL; HTTP is allowed only locally.

For an MCP host, use the independent environment's Python executable with `-m integrations.salesmate_tools.mcp_server`, the repository root as working directory, and URL/TOKEN injected through the environment. Only stdio is exposed; there is no public MCP port. The dependency is pinned to `mcp==2.2.0`, using the low-level Server from the [official Python SDK](https://github.com/modelcontextprotocol/python-sdk).

MCP publishes concrete tool names and schemas. Write tools require `idempotency_key` at the outermost argument level; the adapter moves it into the HTTP envelope. Hosts must handle `tools/list.nextCursor`. Results include JSON text and `structuredContent`; HTTP/network failures set `isError=true`, while pending confirmation is a valid receipt.

```python
from integrations.salesmate_tools.client import ToolClient
client = ToolClient.from_env()
result = client.call("customers.search", {"q": "设备", "page": 1})
```

## Integration boundaries

- These development interfaces do not yet add a credential-management or generic ToolProposal review frontend. Frontends can use the Session API directly. Email/meeting actions can still use the original business pages' ToolAction confirmation.
- Proposal creation validates structure; approval rechecks entity permissions, revisions, and the original token's validity. Expiry, revocation, or conflicts reject execution. Confirmation cannot alter frozen contents, and repeated identical decisions do not execute twice. Agents can only query results through `proposals.get`.
- Chat Skills, model parameters, and read-only prompts are unchanged. Future work includes tool selection, argument clarification, execution loops, confirmation cards, and business evaluation.
- The global-insights page remains a demonstration, while event news has real storage APIs/MCP. Knowledge search remains keyword-based. Tool tokens now support chunked attachment reads; original download links still require Session. PDF parsing, live push, and vector search are not provided.
- Humans still establish OAuth/QQ secret connections through the original pages. SQL, shell, operations, credential reads, and direct approval of outbound actions are unavailable. Only reference entries and unreferenced onboarding attachments support explicit deletion; transactions remain archived.

## Validation

```powershell
python backend/manage.py test tests.integration.test_agent_tools --noinput
.tools-venv/Scripts/python.exe -m unittest discover -s integrations/salesmate_tools/tests -v
python backend/tools/check_docs.py
python backend/tools/check_docs.py integrations
python backend/tools/check_doc_changes.py --base HEAD --fail-on-review
```

Backend tests cover all resource lists, validation, permissions/CSRF, revisions, idempotency, cross-credential concurrency, quote amounts, contact keys, knowledge sources, preparation-only behavior for all three external actions, proposal confirmation/expiry/revocation, and rollback. Client tests include real SDK stdio handshakes and pagination with local HTTP fixtures; they do not establish successful authorization or sending through real external accounts. CI runs tests in an independent SDK environment.

## Semantic graph MCP

Nine `graph.*` tools provide schemas, status, entities, facts, lineage, source list/details, ingestion, and retraction. Credentials must explicitly include their names. See [graph usage and deployment](semantic-graph-deployment.md) for calls, SSH tunnels, and deployment.
