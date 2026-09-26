# Sales chat: backend adaptation and operations

Updated 2026-09-20. The current deployment policy removes duplicate CI/server quality gates and retains tests as independent diagnostics; the historical release gates below have been superseded by the [current deployment instructions](../deploy/lightsail/README.md). This document describes backend adaptation to the current workspace Agent contract. Chat reports now validate only their schema, while retaining permission, request-state, and idempotency checks. The implementation provides questions, task status, Agent claim/context/report, evidence snapshots, citations, consumers, browser presentation, and request-bound read-only tools. See the [workspace chat integration contract](workspace-chat-tools.md) for new interfaces, tool errors, evidence storage, and release steps. Code delivery does not establish completion of production migrations, service installation, or real-model acceptance.

## 1. Boundaries and reuse

- Reuse `sales.Conversation`, `sales.Message`, message `client_key`, and employee-bound `AgentAuthentication`. Do not recreate conversations or backfill tasks for old messages.
- `apps.chat` maintains `AnswerRequest`, `Citation`, and `KnowledgeEntry`, initially in `chat.0001_initial`. General chat adds `sales.0005_general_conversation` and `chat.0002_general_answer_request`, relaxing two company non-null constraints without changing L1–L4 protocols, parameters, or data.
- Ordinary `sales/records/messages/` still saves only user messages; only the protected chat-report service can create assistant messages. Saving a draft does not invoke a model.
- New chats support private workspace conversations without a preselected company; historical customer conversations remain readable. Team business sharing does not grant mail or profile access. Existing sharing and human-confirmed sending remain unchanged.
- External knowledge is disabled. The backend supports request-bound read-only customers.search/customers.context calls, with model tool orchestration implemented by the Agent. Chat does not send mail, access calendar actions, or write CRM/files, and introduces no vector database or distributed queue.

On startup, the new Worker terminates old company-bound pending/processing tasks under employee locks; claiming also cleans up that employee's legacy tasks. Completed/failed history, messages, and evidence remain unchanged. Old questions are neither reassigned nor silently converted into workspace questions. Stop the old Worker before upgrading; repository deployment scripts drain chat before starting the new Worker. This upgrade adds no database migration.

## 2. Data and state invariants

`AnswerRequest` stores owner, company, conversation, user_message, a nullable unique assistant_message, status, timestamps, frozen history, context snapshots, results, prompt version, and Agent errors. `retry_of` identifies the original failed request, with at most one successor per original request.

```text
pending → processing → completed
                     → failed
pending → failed (permissions found invalid at claim time)
pending/processing → failed (legacy company-bound tasks only; workspace_chat_required)
failed --explicit user retry--> pending with a new request_id
```

- At most one pending/processing request exists per conversation, enforced by a conditional database uniqueness constraint and transaction locks. Different employees or conversations may each have pending work.
- Employee row locks serialize submission, claim, reporting, and recovery while preserving the existing business lock order. Model calls do not hold database transactions.
- An original question may have multiple explicit attempts, each with at most one assistant message. New attempts cannot overwrite old results.
- Repeating the same client_key and body returns the original task; different contents return 409. Explicit retries use the retry endpoint rather than resubmission.
- Repeating an identical complete terminal JSON result returns duplicate=true; a different result returns 409. Prompt version, citation order, body, and errors all participate in comparison.
- A completed result may have no citations. Structurally valid reports do not require prior context-snapshot reads, and the backend does not infer citation requirements from answer semantics.
- A failed result requires empty assistant_text and citations and creates no assistant message.
- After confirming process interruption, an administrator can terminate processing as failed. There is no automatic timeout or resetting old requests to pending. Late reports are rejected.
- Retrying an earlier question in the browser returns 409 if later user questions exist in the conversation. Ask again at the end to preserve historical order.

## 3. Browser interfaces

The workspace, business-management, and world-news pages provide a floating chat-assistant button at the lower right. It opens a horizontal bottom panel, closed with its collapse button or Esc, without navigating away or selecting a customer. General Q&A, writing, translation, and planning are supported; history and drafts belong only to the current employee. Opening the panel only reads history; explicit questions, draft saves, or conversation creation perform writes. Mobile uses a height-limited bottom panel with modal focus, restoring background interaction on collapse. Unsaved drafts survive collapse/reopening and navigation within the workspace, but must be saved before a page change or refresh.

