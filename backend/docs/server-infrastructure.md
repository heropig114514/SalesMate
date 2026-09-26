# Server Infrastructure and Online Release

Production uses PostgreSQL 16, pgvector, Redis, Celery, Gunicorn with Uvicorn Workers, Nginx, and systemd. Local checks unrelated to infrastructure require only Python dependencies. Complete database/message integration tests run in CI or isolated servers and do not require local Redis/Celery services. Vector migrations require PostgreSQL with pgvector; never run development tests against production database.

## Task-Execution Boundaries

TASK_EXECUTION_MODE=local is the established local thread/process path. Servers explicitly use celery and must supply CELERY_BROKER_URL and CELERY_RESULT_BACKEND. Connection failure never falls back to local execution.

The database continues to store business queues, employee ownership, approval state, leases, and final results. The CRM scheduler fairly rotates employees, submitting to crm at existing limits of one synchronization and two profile jobs; sales scheduler submits item-by-item to sales. Consumers use three/one processes. Messages contain only type and primary key; workers revalidate employees at execution and CRM still uses revocable employee HTTP credentials.

Redis databases 0/1 hold transport and transient results. Redis listens locally only, uses password, AOF everysec, and noeviction. Consumed results are deleted and unconsumed results expire after one day. Redis never replaces business database; AOF everysec does not guarantee every unflushed message after machine failure.

Celery acknowledges before execution, prefetches one, does not automatically retry business work/publication, and does not reconnect automatically after connection failure. After force kill, machine failure, or lost acknowledgement, inspect database and execution state. A timeout for submitted external mail cannot be treated as unsent and resent automatically. Existing sales approved/running/unknown boundaries remain.

## Vector Interface

apps.vectors.services.put_document accepts employee, space, source, model version, text, and actual embedding. search_documents performs exact cosine retrieval for the same employee, space, model, and dimension. Model versions remain separate; changed dimension for a model is rejected. No ANN index exists.

This change adds storage/retrieval only. It selects no embedding model by default, bulk-processes no historical mail, and does not change CRM/Agent context-retrieval semantics. Before business semantic search, define embedding model, material scope, and update/delete rules. Test two-dimensional vectors are verifiable synthetic input.

## Release Layout and Process

- /opt/salesmate/releases/<SHA>-<random suffix>/: Code and isolated virtual environment per release; old releases remain for manual recovery.
- /opt/salesmate/shared/: Protected runtime.env, media, private_uploads; ordinary release does not overwrite.
- /opt/salesmate/slots/8001/ and 8002/: Fixed-release links; each Gunicorn has two ASGI processes.
- /opt/salesmate/app and venv: Links for current background-task version.
- /etc/nginx/snippets/salesmate-release.conf: Current proxy target and same-release static path.
- /opt/salesmate/active-port and deployed-revision: Traffic-cutover state and complete successful version.

After administrator review, first run deploy/lightsail/bootstrap-infrastructure.sh <reviewed code directory>; Redis and postgresql-16-pgvector are prerequisites. Initialization copies original code into an isolated directory and validates Gunicorn from it; old installation remains backed up. Confirm old Nginx requests drain before stopping/disabling old salesmate-web. Celery consumers do not start before the first new release succeeds; the original scheduler continues old logic.

Routine release: main push triggers deployment and non-blocking CI diagnostics in parallel; build new directory/dependencies/static files; stop schedulers and wait for tasks, then consumers; back up database; run actual migration; start candidate Web and probe database; smoothly cut Nginx traffic; start consumers, schedulers, chat and confirm active; stop old Web after old Nginx requests drain.

Old Web serves during build/migration/draining, with a short window where tasks are not claimed. Candidate failure never cuts traffic automatically. Automatic deployment no longer runs documentation/contract checks, pip check, Django check --deploy, migration-type gating, or Redis/Celery roundtrip diagnostics. check_release_migrations remains manual. Actual migrate uses --skip-checks; database failure still ends release and DDL lock wait remains five seconds. Backup, service start, and basic readiness remain but do not promise zero downtime for every database change.

Deployment failure never rolls back database or retries external actions. Inspect /var/lib/salesmate-deploy/status and deploy.log plus active-port, deployed-revision, service state, and backup directory. If background startup fails after cutover, the website may serve the new release while successful-version record remains old; do not choose recovery from commit files alone. Old release directories and backups are not deleted automatically.

## Verification and Operations

    systemctl is-active salesmate-web@8001 salesmate-web@8002
    systemctl is-active salesmate-crm salesmate-sales salesmate-celery@crm salesmate-celery@sales redis-server
    cd /opt/salesmate/app
    sudo -u salesmate /opt/salesmate/venv/bin/python backend/manage.py check_infrastructure --workers
    curl -fsS https://milkdragon.dev/api/v1/health/ready/

Normally only the active-port Web instance runs; the other is expected inactive. Public ready checks database only. Full Redis/vector/consumer checks use the management command and do not change the API contract.

Implementation references: [Celery Django integration](https://docs.celeryq.dev/en/stable/django/first-steps-with-django.html), [Celery Worker shutdown](https://docs.celeryq.dev/en/stable/userguide/workers.html), [Gunicorn signals](https://gunicorn.org/signals/), and [pgvector Python interface](https://github.com/pgvector/pgvector-python).
