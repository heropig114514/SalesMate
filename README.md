# SalesMate

SalesMate is a mail-understanding and customer-follow-up system for salespeople. The repository maintains Django application and independent Agent boundaries. Employee Gmail OAuth, incremental synchronization, L1–L4 analysis, and the browser workspace are integrated.

    SalesMate/
    ├── backend/          # Django service, web UI, tests, contracts, documentation, and developer tools
    │   ├── README.md     # Software-development guide and code-documentation standards
    │   ├── apps/         # Account, CRM, mail, analysis, and task interfaces
    │   ├── common/       # Logging, errors, and health checks
    │   ├── config/       # Django settings and routing
    │   ├── frontend/     # Native HTML/CSS/JavaScript workspace
    │   ├── tests/        # Framework, protocol, and business integration tests
    │   ├── contracts/    # OpenAPI and response examples
    │   ├── docs/         # Architecture, data model, standards, and Agent integration
    │   ├── tools/        # Documentation checking and browser verification
    │   ├── requirements/ # Python dependencies
    │   └── manage.py     # Django management entry point
    ├── agent/            # Gmail History, L1–L4, routable Skills, backend HTTP client, and tests
    ├── integrations/     # Business-tool HTTP SDK, CLI, and MCP client for user collaboration
    ├── test_tools/       # End-to-end test-data tools usable by developers and testers
    ├── .env.example      # Shared software and Agent configuration template
    ├── start-local.ps1   # Windows one-command environment, start, status, and stop entry point
    ├── start-local.sh    # macOS/POSIX one-command entry point using the same service-management logic
    ├── requirements.txt  # Combined dependency-installation entry point
    └── .gitignore        # Repository credential, cache, and runtime-artifact exclusions

Agent code is beside backend in agent and reads context and submits results through HTTP. It neither imports Django nor writes business data directly. Initial synchronization reads at most the latest 20 messages; later synchronization prioritizes the Gmail History cursor. Model-requiring L1 mail uses at most four concurrent requests and each result is submitted as soon as it completes. L2–L4 run per company revision. The application can use the rules placeholder for offline integration.

    Employee authorizes Gmail
    → Django records the synchronization request
    → Agent incrementally reads mail
    → Concurrent per-message L1 extraction and immediate submission
    → Django groups companies and creates analysis Jobs
    → Agent produces L2, L3, and L4 per company
    → Browser polls quietly and progressively displays results

- [Software development and startup](backend/README.md)
- [Agent implementation and data contract](agent/README.md)
- [Graph model and Agent tool handoff](backend/docs/semantic-agent-handoff.md): HTTP/MCP integration, write effects, idempotency, failure handling, and current performance boundaries; the chat Agent is not automatically integrated.
- [Gmail test-mail injector](test_tools/README.md)
- [Project structure and responsibilities](backend/docs/project-structure.md)
- [Code documentation standard](backend/docs/coding-agent-guidelines.md)
- [API contract](backend/docs/api-contract.md)
- [Agent integration](backend/docs/agent-integration.md)
- [Agent business-tool integration](backend/docs/agent-business-tools.md): dynamically authorized tool catalog, independent user delegation, version/idempotency safeguards, and human confirmation for Agent development.
- [World news map and future push contract](backend/docs/world-news.md): /world/ supplies an industry-news map, summary sidebar, and detail page. It currently uses clearly labeled synthetic demonstration data.

## One-Command Local Startup

On a computer with Python, a configured local database, and .env, one command prepares the project Python environment and runs the system. A new computer still needs the manual preparation in the table. Run every command below from the SalesMate repository root that contains this README.

| Item | Launcher handling | First-use preparation |
| --- | --- | --- |
| Python environment | Creates .venv if absent, installs the original manifests, and checks compatibility | Install Python 3.11+. Specify an interpreter with -Python on Windows or --python on macOS. |
| Database | SQLite preview creates its file database and migrates automatically. PostgreSQL can explicitly start an installed WSL/Homebrew service and check pgvector. | Choose SQLite below for preview with no database install. Full business operation requires PostgreSQL/pgvector and DATABASE_URL. |
| Application configuration | If .env is missing, generates a template and random Django key then requests configuration; never overwrites an existing file. | Fill configuration for the selected mode. Create a local ordinary account or disable automatic sign-in and register in the browser. |
| Real external services | Starts the Worker selected by existing ANALYSIS_PROVIDER | Supply model key and model name. Configure Google OAuth and authorize Gmail when using Gmail. |
| Web and processes | Starts in background, health checks, opens browser, reports status, and cooperatively stops | No npm, frontend compilation, or manual HTML opening is required. |

### SQLite Local Preview, Windows/macOS

To inspect the frontend, register/sign in, and use basic business behavior, SQLite needs neither PostgreSQL, Homebrew, nor WSL. It does not claim complete business compatibility with SQLite.

The first launcher run creates .venv, installs dependencies, and generates .env. If it reports Created .env ... Configure DATABASE_URL, dependencies are ready; edit the generated repository-root .env. Change existing values rather than appending duplicates, and retain the generated Django key and other configuration:

    DATABASE_URL=sqlite:///backend/db.sqlite3
    LOCAL_DEBUG_AUTO_LOGIN=False
    ANALYSIS_PROVIDER=rules