Customer details no longer have a separate AI-assistant button. Both `/#assistant` and historical `/#assistant/<company_id>` links open the workspace without reading the linked company or restoring old company tasks. The Agent searches and queries customers according to the question.

For general conversations created with `POST /api/v1/sales/records/conversations/`, company may be omitted or null; a non-null company is no longer accepted for new conversations. Lists expose `conversation_scope=general|customer`, with general used by the general-chat page. `?company=<uuid>` only reads historical customer conversations. The binding cannot change after creation. MCP/CLI `conversations.list` exposes the same filter.

Routes use `/api/v1/sales/chat/`, existing SessionAuthentication, CSRF, and unified error responses.

| Path | Method | Behavior |
|---|---|---|
| `messages/` | POST | Save the user message and pending request in one transaction |
| `requests/?conversation=<uuid>` | GET | Paginate requests ordered by created_at/id using page/page_size, default 30, maximum 100 |
| `requests/<uuid>/` | GET | Request state, timestamps, errors, assistant message ID, and ordered citations |
| `requests/<uuid>/retry/` | POST `{}` | Explicitly create a new attempt for a failed request; repeated calls return the existing successor |

Exact question request body:

```json
{
  "conversation_id": "<uuid>",
  "content": "客户目前最关心什么？",
  "client_key": "<client-generated-uuid>"
}
```

employee_id/company_id/role are not accepted. The conversation must belong to the workspace with no preselected company; the employee comes from the authenticated identity. Creation returns 201; identical retransmission returns 200.

Status responses contain `request_id`, `conversation_id`, `user_message_id`, `assistant_message_id`, `status`, `error`, `created_at`, `processing_started_at`, `finished_at`, `chat_prompt_version`, and `citations`. Browser citations include position, the identity triple, and backend-stored content for evidence expansion; these additional fields are not sent to the Agent.

Message bodies still come from `records/messages/?conversation=...`. Existing conversation-creation and draft interfaces remain unchanged.

## 4. Fixed Agent interfaces

All `/api/v1/agent/` routes require `Authorization: Agent <service-token>`. One token binds to one employee. Browser sessions cannot replace service authentication.

### claim

`POST chat/requests/claim/` accepts `{}` and returns `{"request": null}` when idle. Otherwise:

```json
{
  "request": {
    "request_id": "<uuid>",
    "conversation_id": "<uuid>",
    "user_message_id": "<uuid>",
    "question": "客户目前最关心什么？",
    "recent_history": []
  }
}
```

The claim response has exactly these five fields and entirely omits company_id. The new Agent parser consumes this structure directly. Model outputs use action=tool or action=answer, with the Agent orchestrating the tool loop; final answer reports retain six fields.

History contains the latest 20 nonempty user/assistant messages from the same employee and conversation before the original question, restored to chronological order. It excludes the current and later questions. History freezes at claim time without changing the Agent's 6000-character history budget.

### context

`POST chat/context/` accepts `{"request_id":"<uuid>","scope":"internal"}`.

```json
{
  "request_id": "<uuid>",
  "scope": "internal",
  "customer_context": [],
  "context_items": [],
  "customer_context_status": "completed",
  "knowledge_status": "completed",
  "retrieval_gaps": [],
  "external_available": false
}
```

Each Context Item has exactly four string fields: `source_id`, `source_type`, `title_or_label`, and `content`. The first three form citation identity. Additional model IDs, database timestamps, links, or other fields are forbidden; the Agent parser rejects unknown fields.

The first internal request reads and saves a snapshot transactionally; later reads for the same request return identical contents. Arbitrary company or query overrides are not accepted.

Initial context contains only the current employee's internal knowledge and an empty customer_context. General conversation can invoke the model without knowledge; knowledge-read failures still fail explicitly. Initial context does not read or search any customer's mail, transactions, or profile.

Workspace initial context returns at most four of the employee's knowledge entries, prioritizing question-term matches and then version timestamps. Each entry is limited to 2000 characters and explicitly marked as an excerpt, preserving the established budget. The Agent obtains customer directories, mail, and profiles through request-bound tools as needed; complete returned profiles and sources are stored separately in ToolRead.

