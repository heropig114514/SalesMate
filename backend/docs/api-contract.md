# Current API contract

Updated: 2026-09-13. The sole machine-readable field definition is Django-generated [OpenAPI](../contracts/openapi.yaml). See the [Agent README](../../agent/README.md) for Agent business-object semantics.

## Identity

Clear account-internal data with `POST /api/v1/accounts/me/reset/`, retaining login identity/password. See [account reset](account-reset.md) for idempotency keys, caching, multiple tabs, and background mutual exclusion.

Browsers first call `GET /api/v1/session/` for a CSRF cookie, then submit `{"username":"...","password":"..."}` to `POST /api/v1/accounts/register/`. Registration accepts only these fields and requires no email, phone, or verification code. Usernames follow existing model rules; passwords are 8–128 characters without restrictions on numeric-only, common, or username-similar values, and still use Django password hashing. Success returns 201, `authenticated`, `username`, and a rotated `csrf_token`, establishing an ordinary-user Session. Invalid inputs/duplicate names return 400, invalid/missing CSRF returns 403, and registration while authenticated returns 409. Passwords are stored as hashes and never returned.

New accounts receive independent empty workspaces and incomplete onboarding, without copied demo data, automatic Gmail authorization, or Agent service tokens. After logout, use `POST /api/v1/session/` to log in; `DELETE /api/v1/session/` logs out. Registration password confirmation is a browser consistency check only and is not sent as a backend field.

From Profile → Company Setting → Your profile, change the current account's password with `POST /api/v1/accounts/me/password/`, passing `current_password`, `new_password`, and `password_confirmation`. Session authentication and CSRF are required. New passwords retain the 8–128-character registration policy, with matching confirmation and no whitespace trimming. The server checks the old password under a user-row lock and returns 204 after saving the new hash. It rotates and preserves the requesting session; other sessions fail Django's password-hash check on their next request. Invalid passwords/confirmation/fields return 400; anonymous or missing-CSRF requests return 403. Account identity, business records, mailbox grants, and Agent/Tool credentials are unchanged.

Browsers use Django Session and CSRF. Agent routes accept only:

```http
Authorization: Agent <service-token>
```

A service token binds one backend user and cannot be replaced by browser Sessions or Gmail access tokens. Backend-created `mailbox_id` and `company_id` are UUIDs. Errors retain `error.code`, `error.detail`, and `request_id`.

## Basic profiles and initial onboarding

`GET session/` adds `onboarding_required`; see [onboarding.md](onboarding.md) for four-step profiles, company size, and private attachments. API errors retain request IDs, which frontends treat only as diagnostic metadata.

## Agent routes

The following paths start with `/api/v1/agent/`:

| Agent operation | HTTP | Main exchanged data |
|---|---|---|
| Submit emails | `POST emails/` | Still accepts `EmailSubmission[]`; the current Agent sends one email per call, returning its `dedupe_key`, `company_id`, and `created/updated/duplicate` |
| Read company grouping | `GET grouping/?company_id=...` | Company, domains, contacts, member email keys; response includes ETag |
| Read company context | `GET context/?company_id=...` | Emails, customer, tickets, quotes, orders; send Grouping's If-Match |
| Save L2 | `POST analysis-inputs/` | Complete `AnalysisInput` |
| Read latest L2 | `GET latest-analysis-input/?company_id=...` | `AnalysisInput`, or 404 when absent |
| Query L3 cache | `GET cached-analysis/?company_id=...&input_version=...&analysis_prompt_version=...` | Complete `Analysis` on hit, otherwise `analysis=null` |
| Save L3 | `POST analyses/` | Complete `Analysis` |
| Save L4 | `POST scores/` | Complete `Score` |
| Claim jobs | `POST jobs/claim/` | `limit`, `lease_seconds` → `Job[]` with top-level `company_id` |
| Report jobs | `POST jobs/report/` | `JobReport` with claim credentials |
| Claim employee mailbox sync | `POST mailbox-syncs/claim/` | `limit` → mailbox address, Google authorization, and read limit |
| Report employee mailbox sync | `POST mailbox-syncs/report/` | Sync summary, errors, optional refreshed credentials → browser-safe state |

