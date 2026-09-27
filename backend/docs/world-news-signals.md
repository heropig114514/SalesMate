# Public-news sales lead API

This contract adapts `WORLD_INSIGHTS_SIGNAL_BACKEND_HANDOFF.md` for one article, one company-lead set, and one amount set. Fields reside in `WorldNews`; no `opportunity_signals.create`, CRM company/contact/opportunity creation, or name-based links to private records. Source amounts must not be interpreted as seller order amounts.

## Fields

All fields below are optional. Creation defaults text to `""` and amount to `null`; migration `sales.0010_news_signal_fields` gives old records the same empty values without parsing/backfilling original bodies.

| Field | Type and limits | Meaning |
| --- | --- | --- |
| `company_name` | string, at most 240 characters | Source company/institution |
| `signal_type` | string, empty allowed | expansion / new_factory / tender / equipment_upgrade / procurement / other |
| `project_name` | string, at most 240 characters | Source project name |
| `demand_description` | string, at most 500 characters | Demand disclosed in news |
| `potential_sales_need` | string, at most 500 characters | Potential procurement inference, not confirmed purchasing |
| `opportunity_reason` | string, at most 500 characters | Explanation relating potential demand to products |
| `time_window` | string, at most 240 characters | Source milestones, without automatic date conversion |
| `evidence` | string, at most 600 characters | Public source text supporting entity/event, preserving whitespace |
| `amount` | Nonnegative decimal string or null; 24 integer/6 fractional digits | Major currency-unit amount; reject JSON numbers, negatives, exponent notation, or overflow |
| `currency` | string, empty allowed | CNY / USD / EUR / GBP / JPY / KRW / SGD / TWD / HKD / INR / CAD / AUD / CHF |
| `amount_type` | string, empty allowed | total_investment / procurement_budget / tender_amount / contract_amount / other |
| `amount_scope` | string, empty allowed | whole_project / equipment_procurement / other |
| `amount_evidence` | string, at most 400 characters | Amount excerpt appearing verbatim within the same evidence |

The user explicitly confirmed limits of 24 integer/6 fractional digits. Overflow returns 400 without silent rounding. Responses use fixed six-decimal strings, such as `"50000000.000000"`, or `null` for unknown. Zero is a valid known value.

When `amount` is present, currency/type/scope/amount evidence must all be nonempty; with `amount=null`, all four must be empty. PATCH validates the complete state after merging existing records and changes. Clearing amounts requires explicitly clearing all four fields in the same update; the backend never removes evidence implicitly for callers.

The backend validates enums, lengths, monetary combinations, and excerpt inclusion without fetching external sites. Inclusion does not independently verify news authenticity. Logs contain only validation location, field names, and existing write receipts, never source bodies/tokens.

## Calls and reads

- Reuse `world_news.create` / `world_news.update`, placing new fields in `arguments.data`. Production writes still require Tool credentials/idempotency keys; updates use current revision.
- REST creation remains `/api/v1/sales/records/world-news/`; update details at `<id>/` under it. Bodies contain writable fields only; send version in `If-Match`.
- Lists/details and `world_news.list`/`world_news.get` return all new fields. Other employees may read public leads; writes, source deduplication, and archival rules are unchanged.
- Agent discovers actual `inputSchema` from `/api/v1/agent-tools/catalog/`. Enums permit empty strings; amount accepts string/null only; unknown fields remain rejected. Generic REST OpenAPI already describes dynamic resource + object; no duplicate routes are added. Detailed fields follow the catalog and this contract.
- Each article accepts one information set, not multiple-company/amount arrays. One-to-many extensions require separate contract agreement.

Example additions to `arguments.data`, to combine with existing title, category, publication time, source, and body; this is not actual collected output:

```json
{
  "company_name": "示例制造公司",
  "signal_type": "new_factory",
  "project_name": "示例生产基地",
  "demand_description": "建设新的生产线",
  "potential_sales_need": "新产线可能需要检测设备",
  "opportunity_reason": "潜在检测环节可能与现有产品相关，仍需核实",
  "time_window": "2027 年投产",
  "evidence": "示例制造公司宣布建设示例生产基地，总投资人民币 5000 万元，计划 2027 年投产。",
  "amount": "50000000",
  "currency": "CNY",
  "amount_type": "total_investment",
  "amount_scope": "whole_project",
  "amount_evidence": "总投资人民币 5000 万元"
}
```