This explicitly selects the rules preview and does not call a real model. Automatic sign-in is disabled so the first user can register a normal account in the browser; no demo account or provision_local run is needed. With an existing real-model configuration, change provider only after choosing this preview mode.

On macOS:

    bash start-local.sh

On Windows:

    powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1

Do not add --brew-service or -WslDistro. The launcher creates backend/db.sqlite3, applies migrations, and opens the [local workspace](http://127.0.0.1:8000/); register an account in the sign-in screen. Use the same command thereafter. With existing managed services, stop before start after changing configuration. SQLite is a separate data file and does not import PostgreSQL accounts or business data.

**Limitations:** pgvector similarity retrieval is unavailable. Mail correction and historical-extraction upgrade use JSON queries that are not adapted. Concurrent claims and multiple Worker writes can conflict under SQLite locks. Do not depend on these capabilities in preview; real Gmail/model workflows and multi-user concurrency use PostgreSQL. Registration, sign-in, pages, and some basic business functions have SQLite test coverage, but this does not promise all business functions. See [SQLite preview](backend/docs/local-development.md#sqlite-local-preview) for results and scope.

### Windows

If PostgreSQL is in configured WSL Ubuntu-24.04, the local development setup:

    powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1 -WslDistro Ubuntu-24.04

The script keeps the WSL database session, opens the [local workspace](http://127.0.0.1:8000/) after readiness, and reuses existing .env, accounts, and data. It changes neither model mode nor imports samples. Replace Ubuntu-24.04 if the distribution differs. Omit -WslDistro for a Windows-local database:

    powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1

Check status or stop:

    powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1 -Action status
    powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1 -Action stop

Services run in the background after startup; closing the startup terminal does not stop them. Repeated startup reuses managed processes. A port-8000 conflict or missing dependency reports an explicit error without changing port, database, or model mode. Logs are in artifacts/local-server; first dependency-install logs are .venv/salesmate-install*.log. Stop before starting after code updates. See [Windows one-command startup](backend/docs/local-development.md#one-command-startup-windows) for prerequisites, logs, and stop semantics.

### macOS

Run from the project directory without PowerShell or WSL:

    bash start-local.sh

To start an already installed Homebrew PostgreSQL service too, specify the actually used version; the example uses 16 and does not install or switch versions:

    bash start-local.sh --brew-service postgresql@16

The script uses .venv/bin/python and supports system Bash 3.2. It creates the environment for the local Python architecture and does not hard-code Apple Silicon or Intel Homebrew paths. With an existing database, Postgres.app, or another local PostgreSQL, start it yourself and omit --brew-service. Both platforms use port 8000, root .env, health checks, Worker arguments, and failure semantics. Do not copy .venv between operating systems.

    bash start-local.sh status
    bash start-local.sh stop
    # Optional: select Python or prevent automatic browser opening
    bash start-local.sh --python /path/to/python3 --no-browser

See [macOS one-command startup](backend/docs/local-development.md#one-command-startup-macos) for first environment/account preparation, Homebrew behavior, and verification scope. Platform CI verifies locks, cooperative stopping, background sessions, and empty-dependency-environment initialization; it does not verify real models, mailboxes, or the complete database path.

### Manual Startup and Hot Reload

For manual startup with code hot reload, run from the repository root:

    .\.venv\Scripts\Activate.ps1
    python -m uvicorn --app-dir backend config.asgi:application --host 127.0.0.1 --port 8000 --reload

On macOS, activate with source .venv/bin/activate; remaining manual Python commands are identical.

Open the [workspace](http://127.0.0.1:8000/). The local default enters with the existing demo ordinary account. Environment initialization, login restoration, and verification commands are in the software-development guide.

All software changes follow the development principles in backend/README.md, maintaining code, comments, directories, and documents together. From backend, run python tools/check_docs.py. When changing the checker, also run python tools/test_check_docs.py.

Django and Agent both read repository-root .env. The database explicitly sets DATABASE_URL; SQLite is never selected automatically. Real-model integration uses ANALYSIS_PROVIDER=agent; analysis versions are managed by corresponding agent/skills/*/SKILL.md. The one-command entry starts CRM, chat, and sales Workers in that mode. With the manual Web command above, use the software-development guide to start required Workers in other terminals.

Synchronization batches and per-message status persist to the database. An independent crm_worker schedules mailbox synchronization and company profiles concurrently. The backend supplies non-business hiding, manual review, progress, and explicit retry interfaces; interruptions retain failed records.

## Sales Business Expansion

/business/ provides customer relationships, business documents, follow-ups, team authorization, attachments, and action confirmation. The assistant sidebar supports persisted conversation and drafts. backend/apps/sales/ manages relation schema and business transactions without changing the original Agent protocol. See [sales expansion](backend/docs/backend-expansion.md) for models, interfaces, verification boundaries, and Worker/OAuth configuration. Read-only sales chat is integrated through independent apps.chat and supports task state, evidence snapshots, citations, and chat_worker. See [chat integration](backend/docs/chat-integration.md) for startup and verification boundaries. Autonomous tool selection is not open. Real Gmail send and calendar execution require explicit write-permission authorization.

## Durable Mail Processing

Synchronization requests return a batch ID. The independent crm_worker processes each message and company profile; the workspace supplies accurate progress, failed-mail retry, and manual review. See [processing integration](backend/docs/processing-integration.md) for upgrade and startup.
