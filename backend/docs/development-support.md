# Backend and frontend integration support

This implementation follows the request to use fictional database placeholders for missing data and relax algorithm-integration access. The backend provides input reads, result storage, business aggregation, and page display; Agent collection, extraction, matching, scoring, and recommendation algorithms remain unchanged. New endpoints are engineering additions supporting integration needs, not claims that requirements explicitly prescribed their names.

## Initialization and development access

Run from the repository root using the project Python:

```powershell
python backend/manage.py migrate
python backend/manage.py seed_development_support --username algorithm-lab
```

The command requires DEBUG or open experiment mode. Initial import creates independent fictional companies without overwriting real records. Repeat execution for the same account returns the original manifest without overwriting later manual/algorithm changes or refreshing news dates. A transaction and `support_seed_completed` audit retain the import manifest.

The batch contains 8 companies, 8 active opportunities, 8 events, 4 news items, 8 signals, 8 fixed placeholder scores, transaction products, and proposal files. Personal/company/reference-product/proposal/seller profiles initialize only when absent for that account. Events/news/signals/scores use `data_source=synthetic`; company/opportunity names contain `【虚拟】`. Placeholder scores do not imply algorithm execution. Real results may create new records or explicitly update placeholders with `data_source=agent`.

Ignored local `.env` and production `/opt/salesmate/shared/runtime.env` use:

```dotenv
LAB_OPEN_ACCESS=True
WORKSPACE_OWNER_ONLY=False
```

Synthetic experiment settings never bypass authentication or account permissions. Use an employee Session or explicitly delegated Tool token. Versions, idempotency keys, CSRF, and confirmation remain required. See [experiment access](laboratory-access.md).

On 2026-09-22, this batch was imported into production PostgreSQL for `algorithm-lab` and record counts verified against the audit manifest. Pre-import database/runtime backups and the import manifest reside at `/opt/salesmate/backups/support-seed-20260922T064840Z`. Deployment restarted services to load configuration; verify through `lab_open_access` in `/api/v1/session/` and `/api/v1/sales/world/`.

## Direct algorithm interfaces

| Capability | HTTP (prefix `/api/v1/sales/`) | Tool/MCP |
| --- | --- | --- |
| Company, personal, reference products, proposals, seller profile, transaction products | `GET seller-context/` | `seller_context.get` |
| Opportunity, company, business emails, seller profile, historical orders, signals, latest score | `GET opportunity-context/<opportunity UUID>/` | `opportunity_context.get` |
| Active opportunity priorities | `GET priority-board/` | `priority_board.list` |
| Map events and per-currency aggregation | `GET world/` | `world_insights.get` |
| Signal CRUD/archive | `records/opportunity-signals/` | `opportunity_signals.list/get/create/update/archive` |
| Opportunity-score CRUD/archive | `records/opportunity-priorities/` | `opportunity_priorities.list/get/create/update/archive` |
| Event/news CRUD/archive | `records/world-events/`, `records/world-news/` | `world_events.*`, `world_news.*` |

Append `<record UUID>/` for individual GET/PATCH; archival uses generic commands or the corresponding Tool. Signal/score lists support `opportunity` filtering. Production still requires Session or authorized Tool credentials and version control. Tool credentials use unified `/api/v1/agent-tools/call/`, not business-page Sessions. New tools require new authorized scopes; catalogs, Python SDK, and stdio MCP share the registry.

Minimal score submission:

```json
{
  "name": "opportunity_priorities.create",
  "arguments": {
    "data": {"opportunity": "<actual-opportunity-UUID>", "priority_score": 82}
  }
}
```

Send to `POST /api/v1/agent-tools/call/`. Minimal signals require `opportunity` and `signal_type`; `signal_value` accepts arbitrary JSON. Company/owner derive from opportunity relations without repeated caller fields. Existing records cannot move to another opportunity; create another record instead.

Optional score fields: `score_breakdown`, `top_reasons`, `evidence` (JSON), `recommended_next_action`, `scored_at`, `score_version`, `data_source`. Optional signal fields: `signal_value`, `confidence`, `source_type`, `source_id`, `evidence_text`, `detected_at`, `status`, `data_source`. No restrictions on signal enums, reason counts, component structures, or algorithm weights. Scores are nullable integers 0–100; confidence is nullable numeric 0–1. Unknown results remain empty rather than forced to zero.

Scores may append history. Select latest results by descending `scored_at`, creation time, then ID; priority lists sort by latest score descending with unscored entries last. Records represent submitted results only: input changes do not automatically recompute/invalidate them. Algorithms decide when to submit new results. Original company-level L4 `score-v2`, the 35/35/30 algorithm, and worker scheduling are unchanged.

## Pages and boundaries

- `/world/` reads shared events/news and company-country highlights. Source monetary fields supply event/news labels; linked opportunities never supply or aggregate displayed money. Source-evidenced news joins events on fixed-size location markers. The map orders by proximity to today; news is retained for 90 days and events for 30 days after ending. Synthetic fixtures stay explicitly labeled and archived records are hidden.
- The standalone `/priorities/` page and its Global Insights entry were retired on 2026-09-27. The old page URL returns 404; dedicated frontend assets are removed. Opportunity context, signals, score APIs, and stored results remain available for algorithm integration.
- `/business/#opportunity-signals` and `#opportunity-priorities` maintain records through existing generic forms.
- News displays at most 4 items from the last 14 days; expired dates are never silently refreshed. Events/news require explicit maintenance; initialization is not background news collection.
- Itinerary buttons export ICS using database times; invitation buttons provide editable templates with the user's signature. No Agent-generated copy, sent emails, or external calendar events.
- Uploads retain PDF/TXT and CSV product-import support; this update adds no Office/OCR/Excel parsing or website-profile scraping.

## Validation commands

```powershell
python backend/manage.py test tests.integration.test_development_support --keepdb --noinput
python backend/manage.py makemigrations --check --dry-run
```

Browser checks require an initialized database and running local experiment service. Set `SALESMATE_TEST_URL=http://127.0.0.1:8011`, `SALESMATE_PLAYWRIGHT_MODULE`, and `SALESMATE_BROWSER_PATH`, then run `node backend/tools/browser_world_news.cjs`. It reads actual local APIs and checks desktop/mobile world pages, exports, priority-page retirement, and request failures; it does not verify real Agent/external services.

In `backend/`, run `python tools/check_docs.py` and `python tools/check_doc_changes.py --base HEAD --fail-on-review`, and manually review frontend documentation. While code remains uncommitted, these checks do not prove Git commit atomicity.
