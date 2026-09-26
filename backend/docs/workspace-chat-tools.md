# Workspace chat: Agent integration contract

Updated: 2026-09-21. This document describes implemented backend APIs. Production must first apply `chat.0003_tool_read` and deploy code. Agent implements tool selection, model-call loops, pagination, and prompts; the backend neither installs MCP for models nor invokes models automatically.

## 1. Scope and identity

Workspace chat requires no preselected company. Omit `company` or use null when creating conversations. Question submission/answer reporting retain original structures; claims return only request_id/conversation_id/user_message_id/question/recent_history, omitting company_id entirely. New conversations reject nonnull company; old conversations retain history.

All endpoints use `Authorization: Agent <employee-bound-service-token>`, never ordinary Tool tokens or browser Sessions. Tokens identify employees; inputs reject owner_id, employee_id, or other overrides. Agent credentials identify employees independently of whether the process is called a chat worker. Catalog discovery/execution both require an owned processing request and an accessible conversation.

Explicitly expose `customers.search`, `customers.context`, `experiments.catalog`, `experiments.rows`, and `experiments.file_read`, requiring live read registration. Also expose `experiments.create/update/delete`, requiring write registration. No other tools open automatically; real-business writes, confirmations, authorization management, sending, and calendar actions are outside this endpoint.

Search reuses `visible_company_ids`; details reuse original company-owner permissions. A team-shared search hit grants no private email/profile access. Experiment tools reuse exact approved batch manifests/fingerprint validation to read/maintain fictional records while retaining owner, without exposing ordinary private details. L1–L4 remains unchanged. See [experiment sharing](experiment-data.md) for arguments/MCP integration.

## 2. Endpoints

Prefix: `/api/v1/agent/chat/`.

| Method and relative path | Purpose | Required input |
|---|---|---|
| `POST requests/claim/` | Claim pending questions; existing API | Empty JSON object |
| `POST context/` | Obtain original fixed context; existing API | request_id, scope |
| `GET tools/` | Discover request tools/JSON Schema | request_id query; optional page/page_size |
| `POST tool-reads/` | Read or maintain experiments and register evidence | request_id, name, arguments |
| `POST answers/` | Save final answer; existing API | Original six-field report |
| `GET requests/<request_id>/` | Reconcile saved state, message IDs, final citations | Path UUID |

New endpoints include `Cache-Control: no-store` on success/errors. Status reads owned pending, processing, completed, or failed requests without reclaiming, rerunning models, restoring requests, or returning complete tool history.

## 3. Discovery and execution

After claiming:

```http
GET /api/v1/agent/chat/tools/?request_id=<chat-request-UUID>&page=1&page_size=30
Authorization: Agent <token>
```

Return `contract_version: "chat-tools-v1"`, request_id, tools, count, page, page_size. Tools contain name, description, inputSchema, executionMode, category, and annotations consistent with the business registry, without internal handlers. Catalog pages default to 30, maximum 100; invalid/duplicate query parameters return 400.

Search example:

```json
{
  "request_id": "<chat-request-UUID>",
  "name": "customers.search",
  "arguments": {"q": "盛微", "page": 1, "page_size": 20}
}
```

`arguments` follows the tool Schema unchanged. Search may omit q or use existing company/archived parameters. Business pagination remains default 30/maximum 100 with count/page/page_size/results preserved. No automatic pagination, added sorting, or claims that partial pages are complete.

Search `data.results[].id` is the company UUID; use it as detail `arguments.company_id`:

```json
{
  "request_id": "<same-chat-request-UUID>",
  "name": "customers.context",
  "arguments": {"company_id": "<search-result-id>"}
}
```

Chat requests need no company_id, but details still require an explicit company; the backend does not guess. Root objects accept only request_id/name/arguments, not idempotency_key. Each call is one explicit read without backend retries.

Success shape:

```json
{
  "request_id": "<chat-request-UUID>",
  "read_id": "<current-read-UUID>",
  "tool": "customers.context",
  "status": "completed",
  "http_status": 200,
  "revision": "<original-tool-version-header-or-null>",
  "data": {"company_id": "<company-UUID>", "company_name": "盛微"},
  "evidence_items": [
    {
      "source_id": "chat-tool:<current-read-UUID>:company:<company-UUID>",
      "source_type": "customer_context",
      "title_or_label": "盛微 · 客户资料",
      "content": "<complete-JSON-text-of-actual-returned-data>"
    }
  ]
}
```

The sample data illustrates shape only. Actual details preserve original summary, grouping, context, analysis, score_detail, and other fields. Missing profiles remain null without substitute content. revision retains the original tool representation.

## 4. Evidence rules and stability

- Successful reads persist arguments, original business receipts, and current evidence_items in separate ToolRead records. Failures register no evidence and do not finish requests.
- Detail evidence is complete JSON serialization of returned data, without requerying, summarizing, or truncation. Large company data may be lengthy; Agent manages its own budget and cannot claim complete reading after truncation. Original chat/context count/character budgets remain unchanged.
- Search creates one customer_search evidence item per company containing that directory row, plus customer_search_page evidence containing original pagination counts and returned_company_ids. Empty results still retain page evidence supporting a no-results statement.
- Each successful read has a new read_id, with sources distinguished by read/company UUIDs. Rereads may obtain newer versions while old source content remains immutable. Search pages are actual results of separate queries, not guaranteed one global database snapshot.
- Original `chat/context/` HTTP structure/frozen content remains unchanged; tool evidence is not inserted into it. Tool reads do not require prior chat/context reads.
- On answer reporting, match source triples against original context/all successful reads in this request and attach content. Browsers receive only final cited sources/content, not complete ToolRead records, arguments, call history, or credentials.

