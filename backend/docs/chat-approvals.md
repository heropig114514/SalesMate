# Chat write approval

All writes exposed through `/api/v1/agent/chat/tool-reads/` require an explicit browser decision. These include private `customers.create` and `experiments.create/update/delete` for synthetic data. Reads remain immediate. Customer creation uses the existing directory handler with only an explicit company name; no guessed domains, contacts or other CRM facts. Order/email proposals retain their separate [action confirmation contract](chat-actions.md). Background jobs and ordinary non-chat Tool/MCP calls are unchanged.

## State and user experience

```text
pending → processing → awaiting_approval
                         ├─ approve: execute frozen write → pending → processing → answer
                         └─ reject: cancelled → conversation accepts another question
```

The frontend displays the operation, batch, model, primary key when applicable, and proposed values. Approval executes that operation once, then the chat Worker resumes its saved loop. Rejection cancels the current request, restores an editable question when the input is empty, and preserves existing messages and audit records. Earlier approved writes in the same question remain committed. It is not a database rollback or deletion of conversation history.

Closing the panel or browser does not decide anything. Reopening shows the persisted pending approval. While waiting, that conversation cannot submit another question, invoke further tools, or save a final answer; other conversations can continue. The Worker releases its temporary identity instead of holding a worker/thread or database transaction while the user decides.

## Agent contract

Read calls retain their existing payloads. Every proposed write adds a `continuation` object alongside `request_id`, `name`, and `arguments`:

```json
{
  "next_turn": 3,
  "observations": [],
  "signatures": []
}
```

The example illustrates the field types only: actual observations must contain the results of all preceding turns (`next_turn - 1`), and signatures contain the attempted tool names and canonical argument JSON. The supplied Agent persists these values itself; models cannot approve through this object. Legacy Agents omitting the checkpoint receive 400 and do not execute writes.

Successful proposal creation returns HTTP 202 with `status: "approval_required"`, `request_id`, `tool`, and `approval`. The request becomes `awaiting_approval`. No mutation receipt or success evidence exists yet. The Agent returns a local suspension result without calling `answers/`. A lost proposal response is reconciled through the existing status read rather than automatically retrying the write.

After user approval, `claim/` returns the original five fields plus `resume`: the saved continuation, canonical operation receipt, its arguments, and the request's registered tool evidence. The Agent retains the same request ID, context, conversation history, model parameters, six-tool-call budget, and evidence budgets. It neither repeats earlier tools nor reruns the approved write. Another write in that continuation requires another approval.

## Browser decision

Request status/list responses include `approval` only while awaiting a decision. The review object contains `id`, `request_id`, `tool`, `arguments`, `target_fingerprint`, `status`, `expires_at`, and `approver_id`. It excludes the Agent checkpoint.

```http
POST /api/v1/sales/chat/requests/{request_id}/approvals/{approval_id}/decision/
Content-Type: application/json
X-CSRFToken: <browser CSRF token>

{"decision": "approve"}
```

`decision` is `approve` or `reject`; extra fields, replacement arguments, and self-reported approval flags are rejected. Only the user who submitted the question may decide using a logged-in Session with CSRF. Legacy requests use their owner. Agent/Tool credentials and public laboratory identity headers cannot approve. Anonymous laboratory chat therefore needs a real eligible login before a decision can be submitted; laboratory access does not disable this gate.

The approval lasts 24 hours, matching the existing tool-proposal lifetime. Execution revalidates access, the current tool schema, and the exact target fingerprint, including in laboratory mode. Modification/deletion locks the batch owner, manifest, and target before the final version check. Expired approvals or changed targets return 409; the user can reject and submit a fresh question. Business failures preserve their HTTP errors and leave the approval pending without a mutation/evidence receipt. No silent retries or automatic refreshed approval are performed.

Approval, mutation, canonical tool evidence, and requeue commit in one transaction. Identical decisions replay without another write; an opposite decision returns 409. Pending approvals are limited to one per request and approval waits count toward the existing one-active-request-per-conversation database constraint.

`ChatApproval` is separate from general `ToolProposal`: it binds an `AnswerRequest`, preserves an Agent checkpoint, bypasses neither approval in laboratory mode nor the chat tool allowlist, and records execution evidence in the chat transaction. General tool confirmation and external-action queues retain their existing contracts.

## Deployment and validation

Stop old chat Workers, apply `python backend/manage.py migrate chat`, and deploy the backend, Agent, and frontend together. Restart the chat Worker with the new code. Refresh cached frontend assets. The schema migration is `chat.0004_chatapproval_and_more`; this code change does not apply it to a live database automatically.

Tests use isolated PostgreSQL with pgvector and synthetic fixtures:

```sh
python backend/manage.py test tests.integration.test_chat_approvals tests.integration.test_experiment_writes tests.integration.test_experiment_tools.ExperimentToolTests.test_chat_agent_write_http_round_trip --noinput
python backend/manage.py test tests.integration.test_chat tests.integration.test_chat_tools tests.integration.test_workspace_chat tests.integration.test_shared_chat_worker tests.integration.test_general_chat tests.contracts.test_chat tests.contracts.test_schema tests.integration.test_lab_access --noinput
python -m unittest agent.tests.test_workspace_chat agent.tests.test_experiment_chat agent.tests.test_chat_approvals
python backend/manage.py spectacular --file backend/contracts/openapi.yaml --validate --fail-on-warn
```

Run `python tools/check_docs.py` from `backend/`. Browser verification uses `node backend/tools/browser_chat_approvals.cjs` with `SALESMATE_PLAYWRIGHT_MODULE` and `SALESMATE_BROWSER_PATH` pointing to installed Playwright/Chromium. Browser HTTP/model results are mocked; backend integration tests cover real database operations and authenticated HTTP. Neither establishes production deployment or real-model planning quality.

## New customer followed by email

The workspace catalog now publishes `customers.create` (write mode, `{ "name": "company name" }`) only for the real employee's private workspace request. It requires the same continuation checkpoint and Session/CSRF review as above. Laboratory access never authorizes creating a customer for another conversation owner. The server rejects an active owned exact-name match, including one created while review was pending; it never silently merges or substitutes a record. Ordinary directory creation keeps its existing behavior.

Approval executes once with the approval UUID as the generic tool idempotency key and records `customer_creation` evidence with the real returned company UUID and HTTP 201. Rejection, stale definitions, identity failures and evidence persistence failures do not create customers or email tasks. No database migration is needed for this extension.

For the user's sequence “send an inquiry → this is a new company → register it”, workspace-chat-v5 preserves the original recipient and purpose in recent history. Search, create, approved continuation, customer context, connection list/get, and email preparation consume the existing six-tool budget. The browser shows the exact company name first; after registration the Agent can prepare the email for a separate full-content confirmation. Customer registration alone never creates a Draft or ToolAction, and a pending email proposal is not a send.

Regression: `tests.integration.test_chat_customers`, `tests.integration.test_chat_actions_live`, the existing approval/action/experiment suites, and `backend/tools/browser_chat_approvals.cjs`. The new live test uses real HTTP and PostgreSQL with deterministic model choices, not a real model or mailbox provider. Production planning verification must be reported separately.
