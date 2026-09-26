# Project structure and responsibilities

Updated: 2026-09-14. Application software resides under repository `backend/`: Django, native frontend, tests, contracts, documentation, and developer tools. Independent Agent code is in sibling `agent/`; developer/test data tools are in `test_tools/`. Application and Agent share root .env and dependency entry points.

```text
SalesMate/
├── README.md                         # Repository overview and responsibility entry points
├── .gitignore                        # Repository credential/runtime-artifact exclusions
├── agent/                            # Gmail History, L1–L4, routable Skills, CLI, Agent tests
├── integrations/salesmate_tools/      # Business-tool HTTP SDK, CLI, stdio MCP, protocol tests
├── test_tools/                       # Independent Gmail test-email injector and instructions
├── .env.example                      # Shared configuration template
├── requirements.txt                  # Shared dependency entry point
└── backend/                          # Application root and command working directory
    ├── README.md                     # Development instructions and comment conventions
    ├── manage.py                     # Django management commands
    ├── requirements/                 # Python dependencies
    ├── config/                       # Django, database, pages, root routes
    ├── apps/accounts/                # User model
    ├── apps/crm/
    │   ├── models.py                 # Mailboxes, companies, emails, versions, analyses, jobs
    │   ├── serializers.py            # Protocol payload and form validation
    │   ├── response_schemas.py        # OpenAPI structures for Agent queries/batch responses
    │   ├── access.py                  # User ownership, service credentials, version errors
    │   ├── ingestion.py               # Persistence, grouping, resubmission, sync state
    │   ├── jobs.py                    # Enqueueing, merging, claiming, leases, reports
    │   ├── results.py                 # L2/L3/L4 validation, persistence, caching
    │   ├── selectors.py               # Context, page projections, statistics
    │   ├── gmail_oauth.py             # Employee authorization, sync claims, credentials
    │   ├── worker.py                  # Independent mailbox/profile execution units
    │   ├── rules.py                   # Replaceable explicit rule placeholders
    │   ├── views.py / urls.py         # Browser and Agent HTTP entry points
    │   ├── migrations/                # Tables and uniqueness constraints
    │   └── management/commands/       # Local account initialization
    ├── apps/sales/                    # Sales relations, permissions, transactions, actions, sales_worker
    ├── apps/chat/                     # Read-only chat jobs, evidence, citations, knowledge versions, chat_worker
    ├── apps/agent_tools/              # Tool registry, independent user delegation, idempotency, confirmation
    ├── common/                       # Logging, errors, health checks
    ├── tests/                        # Framework, contract, business, permission tests
    ├── frontend/
    │   ├── index.html                # Unified workspace, emails, company details, review
    │   ├── business.html              # Business management/action review under shared navigation
    │   └── assets/                   # Shared workspace.js/CSS shell, business modules, same-origin API
    ├── contracts/openapi.yaml        # Backend-generated contract
    ├── docs/                         # Status, protocols, data model, Agent integration
    ├── tools/                        # Documentation checker and browser smoke tests
    └── artifacts/                    # Local artifacts/screenshots, excluded from Git
```

`backend/` defines the application's maintenance boundary; `backend/frontend/` separately organizes page code. Django entry points/Python imports remain unchanged; frontend/contracts resolve relative to the application root. Both sides read only root `.env`. Former backend/.env local PostgreSQL/debug settings were migrated without replacing the database. All application documentation, including application-side Agent APIs and historical design summaries, resides under `backend/docs/`.

crm persists email/L1–L4 analysis; sales handles transactions, collaboration, and tool actions; accounts owns the user model. Agent currently submits individual emails through the existing array API, retaining HTTP contracts/storage structures. L1/L3 model instructions reside in `agent/skills/*/SKILL.md`; workflows read versions/output limits from Skill metadata.

Browsers neither access databases nor fabricate business states. Agent neither imports Django nor writes business tables directly. Rules run after explicit business changes/analysis requests in rules mode, never as implicit network/model failure fallback.

Run `python tools/check_docs.py` in `SalesMate/backend/`; its default scope includes all Python files here, including migrations, tests, tools, and package initializers. When editing the checker, also run `python tools/test_check_docs.py`. Manually review JS/CSS/HTML and browser_smoke.cjs descriptions/directories.

Commit source, migrations, contracts, and documentation together; exclude `.env`, `.local-access.json`, logs, databases, and runtime artifacts. sales contains transaction maintenance, sending/calendar confirmation, conversation drafts, and team permissions; chat contains read-only chat/internal knowledge versions. External retrieval, translation, and autonomous tools await later integration. RAG, LangGraph, Celery, and containers were not introduced in this documented update.

Added `processing_models.py`, `processing.py`, `classification.py`, and `processing_views.py` manage persistent batches, stages, and review. `dispatch.py` rotates employees/manages temporary HTTP identities; `management/commands/crm_worker.py` consumes shared tasks; `classify_emails.py` backfills historical classification.
