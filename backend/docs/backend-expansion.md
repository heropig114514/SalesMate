# Backend business extension: schema and implementation contract

User-confirmed scope: companies/contacts, manual grouping, products, tickets, opportunities, quotes, orders, follow-ups, assistant conversations/drafts, team permissions, audits, files, tasks, and external actions. Chat generation/autonomous tool selection remain subsequent Agent integration; never fabricate model output or external success.

## Data boundaries

- crm retains private employee mailboxes, standard emails, facts, and L1–L4 versions. Scoring weights, prompts, scan limits, default database/timezone remain unchanged.
- New sales stores structured sales records/assistant actions. Company sharing grants business access only, not other employees' private Gmail, facts, or Agent context.
- Transaction entities use UUID, owner, revision, timestamps, and archival state. Writes require read-time If-Match; archival replaces irreversible deletion.
- Existing company tickets/quotes/orders JSON projects Agent contracts. Preserve existing contents; relational records manage new entries. Arbitrary JSON cannot fabricate sent quotes/historical orders.
- Quote/order lines freeze descriptions, quantities, transaction prices, and discounts. Catalog changes do not rewrite history; no cross-currency sums, conversion, or inferred tax rates.
- Append-only audits exclude credentials/file/email bodies. Messages, drafts, and files are separately stored with backend permissions.

## Entity relationships

| Entity | Relationships/responsibility |
|---|---|
| CompanySettings / CompanyAlias | Archival, manual primary contacts, explicit grouping |
| ContactProfile | Manual job title, phone, notes linked to original Contact |
| Team / Membership / CompanyGrant | Membership/business grants without mailbox sharing |
| Product | Employee catalog, explicit currency, price, recorded inventory |
| Ticket / Opportunity / FollowUp | Company tickets, stages, assignees, due follow-ups |
| Quote / QuoteLine | Headers, frozen lines, review state |
| SalesOrder / OrderLine | Headers/lines; drafts are not historical sales |
| Conversation / Message / Draft | Company conversations, immutable messages, versioned editable drafts |
| ToolAction | Tool, fixed parameters, confirmation, execution state/results |
| Connection | Encrypted connections unique by employee/provider/account |
| Attachment | Authorized metadata, content hash, private storage path |
| AuditEvent / Notification | Business audits/in-app due reminders |

## States and failure semantics

- Tickets: open → in_progress → resolved → closed, with explicit reopening.
- Opportunities: new → qualified → proposal → won/lost; changes audited.
- Quotes: draft → approved → sent → accepted/rejected; only real sending success sets sent.
- Orders: draft → confirmed → fulfilled or cancelled; confirmation freezes contents.
- Follow-ups: open → completed/cancelled; reminders deduplicate by unique keys.
- External actions: pending_confirmation → approved → running → succeeded/failed/uncertain, or cancelled. Confirmation freezes parameters; uncertain network outcomes prohibit implicit retries.
- Workers process only explicitly approved/due records and never approve actions automatically. Cancellation is guaranteed only before execution.
- Gmail sending needs gmail.send; existing read-only grants remain. Missing permissions fail explicitly. Calendars require separate authorization and report absent configuration.

## Delivery and validation

Implement models/migrations, transactions, permissions/APIs, frontend, then background actions. Tests cover isolation, transitions, conflicts, amount snapshots, idempotency, message ownership, file safety, failed/unknown outcomes. Use isolated databases/mocked services. Real sending requires confirmation of exact recipients/content; mocks do not verify external execution.

All new Python, including migrations/tests, uses Responsibility, Implementation, Relationships, Directory, Variable index, and pre-declaration Function, Inputs, Outputs, Logic, Constraints. Run existing documentation/business checks without changing standards.

## Implemented APIs

All new endpoints are under /api/v1/sales/ using original SessionAuthentication/CSRF. Management is /business/; company details retain the assistant sidebar entry.

