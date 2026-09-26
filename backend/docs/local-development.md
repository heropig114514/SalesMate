# Local development and integration

Updated: 2026-09-20. See the [application README](../README.md) for complete initialization. This page describes launchers and local runtime boundaries.

## One-command startup: Windows

From SalesMate root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1 -WslDistro Ubuntu-24.04
```

This machine's PostgreSQL 16/pgvector resides in that WSL distribution; root `.env` retains `127.0.0.1:5432`. Startup creates missing `.venv`, installs the original four requirements manifests, and runs `pip check`, skipping reinstalls when manifests are unchanged. Installers explicitly use UTF-8; PowerShell retains a UTF-8 BOM for Windows PowerShell 5.1 compatibility. `-Python '<absolute-interpreter-path>'` selects initial environment creation; `-NoBrowser` suppresses browser launch.

Sequence: exclusive runtime lock/port 8000 check → retain WSL stdin session/start PostgreSQL → validate local settings/database/pgvector → Django `check`/`migrate --noinput` → Web readiness → applicable workers → workspace. WSL systemd alone does not keep the distribution alive; the held session lasts until all application processes exit. Initial forwarding/readiness allows bounded waits, never business retries or automatic failed-process restarts.

`ANALYSIS_PROVIDER=agent` starts `crm_worker`, `chat_worker`, and `sales_worker`; `rules` starts Web/sales only without claiming model-chat availability. PostgreSQL also starts independent `graph_worker` for [business graphs](knowledge-graph.md), without LLM calls; SQLite does not. Worker parameters remain unchanged and may consume existing queued/approved work. Launchers create no sync/send/chat/seed tasks. Only DEBUG, `TASK_EXECUTION_MODE=local`, and loopback PostgreSQL/explicit SQLite are allowed, preventing accidental production use.

```powershell
# Inspect supervisor/service PIDs and Web/database/static-resource health.
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1 -Action status
# Drain workers, stop Web, and release this launcher's WSL session.
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1 -Action stop
```

Services run in background; closing the launcher terminal does not stop them. Repeat startup reuses managed services. Unrelated port-8000 occupancy fails without killing processes/changing ports. `stop` waits at most 30 seconds and reports unfinished tasks for further status checks, without force-kill/resend. It does not stop PostgreSQL, although WSL may sleep after its final session closes. After code edits, stop/start; no hot reload, preserving Web/worker code consistency.

Ignored `artifacts/local-server/` contains `launcher.log` for environment/migrations/lifecycle and `web.log`, `crm.log`, `chat.log`, `sales.log` for processes. `state.json` stores state/exit codes. Cold starts overwrite previous same-name logs; preserve diagnostics first. Dependency logs: `.venv/salesmate-install.log` and `.venv/salesmate-install-error.log`.

New machines require Python 3.11+ and a database; `-WslDistro` requires an installed distribution/PostgreSQL/pgvector. No WSL downloads/system-network changes. Missing root `.env` generates a random-Django-key template and stops for database/mode credentials; existing files are never overwritten. Missing auto-login users prompt explicit `provision_local` initialization or disabling auto-login and registering via UI. Users supply Google OAuth/real-model keys.

Isolated Windows launcher tests access no databases/external services; shell tests run on POSIX:

```powershell
.\.venv\Scripts\python.exe -X utf8 backend/tools/test_local_server.py
```

## One-command startup: macOS

Full operation requires Python 3.11+, local PostgreSQL/pgvector, database credentials, and root `.env`. Preview may use [SQLite](#sqlite-local-preview). Scripts support system Bash 3.2 without installing Homebrew/databases or requiring PowerShell/WSL; the shared backend rejects `--wsl-distro` on Mac. From repository root:

```bash
bash start-local.sh
# Only for installed Homebrew PostgreSQL; specify the actual formula.
bash start-local.sh --brew-service postgresql@16
# Manage running services.
bash start-local.sh status
bash start-local.sh stop
```

macOS/Windows share Python supervision, checks, migrations, health checks, and worker parameters. Platform adaptations use `.venv/bin/python`, POSIX `flock`, independent sessions, and optional Homebrew startup. No hardcoded `/opt/homebrew` or `/usr/local`; locate `brew` via PATH. Independent sessions/redirected streams detach services from launcher terminals; stop drains workers before Web.

`--brew-service` accepts only `postgresql` or `postgresql@version` and calls `brew services run` without login startup entries, following [Homebrew documentation](https://docs.brew.sh/Manpage#services-subcommand). Services must already exist; no database/role/extension/password creation or `.env` address changes. Databases must listen on loopback. Wait at most 30 seconds for TCP; authentication/SQL errors fail directly. Application stop leaves potentially shared Homebrew PostgreSQL running. Start Postgres.app/other installations manually and omit this option.

First execution creates missing `.venv` and installs original dependencies. Missing `.env` generates a random key/template and exits for configuration. Edit `DATABASE_URL` and mode credentials, then initialize an ordinary account if needed:

```bash
.venv/bin/python backend/manage.py migrate
.venv/bin/python backend/manage.py provision_local --username demo --mailbox-address your-account@gmail.com
bash start-local.sh
```

Do not rerun `provision_local` when accounts/`.local-access.json` exist. For browser registration explicitly set `LOCAL_DEBUG_AUTO_LOGIN=False`. Use `bash start-local.sh --python /path/to/python3` for initial interpreter selection and `--no-browser` to suppress launch. Existing `.venv` without POSIX Python fails rather than overwriting Windows environments. Recreate environments between Intel/Apple Silicon instead of copying them.

Dependency digests: `.venv/salesmate-unix-requirements.sha256`; reinstall only on manifest changes, logging to `.venv/salesmate-install.log`. Web/worker logs/state remain in `artifacts/local-server/`. Workspace: `http://127.0.0.1:8000/`. Stop timeouts, port conflicts, repeat startup, and no-retry semantics match Windows.

