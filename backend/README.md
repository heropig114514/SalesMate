# SalesMate software and Agent integration

The current graph uses PostgreSQL business projection, source lineage, and Qwen semantic input. See [Agent graph handoff](docs/semantic-agent-handoff.md) for HTTP/MCP integration. Legacy research interfaces and recommended models are retired; see [model retirement and recovery](docs/model-retirement.md) for retained models and recovery entry points.




Official company priority `score-v2` now includes backend scoring context, seller-data maintenance, explanation persistence, and version triggers. See [L4 backend integration](docs/l4-priority.md) for data definitions and endpoints. The agent submits `score_details` with scores when data is complete; full web explanation display still needs frontend integration.

User-collaboration backend tools provide HTTP, CLI, and stdio MCP adapters, with current counts and authorization scope returned by the dynamic catalog. See [Agent business tools](docs/agent-business-tools.md) for catalogs, permissions, confirmation, and developer integration. The current chat agent does not automatically use these tools.

Pages now form a unified sales workspace with shared navigation, home tasks, cross-page customer context, and business-form prefilling. See [unified workspace](docs/unified-workspace.md) for entry points and validation records.

Customer details and manual analysis now receive continuous read-only updates, progressively displaying saved emails, profiles, and scores while preserving reading position and unsaved content; failures explicitly pause updates. See [progressive results](docs/live-results.md) for behavior and acceptance.

This document lives in `backend/`. Unless stated otherwise, run commands from the **SalesMate repository root**. Django software, pages, contracts, and tools belong to backend/, Agent implementation to agent/, and independent test-data tools to test_tools/; shared configuration and dependency entry points remain at the root. Return to the [repository overview](../README.md).

SalesMate is an Agent MVP for B2B salespeople. It reads Gmail conversations, extracts customer intent with locatable evidence, groups by company, generates customer profiles, sales analysis, and follow-up priority, and displays results in a browser workspace.

Updated: 2026-09-19
Status: The existing L1-L4 pipeline is integrated. Backend context, persistence, and query endpoints support official score-v2, and the agent submits explanations automatically when data is complete; full frontend explanation display remains pending. Real Gmail and Alibaba Bailian require the developer's authorization and API key.

## 1. Current MVP scope

Implemented:

- Read-only Gmail OAuth and recent-email synchronization.
- L1 single-email fact extraction through Alibaba Bailian's OpenAI-compatible API.
- Email deduplication, failed-extraction replacement, and company/contact grouping.
- One-shot analysis jobs triggered by business-email changes.
- L2 company fact merging and customer/ticket/quotation/order context assembly.
- L3 customer profiles, analysis, sales signals, and scoring features.
- Reproducible L4 follow-up priority from 0 to 100.
- Django persistence, Agent service authentication, analysis caching, and job state.
- Employee Gmail inbox, Google authorization management, and customer detail pages following the MVP specification.
- Required `DATABASE_URL` selects the database explicitly. Full business operation uses PostgreSQL; SQLite is an explicit [local preview](docs/local-development.md#sqlite-local-preview) option with JSON-query, vector, and concurrency limitations.

Sales extensions include customers/contacts/grouping, products, tickets, opportunities, quotation/order lines, follow-ups, team authorization, auditing, private attachments, conversation drafts, and an independent sales worker. Gmail sending and Google Calendar adapters have explicit confirmation flows; real execution requires new write-scope authorization and an encryption key. Sourced read-only chat and explicitly imported internal knowledge are available, while WhatsApp, meeting notes, external knowledge retrieval, and industry news are excluded from this scope. Page scores represent processing priority, not deal probability.

The customer detail AI assistant provides private conversations, message history, and explicit draft saving. Workspace pages offer a bottom-right launcher that expands into a bottom horizontal chat bar and preserves drafts when collapsed; at widths up to 1000px it becomes a height-limited bottom panel with a collapse button. Saved drafts survive refreshes; unsaved text exists only on the page. Sending a question atomically saves the question and job, then independent `chat_worker` calls the agent and persists answers/citations; saving a draft makes no model call. Failures are explicit and answering again creates a new request. Apply `chat.0001_initial` and run `python backend/manage.py chat_worker` in another terminal. See [chat integration](docs/chat-integration.md) for contracts, knowledge import, permissions, and recovery. Tool plans are available from Business Management: prepare and review complete content before separate confirmation. `frontend/assets/assistant.js` manages conversations, `assistant-widget.js` mounts the shared widget, and `business.js` manages the sales workspace; existing analysis updates remain independent.

Business Management: `http://127.0.0.1:8000/business/`. See [sales extension documentation](docs/backend-expansion.md) for models, states, APIs, external authorization, and worker deployment.

The workspace inbox belongs to the current employee. The backend isolates mailboxes/emails by employee before grouping customer companies within that employee's data; it is not a shared inbox combining all employees' mail.

## 2. System architecture

```mermaid
flowchart TB
    USER[Current sales employee] --> WEB[Employee Gmail inbox]
    WEB <-->|Session JSON API| DJANGO[Django + DRF]
    WEB -->|Start Google OAuth| DJANGO
    DJANGO <-->|Authorization code and read-only credentials| GMAIL[Gmail]
    DJANGO -->|Employee synchronization request, credentials, and frozen scope| SYNC[One-shot Agent Gmail Sync]
    GMAIL -->|New message IDs and read-only emails| SYNC
    SYNC --> PARSE[Email parsing]
    PARSE --> L1[L1 single-email extraction<br/>At most four concurrent calls]
    L1 <-->|JSON Object| BAILIAN[Alibaba Bailian]
    L1 -->|Submit each EmailSubmission immediately on completion| DJANGO

    DJANGO -->|Job + CompanyContext| ORCH[One-shot Agent job orchestration]
    ORCH --> L2[L2 company fact merging]
    L2 --> L3[L3 customer profiles and analysis]
    L3 <-->|JSON Object| BAILIAN
    L3 --> L4[L4 follow-up priority]
    L2 -->|AnalysisInput| DJANGO
    L3 -->|Analysis| DJANGO
    L4 -->|Score| DJANGO
    DJANGO -->|Company list and details| WEB
```

Module responsibilities:

| Module | Technology | Responsibility |
|---|---|---|
| `backend/frontend/` | Native HTML, CSS, JavaScript | Current employee Gmail authorization, synchronization state, company lists, original emails, profiles, analysis, business records, and follow-up scores |
| `backend/` | Django, DRF | Employee sessions, Google OAuth, credentials, emails, companies, contacts, business context, jobs, and analysis persistence |
| `agent/` | Python, Gmail API, Bailian | Employee mailbox synchronization claims, Gmail reads, L1-L4, backend HTTP client, and one-shot orchestration |
| `test_tools/` | Python, Gmail API | Independent injection of end-to-end synthetic emails into developers'/testers' own Gmail |
| `backend/contracts/` | OpenAPI YAML | Current HTTP interface structure |
| `backend/tools/` | Python/Node scripts | Documentation consistency and optional browser checks |

The agent does not access the database directly and the backend does not perform real model inference. They communicate through JSON APIs under `/api/v1/agent/`. Browsers receive authorization redirects and mailbox synchronization state, never Gmail tokens, Bailian keys, or Agent service tokens. In the local MVP, Django stores Google authorization and the agent claims it through authenticated service endpoints during synchronization.

## 3. End-to-end flow and exchanged data

| Stage | Input | Output | Storage |
|---|---|---|---|
| Employee Gmail authorization | Current employee session, Google OAuth code | Mailbox address, authorization state, `sync_requested` | Django `GmailCredential` and `Mailbox.sync_state` |
| Synchronization claim | Agent service credential, claim limit | `mailbox_id`, address, Google authorization, read limit | State becomes `sync_running` |
| Gmail reads | Claimed employee authorization and frozen day/count scope | Newest scoped emails, excluding synchronized IDs before body reads | Durable worker raw/L1 cache; scope stored in MailboxSyncRun |
| Email parsing | Raw MIME, message/thread IDs | Sender, recipients, subject, body, time, direction | Agent memory |
| L1 extraction | Current email subject and body | `EmailSubmission` | At most four concurrent calls; persist each result immediately to Django `Email` and `Extraction` |
| Backend grouping | Contact email and self-reported company | `company_id`, contacts, member email keys | Django `Company` and `Contact` |
| Job enqueueing | Business email with `has_substantive_update=true` | `email_ingested` job | Django `Job` |
| L2 merging | Grouping, emails, customer, tickets, quotations, orders | `AnalysisInput` | Django `AnalysisInput` |
| L3 analysis | Complete `AnalysisInput` | `Analysis` | Django `Analysis` |
| L4 scoring | Company email signals and backend `priority_context` | `Score`, optionally `score_details` | Django `Score.payload` and `Score.value` |
| Page reads | Current employee session, company list or ID | That employee's mailbox state, companies, details, emails, profiles, scores, and job state | Browser display |

Repeated synchronization rules:

- `dedupe_key` is `mailbox_address:gmail_message_id`.
- The same email with the same extraction returns `duplicate`.
- A previously failed extraction returns `updated` and replaces facts when a later synchronization succeeds.
- Non-business and review-pending emails are saved without automatic analysis jobs; companies containing only such emails are excluded from default lists/statistics.
- Business emails without substantive changes are saved without rerunning company analysis automatically.
- One extraction/submission failure does not roll back other emails; failed IDs remain for subsequent retries.
- Corporate mail groups by domain; common public mail groups by complete contact address.

## 4. Agent data structures

### 4.1 L1：EmailSubmission

```json
{
  "dedupe_key": "sales@example.com:gmail-message-id",
  "mailbox_address": "sales@example.com",
  "gmail_message_id": "gmail-message-id",
  "thread_id": "thread-id",
  "from": "buyer@example.com",
  "to": ["sales@example.com"],
  "cc": [],
  "sent_at": "2026-09-12T10:00:00+08:00",
  "received_at": "2026-09-12T02:00:00+00:00",
  "subject": "采购咨询",
  "body_text": "需要 50 台检测设备，请提供报价。",
  "direction": "inbound",
  "contact_email": "buyer@example.com",
  "non_business_hint": false,
  "non_business_reason": null,
  "extract_status": "completed",
  "extract_prompt_version": "extract-v7",
  "extract_error": null,
  "facts": {}
}
```

`facts` includes control fields:

- `has_substantive_update`
- `message_summary`
- `intent_hint`
- `intent_evidences`

In `extract-v7`, `intent_hint` is the highest verifiable purchasing stage in one customer email: `L1 Exploring`, `L2 Interested`, `L3 Qualified`, `L4 Evaluating`, `L5 Negotiating`, or `L6 Purchase Ready`, otherwise `null`. A stage requires `intent_evidences`; without one the array is empty. Submission accepts only `extract-v7`; clear legacy emails and durable cursors before deployment and resynchronize afterward.

The other 13 fact fields are `contact_name`, `contact_title`, `company_self_reported`, `business_background`, `employee_scale_hint`, `product_need`, `quantity`, `budget`, `delivery_time`, `decision_process`, `concerns`, `quote_reference`, and `order_reference`.

Each fact field is a multi-value array, with multiple original evidence snippets per value:

```json
[
  {
    "value": "50 台",
    "evidences": ["需要 50 台检测设备", "首批数量为 50 台"]
  }
]
```

Unknown facts use empty arrays. Every evidence snippet must be locatable verbatim in the current email subject or eligible body.

### 4.2 L2：AnalysisInput

L2 makes no model calls. It preserves all fact history, adds source emails/timestamps, and carries backend business context.

```json
{
  "company_id": "backend-company-uuid",
  "input_version": "sha256:...",
  "merge_version": "merge-v2",
  "external_snapshot_version": "ext-0",
  "built_at": "2026-09-12T10:01:00+08:00",
  "company": {
    "company_name": "Example",
    "crm_status": "unregistered",
    "domains": ["example.com"],
    "contacts": []
  },
  "business_context": {
    "customer": {},
    "tickets": [],
    "quotes": [],
    "orders": []
  },
  "latest_message_summary": "客户要求正式报价",
  "member_dedupe_keys": [],
  "unparsed_message_count": 0,
  "facts": {},
  "metrics": {}
}
```

`metrics` includes incoming/outgoing counts, substantive inbound count, first-contact time, latest incoming/outgoing times, latest same-thread response interval, historical-order presence, and CRM state. `input_version` supports analysis caching and changes with extraction state, merge version, or backend business snapshots.

### 4.3 L3：Analysis

One Bailian call produces:

- Page A: Main company signal, per-ticket signals, industry, size, summary, and three scoring features.
- Page B: Industry context, company operations, intent, timeline, opportunity, risk, and guidance.
- Fact changes, source conflicts, missing fields, and context completeness.

Main signal enum: `repeat_purchase`, `quoted_not_closed`, `inquiry_intent`, `new_lead_no_profile`, `unknown`.

The three scoring features are `demand_clarity`, `urgency`, and `decision_visibility`, restricted to 0-3 or JSON `null`. All seven detail dimensions use this structure:

```json
{
  "facts": [{"text": "客户要求正式报价", "source_refs": ["email-dedupe-key"]}],
  "inferences": [{"text": "...", "basis": "...", "confidence": "high", "source_refs": ["source-id"]}],
  "missing_fields": []
}
```

Allowed references include email `dedupe_key`, `company_id`, `customer_id`, contact email, `ticket_id`, `quote_id`, and `order_id`. Invalid JSON/enums, nonexistent sources, missing inference rationale, or deal percentages fail L3 and prevent backend analysis/score persistence.

### 4.4 L4：Score

| Feature | Weight |
|---|---:|
| urgency | 0.35 |
| buying_intent | 0.35 |
| opportunity_value | 0.30 |

The official `score-v2` is computed by Agent rules; opportunity value comprises 60% amount band and 40% customer fit. The backend checks that integer contributions sum to the score and accepts only `score=null` with `insufficient_data` when evidence is insufficient; empty legacy L3 features no longer reject official scores. With `ANALYSIS_PROVIDER=agent`, page projections show only the official version; historical data remains, but legacy scores do not participate in formal ranking. Rule-demo mode explicitly uses separate `rules-score-v1`.

## 5. Directory layout

```text
SalesMate/
├── README.md                     # Repository overview
├── .env.example                  # Shared configuration template
├── requirements.txt              # Unified backend/Agent installation entry point
├── test_tools/                   # Independent end-to-end test-data tools and instructions
├── backend/                      # Software application
│   ├── README.md                 # This software development/integration guide
│   ├── apps/, config/, common/    # Django APIs, models, and shared modules
│   ├── frontend/                 # HTML and static assets
│   ├── contracts/                # OpenAPI and response examples
│   ├── docs/                     # Software documentation and integration guides
│   ├── tools/                    # Comment and browser checks
│   ├── tests/, requirements/      # Software tests and dependencies
│   └── manage.py
└── agent/
    ├── README.md、main.py、config.py
    ├── clients/                  # Django HTTP client
    ├── tools/, llm/               # Gmail reads and Bailian calls
    ├── workflows/                # L1-L4 and orchestration
    └── tests/
```

The agent has no separate `schemas`, `prompts`, or runtime fake-backend layer. Model capabilities live in `agent/skills/*/SKILL.md`; workflows load skills, assemble input, invoke models, and validate output. `agent/tests/fake_backend.py` serves only offline tests.

## 6. Shared environment configuration

The project reads only the root `.env`; `agent/.env` and `backend/.env` are retired.

Initial configuration:

```powershell
Copy-Item .env.example .env
```

Edit `.env`:

```dotenv
DJANGO_SECRET_KEY=local-random-secret
DJANGO_TIME_ZONE=UTC
ANALYSIS_PROVIDER=rules
LOCAL_DEBUG_AUTO_LOGIN=True
LOCAL_DEBUG_USER=demo

GOOGLE_OAUTH_CLIENT_ID=your-web-client-id
GOOGLE_OAUTH_CLIENT_SECRET=your-web-client-secret
GOOGLE_OAUTH_REDIRECT_URI=http://127.0.0.1:8000/api/v1/mailboxes/gmail-callback/

DASHSCOPE_API_KEY=your-bailian-key
BAILIAN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
BAILIAN_MODEL=your-model-name
BAILIAN_ENABLE_THINKING=false

SALESMATE_BACKEND_AGENT_URL=http://127.0.0.1:8000/api/v1/agent/
SALESMATE_AGENT_SERVICE_TOKEN=
SALESMATE_MAILBOX_ID=
SALESMATE_JOB_LEASE_SECONDS=120
SALESMATE_BACKEND_TIMEOUT=30
```

`DATABASE_URL` is required; missing or invalid configuration fails directly. This machine uses the existing PostgreSQL database, for example:

```dotenv
DATABASE_URL=postgresql://salesmate:password@127.0.0.1:5432/salesmate?connect_timeout=3
```

Git ignores `.env`, OAuth credentials/tokens under Agent and `test_tools/`, `backend/.local-access.json`, and database files.

## 7. Initial installation and setup

See the [repository README](../README.md#one-command-local-startup) for one-command startup: `start-local.ps1` on Windows and `bash start-local.sh` on macOS prepare the virtual environment/dependencies, check/migrate the local database, start web/workers, and open the frontend. First install Python and configure root `.env`. Full operation requires PostgreSQL/pgvector; lightweight [SQLite preview with web registration](docs/local-development.md#sqlite-local-preview) needs no database service or precreated account. Scripts neither overwrite configuration nor replace databases. The following commands describe manual Windows setup; see [local development](docs/local-development.md#one-command-startup-macos) for macOS paths and steps.

Run these commands from the project root:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python backend/manage.py migrate
python backend/manage.py provision_local --username demo --mailbox-address your-account@gmail.com
```

`provision_local` creates a regular user, business mailbox, and Agent service credential, writes the token/mailbox UUID/username into root `.env`, and stores local credentials including the password in ignored `backend/.local-access.json`. It is only for initial setup and rejects existing users or credential files.

Open the entire `SalesMate` root in PyCharm, then select `SalesMate\.venv\Scripts\python.exe` under **Settings → Project → Python Interpreter**. Set every Run Configuration's working directory to the repository root so `agent.*`, Django settings, and root `.env` resolve consistently.

If root `.env`, a configured database, and `backend/.local-access.json` already exist, do not rerun `Copy-Item` or `provision_local`. Activate the existing interpreter and run only:

```powershell
python backend/manage.py migrate
python backend/manage.py check
```

Employee web Gmail authorization uses a Google Cloud **Web application** OAuth client. Initial preparation:

1. Enable Gmail API in Google Cloud Console.
2. Configure the OAuth consent screen and add each employee Gmail account to Test users during testing; otherwise Google returns `403 access_denied`.
3. Create a **Web application** OAuth client.
4. Add exactly `http://127.0.0.1:8000/api/v1/mailboxes/gmail-callback/` to Authorized redirect URIs.
5. Set `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, and the matching `GOOGLE_OAUTH_REDIRECT_URI` in root `.env`.
6. Use `ANALYSIS_PROVIDER=agent` for live integration and run `python backend/manage.py crm_worker` in a separate terminal after starting the HTTP backend. Web requests only enqueue durable jobs.
7. Set `DASHSCOPE_API_KEY` and Bailian model name `BAILIAN_MODEL` in root `.env`.

Employee web authorization reads no credential file from the Agent directory and requires no manual `SALESMATE_MAILBOX_ID`. Web OAuth uses the root `.env` Web application client; `--sync-authorized-mailboxes-once` remains a debugging entry point with automatic execution disabled. Independent `test_tools/`, alongside `agent/` and `backend/`, supplies an email injector with its own `gmail_inject_credentials.json` and `gmail_inject_token.json`; see [test tools](../test_tools/README.md).

## 8. Startup and complete live testing

### Step 1: Start Django and the frontend

When using one-command startup, do not repeat manual web/worker commands here. See [local development and integration](docs/local-development.md#one-command-startup-windows) for status, shutdown, log paths, and prerequisites on a new machine.

Open the first PyCharm terminal and run from the repository root:

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn --app-dir backend config.asgi:application --host 127.0.0.1 --port 8000 --reload
```

After `Uvicorn running on http://127.0.0.1:8000` appears, open:

- Workspace: http://127.0.0.1:8000/
- API documentation: http://127.0.0.1:8000/api/docs/
- Liveness: http://127.0.0.1:8000/api/v1/health/live/
- Database readiness: http://127.0.0.1:8000/api/v1/health/ready/

`LOCAL_DEBUG_AUTO_LOGIN=True` opens the workspace as the configured local user in `.env`. Set it to `False` and restart to test login.

This starts both frontend and backend. Do not open `backend/frontend/index.html` directly: pages require same-origin sessions, CSRF, and `/api/v1/`, so use `http://127.0.0.1:8000/`. Continue only when all four URLs are accessible and readiness returns `{"status":"ok","database":"ok"}`.

### Step 2: Authorize employee Gmail in the web UI

1. Click Gmail in the workspace sidebar or the Connect Gmail action at the top right.
2. Choose Google account authorization in the dialog.
3. Select the current employee's Google account and grant read-only access.
4. After returning to the workspace, select recent days or recent message count; at least one is required and both form an intersection. The default maximum is 50, including day-only requests. Requests above 50 warn of prolonged process use and are durably queued only after explicit approval; canceling starts no synchronization. Start the independent worker in the next step to process synchronization/analysis.
5. The page reads batch progress continuously and refreshes companies progressively. Later synchronization/refresh still requires scope selection; Gmail selects the newest N messages before skipping synchronized IDs, without filling from older emails or automatic full backfill. Failures require explicit retries.

Each SalesMate login has independent mailbox connections, companies, and email scope. Two employees contacting the same customer domain still cannot see each other's emails in this MVP.

### Step 3: Start the independent worker

Run in a second repository-root terminal using Python with project dependencies installed:

```powershell
python backend/manage.py crm_worker --analysis-workers 2 --poll 1
```

Authorization callbacks and Gmail synchronization actions create database batches only; web execution does not start the agent. The worker processes the employee owning the current service credential, with company profiling running alongside mailbox synchronization:

```text
Read recent incoming/outgoing mail initially, then new emails through the Gmail History cursor
→ Run single-email L1 Bailian extraction with at most four concurrent calls
→ Submit each completed L1 result to Django immediately
→ Claim jobs produced by this pass
→ Merge L2
→ Run L3 Bailian analysis
→ Compute L4 scores
→ Persist results and report jobs
```

The page displays completed emails, failures, and company profiles separately; completed email processing does not imply completed profiles. Failed batches show safe errors and a retry action. Inspect the batch ID in worker logs before explicitly retrying. See [processing integration](docs/processing-integration.md) for migrations, historical classification, and recovery boundaries.

For stepwise debugging, stop the independent worker before using the retained CLI entry point; never run both consumers on one mailbox:

```powershell
python -m agent.main --sync-authorized-mailboxes-once
```

Check these fields:

- `created_count`: Emails saved for the first time.
- `updated_count`: Previously failed extractions successfully replaced in this pass.
- `duplicate_count`: Existing emails with identical content.
- `failed_extraction_count`: Emails for which this pass's L1 could not produce trusted facts.
- `failed_submission_count`: Individual backend submission failures in this pass.
- `failed_email_count` and `email_errors`: Number of retry message IDs and individual failure stages.
- `job_reports[].status`: Expected to be `completed`; the array may be empty without new substantive business emails.

### Step 4: Verify frontend results

The workspace refreshes after synchronization; the synchronization-and-refresh action is also available:

1. Grouped companies should appear in the current employee's Gmail inbox.
2. Lists show summaries, sales signals, industry, size, score, and Agent origin.
3. Open company details and verify email subjects/bodies on the left.
4. Evidence controls beside profiles/analysis should locate source emails; business-record sources display their IDs.
5. The middle region shows three profile and four analysis dimensions.
6. The right region shows follow-up priority, contribution reasons, missing information, contacts, and business-record counts.
7. Insufficient information must display an unscored state, never an invented zero score.

### Step 5: Verify deduplication and updates

Synchronize again through the Gmail management dialog or inbox synchronization-and-refresh action. With unchanged mailbox content, saved emails should contribute to `duplicate_count` without increasing company email counts.

Then send the employee a new email with explicit requirements, quantity, budget, or meeting intent and synchronize again. Email counts, summary, profiles, analysis, and job state should update after processing.

The analysis-update action creates a backend job and starts the agent automatically; after processing, use the detail page's status-refresh action to inspect results.

### Troubleshooting

| Symptom | First check | Resolution |
|---|---|---|
| Google returns `403 access_denied` | OAuth consent-screen publication state and Test users | Add the employee Gmail to Test users during testing and authorize again from the page |
| Authorization fails after returning to the page | OAuth client type and redirect URI | Use Web application and ensure Google Cloud/root `.env` both specify `http://127.0.0.1:8000/api/v1/mailboxes/gmail-callback/` |
| Page waits indefinitely for Agent synchronization | Missing worker or invalid configuration | Start `crm_worker`; inspect Agent token, HTTP URL, Bailian settings, worker error types, and batch progress |
| CLI returns `configuration_failed` | Root `.env` service token and mailbox UUID | Run `provision_local` first; ensure obsolete `agent/.env`/`backend/.env` files are not being maintained |
| CLI receives backend 401 | Token differs from the database record | Do not copy stale tokens; initialize a fresh local database/credential set or verify current root `.env` |
| CLI receives backend 404 | `SALESMATE_MAILBOX_ID` belongs to another token user | Use the token and mailbox ID from the same `provision_local` run |
| Empty `job_reports` | Duplicate, non-business, or nonsubstantive email | Check created/duplicate counts and `has_substantive_update`; alternatively request an analysis update and run `--process-jobs-once` |
| L3 failure | Non-JSON Bailian output, invalid enums, or out-of-scope `source_refs` | Inspect detailed terminal errors; correct prompt/model, create a new analysis job, and process it once |
| No new page results | Worker absent, still processing, or failed | Check batch state and worker logs; reserve legacy CLI for isolated debugging, never the same mailbox as the worker |
| Readiness returns 503 | Database connection failure | For SQLite, check `backend/` is writable; for PostgreSQL, check `DATABASE_URL` |

### Step 6: Debug stages independently

```powershell
# Read backend company context and build only L2
python -m agent.main --analysis-company-id COMPANY_UUID

# Process only L2-L4 for existing jobs
python -m agent.main --process-jobs-once --job-limit 10
```

## 9. Automated checks

Run from the repository root:

```powershell
# Agent offline tests: documented baseline of 120 tests
python -m unittest discover -s agent/tests -p "test_*.py"

# Django business regression tests
python backend/manage.py test tests

python backend/manage.py check
python backend/manage.py makemigrations --check --dry-run
python backend/manage.py spectacular --file backend/contracts/openapi.yaml --validate --fail-on-warn
python backend/tools/check_docs.py
```

With Node.js installed, also run:

```powershell
node --check backend/frontend/assets/api.js
node --check backend/frontend/assets/app.js
```

Automated tests do not access real Gmail or Bailian. Verify actual permissions, credit balance, network access, and model quality through the manual flow in Section 8.

## 10. Offline frontend demo

Without Gmail or Bailian configuration, temporarily set `.env` to:

```dotenv
ANALYSIS_PROVIDER=rules
```

After restarting, the workspace offers demo-sample import and simulated new email. Rule mode is only for UI/database integration and marks output as placeholders. Restore `ANALYSIS_PROVIDER=agent` and restart before testing the real agent.

## 11. Development conventions

- Agent workflows use plain dictionaries and a few local dataclasses, without separate schemas/prompts directories; model instructions and versions live in project skills.
- The backend owns persistence/page queries; the agent owns email understanding and L1-L4 business computation.
- The frontend reads results through Django and stores no service secrets.
- When changing Python under `backend/`, including tools/, update file responsibilities, declaration directories, variable indexes, and key function comments together.
- Run `python backend/tools/check_docs.py` before completion.

See [agent/README.md](../agent/README.md) for prompts and validation rules, and [contracts/openapi.yaml](contracts/openapi.yaml) for current HTTP definitions.

## 12. Known limitations

- Initial scans cover at most the latest 20 emails; subsequent History-cursor passes retain the per-pass limit of 20 and persist remaining IDs.
- L1 runs at most four concurrent calls and saves results in completion order, while Gmail raw bodies are still read sequentially.
- Shared `crm_worker` rotates database jobs across active employees with isolated HTTP identities per unit; web restarts preserve queues. Interruptions appear as batch errors or expired leases and require explicit employee retries.
- Company profiling defaults to two concurrent jobs with per-company exclusion. L1 redo versions/classification after manually confirmed skipped emails still need coordinated completion; see [processing integration](docs/processing-integration.md).
- Existing Gmail read-only synchronization retains GmailCredential JSON. New sending/calendar Connection credentials use an independent Fernet key; browsers receive neither plaintext nor ciphertext. Migrating existing read-only credentials and production key management are outside this change.
- Public email uses the original domain list for automatic grouping. Explicit company merges, selected-email moves, and manual domain/contact mappings are supported without guessing corporate-group relationships.
- Tickets, opportunities, products, quotations/lines, orders/lines, and follow-ups have relational records and management entry points. Inventory is manual, without automatic deductions or inferred taxes/revenue recognition.
- L3 does not use external news or knowledge bases.
- L4 weights are not calibrated with real sales outcomes.
- DATABASE_URL explicitly selects the database. PostgreSQL has been validated locally; SQLite is never a failure fallback.

## Required coding-agent development principles


Every coding agent adding, modifying, refactoring, or deleting code in this software directory, including frontend, tests, and tools, must follow these requirements:

1. **Rigorous implementation comments:** Document functions, methods, and key blocks accurately and verifiably, covering purpose, inputs/outputs, logic, design rationale, and constraints. Explain state transitions, boundaries, exceptions, and side effects where relevant. Explain reasons and relationships rather than merely restating code; invent neither academic references nor unverified conclusions.
2. **File-level description and directory:** Begin every code file with its responsibility, main implementation logic, and module relationships. List functions, classes, and key methods actually implemented, plus important variables, constants, and configuration names/purposes for navigation. Keep the directory consistent with current code and remove deleted/renamed entries.
3. **Atomic code/documentation changes:** Update, check, and deliver implementation, comments, and file directories as one logical unit; when committing, include them in the same commit. Revise affected documentation with changes to signatures, behavior, data flow, key variables, or module responsibility. Remove obsolete comments/index entries with deleted or replaced implementations; never defer documentation until after code delivery.
4. **Consistency checks before delivery:** Review every affected comment/index against actual behavior and run `python tools/check_docs.py` from `SalesMate/backend/` to check declaration comments, directories, and module variable indexes. Changes to the checker also require `python tools/test_check_docs.py`. Automated checks do not replace semantic review of responsibilities, logic, state, and code consistency, or prove Git commit atomicity.

The checker is included: run the commands above from the software root. By default it scans every `.py` file here, including tools, tests, migrations, and package initializers. Failures return nonzero with file, line, and specific issue. See [comment and consistency guidelines](docs/coding-agent-guidelines.md) for format, index scope, checker capabilities, and manual review requirements.

For subsequent changes, `python tools/check_doc_changes.py` compares HEAD with working-tree implementation/documentation; use `--staged` before committing to inspect actual staged content. Structural errors block progress, while implementation changes without corresponding documentation are flagged for review; `--fail-on-review` explicitly enables strict review. Regression tests are in `python tools/test_check_doc_changes.py`. The repository includes pre-commit configuration and GitHub Actions workflows; see the guidelines for local installation, required remote checks, and capability limits. Configuration alone does not mean every clone installed hooks or branch protection is enabled.

## Updating a legacy workspace

When upgrading, pull code, install root requirements.txt, and run `python backend/manage.py migrate`. Move legacy backend/.env configuration into root .env and express the original PostgreSQL connection equivalently in DATABASE_URL, including its connection timeout. Preserve keys, timezone, runtime mode, and development account without reinitializing databases/users. Rule mode and automatic login remain available.

Data migration 0004 appends versions to convert legacy rule facts, retaining original emails/extractions and expiring old analysis. Opening a company or explicitly requesting analysis generates new results. See [data model](docs/data-model.md).


## Durable synchronization and manual review

Synchronization batches, per-email progress, independent workers, per-company exclusion, hidden non-business defaults, and manual review are integrated. See [processing integration](docs/processing-integration.md) for startup, upgrades, APIs, and follow-up rules. Preview historical classification with `python backend/manage.py classify_emails`, then apply with `--apply`; original emails are not deleted.

## extract-v7 backend integration

See [backend v7 integration](docs/backend-v7-adaptation.md) for historical-fact preview/explicit upgrade, durable repair state, direction validation, and provisional L4 score persistence. Upgrades never run automatically or refetch the mailbox.

See [deployment documentation](docs/semantic-graph-deployment.md) for semantic graph HTTP, MCP, and server usage.