- Internal knowledge uses only real material imported by maintainers, with no default policies. There is no independent remote knowledge source. Database errors fail normally instead of silently producing empty knowledge.
- `external_available=false`; external scope returns 409 and other invalid scopes return 400. Future external support requires a versioned contract and tests; no fallback implementation is preinstalled.
- Citation reports validate only the identity triple's structure. Matching original context or successful ToolRead evidence from this request contributes copied content; unmatched triples are stored as Agent assertions with empty content. The backend does not use source_id to query other requests/business records or accept Agent-supplied content. Empty content means no verifiable evidence was attached by the backend, not a verified source. Tool records remain separate, with no new chat/context response fields.

### report

Successful `POST chat/answers/` example:

```json
{
  "request_id": "<uuid>",
  "chat_prompt_version": "workspace-chat-v1",
  "assistant_text": "现有资料不足，无法回答该问题。",
  "citations": [],
  "status": "completed",
  "error": null
}
```

`chat_prompt_version` is a nonempty string of at most 100 characters. The backend has no version allowlist and does not bind versions to companies; workspace-chat-v1 is accepted directly. Version acceptance does not establish implementation of new tool orchestration; the Agent must adapt its workflow. The backend does not judge answer semantics, inline citation numbers, duplicate citations, or membership in the request snapshot. The Agent owns citation accuracy and factual support.

Failure example:

```json
{
  "request_id": "<uuid>",
  "chat_prompt_version": "workspace-chat-v1",
  "assistant_text": "",
  "citations": [],
  "status": "failed",
  "error": {"code":"model_unavailable","message":"回答模型暂时不可用，请稍后重试。"}
}
```

For failed reports, error must contain exactly code/message, both nonempty strings. Specific codes and fixed wording are no longer restricted. The Agent must provide redacted, user-displayable errors, which the backend stores unchanged. The current Worker's report_failed still means saving was not confirmed locally and does not automatically submit another report through that failed request.

Save responses contain `request_id`, `saved=true`, boolean `duplicate`, and `assistant_message_id`; completed requires a message ID, while failed returns null. Each citation contains exactly three nonempty strings, source_id/source_type/title_or_label; source_type is limited to 80 characters. Citation order is preserved without deduplication or validation of inline markers such as `[1]`. The top level still requires exactly request_id/chat_prompt_version/assistant_text/citations/status/error, with a UUID request_id. completed requires nonempty assistant_text and error=null; failed requires assistant_text="", citations=[], and an error object. Unknown fields, wrong types, and invalid states still return 400.

401 indicates missing/invalid service authentication; unauthorized and nonexistent resources both return 404; invalid formats return 400; state or version/result conflicts return 409. Existing `error.code/error.detail` and HTTP request_id envelopes are reused.

## 5. Running, recovery, and knowledge maintenance

From the repository root, use the project Python environment and existing `.env`:

```powershell
python backend/manage.py migrate
python -m uvicorn --app-dir backend config.asgi:application --host 127.0.0.1 --port 8000
# In another terminal; the shared process rotates pending requests across active employees.
python backend/manage.py chat_worker
# Process at most one request.
python backend/manage.py chat_worker --once
```

Shared chat_worker reuses `SALESMATE_BACKEND_AGENT_URL` and Agent model configuration. Each work unit obtains a temporary employee token through server-internal `scoped_backend`, revoked on exit. The environment's fixed `SALESMATE_AGENT_SERVICE_TOKEN` no longer determines whose work is consumed. The standalone Agent CLI still uses its explicitly configured fixed identity. chat_worker is independent of ANALYSIS_PROVIDER and crm_worker and changes neither L1/L3 model parameters, mailbox scope, nor profile concurrency. Chat still requires usable model configuration and has no rule-based fallback.

The persistent consumer rotates pending work by employee primary key, excludes inactive employees, and discovers only pending tasks. It serially processes one task through that employee's own HTTP identity. Empty queues are polled every two seconds by default; `--poll` explicitly accepts (0,60] seconds. SIGTERM waits for in-flight work before stopping claims. Saved failures retain failed records; claim errors or report_failed stop the consumer with a nonzero exit and no implicit retry. Logs contain only task IDs, employees, states, error codes, and exception types.