## Pages and release

News cards show entities, events, and source-amount meaning. Details separate source facts, evidence, amount type/scope, and inferred needs explicitly labeled as unconfirmed procurement. Amounts use string-based thousands grouping and trim insignificant trailing fractional zeros, without floats, significant-digit truncation, or currency conversion. Missing structured data is distinct from known zero.

Events use the same source amount fields; neither news nor event money increases internal opportunity totals. News maps only after city, latitude, longitude, location_evidence and location_source_url are explicitly verified; unlocated news is still displayed below the map.

Deploy database migrations/APIs before the collaborator's corresponding Agent version. Backend adaptation preserves Agent files, sources, and model parameters. Verify old/new payloads, permissions, and actual Tool receipts before collection tests. This document is not a production-release or real-collection acceptance record. Agent skips existing sources; old articles require separately authorized backfills.

## Checks

`tests.integration.test_news_signals` uses isolated PostgreSQL for complete/empty/legacy payloads, 24+6 precision, evidence combinations, partial updates, Tool catalogs, shared-write isolation, database constraints, and migrations. Existing shared-insight/tool tests retain source concurrency and exhibition compatibility coverage.

`node backend/tools/browser_world_map.cjs` uses mocked APIs and actual pages for companies, fact/inference separation, monetary meaning, exact characters, HTML escaping, zero/unknown amounts, old news, and mobile layout. It does not establish real-news or updated-Agent integration.

## Explicit historical-news refresh

After production backup, maintainers may run this for explicitly authorized news UUIDs:

```bash
python backend/manage.py refresh_world_news_signals NEWS_UUID [NEWS_UUID ...] --apply
```

Omit `--apply` for previews. All targets must be unarchived Agent news; implicit full-table refresh is unsupported. The command rereads original source pages and directly calls existing Agent `summarize_news`, retaining model/prompt/validation parameters. Old generated summaries are not source evidence. Update only thirteen public lead fields, preserving title/body/time/source and creating no CRM links. Old-revision checks protect concurrent edits; identical fields are not rewritten.

Source/extraction failures retain that record, report failure, and exit nonzero; other completed items remain. No automatic retries/fallbacks. If Agent extracts no valid amount, retain null rather than weakening evidence rules to invent values. This command runs separately from ordinary collection, whose source deduplication is unchanged.

If redirects prevent direct refresh, explicitly run existing Agent `python -m agent.world_insights --dry-run`, save complete stdout result JSON to a protected file, then execute:

```bash
python backend/manage.py refresh_world_news_signals NEWS_UUID --agent-preview /absolute/path/preview.json --apply
```

This mode reuses Agent's existing collection/feed-summary rules without further network/model calls. Each selected UUID must match exactly one preview news item by source; missing/ambiguous matches reject imports. Unselected news/exhibitions are not written; original titles, bodies, and dates remain. Maintainers choose the mode explicitly; original-page failures never switch automatically to file imports.

## Source amount alignment (2026-09-27)

News and events both expose `amount_qualifier` (blank, exact, up_to, at_least, more_than, approximate). Amount types additionally include grant, registration_fee and exhibition_fee. A null amount requires empty currency/type/scope/quote/qualifier. Event `evidence` encloses `amount_evidence`; amount strings retain six fractional digits at most. Qualifiers preserve source bounds; an award of up to USD 1 billion is not an exact procurement budget.

Agent extraction validates source money independently of company lead extraction. Event amounts are extracted once after date/location eligibility. REST, generated tools, persistence, cards/details and map labels share this contract. Maps no longer use CRM amounts or a currency switch. Historical source review is explicit and audited, never automatic text parsing during migration.

## Evidenced news locations

`city`, `latitude`, `longitude`, `location_evidence`, and `location_source_url` optionally place news on the map. Coordinates require all location fields plus country; the evidence must name the city and use a credential-free HTTPS source. Existing records default to blank/null. A mapped facility does not imply that a company-wide financing amount is allocated solely to that site; curate that distinction in the body. No migration, read path or serializer infers locations from the publisher or a country code.
