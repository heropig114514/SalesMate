# Shared CRM worker for multiple employees

Previously, workers resolved the environment Agent token to one employee at startup, leaving other employees' batches unclaimed even while healthy. `crm_worker` now rotates synchronization, manual extraction repair, and company profiling across all active employees without requiring administrators to rebind environment tokens.

## Identity and concurrency

- `dispatch.next_owner` discovers pending work in the database and cycles through employee IDs. Fairness applies at task boundaries: it does not interrupt large mailbox batches or change message/time limits.
- Each process still has 1 synchronization channel, 2 default profiling channels, and the original 1-second poll. Multiple processes use existing transaction locks/leases. Different companies of one employee may profile concurrently; same-company exclusion is unchanged.
- `dispatch.scoped_backend` creates a random temporary AgentCredential per work unit, storing only its digest. Pass plaintext tokens explicitly to independent DjangoBackendClient instances; mailbox, ETag, company leases, and pools are instance-isolated. Never mutate `os.environ` or inherit another employee's environment mailbox into tasks.
- Tasks use original Agent HTTP APIs with unchanged authentication and owner filters. Browsers/external Agents cannot call the internal credential factory to obtain other employee identities.
- Normal/error exits close clients and revoke temporary credentials; SIGTERM waits for in-flight units. Forced termination may leave `name=crm-work-unit` digest records without persisted plaintext tokens. Administrators may remove them only after confirming no related in-flight work, never deleting active credentials in bulk by name.
- Fixed `SALESMATE_AGENT_SERVICE_TOKEN` and `SALESMATE_MAILBOX_ID` remain for existing CLI use. Shared workers no longer use them to choose employees. Internal HTTP addresses, timeouts, leases, and model settings remain unchanged.

## Deployment and diagnosis

After pushing main, existing Actions verifies and deploys automatically, with no new migrations, dependencies, or operating-system services. Draining/restarting workers discovers previously queued new-employee batches. Deployment neither retries failed batches nor transfers mailbox ownership. After authorization/model failures, fix the actual cause and require explicit user retries.

`crm_worker_started scope=all_active_owners` indicates shared mode. `crm_work_scheduled` includes channel/owner_id; `mailbox_run_claimed` includes batch ID. `crm_identity_created` / `crm_identity_revoked` log credential lifecycles without tokens. A queued page state means unclaimed, not necessarily mailbox authentication failure; diagnose using logs, service state, batch start time, and leases. active indicates process liveness only, not successful synchronization.

## Validation scope

`tests.integration.test_shared_worker` uses PostgreSQL and a real local HTTP service to verify two employees without preconfigured credentials both complete scheduling, cross-employee requests return 404, revoked tokens return 401, profile identities remain isolated, rotation is fair, disabled employees are excluded, expired leases fail explicitly, and concurrent claims are unique. Existing Gmail/QQ regressions still mock mailbox/model services. These tests do not establish real authorization/model availability; separately inspect users' original batch terminal states after deployment.