`No broken requirements found` confirms Python dependencies only, not application/database readiness. Failures print current-run redacted diagnostics; absent current diagnostics point to logs without reusing historical errors. If older versions print only `Start failed. Inspect ...`, read logs before reinstalling Python or overwriting `.env`:

```bash
tail -n 80 artifacts/local-server/launcher.log
```

`Created .env ... Configure DATABASE_URL` means configuration is required. `OperationalError` requires checking database connections; missing `vector`/accounts follow explicit diagnostics. Diagnose from current logs, not terminal summaries alone. Do not share `.env`, keys, or complete connection strings.

Cross-platform checks:

```bash
/bin/bash -n start-local.sh
python3 -X utf8 backend/tools/test_local_server.py
```

Tests use Python's standard library, explicitly mocking Django/Homebrew; shell initialization installs empty requirements. CI `Local launcher` runs Windows/macOS/Linux with Python 3.11/3.12; POSIX uses host `/bin/bash`. Only actual passed jobs establish platform validation, not real Homebrew, full dependencies, databases, mailboxes, or models. Existing real Windows services receive separate regression checks.

## SQLite local preview

For explicitly chosen lightweight previews, use Python's SQLite. If `.env` is missing, run the platform launcher to generate it; this exit awaits configuration, not dependency repair. Replace these existing root entries while preserving other settings/random keys:

```dotenv
DATABASE_URL=sqlite:///backend/db.sqlite3
LOCAL_DEBUG_AUTO_LOGIN=False
ANALYSIS_PROVIDER=rules
```

Run `bash start-local.sh` on macOS or `powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1` on Windows without PostgreSQL startup options. The launcher applies all migrations, creates ignored `backend/db.sqlite3`, and starts Web plus the existing rules-mode sales worker. Open `http://127.0.0.1:8000/` and register an ordinary account. No demo precreation, mailbox setup, or `provision_local` is needed. Empty lists are normal; no automatic seeds.

This explicit rules preview calls no real models. Stop/start to load changed settings. Switching databases does not migrate PostgreSQL data/accounts/authorization; restore original settings to switch back. Never share `.env`, credentials, or SQLite business data.

Known limitations retain original failures without substitute algorithms, retries, or reduced concurrency:

- pgvector cosine SQL is PostgreSQL-only; SQLite cannot run vector retrieval.
- Lineage invalidation for manual correction, failed-extraction updates, and historical upgrades uses JSON-array containment not adapted to SQLite.
- SQLite lacks required PostgreSQL row locks; concurrent claims, shared workers, and multiprocess writes may encounter locks. Do not validate multiuser concurrency or real external actions here.

Windows isolated-SQLite validation on 2026-09-20: all migrations passed. Real Uvicorn HTTP passed health, home/business/company-settings/world-news pages, JavaScript, CSRF registration, and company create/list checks. A temporary port isolated existing services without changing defaults. Complete backend tests ran 265 cases with 22 errors/3 failures in JSON/vector/concurrency paths. Startup is not full compatibility. Tests neither read nor modify actual PostgreSQL business data; models/mail use existing test boundaries. No real application validation occurred on the user's Mac.

## Environment

