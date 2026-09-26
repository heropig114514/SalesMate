# Clear Internal Account Data

This feature retains the original accounts_user record, account ID, username, password hash, and sign-in state. Authentication groups and identity permissions remain. It clears the account's company and personal business data, onboarding and documents, customers, mail copies, extraction/analysis/scores, sales records, chat and drafts, knowledge base and vectors, business audit records, notifications, connections and service credentials, and attachments.

After connection authorization is cleared, reconnect the mailbox. The operation does not delete original mail in the external mailbox or retract mail already sent. It does not alter historical backups or operations logs.

## Frontend Call

    POST /api/v1/accounts/me/reset/
    X-CSRFToken: <current CSRF token>
    Idempotency-Key: <UUID for this operation>

Use the current Session sign-in state. No request body, password, owner_id, or company_id is required. Retrying the same operation must reuse its UUID. A different UUID means the user explicitly initiates another reset. The backend retains submitted keys, and replaying an earlier request does not delete data created later.

Successful HTTP 200 response:

    {"status":"completed","generation":1,"owner_id":7,"reset_id":"UUID for this operation"}

| Status | Meaning and handling |
| --- | --- |
| 400 | The operation key is not a UUID; correct request format. |
| 403 | The caller is not signed in or CSRF validation failed. |
| 409 | The account has active work, or another user's business record has a foreign-key reference to this account's data. This database deletion was not committed. |
| 503 | File cleanup or ownership rules are unfinished. Inspect logs and continue explicitly; do not retry automatically or show completion. |

A successful response sends Clear-Site-Data: "cache" to clear browser HTTP cache while retaining the sign-in cookie. API responses use Cache-Control: no-store. Account requests carry X-Account-ID, X-Account-Data-Version, and X-Account-Reset-Status headers. Frontend writes send the read X-Account-Data-Version; an old page write returns 409. Existing Agent/Tool clients need not send the version header because their old credentials were deleted.

The Profile Clear account data control describes the deletion scope and requires one confirmation. On success it clears Web Storage, Cache Storage, and enumerable IndexedDB in the salesmate:<account ID>: namespace; broadcasts to same-account tabs and reloads; and releases page memory, unsaved drafts, and stale query results. Current business behavior stores no mail body in IndexedDB. Future storage must follow this namespace rule. Other-account storage is never deleted.

When file cleanup fails, identity/session GET remains available while other business interfaces pause. The frontend uses X-Account-Reset-Status: cleaning to show Continue clearing and resumes after refresh. The page operation key remains in Session Storage until cache cleanup completes. Continue only resumes unfinished cleanup stages and does not repeat database deletion.

## Backend Execution and Boundaries

A PostgreSQL account-exclusive advisory lock covers the complete reset. HTTP, CRM/chat work units, external sales actions, and due reminders hold shared locks. An active task returns 409 immediately and the user retries later explicitly; Workers for other accounts are not stopped. SQLite supports only the existing local preview and explicitly rejects account reset.

The database transaction first freezes the account-scoped primary-key set for every table, then deletes with parameterized DELETE. Foreign keys defer only to final in-transaction validation; constraints are never disabled and ORM cascades never expand deletion scope. Membership, shared authorization, and dependent notifications may be detached; mutable assignee links are set null. If another user's independent business still references a record to delete, the whole transaction rolls back and the shared reference must be handled explicitly first.

Business deletion, file manifest, and data version commit in one transaction. The system then deletes manifest-listed files under private_uploads/<account ID>/, clears business cache and OAuth state from all database sessions for the account, and retains Django authentication fields. File failure persists state preventing business data regeneration; an explicit retry can continue. The system does not use a separate Django cache for business content. Analysis cache and vectors are deleted with this account's database records, and no global-cache-clear command is used.

AccountReset retains only account coordination metadata: data version, idempotency key, cleanup state, and unfinished file paths. Its file manifest is empty after success and it retains no business body. New business models with direct owner are included automatically; models without direct owner must declare ownership in INDIRECT_OWNERS. A missing rule fails explicitly.

Deployment applies accounts.0004_accountreset, dependent on onboarding migration 0003, and restarts Web, CRM, chat, and sales Workers together so all use the same lock protocol. Do not switch new Web directly before database initialization. Update server templates and static assets through the existing deployment process.

## Verification

    .venv/Scripts/python.exe backend/manage.py test tests.integration.test_account_reset --noinput
    node backend/tools/browser_account_reset.cjs

Browser tests use existing SALESMATE_PLAYWRIGHT_MODULE and SALESMATE_BROWSER_PATH variables. Backend tests use an isolated PostgreSQL test database, real sessions, and temporary files. Browser tests use real storage and page modules with mocked business HTTP; they do not demonstrate that a production account was cleared.
