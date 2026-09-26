# Open Laboratory Mode for Algorithm Integration

This mode opens all business data, including non-KGSEED data, by the project owner's explicit choice. Enable it only with LAB_OPEN_ACCESS=True and WORKSPACE_OWNER_ONLY=False. It is independent of Django DEBUG, user roles, and synthetic-batch markers, and is disabled by default. Personal workspaces were restored on 2026-09-21; local .env and production /opt/salesmate/shared/runtime.env enabled this mode on 2026-09-22 for integration and imported a synthetic support batch into production. Configuration loads when deployment restarts services. The authoritative state is lab_open_access from /api/v1/session/. Personal-workspace isolation takes precedence; see [Account Isolation and Fact Compatibility](workspace-isolation.md).

## Invocation

Access /business/ and /experiments/ in the browser without sign-in. Existing signed-in users, new users, and anonymous visitors can all view cross-account business records. Lists remain paginated; include archived data through archived=all or the page archive filter.

Existing /api/v1/ HTTP routes require no Cookie, Authorization, CSRF, or dedicated Tool credential:

    curl https://milkdragon.dev/api/v1/sales/directory/
    curl https://milkdragon.dev/api/v1/agent-tools/catalog/
    curl -X POST https://milkdragon.dev/api/v1/agent-tools/call/ -H 'Content-Type: application/json' -d '{"name":"products.list","arguments":{}}'

When enabled, API responses include X-Lab-Open-Access: true and /api/v1/session/ returns lab_open_access: true.

The existing MCP is a local stdio bridge forwarding the same HTTP Tool API, not a separately deployed remote MCP URL. After updating to this code, install integrations/salesmate_tools/requirements.txt and configure the repository-root MCP process with command python, args -m integrations.salesmate_tools.mcp_server, and SALESMATE_TOOLS_URL=https://milkdragon.dev. Python can call ToolClient("https://milkdragon.dev") directly. The laboratory catalog declares idempotency_required: false, so MCP does not require a key for every write. A caller can still provide a UUID to identify duplicate submission; conflicting content under an existing key remains rejected and is not retried.

## Identity and Data Ownership

Identity establishes data ownership and logging only; it does not prove caller identity in this mode. Selection order is public X-Lab-User: tst1 header, existing signed-in session, original owner from recognizable Agent/Tool credentials, then default algorithm-lab account.

The default laboratory account is created on first use and has no usable password. Rename it with LAB_DEFAULT_USER. MCP may use SALESMATE_TOOLS_USER=tst1 and Python may use ToolClient(url, user="tst1"), both without password or token.

Existing business-record edits retain the original owner while audit records identify the operator. Ordinary new records belong to selected identity; new shared-experiment records retain the batch's original owner. Account-specific data, including company setup, sales profiles, onboarding material, and Worker queues, follows selected identity. Worker queues still claim by account to preserve execution semantics.

## Relaxed and Retained Rules

| Item | Laboratory mode behavior |
| --- | --- |
| Browser, CRM Agent, Tool/MCP authorization | No sign-in; invalid old credentials do not block business access. |
| Customers, mail, business data, knowledge, conversations, attachments | Cross-account access, including non-KGSEED data. |
| Owner/team administration | Ordinary maintenance no longer blocks cross-account operations. |
| If-Match, Tool revision, experiment expected | Optional; expired versions and old fingerprints are not checked. |
| Tool scope, expiry, revocation | Inactive; full catalog is published. |
| Tool internal-management confirmation | New calls execute directly. |
| Agent L2/L3/L4 save lease | Submission works without a lease header; provided Worker leases still verify runtime state. |
| Deletion | experiments.delete removes shared synthetic records; ordinary resources retain existing archive/delete interfaces. |
| Data structure, amount, foreign key, references, state | Business correctness checks remain; running tasks and frozen documents cannot be altered freely. |
| External send/calendar actions | Existing preparation, explicit confirmation, and execution remain; public identity selects owner account. |
| Secrets/admin | Passwords and OAuth secrets remain private; mailbox-syncs/claim still requires Agent machine credentials; Django admin still requires administrator sign-in. |

Sign-in/registration password and CSRF flows remain, but business APIs do not require them. Real mailbox-provider authorization remains necessary externally. Chat Agent tool selection, knowledge budget, model prompts, and scoring parameters do not change; full tool capability is directly available through Tool/MCP.

KGSEED views still identify only manifest-registered synthetic records and never label real records synthetic. Ordinary interfaces may edit them in laboratory mode and reads return current fingerprints; deleted manifest rows no longer return. Restoring production mode strictly validates manifests again. Synthetic rows changed through ordinary business paths require manifest review/update before strict display or batch cleanup resumes. Experiment CRUD maintains target fingerprints and audit; cleanup itself is not relaxed.

## Restore Production Authorization

Set LAB_OPEN_ACCESS=False in runtime configuration and restart Web, CRM/Chat/Sales Workers, and Celery. No code deletion or database migration is needed. X-Lab-User immediately loses access; original Session/Agent/Tool authentication, owner/team isolation, version checks, and Tool-scope checks resume. Development tests verify both enabled and disabled paths through configuration overrides.
