# Employee-confirmed chat order updates and email

The backend implements the Agent contract introduced in `593b3ec` without changing the Agent, model parameters, tool budget or L1–L4 behavior. Preparation saves only `chat.ActionProposal`, audit and `ToolRead` evidence in one transaction. The answer request can finish normally while its independent proposal awaits employee confirmation.

## Interfaces

Existing employee-bound Agent endpoints remain `/api/v1/agent/chat/tools/` and `/api/v1/agent/chat/tool-reads/`, with `chat-tools-v1` discovery. The following seven capabilities supplement the existing customer and experiment tools:

- `orders.list`, `orders.get`, `connections.list`, `connections.get`: strict business reads, including order/line revisions and credential-free connection metadata.
- `chat_actions.prepare_order_update`, `chat_actions.prepare_email`: `executionMode=confirm`, return HTTP 201 with outer `completed` and inner `pending_confirmation`.
- `chat_actions.get`: read the same employee/conversation proposal, including its current execution state.

All three `chat_actions.*` tools declare `confirmationContract=chat-actions-v1`. The closed Agent data object has eight fields: `id`, `kind`, `status`, `revision`, `expires_at`, `confirmed_by_employee`, `arguments`, `preview`. Email input has seven fields: `company_id`, `connection_id`, `to`, `cc`, `bcc`, `subject`, `body_text`. These counts correct the handoff document's prose; the actual Agent schema is authoritative.

Employee browser endpoints follow the existing sales chat namespace:

```text
GET  /api/v1/sales/chat/action-proposals/?conversation_id=<UUID>&page=1&page_size=30
GET  /api/v1/sales/chat/action-proposals/<UUID>/
POST /api/v1/sales/chat/action-proposals/<UUID>/decision/
{"decision":"approve","revision":1}
```

Cancellation uses `decision=cancel`. All three endpoints require a real Session; decisions enforce CSRF. Agent/Tool credentials and laboratory identity headers cannot decide. List entries additionally contain `request_id` and `user_message_id` for positioning cards beside the original user message; the Agent's closed eight-field response is unchanged.

## Business semantics

- The existing 24-hour approval lifetime is reused. Preparation hashes employee/request/tool/canonical arguments; replay returns the original pending proposal and receipt. Replaying an executed, cancelled or expired preparation gives 409 and must be followed by a status query.
- Within one employee conversation, a new pending proposal for the same operation and target explicitly cancels older pending proposals and records `chat_proposal_superseded` with the replacement ID. The target is the order for changes and the customer for email. Approved or executing proposals are never cancelled by preparation.
- Approvals lock employee/business owners in stable order before proposal/order/line records. Strict identity, grants and revisions remain mandatory in laboratory mode. Existing experiment writes retain their separate checkpointed approval flow, as required by the current Agent implementation.
- Only existing, unarchived draft orders/lines can change. Existing no-currency-change-with-lines, Decimal precision, discount and ownership rules remain active. The whole operation commits or rolls back; header updates precede lines because existing line services advance the parent revision. Existing audits, snapshots and scoring dependencies are reused.
- Mail preparation checks employee/customer ownership, an active Gmail connection and encrypted granted scopes without refreshing tokens or contacting Google. Confirmation checks them again, creates a business Draft and approved ToolAction atomically, and records both links on the proposal. Cc/Bcc remain in the frozen ToolAction snapshot because the legacy editable Draft stores To recipients only.
- The existing `sales_worker` claims approved ToolActions. It constructs MIME using the exact frozen To/Cc/Bcc, subject and body and never automatically retries uncertain sends. Proposal reads use the linked action's current state, including after restart or manual reconciliation. `succeeded` means provider acceptance, not recipient delivery or reading.
- The browser renders frozen content as escaped text, submits only decision/revision, and restores cards from the server on refresh. Pending proposals do not block another chat question. Approved/running mail status polling is bounded and stops on errors; manual refresh does not execute anything.
- Account reset recognizes proposal ownership through the originating request. No changes were made to ordinary non-chat tool authorization or experimental business rules.
- Conversation responses expose read-only `can_review_chat_actions`, computed from the actual Django Session and original conversation owner. The browser only requests private proposals when this capability is true. Anonymous or cross-account laboratory browsing retains its existing chat behavior without acquiring confirmation permission.

## Validation and rollout

Tests use isolated PostgreSQL with pgvector. When the test role cannot create extensions, preinstall vector only in the dedicated test database and use `--keepdb`; do not raise application-role privileges or replace PostgreSQL. Authorization regressions run with `LAB_OPEN_ACCESS=false` and `LOCAL_DEBUG_AUTO_LOGIN=false`; these are test process settings, not changed application defaults. Individual cases explicitly test laboratory mode too.

```text
python backend/manage.py test tests.integration.test_chat_actions tests.integration.test_chat_actions_live tests.integration.test_chat_tools tests.integration.test_chat_approvals tests.integration.test_sales_external tests.integration.test_workspace_chat tests.integration.test_general_chat tests.integration.test_account_reset tests.contracts.test_chat tests.contracts.test_schema --keepdb --noinput
python -m unittest agent.tests.test_chat_actions agent.tests.test_workspace_chat agent.tests.test_experiment_chat agent.tests.test_chat_approvals
python backend/manage.py spectacular --file backend/contracts/openapi.yaml --validate --fail-on-warn
python backend/manage.py makemigrations --check --dry-run
python backend/tools/check_docs.py
python backend/tools/check_doc_changes.py
node backend/tools/browser_chat_actions.cjs
node backend/tools/browser_chat_approvals.cjs
node backend/tools/browser_chat.cjs
```

Browser scripts use `SALESMATE_PLAYWRIGHT_MODULE` and `SALESMATE_BROWSER_PATH` for installed runtimes. They exercise actual frontend assets with mocked HTTP and save desktop/mobile screenshots under `backend/artifacts/browser/`. Live-server tests separately use the actual Agent HTTP client, actual Session+CSRF requests, a real database and concurrent transactions. Deterministic model selections and mocked Gmail transport do not establish real-model planning quality, real OAuth authorization or delivery.

Deployment requires the new `chat.0005_action_proposal` migration, updated backend/frontend assets and the existing sales worker running. Apply migrations through the normal backup and blue-green deployment process and refresh cached frontend assets. A successful automated test does not establish real OAuth or email delivery; validate those separately with an explicitly authorized mailbox and recipient.

Google Cloud's Web OAuth client must authorize the exact send callback `https://milkdragon.dev/api/v1/sales/oauth/`, including its trailing slash. Preserve the independent read callback `/api/v1/mailboxes/gmail-callback/`. The send connection requests its own Google consent and never reuses or expands a read-only mailbox credential implicitly.

The CI browser diagnostic uses `browser_world_map.cjs`, the documented isolated current-world-page harness. `browser_world_news.cjs` is a separate local-database harness requiring explicitly seeded synthetic data and `SALESMATE_TEST_URL`; it cannot run as an unconfigured mocked-API step. The i18n harness follows current shared-browse routes and supplies explicit world-page fixtures.