- Python 3.11+.
- Install from root `requirements.txt`.
- Django/Agent read root `.env` only.
- `DATABASE_URL` is required; local/template settings use PostgreSQL, with SQLite only for explicit previews above.
- Same-origin native HTML/CSS/JavaScript needs no separate install/build.

Initial setup from repository root:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python backend/manage.py migrate
python backend/manage.py provision_local --username demo --mailbox-address your-account@gmail.com
```

`provision_local` creates an ordinary developer user, mailbox, and Agent credentials. It writes Agent token/mailbox UUID/local username to root `.env`, and local login credentials to `backend/.local-access.json`; both are Git-ignored.

## Startup

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn --app-dir backend config.asgi:application --host 127.0.0.1 --port 8000 --reload
```

Entry points:

- Workspace: `http://127.0.0.1:8000/`
- API docs: `http://127.0.0.1:8000/api/docs/`
- Liveness: `http://127.0.0.1:8000/api/v1/health/live/`
- Default database readiness: `http://127.0.0.1:8000/api/v1/health/ready/`

`LOCAL_DEBUG_AUTO_LOGIN=True` establishes an ordinary `LOCAL_DEBUG_USER` session only with DEBUG/loopback requests. Set False/restart to test login. Browser writes retain CSRF; Agent uses independent service tokens.

## Agent integration

QQ needs no Google OAuth callback; follow [QQ integration](qq-mailbox.md) for vault keys, migrations, and page connections. Gmail remains; both share the current employee's `crm_worker`.

Create a Google Cloud Web application OAuth client with `http://127.0.0.1:8000/api/v1/mailboxes/gmail-callback/` as Authorized redirect URI. Set `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, and `GOOGLE_OAUTH_REDIRECT_URI` in root `.env`, then connect Gmail from the employee workspace.

Callbacks/page synchronization persist batches and quietly poll progress. After HTTP startup, run `python backend/manage.py crm_worker` separately. Web restarts preserve queued work; employees retry explicitly.

Legacy CLI is for separate stepwise debugging, never concurrent use of the same mailbox with new workers:

```powershell
python -m agent.main --sync-authorized-mailboxes-once
```

It claims once, initially scans recent emails then History increments, runs up to four concurrent L1 calls with individual submissions, and completes L2–L4. Full per-email state/read-failure isolation uses new workers. Company Jobs retain one-shot debugging:

```powershell
python -m agent.main --process-jobs-once --job-limit 10
```

Without Gmail/Bailian configuration, explicitly switch `ANALYSIS_PROVIDER` to `rules` temporarily and restart for demonstrations/simulated incoming mail. Rule results support UI/backend integration only.

## Checks

### Local acceptance examples

For temporary business-page data:

```powershell
python backend/manage.py seed_sales_demo --username demo
```

Requires DEBUG and an existing ordinary employee. Creates 4 fictional companies marked `【验收示例】`, 8 contacts, 6 products, and related opportunities, quotes, orders, tickets, follow-ups, conversations, manual messages, and drafts. Numbers begin `DEMO-V1-`. Order states are acceptance scenarios, not real completion/fulfillment evidence. Quotes have no actual send records; no emails, meetings, or model scheduling occur.

Batch `sales-demo-v1` IDs/counts reside in `acceptance_seed_completed` audits. Repeats return the original manifest without overwriting edits; conflicts roll back imports while preserving existing data. Records enter employee statistics, so filter marked companies. No automatic expiration/deletion; later cleanup can use exact manifests.

### Automated checks

```powershell
python -m unittest discover -s agent/tests -p "test_*.py"
python backend/manage.py test tests
python backend/manage.py check
python backend/manage.py makemigrations --check --dry-run
python backend/manage.py spectacular --file backend/contracts/openapi.yaml --validate --fail-on-warn
python backend/tools/check_docs.py
```

Tests do not access Gmail/Bailian. Real OAuth, networking, account balance, and model quality require manual smoke checks.

## Local post-merge runtime

Local development retains `D:/my_files/conda_envs/django_env` and WSL PostgreSQL; former backend/.env moved to root .env. Provider, database identity, and timezone remain. customer-analysis Skill manages analysis versions; the old Web thread switch was removed, persistent-batch migrations applied, and historical emails reclassified. Tests mock SDKs; users/.local-access.json remain.

Installing requirements into shared Python reported missing dependencies for unrelated packages. Project tests are separate and do not establish complete shared-environment consistency.

## Persistent-processing upgrade

Run `python backend/manage.py migrate` and preview historical classification before explicit `classify_emails --apply`. After HTTP startup, run `python backend/manage.py crm_worker` separately; see [processing integration](processing-integration.md) for requirements/rule boundaries. Web no longer starts background Agent threads.