**The latest user requirement governs: validate answer Schema only; registered sources are not mandatory.** Unregistered, title-mismatched, or other-request citations may be stored as metadata with empty content. Never look up bodies across requests or claim source validation. This replaces the earlier requirement to reject unregistered citations. Agent remains responsible for whether registered evidence supports answers and references the correct company.

## 5. Errors and recovery

Business-tool errors retain original HTTP status:

```json
{
  "request_id": "<chat-request-UUID>",
  "tool": "customers.context",
  "status": "failed",
  "http_status": 404,
  "error": {"scope": "tool", "code": "not_found", "detail": "公司不存在。"}
}
```

| Condition | HTTP | Handling |
|---|---|---|
| No search results | 200 | completed, count=0, results=[]; not an exception |
| Invalid tool Schema arguments | 400, scope=tool | Correct explicit arguments, then decide whether to query |
| Missing/inaccessible company detail | 404, scope=tool | Does not prove absence; report unavailable details while chat remains processing |
| Invalid request envelope/catalog parameters | 400, scope=request | Correct call structure |
| Agent authentication failure | 401, scope=request | Check employee credentials without switching identity |
| Tool not allowlisted or no longer read | 403, scope=request | Do not attempt write/confirmation entry points |
| Missing/wrong-owner request or inaccessible conversation | 404, scope=request | Stop reading through this request |
| Unclaimed/finished request | 409, scope=request | Check state without reviving the request |
| Unexpected query/database/service exception | 500, scope=request | Report service failure, not empty data; query status/check logs |

Request-level errors retain `error.code/error.detail` and add error.scope. Their top-level request_id is the HTTP log correlation ID, not the chat UUID. Only tool success/tool-level failure uses the current chat UUID at top level. `X-Request-ID` also supports log correlation.

Never invent success after network timeouts without reliable results. Agent may explicitly choose a new read, generating a new read_id; no backend automatic retries. If answer-report responses are lost, first query `GET requests/<request_id>/` for terminal state, then retransmit identical results if needed using existing answer idempotency.

Employee/request row locks cover tool queries/registration using the same order as final-answer saves. Reads/reports serialize by lock acquisition; after terminal commit no new successful business read can begin.

## 6. Answer and Agent responsibilities

Reports retain six fields: request_id/chat_prompt_version/assistant_text/citations/status/error. Versions are nonempty strings up to 100 characters, optionally workspace-chat-v1; the backend imposes no exact version or company binding.

Each citation contains exactly nonempty source_id/source_type/title_or_label; source_type is at most 80 characters. Backend does not validate body numbering, duplicate citations, or semantics. Success requires nonempty text/error=null; failure requires empty text/citations and a nonempty code/message error object. Agent handles redaction. Permissions, state, terminal immutability, and idempotency always remain.

Agent developers must:

1. Claim requests and decide whether tools are needed; ordinary greetings may be answered directly.
2. Discover Schema and map selected calls to tool-reads without ordinary Tool tokens or changing original MCP authentication.
3. Read multiple companies individually, retain distinct sources, and infer coverage from pagination fields.
4. Distinguish error.scope; detail 404 need not fail the entire answer.
5. Build citations from actual evidence_items and report through existing answers.

The repository's documented general-chat workflow does not automatically enable this tool loop. This delivery provides callable backend APIs without changing model-call counts, prompts, or experimental budgets.

## 7. Migration and validation

Apply migrations before enabling new APIs through the normal release flow:

```powershell
python backend/manage.py migrate chat
python backend/manage.py check
python backend/manage.py makemigrations --check --dry-run
```

Migration adds chat_toolread only, without request backfills, profile recalculation, or permission changes. Old-application rollback can retain the table; do not reverse-migrate away reading evidence without assessment.

Regression commands:

```powershell
python backend/manage.py test tests.contracts.test_chat tests.contracts.test_schema tests.integration.test_chat_tools tests.integration.test_chat tests.integration.test_general_chat tests.integration.test_shared_chat_worker tests.integration.test_agent_tools --noinput
python backend/manage.py spectacular --file backend/contracts/openapi.yaml --validate --fail-on-warn
```

Run `python tools/check_docs.py` in backend/. Database tests use real PostgreSQL, isolated databases, and synthetic records. Existing HTTP/worker chat tests mock model output, not real orchestration or production deployment.

## Workspace contract upgrade

No new migration is required for this upgrade. Stop the old chat worker, then start the new version. Startup explicitly fails legacy company-bound pending/processing requests with workspace_chat_required, preserving messages, completed results, and evidence. Claims also handle that employee's old tasks. Old company conversations cannot be resubmitted/retried; users explicitly ask again in workspace chat. Ordinary workspace processing is neither reset nor reassigned.

Frontend removes company-specific chat entry points; old links open workspace chat. Email drafts may come from the user's workspace, but sending still requires explicit company selection and separate review/approval. Other employees' drafts or another company's historical drafts are invalid.

CI retains/updates contract tests for five-field claims, action:tool → real HTTP query → action:answer, shared-worker employee isolation, and actual browser questions/citation reads. Backend does not validate model semantics, prompt-version enums, or citation authenticity; Schema, identity, permissions, state, and idempotency remain enforced.

Experiment writes use the [chat approval protocol](chat-approvals.md): request-bound calls freeze exact arguments and an Agent continuation, return 202 approval_required, and suspend the request. A separate browser Session decision executes the write, registers evidence, and requeues the same request atomically; its approval UUID is the idempotency key. Rejection cancels the pending request and restores chat input without rolling back earlier approved writes. This gate also applies in laboratory mode and does not affect scheduled jobs or non-chat Tool calls. See [experiment sharing](experiment-data.md) for fields, boundaries, and cleanup.
