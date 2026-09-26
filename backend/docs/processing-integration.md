# Email processing and review integration

Implemented from 2026-09-13 requirements and subsequent user decisions. Web enqueues only; independent workers invoke Agent, preserving scoring/model parameters. Since 2026-09-20, each Gmail sync explicitly selects recent N days or messages, querying that scope at 20 per page. Ordinary runs cap at 50, including day-only requests; larger runs require approval. No full-history backfill/expired-History fallback. Agent reads analysis-v3 from customer-analysis Skill; backend saves/caches submitted versions without fixed analysis-version settings.

## Implemented behavior

| Requirement | Implementation |
|---|---|
| Persistent batches/per-email state | MailboxSyncRun/EmailProcessingJob register discovery and fetching/extracting/persisting/completed/failed stages |
| Fast response | request-sync returns 202 with run_id, queued, original mailbox fields; active-batch repeats return 409 without replacing scope |
| Failure isolation | Agent observation callbacks persist independent Gmail-read/L1/submission failures |
| Independent scheduling | crm_worker: one sync channel, two default profile channels; four-way L1 retained |
| Company exclusion | Existing Jobs with owner-row-serialized claims; same-company successors wait, others proceed |
| Overall progress | Counts derive from tasks; profile statistics cover linked companies independent of frontend pages |
| Nonbusiness/review | Rule skips hidden by default; inbound v7 without procurement stages enters review; manual decisions win |
| Consistent queries | Inbox/statistics/details/Agent context share classification; management directory remains separate |
| Historical processing | classify_emails previews by default, --apply writes; retain source, extractions, transactions, decisions |
| Cursor integrity | Historical rescan after expiry retains pending/failed IDs; explicit retries use failed scope |
| Source/L1 caches | StoredMessage saves text then completed L1; failed HTTP retries reuse output without model calls |
| Lineage repair | SnapshotSource tracks email/extraction/review versions; invalidate L2→L3→L4 and recalculate remaining business sources |

Per-email counts derive from tasks instead of drifting accumulators. Persisted email bodies with failed extraction still count as failed tasks. Mixed success/failure yields partial; all failed yields failed.

## Startup and upgrade

From repository root with existing Python/PostgreSQL:

```powershell
python backend/manage.py migrate
python backend/manage.py classify_emails
python backend/manage.py classify_emails --apply
```

After HTTP startup, separately run:

```powershell
python backend/manage.py crm_worker --analysis-workers 2 --poll 1
```

--once drains currently claimable work before exit and actually calls Gmail/Bailian; it is not read-only inspection. Workers require ANALYSIS_PROVIDER=agent. Rules demonstrations retain explicit page entry points without Agent-failure fallback; existing provider settings are unchanged.

Old Web scheduling threads/switches were removed. Shared workers rotate all active employees from the database, creating independent temporary credentials/clients per unit and revoking credentials afterward. Employees need no manual process binding. Code creates neither OS services nor Web-startup subprocesses. sales_worker retains confirmed sending/calendar actions and follow-up reminders only.

## State, recovery, compatibility

- Queued/pending work survives Web/worker restarts. HTTP failures during Web restarts leave visible failed units; employees explicitly retry after services recover.
- Mailbox leases are 600 seconds, renewed by stage events. Hard-interrupted running work becomes failed after expiry, retaining per-email state. Page retries create new batches only for failed emails.
- Company Jobs retain leases/revisions/failure semantics. Stale results cannot overwrite newer context; updates during execution merge into pending successors.
- New final reports validate run_id, lease, and state. Legacy mailbox-syncs claim/report APIs remain for migration, matching current mailbox runs; their missing executor identity means they must not share mailboxes concurrently with new workers.
- Optional Agent progress callbacks pass IDs/stages/controlled codes only and propagate failures. Legacy CLI without callbacks retains behavior; use crm_worker for full read-failure isolation.
- Worker takeover markers, scoped discovery, and tasks commit together without advancing global History cursors. Database/network errors propagate. Legacy cursor errors also propagate; takeover blocks CLI cursor writes to prevent dual ownership.

## APIs

| Endpoint | Behavior |
|---|---|
| POST /api/v1/mailboxes/{mailbox_id}/request-sync/ | Requires sync_options; returns 202/batch without waiting for models |
| GET /api/v1/mailbox-sync-runs/{run_id}/ | Email/profile progress and safe per-email errors |
| POST /api/v1/mailbox-sync-runs/{run_id}/ | Explicit failed/partial retry; new batch with 202 |
| GET /api/v1/email-reviews/?status=pending&page=1 | Review pagination across current employee mailboxes |
| GET /api/v1/mailboxes/{mailbox_id}/email-reviews/ | Single-mailbox review pages |
| PATCH /api/v1/email-reviews/{email_id}/ | confirmed_business/confirmed_non_business review_status with review revision in If-Match |

status=non_business shows rule-hidden emails; all includes hidden, pending, and manual decisions. Added saved includes all persisted in-scope emails, including unreviewed business emails, for QQ saved-email reconciliation. source, received_at, classification describe provenance/time/classification without changing it. URL-encode original email_id; other employees receive indistinguishable 404. Writes use Session/CSRF without caller-supplied owner.

## Unified rules and persistence boundaries

