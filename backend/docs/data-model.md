# Current data model

Updated: 2026-09-13. Required `DATABASE_URL` explicitly selects the database. This update retains local PostgreSQL without changing database, timezone, or analysis settings. accounts, crm, and sales jointly maintain the schema through migrations.

## Tables

| Model | Responsibility |
|---|---|
| accounts.User | Project user model; ordinary users have no administrator permissions |
| Mailbox | Employee business mailbox, sync request state, and sync version |
| GmailCredential | One-to-one Google authorization JSON for Mailbox; backend OAuth and Agent synchronization only |
| AgentCredential | Single-user service credential SHA-256 digest; no plaintext token |
| Company | User ownership, grouping key, company name/domain, CRM state, business snapshots, revision, external_version |
| Contact | Company contact email and nullable name; interaction counts derive from emails |
| Email | Standard email JSON, mailbox/company foreign keys, optional contact, direction/time; dedupe_key primary key |
| Extraction | Email prompt version, state, facts, errors, and creation time |
| AnalysisInput | Agent input version, original L2 JSON, and backend revision |
| Analysis | Input snapshot, prompt version, original L3 JSON, rules/agent source |
| Score | Specific parent analysis, original L4 JSON, rule version, nullable score, and creation time |
| Job | Company, event, revision, state, attempt, lease, claim credentials, and final report |

Relations: User → Mailbox/Company; Mailbox → GmailCredential/Email; Company → Contact/Email/Job/AnalysisInput; Email → Extraction; AnalysisInput → Analysis → Score. Account, mailbox, and company queries are owner-isolated, so pages represent the current employee's inbox rather than a company-wide shared inbox.

## Internal structure changes in this update

Initial business models reside in `apps/crm`, with ingestion.py, jobs.py, results.py, selectors.py, and rules.py separating transactional writes, claims, result validation, queries, and placeholder rules. The original multi-app layout was a plan; no empty modules were created. Later internal separation does not affect HTTP contracts.

Relational fields implement authorization, querying, and uniqueness, while JSON stores protocol payloads. Email bodies and extractions use separate tables, as do L2/L3/L4. Backend revision and Agent input_version are independent: the former blocks stale overwrites, while the latter preserves Agent-defined cache identity.

Company.customer retains confirmed CRM profiles; tickets/quotes/orders project the original Agent contract. New `sales` relational tables maintain documents/lines, replacing only projection entries with `source=sales_record` and preserving historical JSON. Quotes enter projections only with actual send evidence; orders become historical orders only after confirmation. Transactions increment external_version/revision and enqueue together. See [sales schema and APIs](backend-expansion.md) for full relations/state contracts.

## Constraints

- owner/group_key is unique. Automatic grouping requires identical complete corporate domains; listed public-mail providers group by complete contact email. The finite provider list does not claim universal coverage.
- Customer-reported company names are display-only and do not drive automatic grouping. New emails prefer manual CompanyAlias mappings (contact before domain), otherwise using original grouping rules. Owners may explicitly move selected emails, merge companies, or configure multidomain mappings.
- Same-domain correspondence across one user's business mailboxes joins one company; the same domain across different users remains isolated.
- Mailbox addresses, company contacts, extraction versions, input versions, and analysis prompt versions have corresponding composite uniqueness constraints.
- Email/fact/CRM changes increment revision; CRM changes also increment external_version. Running jobs retain their own input revision.
- Completed extractions remain unchanged; failed ones can succeed during later ordinary synchronization or compatibility resubmission. Complete fact history remains without overwriting old budgets.
- Unknown facts, headcount, timestamps, and scores remain null/unknown rather than inferred or zero-filled.

`Email` business classification/manual-review fields are separate from original payloads. Classification follows `Extraction` skip states and intent signals, with manual decisions taking precedence. `Email.company` remains required; default lists/statistics retain only companies with business emails. `MailboxSyncRun` stores batches/leases, `EmailProcessingJob` stores per-email stages, and progress derives from task tables. See [email processing integration](processing-integration.md) for migration/rule boundaries.

## Validation boundaries

This update verifies relational records, states, isolation, monetary snapshots, grouping, drafts, attachments, CSRF, action confirmation, and failure semantics in an isolated PostgreSQL test database. Google SDK boundaries are mocked and do not prove real account authorization/external execution. Production deployment, multiprocess load tests, real process-crash exercises, and pgvector retrieval have not been performed.

## Legacy rule-fact upgrade

Migration 0004 handles only successful rules-extract-v1 records still serving as current extractions: convert single value/evidence fields to multivalue arrays, intent_evidence to intent_evidences, and append rules-extract-v1+multivalue-v1. No model calls or changes to original emails/extractions/historical analyses occur. Increment company revision so old analyses display stale. Do not overwrite newer versions; reject unknown structures explicitly. No automatic reverse operation is provided, preserving audit records; database rollback requires a separate data plan.

Query projections fill missing mailbox_address in old email bodies from their Mailbox, retaining original email deduplication identifiers.
