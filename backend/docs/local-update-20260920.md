# Local synchronization check, 2026-09-20

Synchronization scope: `4429871` → `253d144`, 7 commits. After fast-forwarding main, the existing uncommitted frontend redesign and experiment scripts were restored. The pre-sync backup remains in the Git stash named `before-origin-main-update-20260920-local-frontend-and-experiment`.

## Remote behavior changes

- Chat normalizes model citations, removes unused references, and improves failure logs. Frontend citation evidence uses two nested disclosure levels; customer-profile citations link to customer pages.
- Mismatches between numbers and cited evidence now produce diagnostic logs instead of rejecting the entire answer. Source authorization and structural validation remain enforced. This changes answer acceptance criteria.
- L1 switched from extract-v6 to extract-v7: procurement stages range from L1 Exploring to L6 Purchase Ready, with null when undetermined.
- L4 no longer calls a separate model to extract signals; it derives rule inputs from L1 procurement stages. Incomplete business profiles yield provisional scores rescaled over available dimensions; complete profiles retain main-dimension weights of 35/35/30.
- CI reduced QQ checks and skipped chat and email-processing browser checks.

## Adaptations in this update

- Resolved app.css merge conflicts by retaining the local dark theme and using shared color variables for new chat citation cards.
- Updated static entry-point and chat-module versions to avoid stale caches.
- Fixed multiple-element matching of details in browser_chat.cjs, separately verifying default outer/inner collapse, source expansion, and safe escaping.
- Restored chat and email-processing browser checks to CI after they passed; QQ-specific browser checks remain disabled according to remote configuration.
- Restarted local Web and chat Worker processes to load synchronized Python code; model configuration remains qwen3.7-plus.

## Subsequent backend implementation

An explicit historical-fact upgrade endpoint and compatibility for empty provisional-score explanations have been added; see [Backend v7 adaptation](backend-v7-adaptation.md). The following issues were identified during synchronization; real historical-fact upgrades, experiment-script changes, and Agent temporal signals were not executed.

## Adaptation issues identified during synchronization

1. **Experiment scripts** still generate extract-v6, while current APIs accept only v7. Upgrading experiment fact criteria and generator versions requires explicit approval. Do not merely rename old facts as v7 and replay them or reuse idempotency identifiers to impersonate a new batch.
2. **Historical facts:** read-only inspection found 80 extract-v6 emails and 7 earlier rule-format emails in the local demo's current extractions. Building L2 for real local companies containing v6 returns invalid_backend_data (unsupported L1 structure). This update includes no database migrations or automatic re-extraction. Historical emails remain persisted, but this does not establish compatibility of new analysis with old facts. Separately choose re-extraction from persisted bodies with new versions and recalculation, or explicit compatibility rules; no such experiment-data rewrites were performed here.
3. **Urgency signals:** new L2 produces procurement-stage signals only, while L4 urgency still recognizes DEADLINE/UPCOMING_MEETING/PROMISED_ACTION/OVERDUE_ACTION. Under the current data flow, ordinary new processing does not produce these temporal signals, so urgency uses its base tier. The temporal-signal source requires a separate decision; passing these tests does not establish complete urgency detection.
4. **Scoring version** remains score-v2 despite algorithm behavior changes. Cross-version experiments should also record Git commit SHAs rather than grouping solely by score_version.

## Completed validation

- 210 targeted Agent unit tests passed (chat, L1, L2, L4, pipeline, and backend client).
- 54 backend integration tests passed (durable lineage, legacy facts, email processing, and chat), using isolated test_salesmate with model and mailbox test doubles.
- Four browser-check groups with simulated APIs passed: chat, email processing, workspace, and world news.
- Local migrate --check passed; the remote update added neither migrations nor dependency-manifest changes.
- Python documentation checks covered 164 files; change checks reported 0 errors and 0 items requiring review. JS/CSS/HTML/YAML documentation and this adaptation were manually reviewed.
- Actual local home, follow-up, and chat pages loaded normally. Customer lists, sales overview, and new chat static assets returned HTTP 200 with no observed page-script errors. Django check and makemigrations --check --dry-run passed.
- No commit or deployment was made in this update; real mailbox sending/receiving, analysis after historical-fact upgrades, and new scoring quality remain unverified.
