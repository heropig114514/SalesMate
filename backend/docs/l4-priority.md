# Company-level L4 backend integration

This implementation integrates Agent `score-v2` while retaining one current score per company, the L1→L4 flow, Agent HTTP protocol, and employee email isolation. Agent computes scores; the backend supplies authoritative inputs, validates results, stores explanations, and projects queries without duplicating scoring algorithms.

Opportunity-level signals, result storage, and `/priorities/` were added on 2026-09-22; see [integration support](development-support.md). This support layer accepts explicit algorithm submissions without copying company scores to every opportunity. Original L4 validation/scheduling remains unchanged. Limitations below concerning incomplete explanation display apply only to the original company-detail page.

## 1. Confirmed data definitions

- Seller profiles, product catalogs, opportunities, and order statistics are isolated by business `owner`; team sharing is not private-mailbox or organization-wide statistical access.
- Active opportunities are unarchived `new`, `qualified`, or `proposal` records for the same company. Aggregate only `Opportunity`, without adding quotes. Sum only fully known same-currency amounts; omit `deal_value` for mixed currencies, unknown amounts, or nonpositive totals.
- Products use explicitly entered `product_names`; if any active opportunity has unknown products, company-level products remain unknown. Trim names and deduplicate case-insensitively without synonym inference.
- Historical averages use all of the owner's unarchived `confirmed`/`fulfilled` orders, once per order, filtered by opportunity currency. Reuse Decimal line-net calculations; exclude drafts, cancelled orders, and other currencies, without recounting quotes or won opportunities. Orders without lines have unknown amounts; any such same-currency sample suppresses the average. Do not additionally round averages to integers or two decimals.
- Similar wins require the same industry and at least one identical canonical product. Omit the boolean when reliable history is absent, current industry/products are unknown, or historical information is incomplete without a verified match. Return `false` only for complete information with no match.
- Country, industry, and headcount come from authoritative profiles, never invented from email. Explicit maintenance APIs supply seller targets.
- Lists sort by descending score, descending urgency contribution, then ascending company ID; null scores appear last.

## 2. Profile maintenance

### Seller profile

`GET /api/v1/sales/seller-profile/` returns only the authenticated employee's own configuration:

```json
{"revision": 0, "profile": {}}
```

Responses include `ETag`. Missing configuration does not cause GET to create records or fill default targets.

`PATCH /api/v1/sales/seller-profile/` requires `If-Match` and merges explicitly submitted fields only:

```json
{
  "target_industries": ["Manufacturing"],
  "target_company_size": {"min": 20, "max": 500},
  "service_regions": ["Singapore"],
  "time_zone": "Asia/Singapore"
}
```

Ranges must be complete/nonnegative and timezones valid IANA names. Empty arrays or allowed `null` clear data; omitted fields retain values. Averages, similar wins, product lists, and owner cannot be written here. Product lists come from the existing catalog. Actual changes atomically increment profile version and both related-company versions and merge `external_updated` work.

### Companies and opportunities

- Existing `POST /api/v1/companies/{id}/register/` adds optional `country`; omission retains it and `null` clears it. Nonnull headcount still requires provenance.
- Existing `/api/v1/sales/records/opportunities/` create/update adds `product_names`, such as `["WMS", "OHT"]`. `null`/`[]` denote unknown; never infer products from descriptions.
- Catalogs, orders, and opportunities retain permissions, state machines, and If-Match protocols.

## 3. Agent context

`GET /api/v1/agent/context/?company_id=...` retains existing fields, ETag, and authorization, adding:

```json
{
  "priority_context": {
    "customer": {
      "customer_id": "<company-UUID>",
      "company_name": "Example",
      "industry": "Manufacturing",
      "company_size": 100,
      "country": "Singapore"
    },
    "deal": {"status": "ACTIVE", "currency": "SGD", "deal_value": "250000.00", "product": ["WMS"], "stage": "proposal"},
    "seller": {
      "target_industries": ["Manufacturing"],
      "target_company_size": {"min": 20, "max": 500},
      "service_regions": ["Singapore"],
      "time_zone": "Asia/Singapore",
      "products": ["WMS"],
      "average_deal_value": "30000.00",
      "average_deal_currency": "SGD",
      "similar_won_deals": true
    }
  }
}
```

