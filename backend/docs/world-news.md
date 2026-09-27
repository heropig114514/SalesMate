# Global-insight event interface

Entry point: `/world/`; news details: `/world/news/<UUID>/`. Pages read the backend database. Original static demonstration sources and unconnected browser push modules were removed. Fictional data requires explicit `seed_development_support` imports; pages never create it automatically or fall back to demonstrations on API failure. See [integration support](development-support.md).

Public news adds one structured set of company, demand, project, amount, and evidence fields; see the [news lead contract](world-news-signals.md). News and events use the same source monetary fields; CRM amounts never populate this interface.

## Data and interaction

- Events explicitly load all pages from `/api/v1/sales/world/`. News loads all pages of items from the last 90 days through `records/world-news/`; details query IDs directly.
- Explicit company-country fields determine map highlights. Unrecognized countries count as missing, never inferred from email/addresses.
- Manual, Agent, and synthetic events/news are shared facts. In production and under `WORKSPACE_OWNER_ONLY=true`, authenticated employees may read them while anonymous access is rejected. Only owners may update/archive; team administrators gain no implicit rights. Synthetic experiment mode retains these same account permissions.
- `opportunity_ids` remains permission-filtered context only. Event monetary fields are source-disclosed amounts, independent of linked CRM opportunities.
- Event titles, description, onsite, and suggested_actions are public, not private notes. Review historical manually entered internal-company information before production sharing. The backend neither infers sensitivity from keywords nor rewrites source text.
- News remains eligible for 90 days after publication; events until 30 days after their end. Future events are retained. The mixed map list sorts by distance from now (ongoing events first), without monetary ranking. News cards sort publication time descending. Type/country/time filters preserve ordering.
- Each record retains its own decimal-string amount, currency, type, scope, quote and qualifier (exact/up_to/at_least/more_than/approximate). Missing amounts are null with empty metadata; known zero remains zero. Source values remain unchanged. A separate approximate SGD reference uses a fixed 2026-09-21 table covering all 13 supported currencies, with ECB cross-rates and CBC TWD/USD; no live FX requests.
- News with complete source-evidenced city/country/coordinates joins events on the map. Unlocated news stays in the news list; country centroids are never substituted.
- Events and news sharing country/coordinates share a location marker. The selected record supplies its label; tooltips list each event and its own amount without summation.
- Known amounts use diameter = min(80, 18 + 6*log10(1 + SGD/100)) pixels; unknown amounts use 14px white markers; dashed borders identify news and solid borders events; size encodes the displayed record’s reference amount on a fixed logarithmic scale, not proportional area or expected sales value. The legend, dated note and expandable rate table explain the mapping. Projection centers, mouse/keyboard selection and translucent missing-amount styling remain supported.
- Known-amount bubbles use 20% ordinary and 30% selected fill opacity, with clear borders/center markers to preserve basemap/neighbor visibility.
- News/exhibition/sales type, 30-day/current-quarter, and country filters link event lists/region counts. Global/Asia-Pacific/Europe views change map center/zoom. URL `type/time/country/view/event` stores selections.
- Provenance labels identify `synthetic` placeholders. Stored news retains original publication times; expired data displays empty without date refresh.
- Itinerary export uses actual UTC ICS times for `datetime` and `VALUE=DATE` all-day ICS for `date`. UI `starts_on/ends_on` includes the final day; ICS excludes its end date. An October 27–29 source displays through the 29th with DTEND on the 30th, without UTC-noon placeholder times. Text remains escaped/folded and fictional titles include Synthetic; no external calendar connection.
- Invitation generation opens editable templates signed from the selected identity's profile, with no automatic recipients, model calls, or sending.
- Escape all business text. Request failures display errors without static fallback; no SSE/WebSocket or automatic reconnection.

## File relationships

| File | Responsibility |
| --- | --- |
| `frontend/world.html`, `assets/world-news.css` | Page structure and responsive layout |
| `assets/world-news.js` | Database pagination, filters, details, calendars, invitation templates |
| `assets/world-signals.js` | Public leads, exact source-amount formatting, evidence/inference separation |
| `assets/world-dates.js` | Date precision, filter boundaries, all-day ICS properties |
| `assets/world-currency.js` | Frozen SGD rates, reference sources and pure logarithmic marker sizing |
| `assets/world-map.js` | Real borders, company-country highlights, amount bubbles, views |
| `assets/world-countries.geojson`, `assets/vendor/` | Local basemap, Leaflet, licenses |
| `apps/sales/world.py` | Authorized events with source monetary fields and country counts |
| `apps/sales/insights.py` | Basic event/news relation, source, and time validation |
| `apps/sales/insight_dates.py` | Compatibility with explicit existing Agent date placeholders, without Agent changes |

