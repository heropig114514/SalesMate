# KG experimental-data sharing

The algorithm integration server currently enables [public laboratory mode](laboratory-access.md): all business data, including non-KGSEED data, is readable and writable across accounts without login, and Tool/MCP requires no token. The authorization constraints below apply only when that switch is disabled; see that document for retained external-action and secret boundaries.

All active signed-in accounts can read the 44 tables in batch **KGSEED_20260921_01** across accounts. The batch initially contained 6,000 fictional records; use the catalog for counts after maintenance. Existing customer, opportunity, quote, order, and ticket lists merge the corresponding experimental rows, identifying fictional status, maintenance permissions, and original ownership. The experiment-data entry in the top navigation opens `/experiments/` to browse all 44 tables and lineage. Neither team membership nor administrator access is required.

The website, built-in chat Agent, tool API, and MCP use the same batch manifest. Original customer workspaces and business writes retain their existing authorization scope. Sharing does not transfer `owner`; the shared-maintenance entry points permit the business-data creation, modification, and deletion described below without granting external sending or queue-execution permissions. The 100 disabled test accounts in the profile table are also fictional; login fields such as passwords are not returned.

## Using the page

1. Sign in with any normal account and open experiment data from the top navigation.
2. Select a table on the left. Search raw field contents or filter by owner username/ID.
3. Details show all permitted fields, original primary keys, ownership, batch, write capabilities, and fictional status. Follow foreign-key links to related records.
4. Snapshot-source lineage traces analysis inputs to mail and extraction. Answer-generation records, citations, and tool-read evidence show the answer evidence chain.
5. Downloading complete JSON returns every table's records, field structures, and scenario-link manifest. Binary documents retain size and digest in JSON; use the detail view's file-download entry for contents.

Existing sales lists deduplicate normal and experimental records by primary key and paginate them together. Experimental records open detail views with edit/delete links where maintenance is allowed. Selecting an experimental customer hides new-business buttons; customer choices in write forms still come from original business permissions. Private-mail entries in customer context become experimental-source entries. Count cards include and separately identify shared experimental counts; monetary totals retain original business scope and exclude additionally shared simulated transactions.

Merged page reads use session-authenticated `GET /api/v1/sales/browse/{resource}/` and `GET /api/v1/sales/browse/overview/`. Lists support `company`, `status`, `archived`, `page`, and `page_size`, defaulting to 20 and allowing at most 100 rows. Only GET/HEAD/OPTIONS are accepted. Original sales/directory/, sales/records/, private customer details, and write endpoints retain their authorization contracts. Normal records appear first, shared experiments second; batch records already visible through ownership/team access are not duplicated. Shared details expose only listed contacts, settings, and fields, not ordinary private records linked later.

Analysis and scores can change through explicit maintenance requests; vectors retain original fixture values. These are simulated records, not real model inference, semantic embeddings, or model-quality evaluation results.

## Read-only interfaces

These website interfaces use Session authentication and accept only GET, HEAD, and OPTIONS. Anonymous users cannot read data under the ordinary authorization mode. Do not share website sessions or Agent credentials. Agent-tool authentication is described next.

| Interface | Contents |
|---|---|
| `/api/v1/experiments/` | Approved batches, 44 tables, registered counts, field and foreign-key structures |
| `/api/v1/experiments/KGSEED_20260921_01/crm.Company/` | Customer table; use catalog names for other models |
| `/api/v1/experiments/KGSEED_20260921_01/export/` | JSON download of all tables |
| `/api/v1/experiments/KGSEED_20260921_01/sales.Attachment/{pk}/download/` | Customer attachments |
| `/api/v1/experiments/KGSEED_20260921_01/accounts.SetupDocument/{pk}/download/` | Product and solution documents |

Table endpoints support `q`, `owner`, `pk`, `page`, and `page_size`, defaulting to 50 rows and allowing at most 200. Exact primary and foreign keys retain database identities without renumbering. Exported `scenario_links` are simulated scenario links registered by the generator, not relationships discovered by a model.

## Supplemental company-analysis material