Compatibility APIs also include `POST facts/`, `GET failed-extractions/`, `GET sync-state/`, and `POST sync-state-save/`. Product Gmail synchronization requires `sync_options` (at least one of `recent_days` or `max_messages`). Ordinary Gmail batches default to at most 50 emails; larger batches require explicit `max_messages` and `allow_large_sync=true`. Workers limit before deduplication within the frozen scope. StoredMessage stores source text/L1 output; SyncCheckpoint retains only worker takeover markers and old audit state. Retries are explicit and reuse stage caches. After worker takeover, legacy CLI cursor dual-writes are rejected. Configured legacy CLI cursor read/write errors propagate.

## Write consistency

Claimed jobs return `job_id`, top-level `company_id`, `trigger`, `expected_version`, `lease_token`, and `lease_until`.

When saving L2/L3/L4, HTTP adapters send `If-Match`, `X-Job-ID`, and `X-Lease-Token`. Changed context revisions, nonrunning jobs, invalid credentials, or expired leases reject writes. MVP does not automatically renew leases or retry expired jobs.

Email natural keys must be `mailbox_address.casefold():gmail_message_id`. Identical payloads return `duplicate`; a previously failed extraction later succeeding for the same email returns `updated`. Agent submits one email per call, so conflicts do not roll back other emails. Nonbusiness emails or emails without substantive changes are saved without analysis Jobs; default company lists/statistics and Agent context exclude nonbusiness and review-pending emails.

Core L2/L3/L4 constraints:

- L2 preserves all current-company email facts, sources, timestamps, and backend business snapshots.
- L3 fact/inference references belong to current L2 inputs; missing fields and completeness reside in `detail_view`.
- Scoring features are integers 0–3 or JSON `null` only.
- Unknown signals, any `null` scoring feature, or absent latest inbound time produce a `null` Score.
- Cache hits require matching company, current revision, `input_version`, and `analysis_prompt_version`.
- Invalidated L2/L3/L4 lineage is excluded from display/cache. The same `input_version` may have independent snapshots at different revisions. Manual re-extraction retains actual prompt versions and uses internal `repair_generation` to preserve extraction history without changing Agent EmailSubmission fields.

## Browser routes

Browsers use Session, mailboxes, companies, and demo routes under `/api/v1/`. Pages view the current employee's Gmail connection/company lists/details, complete Google OAuth, request synchronization, create profiles, request analysis updates, and import examples/simulate incoming mail in `rules` mode. Browsers never receive Google credentials.

Employee Gmail routes:

- `POST mailboxes/gmail-authorize/`: generate a Google authorization URL.
- `GET mailboxes/gmail-callback/`: exchange authorization code, verify Gmail address, bind the current employee, and request initial synchronization.
- `POST mailboxes/{mailbox_id}/request-sync/`: persistently enqueue and return HTTP 202, run_id, and queued; duplicate requests reuse active batches.
- `DELETE mailboxes/{mailbox_id}/gmail-authorization/`: remove authorization while retaining historical business data.

Independent `crm_worker` processes sync batches/company jobs. See [email processing integration](processing-integration.md) for batch progress, explicit retries, review queries, and If-Match confirmation. Web starts no background threads.

Generate and validate the contract:

```powershell
python backend/manage.py spectacular --file backend/contracts/openapi.yaml --validate --fail-on-warn
```

## Incremental QQ mailbox APIs

QQ connection/removal APIs reuse existing synchronization, progress, and explicit-retry APIs. Mailbox responses add `qq_authorized`; email sources add `qq_real`. Gmail routes and `gmail_authorized` semantics remain unchanged. See [QQ mailbox integration](qq-mailbox.md#api-and-compatibility-boundaries) for request formats, authentication, and compatibility fields; machine-readable definitions remain in `../contracts/openapi.yaml`.
