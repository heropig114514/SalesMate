# Historical Fact Upgrade: White-Box Test Record

This change adds tests and documentation only. It does not change business logic, models, scoring weights, or experiment data. The test file is tests/integration/test_extraction_upgrades.py. Nine cases were added to the existing seven, for sixteen total.

## Correspondence Between Tests and Implementation Branches

| Added case | Target branch/invariant | Verification method |
| --- | --- | --- |
| test_concurrent_queue_serializes_revision | Company row lock, If-Match, single enqueue, and revision advance | Two threads, independent PostgreSQL connections and APIClients, synchronized by barrier; requires exactly one 202 and one 409. |
| test_queue_rolls_back_partial_batch | Mid-transaction failure for multiple mail items and restoration of prior failed state | Write two real request_repair calls, inject an exception, and confirm task, failure state, revision, and Job all roll back. |
| test_queue_rolls_back_revision_and_analysis | Exception after revision persistence and analysis scheduling | Inject failure after original schedule_analysis and confirm repair, company, and analysis queue roll back together. |
| test_expired_lease_rejects_result_without_retry | lease_until equals current time; completion rejection and expiry cleanup | Fix the clock at the real lease deadline and check Conflict, failed/error, and no automatic retry. |
| test_replaced_source_rejects_result | New and old extraction identity mismatch | Keep other conditions valid, inject only a new source, and independently verify rejection of old completion. |
| test_duplicate_completion_has_no_side_effects | Resubmission after completed state | Check that extraction is not saved twice, revision does not advance again, and Job does not change. |
| test_mixed_batch_blocks_until_all_repairs_complete | New/old versions, review-pending mail, partial success, and failure | Use real queue and Worker with a model substitute. Upgrade only target old business sources; analysis unlocks only after explicit retry. |
| test_latest_version_and_empty_batch_are_noops | Empty loop, skip current version, and count latest extraction only | Check zero creates/reuse, no repeated count of old history, and unchanged revision/Job. |
| test_upgrade_http_rejects_missing_version_and_payload | Missing HTTP version and nonempty request parameters | Both requests return existing 400 and have no database side effect. |

## Execution and Boundaries

Run this targeted command at repository root in the project virtual environment:

    .\.venv\Scripts\python.exe backend/manage.py test tests.integration.test_extraction_upgrades --keepdb --noinput

All sixteen targeted cases passed. A subsequent one-shot run of all 194 backend tests passed in 120.572 seconds, including the new cases and OpenAPI contract, using CI's existing QQ-specific exclusion scope. Logs are in ignored artifacts/extraction-whitebox-tests.log and artifacts/backend-whitebox-full-tests.log.

The Python documentation structural check covered 166 files. The change check reported zero errors and zero review findings. Test comments and file-level directory were manually reviewed. git diff --check passed. Changes were not committed or deployed, so this record does not claim Git commit atomicity was verified.

Tests exercise real database transactions and locks. Only external model, clock, and designated exception-injection points are substituted. They do not access a real mailbox or model and do not modify local demo/tst1 data. Threads close independent connections after completion. Concurrent cases verify mutual exclusion for this interleaving and do not claim to exhaust all thread schedules.

There is no configured line/branch-coverage threshold or quantified branch-coverage report because the project virtual environment has no coverage package. The branch-by-branch test matrix above states coverage scope. Passing tests do not claim 100% branch coverage, and no dependency was added merely for testing.

The first new HTTP boundary test incorrectly expected 409 for missing If-Match. It was corrected to match the existing check_version contract: missing or malformed input is 400 and an expired version is 409. Production behavior was not adjusted.