The company-analysis Worker can obtain matching cross-account experimental material through the original Agent context endpoint without an additional Tool token. L2 stores sources separately; L3 can use supplemental headcounts; changed sources invalidate old caches. See [company enrichment](company-enrichment.md) for contracts and integration.

## Agent, tool API, and MCP

A normal account can ask the website assistant to inspect the KGSEED catalog, read two customer rows, and state their original ownership and sources. The built-in Agent automatically uses request-bound data tools without manual Tool-token configuration. Prompt version is `workspace-chat-v3`; query counts, pagination budgets, context, and model parameters retain their existing values. Maintenance receipts and files precede catalog/pages and row records so prior-page rows cannot displace file evidence. Undisplayed sources are never cited.

External algorithm clients first sign in with their own accounts and submit this authorization to `POST /api/v1/agent-tools/credentials/` with Session and CSRF:

```json
{"name":"KG实验只读","expires_in_hours":24,"allowed_tools":["experiments.catalog","experiments.rows","experiments.file_read"]}
```

The returned token appears only once. Clients use `Authorization: Tool <token>` with `GET /api/v1/agent-tools/catalog/?category=experiments` and `POST /api/v1/agent-tools/call/`. Existing token name lists are frozen at issuance and never gain permissions through deployment; issue a new token containing new tools. Newly issued `read_only` presets include these three tools.

| Tool | Arguments and results |
|---|---|
| `experiments.catalog` | `{}`; returns batches, original owners, table counts, fields, and foreign keys |
| `experiments.rows` | Required `batch`, `model`; optional `q`, `owner`, `pk`, `page`, `page_size`; returns paginated original records with unchanged primary/foreign keys |
| `experiments.file_read` | Required `batch`, `model`, `pk`, `format`, `offset`, `limit`; attachments/solution documents only; returns ownership, digest, content chunks, and `next_offset` |

Example tool call:

```json
{"name":"experiments.rows","arguments":{"batch":"KGSEED_20260921_01","model":"crm.Company","page":1,"page_size":20}}
```

Tool pagination defaults to 50 and allows at most 100 rows; the website Agent allows at most 20 per page. Read every page using count and page numbers; one page is not the complete dataset. File `text` supports UTF-8 TXT or experimental records verified as text/plain, at most 16,000 characters per read. `base64` allows at most 256 KiB of raw bytes. Both require explicit offsets and lengths; binary documents do not receive automatic text extraction.

MCP uses the existing stdio bridge without another public port. Install `integrations/salesmate_tools/requirements.txt`; configure the host with the repository root as working directory and `python -m integrations.salesmate_tools.mcp_server`, privately injecting `SALESMATE_TOOLS_URL=https://milkdragon.dev` and the account's `SALESMATE_TOOLS_TOKEN`. MCP dynamically publishes the permitted catalog and maintains no separate experiment permission system. See [business-tool integration](agent-business-tools.md).

Successful website Agent reads create ToolRead records belonging to the questioner's request. Answer citations retain actual read evidence with its original owner. Reads do not modify business records. Maintenance atomically updates the current cleanup manifest and saves the original manifest and operator audit. Evidence snapshots remain separate from fixtures.

## Creation, modification, and deletion

All active signed-in accounts, including new registrations, can maintain 28 business/knowledge model types: company profiles, onboarding profiles, seller profiles; customers, contacts, mail, durable mail, extracted facts, analysis snapshots, analysis results, scores, snapshot sources and invalidations; company settings, aliases, supplemental contact profiles, products, tickets, opportunities, quotes and lines, orders and lines, follow-ups, conversations, messages, drafts; and knowledge entries. Three per-account profile models permit only update/delete, preventing creation from overwriting the batch owner's real profiles.

The other 16 table types remain read-only, including identity accounts, teams/grants, attachments/documents, vectors, notifications, execution requests/receipts, and audit evidence. Catalog `tables[].write` and paginated response `write` are authoritative for current capabilities and fields.