For recovery, first inspect request state, especially after a lost report response: the database may already show completed. After confirming the original process stopped and the request remains processing, run:

```powershell
python backend/manage.py chat_interrupt --owner <username> --request-id <uuid> --confirm-interrupted
```

This marks the original request worker_interrupted/failed. The user then explicitly retries, creating a new ID. There is no automatic timeout threshold or implicit requeue.

Internal knowledge files are UTF-8 JSON arrays. Every entry has exactly source_key, version, title, content, and active; the four text values must be nonempty and active must be boolean. Import only confirmed material, not fictional example policies from requirements documents.

```powershell
python backend/manage.py chat_knowledge --owner <username> --file <knowledge.json>
```

The same key/version cannot change title or content; changed contents require an explicit new version. A new active version disables old versions of that key. Identical contents with active=false explicitly deactivate an entry. Batch failure rolls back everything, preserving historical request evidence.

## 6. Browser behavior

Sending a question creates an answer task; saving a draft retains its existing behavior. An active request prevents another question in that conversation. The page checks status every two seconds, at most 120 times per observation round, then pauses on failure or the limit and offers continued checking. Resuming observation does not regenerate an answer.

Completion automatically loads the assistant message and expandable citation evidence. Unsaved input, focus, and history reading position remain intact. Failures display safe messages and offer explicit retry. Closing the sidebar or switching customer/conversation cancels observation eligibility and in-flight status requests; stale responses cannot overwrite the new context. All text is escaped, and source contents never execute as HTML or scripts.

## 7. Lightsail deployment

`backend/deploy/lightsail/salesmate-chat.service` runs as the ordinary salesmate user with shared `/opt/salesmate/shared/runtime.env`, without automatically restarting failed processes. Blue/green deployment drains chat, CRM/sales schedulers, and Celery consumers before backup and migration. The old Web keeps serving; chat starts after candidate health checks and successful traffic switching, then the old Web drains and retires. Shared chat scheduling reuses scoped_backend temporary identities; each HTTP client still binds to one employee with unchanged permission checks.

Repository `deploy-from-git.sh` includes chat-service preflight, stop, start, and health checks. The server script is a separate root-protected copy and **does not update through ordinary code pulls**. For initial rollout, review and install the chat service without starting it, update the protected deployment script through the established operations process, and then release the code. Before stopping services, the script verifies service installation; it does not automatically replace systemd configuration from ordinary source code. A merge alone does not establish that the chat consumer is running; verify deployment results and actual systemd state.

Before the first release, the target production database was confirmed not to have applied chat migrations, allowing the four constraints in `chat.0001_initial` to reside in the corresponding `CreateModel.options.constraints`. The migration creates only three new tables without changing existing business tables. Active-conversation uniqueness, request states, citation positions, and knowledge-version constraints remain equivalent. The online migration gate still rejects standalone `AddConstraint`. Follow the existing backup-before-migrate process; never rewrite migrations after application.

For manual deployment, migrate, install service files in `/etc/systemd/system/`, run daemon-reload, and explicitly enable/start salesmate-chat after checking model/backend configuration and the pending queue. Use `journalctl -u salesmate-chat` for logs. One service covers all active employees; new registrations need neither permanent service tokens nor separate processes.

## 8. Validation and delivery boundaries

```powershell
python backend/manage.py test tests --noinput
python -m unittest discover -s agent/tests
python backend/manage.py makemigrations --check --dry-run
python backend/manage.py spectacular --file backend/contracts/openapi.yaml --validate
# After configuring the existing Playwright/browser environment, run the joint browser acceptance check.
python backend/manage.py test tools.chat_browser_e2e --noinput
# Run from backend/.
python tools/check_docs.py
python tools/check_doc_changes.py
```

Browser checks use the project's explicit Playwright/Chrome configuration and `node backend/tools/browser_chat.cjs`, added to deployment CI. Backend tests cover real PostgreSQL concurrent submission/claim/report, rollback, isolation, permission revocation, profile versions, evidence snapshots, citations, knowledge versions, new attempts after failure, and recovery. A real temporary Django HTTP server integrates the original Agent client/workflow with mocked model outputs.

