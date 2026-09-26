# Analysis Conflicts, Worker Connection Lifecycle, and News-Source Maintenance

Version v1.2.2 adapts Agent submission 851d9e8 (analysis-v5). It does not change Job status enumerations, leases, model budget, or retry rules.

## Analysis Conflicts

L2/L3/L4 saves continue to validate revision, lease, source, and immutable payload. The following conflicts still return HTTP 409, with error.code:

| Error code | Cause |
| --- | --- |
| analysis_revision_changed | Read revision or Job revision differs from the company's current material. |
| analysis_lease_required / analysis_lease_invalid | A complete lease is absent or the lease credential does not match. |
| analysis_lease_expired / analysis_job_inactive | Lease expired or Job already ended. |
| analysis_snapshot_changed | Current input snapshot is absent, invalid, or inconsistent with business context. |
| analysis_input_conflict | The same input_version has different content. |
| analysis_result_conflict / analysis_score_conflict | The same immutable key has different analysis/score payload. |

Invalid version format still returns the existing 400. The common conflict code for other business APIs is unchanged. Agent SDK requires no new Job state: it can log the specific backend_code and retain a readable message in existing failed/job_failed reports. Detail pages continue to show job_error.message; historical generic errors are not rewritten automatically.

Conflict logs include stage, company ID, normalized Job ID, input/current revision, and SHA-256 fingerprint of input version. They exclude lease tokens and business bodies. Logs support diagnosis and do not write additional business state within a rolled-back transaction.

Explicit reanalysis continues through POST /api/v1/companies/{id}/analyze/. Pending Jobs for a company merge, and an effective running Job for the same revision is reused. An old result cannot be resubmitted by refreshing If-Match. Added regression coverage rejects old results, shows failure messages, deduplicates repeated clicks, and rebuilds subsequent input from the latest revision.

## Worker Database Lifecycle

During long mailbox/model HTTP calls, a local database connection can expire or fail. After an external call and before saving final state, reporting failure, or revoking temporary identity, the Worker calls Django close_old_connections() to clean old connections under existing connection configuration.

This does not alter CONN_MAX_AGE or retry mailbox, model, or failed transactions. If the database remains unreachable, failure is retained and report failure is logged; original lease expiry explains unfinished batches. An isolated test reproduces the issue by closing the actual underlying PostgreSQL connection in the thread: before the change a batch stayed running; after it, normal work completes and failed work persists failed while revoking temporary identity. This reproduction does not establish that three historical production synchronization failures had the same cause.

## News-Source Maintenance

maintain_news_sources maintains only explicitly selected old/new Eurostat indicator URLs. It reuses Agent normalization rules but does not call a website, model, or news-signal refresh.

    python backend/manage.py maintain_news_sources \
      86c674ff-c726-4b22-96d0-fa582efaf5c3:0 \
      49a256cb-bbfb-4639-9449-148a659c73d7:0

The default is preview only. Versions in ID:revision must match the database immediately before execution. Add --apply explicitly to write. The command first validates every target, expected revision, and global old/new URL duplication, then writes through the original save service and appends audit. Any error rolls back the complete batch. Laboratory mode likewise cannot use an old revision to overwrite new data. Repeating an identical canonical URL does not advance revision.

Source maintenance does not treat an old summary as original text, merge duplicate records automatically, or treat clearing a system-service failed state as a successful refresh. Targeted model-backed news refresh, customer reanalysis, and historical-mail retry remain separate explicit business operations.
