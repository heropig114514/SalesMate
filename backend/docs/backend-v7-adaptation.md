# Backend adaptation for extract-v7 and provisional L4 scores

## Implemented API and state boundaries

### Historical fact upgrades

`GET /api/v1/companies/{company_id}/extraction-upgrade/` uses the authenticated employee Session and returns only owned companies. The response ETag is the company revision. Version statistics count only each business email's latest extraction, without recounting historical versions. Responses include `target_version`, `versions`, `incompatible_emails`, and `repairs` (pending/running/failed). GET neither writes the database nor calls models.

Use the GET ETag to `POST` to the same path with an empty object `{}`, supplying CSRF Token and `If-Match` according to the existing Session API. Success returns 202, a new ETag, `created`, `reused`, `revision`, and the latest preview. Unauthorized access returns 404; missing/stale If-Match follows the existing version protocol. Caller-supplied facts, model selections, and prompt parameters are not accepted.

POST enqueues only the latest extractions of current business emails whose versions differ from the target. Existing pending/running repairs are reused; failed repairs are requeued only after this explicit operation. Enqueueing preserves old facts and manual decisions. New tasks advance company revision and update existing analysis tasks. Ordinary failed extractions already at the current version retain the existing fact-resubmission/manual-review workflow.

The existing `crm_worker` consumes repairs and re-extracts StoredMessage source text (or persisted Email bodies for legacy records) using current L1 prompts and configured models, without rereading Gmail/QQ. New results are saved as new Extraction records; original text, facts, and versions remain. Before saving, revalidate lease, source, manual-review version, direction, and source evidence, rejecting expired results. Machine classification updates from new facts, with manual classification taking precedence; missing procurement stages may produce needs_review.

pending/running/failed repairs block analysis claims for that company. Successful saves propagate lineage invalidation and enqueue reanalysis; failures are not retried automatically. An upgrade POST returning 202 alone does not establish completed profiles or scores. Existing service management remains responsible for running workers in deployment. No local mailbox synchronization worker was started in this update, to avoid consuming unrelated pending work.

In agent mode, company analysis POST returns 409 with the upgrade entry point when it detects old facts. It neither maps old stages to new ones nor automatically calls models to alter experimental conditions. Rule-based integration mode retains existing behavior.

### v7 direction constraints

New email submissions, failed-fact resubmissions, and worker repair saves all enforce: only inbound emails may declare nonempty procurement stages; outbound/unknown must use null with empty evidence arrays. Evidence must still occur in persisted source text. Do not silently discard invalid stages or infer L1 from unknown.

### Provisional L4 scores

Retain the remote score-v2 algorithm and weights. The backend allows nonnull scores with a completely empty explanation:

```json
{"score_breakdown": null, "top_reasons": [], "evidence": [], "recommended_next_action": null}
```

This means Agent has not supplied displayable components, not that components equal zero. Contributions must still be complete, nonnegative, and sum exactly to the score; partially empty explanations remain invalid. Nonempty explanations still validate components, contributions, source excerpts, and current-company source ownership. Detail APIs expose `score_detail.score_details` unchanged; frontends should display existing score_reasons without rendering empty explanations as zero.

## Responsibilities shared with Agent/frontend

- After analysis reports old facts, the frontend can display an upgrade preview and require explicit user submission. This update provides backend APIs without adding a frontend button.
- Temporal-signal generation still requires Agent adaptation: current L2 rebuilds signals using procurement stages only, so the backend alone cannot restore deadline signals. Injecting backend signals alone is insufficient because L2 overwrites them. Existing seller-profile, opportunity-amount, and historical-transaction definitions remain unchanged.
- score-v2 does not distinguish the algorithm before/after the remote change; experiments must also record code commits. Coordinate a scoring-version rename with Agent and frontend; this update does not change the protocol version independently.
- Experiment JS remains v6; its upgrade and new experiment batches have not been executed. This API does not simply relabel old facts as v7.

## Validation scope

An isolated PostgreSQL test database and fixed model doubles verify upgrade authorization, read-only previews, version locks, reuse, no automatic failure retry, old-fact retention, source reuse, manual-decision changes, direction constraints, machine classification refresh, and provisional-score persistence. Actual model quality, real historical-batch upgrades, and production deployment require separate execution. This update did not rewrite real history, commit Git changes, or deploy.

Actual checks for this update:

- 70 targeted backend regressions passed, covering historical upgrades, scoring, durable lineage, email processing, and chat.
- Of 185 complete backend regressions (excluding disabled QQ-specific tests), 184 passed and 1 failed because the new API was absent from the OpenAPI snapshot. After regenerating the contract, that test passed independently. No assertions were changed or tests removed.
- OpenAPI generation/specification validation, Django check, makemigrations --check --dry-run, and git diff --check passed; no new database migrations.
- Python comment/directory checks covered 166 files, with 0 errors and 0 review items in change checks. Natural-language semantics and API descriptions were manually reviewed. Changes remained uncommitted; commit atomicity was not claimed.
- Local Web was restarted; actual company lists, sales overview, and new upgrade previews returned HTTP 200. Only GET previews were invoked; no real account upgrades were queued.

Additional concurrency, rollback, lease, and mixed-batch white-box verification appears in the [white-box test record](extraction-upgrade-whitebox-tests.md).

Latest supplemental results: 9 tests added and 16 upgrade-module tests passed; subsequently all 194 complete backend regressions passed in one run (QQ-specific tests still excluded), covering the earlier OpenAPI contract recheck.