Browser interaction tests use real pages/JS with mocked APIs, covering submission, completion, citation escaping, retry, preserved edits, query failures, observation limits, switching, and closure. `tools.chat_browser_e2e` additionally uses a real browser, Session/CSRF, temporary Django HTTP, and isolated PostgreSQL. After the browser creates a task, the original Agent workflow claims it, reads evidence, and saves an answer that appears automatically with citations. Only the application bootstrap is replaced to mount the real sidebar separately; all business APIs execute normally, and model outputs are mocked at the Python call boundary. Both browser check types were added to CI.

These checks do not establish acceptance against real Bailian models or deployed pages. Production migrations, service installation, real-model quality, and complete external authorization require validation in their actual environments.

Implementation, comments, directories, and migrations are maintained together in the workspace. Git commit atomicity is not claimed before a commit exists.

### Local validation record: 2026-09-18

- Isolated PostgreSQL 16 test database: all 155 backend tests passed, including 24 new chat tests; no business database was connected.
- The original 186-test offline Agent suite and nine mailbox-tool tests passed; Agent executable code was unchanged.
- One real-HTTP browser integration check passed with mocked model output; all four chat, workspace, mail-processing, and QQ-send browser scripts passed.
- Migration consistency found no missing generated changes; OpenAPI generation/validation and version-contract tests passed.
- Python documentation structure covered 128 files; differential checks reported zero errors and zero review items, supplemented by manual implementation/directory review. Ruff F checks for new Python, changed-JS syntax, Bash deployment-script syntax, dependency consistency, and Git whitespace checks passed.
- Temporary PostgreSQL was stopped. No production migration, online service installation, real-model call, email send, Git commit, or push was performed.

### Pre-release review: 2026-09-19

- After infrastructure changes were merged, all 171 backend tests passed on an isolated PostgreSQL 16/pgvector instance, including chat concurrency, permissions, and constraints; no production business database was used.
- All 186 Agent tests, nine mailbox-tool tests, and five mocked-API browser check groups passed, including the new primary chat entry point, world map, mail processing, and QQ sending.
- One joint acceptance check using a real browser, HTTP, Session/CSRF, isolated PostgreSQL, and the original Agent workflow passed; model outputs remained mocked at the call boundary.
- Fresh-database migrations, `makemigrations --check --dry-run`, and OpenAPI generation/validation passed. Read-only online migration gates and chat-token ownership preflight checks against the target production database passed.
- Python documentation structure/change checks covered 141 files, with zero errors and zero review items; checker tests passed 12 + 9 cases. Frontend, deployment files, and third-party resource notes also received manual review.
- These are pre-release validation records, not proof of real-model quality or successful production deployment. The final authority is GitHub Actions for the corresponding commit, server version, and health checks.

## Historical general-mode migration notes

The following two existing nullable-company migrations apply to environments without general chat; the current workspace upgrade adds no migration. Old consumers cannot process current workspace requests. Before rolling back to non-null fields, handle general conversations and answers explicitly; migrations do not delete history automatically. The conservative online migration gate requires separate AlterField review and is unchanged here.

The two AlterField operations were structurally reviewed: only `sales_conversation.company_id` and `chat_answerrequest.company_id` become nullable. Column types, foreign keys, indexes, and PROTECT semantics remain intact, and history is neither deleted nor rewritten. Production deployment first backs up under the deployment lock, checks actual SQL and plans using the release commit, and explicitly applies both migrations. The conservative automatic gate remains unchanged. Old Web versions still require customers, so their UI cannot create general tasks during transition. The existing blue/green release process drains old chat Workers, switches Web, and starts new Workers; new Workers terminate legacy active company-bound tasks.

## Multi-user queue regression

The old chat service polled only one employee using a fixed environment credential, leaving other employees' requests pending even when systemd reported active. Real HTTP checks for the shared scheduler verified saved ping answers for two users without service credentials, temporary credential revocation, and rejection of cross-user reads. Single-task serial execution, default two-second polling, no failure retry, and SIGTERM completion of in-flight reporting are preserved. Existing pending work is claimed normally without recreating messages or resetting processing.