On the website, select a table and create a shared fictional record, or edit/delete from details. Ordinary business detail pages also link to maintenance. Foreign keys must belong to the same batch, and ownership cannot change. Durable mail and follow-ups accept only terminal states (completed/failed or completed/cancelled), without launching processing or reminders. Every operation records operator, time, changed field names, and before/after fingerprints. Deletion permits only one row without reverse references and never cascades implicitly.

Tool writes still use `POST /api/v1/agent-tools/call/`; Session requires CSRF and external callers need a Tool token containing the tool name. The envelope requires a UUID `idempotency_key`. If the outcome is unknown, retain the original arguments and key instead of automatically generating another logical operation.

| Tool | arguments |
|---|---|
| `experiments.create` | `batch`, `model`, `data` (only fields listed in `write.fields`) |
| `experiments.update` | `batch`, `model`, `pk`, `expected`, `data` |
| `experiments.delete` | `batch`, `model`, `pk`, `expected` |

`expected` must equal the latest read's row fingerprint; stale versions return 409. Saving and current-manifest fingerprint updates are atomic. Invalid fields/relationships return 400, unavailable models 403, and unlisted primary keys 404. New rows register automatically in the batch with ownership fixed to its owner. Initial maintenance preserves `original_rows`; later changes append `mutations`. Generator scenario_links retain their original values; after maintenance, `scenario_links_status=original_before_edits` prevents treating them as updated ground truth.

The built-in website Agent can propose fictional-data maintenance on explicit request, such as renaming a KGSEED customer with an identified primary key. It reads the catalog and latest fingerprint, then suspends for [browser approval](chat-approvals.md). Only approval executes the frozen operation; the Agent resumes with the actual receipt. Request-bound interfaces retain the tool-reads/ path and executionMode=write, but return approval_required before execution; the approval UUID is the write idempotency key. This gate remains active in laboratory mode. Real-business writes and external actions remain outside workspace chat.

MCP dynamically publishes the three write tools with explicit idempotency_key in addition to the arguments above. Old read-only tokens do not gain permissions. Issue a token explicitly containing all six experiment tools, or use data_management, which also includes other business-maintenance permissions.

## Sharing boundaries and cleanup

- Only this batch explicitly listed in code `APPROVED_BATCHES` is available. A KGSEED name prefix grants no authorization, and future batches are not automatically shared.
- Read only exact primary keys in the current AuditEvent batch manifest and verify stored row fingerprints. Unlisted records, including ordinary records linked later to the same fictional customer, are excluded.
- Only 44 models are allowed. Credentials, connections, framework permissions, and scheduler tables have no experimental-read entry point. Passwords, internal attachment paths, and tool-authorization links are excluded.
- Missing or modified rows not registered through maintenance cause that table to return 409; full export also fails instead of skipping damaged data. Restoring sharing requires maintainer verification, not manually refreshing fingerprints outside maintenance transactions.
- Reads stop during file cleanup. After manifest deletion, original batch routes return 404. Existing `seed_kg_lab --action delete` uses the current manifest and current foreign-key dependency order, without needing to undo permission records.
- Read logs include only requesting account, batch, model, and download type, never mail bodies or credentials. Maintenance logs contain diagnostic metadata only; chat evidence snapshots store JSON without adding foreign-key dependencies to original fixtures.

## Validation

Run against isolated PostgreSQL:

```sh
python manage.py test tests.integration.test_experiments tests.integration.test_sales tests.integration.test_crm --noinput
python manage.py test tests.integration.test_experiment_tools tests.integration.test_chat_tools --noinput
python manage.py test tests.integration.test_business_browse tests.integration.test_experiment_writes --noinput
python manage.py test tests.contracts.test_schema --noinput
python tools/check_docs.py
```

Experiment tests cover cross-account reads for every table, ownership, exact batches, forged prefixes, anonymous rejection, protected-write rejection, cross-account CRUD, idempotency, stale fingerprints, unchanged original business permissions, stopping reads on changes/missing rows, pagination, lineage foreign keys, complete export, attachment digests, and manifest revocation. Agent tests use real HTTP/database with deterministic mocked model decisions. MCP uses the real SDK and stdio subprocesses and explicitly skips that case when the SDK is absent. Never run these tests against the experimental site's actual database.