This is a structural example; insufficient real data leaves corresponding fields absent. The backend does not duplicate `communications`; Agent generates it from existing emails. This context is not inserted into L2 `business_context` and does not change L2 submission fields.

Opportunity changes refresh the current company. Order/product changes propagate to other same-owner companies; industry changes and company merges also propagate similar-win dependencies. Transactions keep `revision`, `external_version`, and jobs consistent; stale Agent writes conflict. Pending jobs retain existing merging without automatic failure retries.

Shared dependencies currently conservatively update all same-owner company versions, including companies without business emails. Larger deployments should narrow affected scope by dependencies. External-version changes alter L2 input versions and invalidate current Agent L3 caches.

## 4. Score and explanation submission

`POST /api/v1/agent/scores/` retains version/lease headers and adds optional `score_details`. Nonnull `score-v2` results require exactly three unique integer contributions: `urgency`, `buying_intent`, and `opportunity_value`, summing exactly to `score`. Old L3 feature completeness is no longer checked.

Explanations contain `score_breakdown`, `top_reasons`, `evidence`, and `recommended_next_action`. Components are integers 0–100; explanation contributions match the main score. At most three reasons appear in descending impact order. Email reasons require locatable sources; `DEAL_VALUE` reasons require `source_id=null`.

When explanations accompany null scores, only this structure is accepted:

```json
{"score_breakdown": null, "top_reasons": [], "evidence": [], "recommended_next_action": null}
```

Within the current revision/lease, the backend verifies emails belong to the company and corresponding L2 membership and remain business emails, then validates source text using Agent whitespace normalization. Scores/explanations share existing `Score.payload`; lists use existing `Score.value`, without another score field. Duplicate requests at the same version/scored_at must match completely; stored explanations cannot be overwritten later.

Existing company-detail `score_detail` contains the full payload; read explanations at **`score_detail.score_details`**. Email location uses `source_id == dedupe_key` in authorized `context.emails`. Existing lineage invalidation/email visibility checks still apply.

With complete data, Agent `analyze_company()` submits `score_details` from the same computation alongside `score`. Provisional scores lacking complete components submit missing-data descriptions in `score_reasons` only. The backend validates/stores/returns full explanations; the frontend currently does not display them fully.

## 5. Deployment and subsequent scope

- Apply `sales.0006_l4_priority_context` to create SellerProfile and nullable opportunity `product_names`. Historical data remains unknown without generated test records.
- Production continues explicitly using `ANALYSIS_PROVIDER=agent`; code preserves runtime configuration. Production pages select only `score-v2`; old scores remain stored without posing as current results. Before rescoring, displayed scores are empty.
- Maintain real company data, opportunity products, and seller profiles, then rescore through existing analysis jobs. Complete explanation display still needs frontend integration.
- Scheduled `priority_refresh`, independent Signal lifecycle, and automatic external operations are outside this adaptation; elapsed time does not fabricate business versions.

## 6. Validation

Run in `backend/` using the project virtual environment:

```text
python manage.py test tests.integration.test_priority --noinput
python manage.py test tests --noinput
python manage.py makemigrations --check --dry-run
python tools/check_docs.py
python tools/check_doc_changes.py
```

Databases need existing PostgreSQL/pgvector dependencies. If the ordinary test role cannot install extensions, an administrator may preinstall them only in an independent test database used with `--keepdb`; do not elevate application permissions or skip migrations. Current CI uses pgvector 0.8.2.

Tests use synthetic records/structured signals without real model/mailbox calls. Added coverage includes per-order same-currency statistics, isolation, opportunity changes, dependency propagation, new results without old L3 features, explanations/source validation, idempotency, null scores, stale-version conflicts, and sorting.

Local validation on 2026-09-19: after preinstalling CI-matching pgvector 0.8.2 in an independent PostgreSQL test database, all 10 new and 198 complete backend tests passed with `--keepdb`. OpenAPI contract tests passed; migration checks found no differences. Documentation structure passed for 162 Python files; change checks reported 0 errors/0 review items, with manual review of descriptions. New migration operations satisfied existing conservative release gates; operation-level checks do not verify the production migration plan. No deployment, business-database migration, real-model testing, or frontend integration testing occurred.