| Path | Method/purpose |
|---|---|
| catalog/ | GET actual types, required/read-only flags, relations, transitions |
| directory/ | GET company/contact search/pages; POST manual creation |
| directory/{company_id}/contacts/ | POST create/edit contacts; company revision in If-Match |
| records/{resource}/ | GET authorized pages; POST strict creation |
| records/{resource}/{id}/ | GET record/ETag; PATCH If-Match updates |
| records/{resource}/{id}/commands/ | POST explicit archive / transition / decide / read / interrupted / verify |
| grouping/move/, grouping/merge/ | POST source/target/both versions; move additionally explicit email keys |
| files/, files/{id}/download/ | POST private multipart upload / GET authenticated download |
| oauth/ | POST authorization URL; GET callback validation |
| calendar/events/, calendar/freebusy/ | GET connection/calendar/timezone-aware range; event pagination token |
| audit/, overview/, people/ | GET audits, per-currency statistics, collaborators/exact usernames |

Resources: customers, aliases, contact-profiles, teams, memberships, grants, products, tickets, opportunities, quotes, quote-lines, orders, order-lines, follow-ups, conversations, messages, drafts, actions, files, notifications, connections.

- Lists paginate at 30 default/100 maximum, with applicable company/conversation/quote/order/team/status filters and explicit archived=true/false/all; some text models support q. Management pages use 20. Directories hide archived companies by default.
- Updates/commands require If-Match: <revision>; missing/malformed versions return 400, stale versions 409. Forms show conflicts without overwrites/retries.
- Original company profiles retain POST /api/v1/companies/{id}/register/; nonnull headcount requires provenance. This update adds transactional audits/archival checks.
- Messages accept user content only; client_key makes same-conversation identical submissions idempotent, with conflicts for changed content. Messages are immutable. Chat questions use separate sales/chat/messages/ and protected Agent reports create one assistant message; see [chat integration](chat-integration.md).
- Managers maintain ordinary members, but only team owners grant/change/archive managers. Company access requires both membership role and company grant.
- Inventory is manually recorded, never automatically decremented by orders. Prices/quantities/discounts retain Decimal; round each line net with ROUND_HALF_UP to two decimals before summing. No inferred taxes; documents with lines cannot directly change currency.
- Drafts are editable; effective transaction documents freeze by state. Only drafts, cancelled orders, and rejected quotes currently permit archival, preventing history changes through archive.
- Files are limited to 20 MiB under ignored backend/private_uploads/. Random keys remain private. Downloads use attachment/nosniff without parsing/execution.

## Workers and external services

In SalesMate/backend/ with the existing environment:

```powershell
python manage.py migrate
python manage.py sales_worker --once
python manage.py sales_worker --poll 5
```

--once actually executes approved actions; run only when intentionally consuming queues. Persistent workers handle approved tools/in-app reminders, polling every 5 seconds by default. Analysis retains existing scheduling/switches. No implicit retries for failed/uncertain/running; empty queues do not continuously emit INFO. Production process managers control lifecycle; this update installed no OS services/startup tasks.

External integration:

1. Configure a new SALESMATE_VAULT_KEY in root .env using Fernet.generate_key() and back it up; losing it makes these connections unreadable. No automatic generation/plaintext fallback.
2. Reuse Google Web OAuth clients and register the server's /api/v1/sales/oauth/ callback; locally http://127.0.0.1:8000/api/v1/sales/oauth/.
3. Authorize Gmail sending/Google Calendar separately under external connections. Gmail requests send + readonly for reconciliation; Calendar requests events + readonly. Existing read-only sync never expands automatically.
4. Create drafts/meeting plans; review account, company, recipients/attendees, body/times, and notification mode under external actions. Separate confirmation enqueues execution.
5. Reconcile unknown outcomes read-only using fixed Message-ID/event IDs. Absence does not prove no execution and never resends. Interrupted processes leave running; verify interruption before marking uncertain/reconciling.

Gmail uses MIME Base64URL raw; Calendar uses stable UUID hexadecimal event IDs. SDK calls explicitly use num_retries=0. References: [Gmail sending](https://developers.google.com/workspace/gmail/api/guides/sending), [Calendar events.insert](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert).

Validation for this historical update: 70 Django/123 existing Agent tests passed, plus schema generation/validation, migration consistency, Python documentation/diffs, added Python static checks, and JS syntax. Browser checks covered navigation, forms, customer reads, responsive layouts. Mocked authorization/sending sent no real emails/meetings. Production load, disaster recovery, and real authorization remained unverified. Code was uncommitted; commit atomicity was not claimed.
