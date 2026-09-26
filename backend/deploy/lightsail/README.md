# Lightsail automatic deployment

The CRM Worker now schedules all active employees through a shared process. After upgrading, it automatically processes queued batches for new employees without rebinding old credentials. See [shared CRM Worker](../../docs/shared-crm-worker.md) for identity isolation and acceptance checks.

Pushing `main` triggers GitHub Actions **Verify and deploy**. Deployment runs immediately and independently, without waiting for backend, Agent, contract, documentation, translation, or browser tests. Verification is a nonblocking diagnostic: failures remain in logs and summaries without cancelling deployment. Pull requests run diagnostics only; main can also be released manually. Other independent CI workflows are not dependencies of this deployment job.

## Operation

1. Actions connects to `salesmate-deploy@47.131.232.143` with a dedicated SSH key and passes only the full SHA of the current main commit.
2. The forced SSH command permits only root-installed `deploy-trigger.sh`. Arbitrary shells, port forwarding, and non-SHA input are rejected. It starts an independent transient systemd service, so disconnecting does not intentionally terminate deployment.
3. The server fetches main using its own read-only GitHub Deploy Key. Stale commits are logged and skipped; an identical version with healthy services is not redeployed. The server deployment lock and Actions concurrency group prevent overlapping releases.
4. Dependencies, candidate code, and static assets are built in an independent directory, without repeating tests, documentation checks, pip check, Django check --deploy, migration-type gates, or infrastructure round-trip diagnostics.
5. Drain the independent chat service, then stop schedulers and await in-flight work, then stop Celery consumers. The old Web remains running. Back up the database and perform actual migrations with --skip-checks; migration execution errors still terminate the release.
6. Start candidate Gunicorn, switch Nginx traffic after health checks pass, then start new consumers, schedulers, and chat and verify active process states. Drain old requests before stopping the old Web.

See [server infrastructure and online releases](../../docs/server-infrastructure.md) for initialization, directories, failure boundaries, vector interfaces, and service configuration. Background claiming pauses briefly. Automatic releases no longer block by migration operation type; check_release_migrations remains an optional manual diagnostic. Actual migration failures are not ignored, and arbitrary migrations are not guaranteed to be interruption-free.

## One-time installation and credential boundaries

Administrators initialize these paths; the website process must not control their permissions:

| Location | Purpose |
|---|---|
| `/usr/local/sbin/salesmate-deploy-trigger` | Installed deploy-trigger.sh, root-owned, 0755 |
| `/usr/local/sbin/salesmate-deploy-from-git` | Installed deploy-from-git.sh, root-owned, 0755 |
| `/var/lib/salesmate-deploy/` | Root-private Git cache, locks, state, and logs |
| `/etc/salesmate-deploy/repository_ed25519` | Read-only repository private key, readable only by server root |
| `/etc/salesmate-deploy/github_known_hosts` | SSH host public keys verified using GitHub HTTPS API metadata |
| `/home/salesmate-deploy/.ssh/authorized_keys` | Dedicated trigger public key with restrict and a forced command, managed by root |
| `/etc/sudoers.d/salesmate-deploy` | Allows the dedicated user to execute only the SHA-validation entry point |
| `/etc/systemd/system/salesmate-{crm,sales}.service.d/graceful-stop.conf` | Installed graceful-stop.conf; run daemon-reload after updates |

Register a read-only repository Deploy Key. Configure two GitHub Actions [encrypted secrets](https://docs.github.com/en/actions/concepts/security/secrets): `LIGHTSAIL_DEPLOY_SSH_KEY` is a dedicated deployment-trigger-only private key; `LIGHTSAIL_DEPLOY_KNOWN_HOSTS` pins the server's SSH identity. Do not upload the administrator SSH key, database passwords, or mailbox authorization codes to Actions.

The repository cache is fixed to `git@github.com:heropig114514/SalesMate.git` and fetches only main. Source files must be ordinary files. Submodules, symlinks, and Git ownership of .env or runtime-data directories explicitly fail. Repository updates do not replace root-level runtime data. Update deployment validation boundaries before adding runtime directories.

Business code can deploy automatically. Changes to root deployment scripts or systemd configuration require separate administrator review and reinstallation; ordinary source updates cannot directly replace privileged entry points.

## Failure and manual recovery

Failures do not implicitly retry, roll back the database, or resend mail. GitHub Actions retains failed steps; server logs record the commit, phase, and backup location. Fetch/preflight failures occur before application shutdown. Failures after background draining preserve the state and may require manual background-service recovery; failures after switching traffic also require checking active-port.

```bash
sudo cat /var/lib/salesmate-deploy/status
sudo tail -n 80 /var/lib/salesmate-deploy/deploy.log
sudo journalctl -u salesmate-deploy --no-pager
systemctl is-active salesmate-crm salesmate-sales salesmate-chat salesmate-celery@crm salesmate-celery@sales
cat /opt/salesmate/deployed-revision
```

Backups reside in `/opt/salesmate/backups/online-<SHA>-<UTC-timestamp>/` and contain database.dump, old code/environment paths, Nginx configuration, and previous-revision. Old code and independent environments remain under releases. Before recovery, inspect running processes, migration outcomes, and sending state; an administrator then explicitly chooses code, environment, or database recovery. Backups are not automatically cleaned, so inspect disk space regularly.

To pause automatic releases, disable **Verify and deploy** in GitHub Actions and confirm whether existing deployment jobs have finished. Do not pause releases by deleting mailbox configuration or stopping PostgreSQL.

## Initial chat-consumer enablement

This change adds salesmate-chat.service and deployment preflight/drain/start handling. An administrator must review and install the service, run daemon-reload and enable without starting it before code/migrations are ready, and update root-owned `/usr/local/sbin/salesmate-deploy-from-git`. If the chat service is missing, the new script fails before shutdown. Ordinary Git updates do not replace systemd configuration. The service uses the same employee token and .env, with no automatic restart after failure. See [chat deployment and recovery](../../docs/chat-integration.md). After installation, verify the deployed commit, migrations, and service state; a code merge alone does not establish a running consumer.

## Nonblocking deployment policy: 2026-09-20

Removing predeployment quality/contract gates does not change Agent business protocols or runtime permissions. Actual dependency installation, static builds, database backups/migrations, candidate Web/database readiness, Nginx configuration, and process startup must still succeed. SHA/SSH identity checks, deployment exclusion, and source/environment-file protection remain. Traffic cannot switch to a version that has not started or cannot connect to the database.

Policy updates must change both the GitHub workflow and server root-owned `/usr/local/sbin/salesmate-deploy-from-git`. Wait for any active deployment to finish, then under the same `/var/lib/salesmate-deploy/deploy.lock`, back up the old script, run bash -n, and replace it atomically as root:root 0755. Pushing the repository alone cannot change old server-side gates.