Maintain events/news through generic CRUD or `world_events.*` / `world_news.*` Tools; see [application support APIs](software-support-tools.md). Experiment mode or explicit synthetic records allow empty sources; provided sources must be credential-free HTTPS URLs, which the server does not fetch.

## Compatibility, deduplication, and release

- New writable `time_precision=date|datetime` defaults to datetime. date uses consistent UTC-midnight/noon boundaries; `ends_at` UTC date is exclusive. Read-only `starts_on/ends_on` represent inclusive source dates; ordinary datetime returns null.
- Agent event code/date payloads are unchanged. Adapt to date only when `data_source=agent`, description includes the complete standalone line `来源仅提供日期；起止钟点是系统占位值，请以来源页为准。`, and both boundaries are increasing UTC-noon instants. Full markers inconsistent with timestamps are rejected; do not infer precision for other prose/midnight events. Migration adds precision to explicitly marked historical records without changing timestamps.
- Nonempty Agent news source URLs are unique; Agent events have unique URL + starts_at pairs. Manual/synthetic records are exempt. Archival does not release uniqueness; duplicates return 409 without overwriting. Compare saved URL strings without merging redirects, query variations, or similar titles; existing Agent handles URL normalization.
- Current Agent skips identical sources, including archived ones, without updating existing news. Although the backend permits different event editions, existing Agent URL deduplication may still skip them; collection strategy changes are not claimed. Concurrent 409s become Agent item_errors, never fabricated backend success.
- Release requires `sales.0009_shared_insights`. Migration first checks duplicates and fails explicitly if present; resolve manually and rerun. No automatic deletion/merge/archive. Follow existing backup procedures; code changes do not establish production migration completion.

See [collection operations](world-insights-operations.md) for scheduled deployment/credential rotation.

## Validation

`tests.integration.test_development_support` verifies real database aggregation, placeholder idempotency, and production/experiment permissions. With a running local experiment service, explicit `SALESMATE_TEST_URL`, and Playwright/Chrome, run `node backend/tools/browser_world_news.cjs` for database pages, country filters, colocation aggregation, ICS, templates, news details, score evidence, mobile overflow, and 503 states; it connects to no real Agent/news/mail/calendar services.

`python backend/manage.py test tests.integration.test_shared_insights tests.integration.test_support_tools tests.integration.test_development_support --noinput` uses isolated PostgreSQL for two-account sharing, relation projections across entry points, write isolation, anonymous/Tool restrictions, date compatibility, historical migrations, concurrent uniqueness, and 409 responses. It neither migrates development databases nor deploys collection tasks.

`node backend/tools/browser_world_map.cjs` uses the same Playwright/Chrome environment with an isolated static server to check actual Leaflet projection/DOM centers, SGD-scaled markers, colocated event labels, translucent fills, zero/unknown amounts, mouse/keyboard interaction, views, zoom, and mobile sizes. Mocked full-page APIs additionally cover source currencies, URLs/refresh, empty events, and—in Pacific/Kiritimati and America/Los_Angeles—inclusive dates, downloaded all-day/ordinary-time ICS, invitation dates, and mobile layout. Browser fixtures do not verify production databases or external Agent integration.

In `backend/`, run `python tools/check_docs.py` and `python tools/check_doc_changes.py --base HEAD --fail-on-review`. Manually review JS/CSS/HTML top-level descriptions/directories.

Pure timeline checks: `node backend/tools/test_world_timeline.mjs` covers mixed chronological order, exact retention boundaries, future/ongoing events and missing/zero coordinates without browser or network mocks. News location integration: `python backend/manage.py test tests.integration.test_news_locations` verifies real persistence, partial-update consistency and generated Tool schema.

`node backend/tools/test_world_currency.mjs` verifies all supported currencies, independently calculated cross-rates, zero/missing values, malformed/unsupported input rejection, unchanged source values, monotonic sizes and maximum-size protection.
