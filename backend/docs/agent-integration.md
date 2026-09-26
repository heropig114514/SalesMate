# Agent and Django integration

The current algorithm integration server enables [public experiment mode](laboratory-access.md): all business data, including non-KGSEED data, is readable/writable across accounts without login, and Tool/MCP requires no token. Original authorization constraints below apply only after disabling experiment switches; see that document for retained external-action and secret boundaries.

Updated: 2026-09-13. Agent connects to the real Django backend through `agent/clients/backend_api.py`. The [Agent README](../../agent/README.md) defines business workflows/JSON structures; [OpenAPI](../contracts/openapi.yaml) defines HTTP transport.

## Responsibility boundaries

- Agent handles Gmail reads, MIME parsing, L1 fact extraction, L2 company-fact merging, L3 profiles/analysis, and L4 follow-up priority.
- Django persists users, employee Google OAuth, mailboxes, emails, companies, contacts, business snapshots, Jobs, and analysis results.
- Browsers read the current employee's Gmail status and company lists/details from Django, never Gmail tokens, Bailian keys, or Agent service tokens.
- Agent neither imports Django nor accesses the database directly.

## One synchronization run

1. The employee connects Gmail through web OAuth and requests synchronization.
2. Django persists the batch; an independent worker claims it with employee authorization and invokes Agent. Legacy CLI `mailbox-syncs/claim/` remains only for migration debugging.
3. The worker backfills inbox/sent history at 20 emails per page, persisting discovered IDs/page positions before source processing. After history completes, use History increments; only expired cursors trigger historical rescans, reusing cached results.
4. Batch-reuse successful extractions by `dedupe_key`, persisting source text and completed L1 output by stage. New emails run L1; explicit retries reuse source text or resubmit extraction results according to the failed stage.
5. At most four emails requiring L1 run concurrently. As each finishes, `DjangoBackendClient` immediately submits it individually to `POST /api/v1/agent/emails/`.
6. The backend deduplicates by `mailbox_address:gmail_message_id` and groups emails into companies within the current employee's scope; later successful syncs may update failed extractions.
7. Only completed, business-relevant emails with substantive changes create `email_ingested` Jobs.
8. The worker records per-email progress continuously and saves batch results after mailbox processing; browsers poll full batch counts.
9. An independent profile channel runs alongside synchronization. Agent claims Jobs, reads Grouping/CompanyContext, then builds and saves L2.
10. Agent queries/generates L3, computes/saves L4, and reports the Job. Company profiles are revision-scoped, combining multiple emails into one analysis input.

Jobs expose top-level `company_id` to Agent workflows. HTTP additionally returns `lease_token` and `expected_version`; adapters handle ETag, If-Match, and lease headers, keeping L1–L4 on a simple backend protocol.

## Runtime modes

`ANALYSIS_PROVIDER=agent` enables real Agent mode. The page's update-analysis action creates a Job only; a worker in a separate terminal continuously consumes jobs:

```powershell
python backend/manage.py crm_worker
```

`ANALYSIS_PROVIDER=rules` enables offline demonstrations. Pages can import synthetic examples or simulate incoming mail; deterministic Django rules write demonstration analyses. Rules never take over automatically when Agent network/model calls fail.

## Current limitations

- Agent CLI remains for one-off debugging; independent `crm_worker` consumes database batches/company jobs in the product flow.
- Django stores web-authorized Google credentials and supplies them to Agent only through AgentAuthentication-protected sync claims. Agent no longer maintains legacy local Desktop OAuth read commands; `test_tools/` email injectors use independent Desktop OAuth credentials/tokens, as documented in their README.
- Workers require `sync_options` selecting recent days or message count. Ordinary Gmail limits are 50 emails; larger runs require risk disclosure, explicit user approval, `allow_large_sync=true`, and a specific count. StoredMessage retains in-scope source text/L1 output; SyncCheckpoint marks takeover and retains old audit fields, while legacy SyncState provides compatibility projections. Worker takeover rejects CLI cursor dual-writes; `dedupe_key` still ensures idempotent saves.
- L1 supports at most four concurrent tasks with per-email failure isolation; batches/email tasks persist in the database. Company profiling defaults to two concurrent tasks with same-company exclusion.
- The backend stores separate classification based on Agent signals, hiding nonbusiness/review-pending emails; manual confirmation takes precedence over later automatic classification.
- Inbound `extract-v7` emails without procurement stages enter review. Manually confirmed business emails with missing facts receive L1 repair before automatic profile recalculation. Classification/fact changes invalidate L3/L4 along snapshot lineage and recalculate from remaining business sources.
- Leases/revisions prevent stale jobs overwriting newer context. Company jobs have no automatic lease renewal or implicit retries; mailbox batches refresh leases through stage events.
- Sales relational records maintain tickets, quotes, and orders and project them into CompanyContext. Management pages expose edits/transitions; only sent quotes and confirmed orders provide corresponding analysis evidence.
- Real Gmail and Bailian services are not automated-test dependencies.

See [email processing integration](processing-integration.md) for persistent batches, workers, review, and migration compatibility boundaries.

## Read-only chat integration

Independent `apps.chat` was added on 2026-09-18, reusing employee Agent service authentication. Its fixed claim/context/answers APIs remain compatible with the original Agent chat workflow. It uses an independent `chat_worker` without changing mailbox or L1–L4 flows documented here. See [chat integration](chat-integration.md) for exact contracts, migrations, knowledge, and recovery.