1. Inbound extract-v7 intent_hint=null enters review, including substantive updates. Null is not automatically nonbusiness; employees may confirm either. Rule-skipped mail remains correctable under nonbusiness filters.
2. Confirming business mail without completed L1 creates ExtractionRepair. Workers use persisted text/original L1 prompts, bypassing automatic filters already rejected by human review. Preserve old extraction/text; repair_generation records generations without fabricated prompt versions. Profiles wait for repair. Failures appear in all review results and retry only after another explicit business confirmation.
3. Classification/extraction changes invalidate Email → Extraction → AnalysisInput → Analysis → Score. Preserve history but immediately exclude stale display/cache. Remaining business mail merges a recalculation Job; no business mail stops pending work. Reject old running revisions. Changed manual decisions revoke unfinished repairs and reject stale model saves.
4. StoredMessage.raw retains Gmail-parsed headers/body/eligible body. Save LLM output to submission before business APIs. Same-version terminal cached records skip source rereads/L1. Failed L1 reuses text; failed HTTP reuses output. Batch ORM reads replace per-email HTTP reads while retaining original write APIs.
5. Gmail freezes second-resolution UTC windows (after:since before:until). recent_days means 24-hour periods; max_messages selects latest N across inbox/sent. Ordinary default is 50; larger counts require allow_large_sync=true specific to that count/run. Limit first, then exclude saved/completed/failed cache, without backfilling older mail or draining out-of-scope pending work. Ordinary sync does not re-extract completed mail after prompt upgrades. SyncCheckpoint now marks takeover; old cursor fields remain audit-only. Explicit retries use failed IDs; failed pagination without failed IDs reselects frozen scope and skips completed mail. Legacy batches lacking both scope/IDs require new scope selection.
6. L1 processes new/missing/explicitly retried mail only. L2 still reads complete valid company context; L3 generates a whole-company profile and L4 follows existing rules. Models were not reduced to the last email only. Email misclassification never automatically deletes/rewrites authoritative profiles, quotes, or orders.

New snapshots retain exact relational lineage. Historical snapshots infer dependencies only from existing member_dedupe_keys, never backfilled unknown extraction generations. Snapshot uniqueness spans company/input_version/revision, permitting restored identical content after classification revocation with separate history. Apply crm.0006_durable_lineage, preview/apply historical classification, preserving manual decisions.

Gmail reads current inbox/sent within selected scope. Gmail deletion/label changes do not delete local business archives. Source caching is not binary attachment archival. Discovered unstarted work runs only within current scope; failed work needs explicit retries. Recovery/increments require workers; Web restart never starts them automatically.

## Validation boundaries

2026-09-13 lineage/persistence hardening: all 96 local Django tests passed, including 3 existing demo tests; 120 offline Agent tests passed. Browser checks covered repair-failure explanations, versioned confirmation, explicit retry, escaping, mobile layouts. JS syntax, migration consistency, and schema regressions passed. Documentation structure passed for 95 backend Python files; change checks had 0 errors/0 review items. Modified Agent files passed separately with manual semantics review. Test databases were destroyed normally.

Local crm.0006_durable_lineage was applied; historical preview/apply/repreview each found 0 changes. Restarted Web returned HTTP 200/readiness ok. This update remained uncommitted and started no real Gmail/LLM queue consumption. Real model quality, Gmail pagination-token lifespan, and production throughput were unverified; historical snapshots gained no fabricated precise source versions.

Tests use isolated PostgreSQL and mocked Gmail/models, covering permissions, conflicts, manual precedence, batch merging, per-email failures, expiry, company exclusion, and worker callbacks/persistence. Mock success is not real authorization/model/recovery acceptance. Delivery notes determine actual checks/commit state.

Initial implementation checks on 2026-09-13 (historical; later release checks in [live results](live-results.md)):

- Merged remote documentation commit 0a4667e while functionality remained uncommitted; this release additionally merged 19abc8b.
- 84 isolated PostgreSQL Django/141 offline Agent tests passed; mocked browser APIs verified review, version headers, escaping, retries, mobile layouts.
- Schema, migration, Django, JS checks passed. Documentation covered 90 backend/two changed Agent files, with 0 errors/0 review items and manual review.
- Actual crm_worker --help was verified without real queue consumption, Gmail/Bailian integration, production load, or crash acceptance.
- Applied crm.0005_persistent_processing locally; historical classification updated 21 emails, then preview found 0 differences. Original text/manual decisions remained. 21 counts metadata changes, not newly hidden emails.
- Ruff was absent, so that static check was not completed. Checkers were unchanged and Git atomicity was not claimed.

## Gmail scope examples

```json
{"sync_options":{"recent_days":7,"max_messages":50}}
```

This selects the last 7 days and at most 50 messages. Supply days or count; both intersect. Day-only requests default to 50. Above 50, a warning explains count, long worker occupancy, and analysis cost. Cancellation preserves the form without submission; approval sends allow_large_sync=true. Backend/workers independently reject unapproved oversized requests, preventing UI/legacy-queue bypass. Approval does not transfer to new selections; explicit retry of the same failed batch retains count/approval without expanding scope. OAuth callbacks save connections only, not batches; returned pages collect scope, and cancellation performs no sync. Inbox refresh also requests scope.

Read scope using [Gmail newest-first pagination](https://developers.google.com/workspace/gmail/api/guides/list-messages) and [second-resolution filtering](https://developers.google.com/workspace/gmail/api/guides/filtering). Body-read/L1 parameters remain unchanged.

Oversized example requiring prior explicit approval:

```json
{"sync_options":{"recent_days":7,"max_messages":100,"allow_large_sync":true}}
```

Approval never permits unlimited synchronization; a concrete count is mandatory. Legacy day-only batches still cap at 50; legacy oversized unapproved batches fail. The cap limits email count, not each external call's latency. Page size, model parameters, and failure-retry semantics remain unchanged.
