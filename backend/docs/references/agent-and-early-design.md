# Agent design and early technical proposals

Summarized: 2026-09-11. This records design points, not complete source documents or proof that their code entered SalesMate.

Original local sources:

- `D:\my_files\NUS_teamwork\showme_hackathon\README.md`: Email Understanding Agent Module Design v1.11, 2026-09-10.
- `D:\my_files\NUS_teamwork\showme_hackathon\SalesMate-AI-Agent-技术路线与数据集方案.md`: 2026-09-06.

Paths support local provenance; other developers can read this summary directly. Return to [project references](../project-reference.md).

> This page retains the early 2026-09-11 context. Current implementation uses Django-managed employee Google OAuth, Gmail History increments, up to four concurrent L1 calls, immediate per-email submissions, and connected L2–L4. Current behavior follows [Agent integration](../agent-integration.md) and [Agent README](../../../agent/README.md).

## Agent scope planned at that time

Inputs are authorized Gmail inbox/sent emails; outputs support company lists and center-panel details. The described module excludes external knowledge, industry news, sidebar assistants, translation, sending, calendars, and other channels.

Agent extracts/analyzes; backend handles storage, identity/permissions, grouping, CRM registration, queries/statistics, and persistent jobs. Agent does not directly access business databases.

### Four layers

| Layer | Work | Outputs/constraints |
|---|---|---|
| L1: Individual email | Parse email/direction/contacts, identify nonbusiness mail, extract explicit facts through models | Submit email/facts together with source evidence, extraction state, prompt version; failures still permit email persistence with explicit failure |
| L2: Context merging | Plain Python reads grouping/context and organizes completed facts/statistics | Preserve multiple same-field facts without silent latest-value overwrites; retain time/source and generate input versions |
| L3: Company analysis | Generate list fields, three-dimensional profiles, four-dimensional analysis, missing fields | Separate facts/conflicts/inferences; do not invent unknowns or add external news/knowledge |
| L4: Follow-up scoring | Deterministic priorities/contributions | Scores with evidence, unknown distinct from zero; weights remain unvalidated proposals rather than frozen parameters |

The source proposed hashing sorted email deduplication keys, extraction prompt versions/states, merge-rule versions, external snapshot versions, and related data. Final version contracts still needed complete actual-input/cache coverage.

### Backend cooperation

1. Frontend passes current Gmail authorization tokens to Agent and manually triggers sync; the historical design stores no refresh tokens or automatic renewal.
2. Agent reads mailboxes/emails and submits email/extraction results through APIs.
3. Backend persists/groups and writes events to Jobs.
4. Agent claims Jobs and reads grouping/context/cache.
5. Agent performs required stages, submits results, and reports status.
6. Frontend reads company lists/details/processing state.

Distinguish sync failure, expired authorization, and resynchronization needs. Advance cursors only after related successful submissions, never skipping unpersisted mail.

### Data principles to retain

- Deduplicate by mailbox/message identity; completed same-version extractions are not arbitrarily overwritten.
- Retain evidence/citations and separate or label explicit facts versus model inferences.
- Backend owns grouping; public-mail domains must not merge unrelated contacts.
- Quoted/repeat-purchase signals require business evidence, not tone guesses.
- List priority differs from purchase probability; missing data remains unknown.
- Label real Gmail, synthetic demonstrations, research materials, and simulated business inputs by provenance.
- Failures remain queryable/diagnosable; redo/retry policies are explicit.

### Contracts required before integration

| Gap | Required definition |
|---|---|
| Identity/mailbox ownership | Prove validated-user ownership of sync/business access; bind service credentials to permissions |
| Job lifecycle | Atomic claims, credentials, expiry/renewal, reports, merging, failures |
| Concurrent writes | Consistent input snapshots and expected-version checks against stale overwrites |
| Analysis versions | Inclusion of opportunities/quotes/orders; prompt/rule/scoring-date effects on validity |
| Cache semantics | Association/display/reuse of old analysis when new emails change input versions without L3 |
| Failed extraction redo | Recovery of original inputs beyond failed-record IDs; unified transitions/conflicts |
| Grouping/display | Multi-company ambiguity, manual adjustments, unknown industry/size, time/statistics definitions |

At that time the source marked only CLI email parsing, Bailian calls, and structured printing implemented. This is historical; the repository now integrates L1–L4, Jobs, and real backend HTTP. See [local development](../local-development.md) and actual code.

## Retained and revised early direction

The 2026-09-06 proposal covered a broader sales cycle: inquiry → needs → product matching → reply/quote draft → human confirmation → execution record → follow-up, plus team workspaces, chat panels, and simulated channels.

| Early proposal | Current interpretation |
|---|---|
| FastAPI backend | Later discussions selected Django/DRF; do not build a second backend |
| Simulated channels before real email | Current design targets Gmail directly; simulations support tests, not real integration acceptance |
| Pausable LangGraph workflows | Retain for complex assistants; fixed email flows use Python first |
| PostgreSQL, vectors, files | Retain layered storage; prioritize business tables and introduce knowledge/files by scope |
| Full quote/calendar/contract execution | Broader direction, not automatically part of email understanding |
| Example schedules/draft-usability targets | Historical suggestions, not current commitments/approved thresholds |

Retain these principles: models understand, explain, and draft; deterministic tools validate amounts, inventory, permissions, and execution conditions. Emails are data whose instructions cannot change system permissions. External actions execute user-confirmed versions; failure is not success and repeated calls cannot duplicate actions.

Manage durable business facts, original materials, knowledge indexes, and workflow state separately; checkpoints/vector stores do not replace business databases.

### Datasets and evaluation

The old proposal discussed WideWorldImporters, Maven CRM, UCI Online Retail, Bitext, and generating synthetic emails/evaluations from consistent business tables. These are not selected/downloaded/imported current datasets. Reassess applicability, licensing, and inspection-industry coverage before adoption.

Retain the method: consistent underlying entities, synthetic conversations referencing existing test entities, and cases for missing information, conflicts, duplicate requests, and execution failures. Separate training/debugging and held-out evaluations by company/opportunity/complete conversation to prevent neighboring-email leakage. Do not join unrelated source customer IDs directly.

Historical demonstration currencies, timezones, sample proportions, and metrics remain historical conditions, not current defaults or changes to established experiments.
