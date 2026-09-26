# Experiment enrichment for company analysis

Company-analysis workers reuse `Authorization: Agent` without separate employee Tool tokens or chat request_id dependencies. Both new and existing accounts analyzing their own companies receive matching information from approved shared batches. Approval remains limited to `KGSEED_20260921_01`, with original ownership retained.

## Integration

1. Obtain company information and ETag through existing `GET /api/v1/agent/grouping/?company_id=...`.
2. Call `GET /api/v1/agent/context/?company_id=...` with the same Agent credentials and `If-Match: <ETag>`.
3. Copy the new response field `company_enrichment` directly into L2 `business_context.company_enrichment`. No further paginated search, read receipts, or Agent entity rematching is needed.
4. Compute the input version with `integrations.company_enrichment.input_version(emails, merge_version, external_snapshot_version, company_enrichment)`, then save through existing `analysis-inputs/`, `analyses/`, and `scores/`. Task lease headers retain their original contract.

The repository Agent implements these steps. External algorithms exploring arbitrary experiment tables still use existing `experiments.*` Tool APIs or MCP with each account's Tool credentials. MCP ultimately invokes the same HTTP Tool API. Company enrichment uses the original Agent HTTP context endpoint and adds no MCP tools.

Example of a matched enrichment object:

```json
{
  "status": "matched",
  "match_basis": "exact_domain",
  "source": {
    "source_id": "experiment:KGSEED_20260921_01:crm.Company:<original-primary-key>",
    "batch": "KGSEED_20260921_01",
    "model": "crm.Company",
    "record_pk": "<original-primary-key>",
    "synthetic": true,
    "owner": {"id": 8, "username": "tst1"},
    "fingerprint": "<current-manifest-fingerprint>"
  },
  "facts": {"employee_count": 81, "industry": "精密量测"},
  "enrichment_version": "sha256:<content-digest>"
}
```

`enrichment_version` resides inside enrichment, adding neither database columns nor required top-level L2 fields. Hash UTF-8 JSON with `ensure_ascii=False`, `sort_keys=True`, and `separators=(',', ':')` using SHA-256 after removing `enrichment_version`.

New `input_version` similarly hashes `[email_identity_list, merge_version, external_snapshot_version, complete_enrichment_object]`. Sort email identities by `[dedupe_key, extract_prompt_version, extract_status]`. CRM `external_snapshot_version` retains its meaning. Legacy clients omitting enrichment retain the original contract but cannot cite unregistered experiment sources.

## Matching and error states

The backend reuses experiment-tool manifest/current-row fingerprint validation, scans the approved batch's company table, and performs one centralized unique match:

- Prefer complete-domain matches, ignoring surrounding whitespace, case, and trailing dots. Subdomains/substrings do not match.
- Without domain matches, permit exact company-name matches only with the approved batch marker. When both sides have disjoint domains, never force a name-based merge.
- One candidate yields `matched`, multiple candidates `ambiguous`, and none `not_found`.
- Missing/cleaned batches yield `unavailable / batch_unavailable`; manifest/fingerprint errors yield `unavailable / integrity_error`, logging company, batch, and reason.
- Nonmatched states contain no source or facts. Explicit unavailability can enter L2 so analysis continues without enrichment; unknown database/programming exceptions are never silently ignored.

Only approved manifest information is shared. Reading the analysis target still requires its original Agent permissions; this grants no access to other employees' ordinary private company analyses, emails, or credentials.

## Saving, citations, and caching

On L2 saves, the backend fetches current enrichment again and validates content/source through one business-context comparison plus version checks. Changes since reading return 409 and require rereading, without automatic retries. Agent need not separately validate batch, owner, manifest, hashes, or fields.

L3 `analysis-v4` may cite the `source.source_id` actually registered in L2. Prefer CRM headcount when available; otherwise matched experiment headcount may be used with `size_source` fixed to `synthetic_sample`. Industry enrichment must also disclose fictional experiment provenance. Enrichment is neither written back to CRM nor inserted into email L1 facts. `extract-v7`, headcount thresholds, model budgets, and L4 formulas remain unchanged.

L3 saves, score saves, cache hits, current-profile projections, and latest-L2 queries all verify enrichment consistency. Source edits/deletion, revoked approval, ambiguity, or newly matching records invalidate old inputs for current use. History remains, profiles stop displaying, and caches miss. Existing explicit tasks trigger reanalysis; no automatic bulk recalculation.

## Validation scope

`backend/tests/integration/test_company_enrichment.py` uses isolated PostgreSQL to cover new-account Agent credentials with cross-account enrichment, tampering, same-name/subdomain counterexamples, ambiguity, CRM precedence, citation allowlists, version changes, approval revocation, and integrity errors. LiveServer tests use real DjangoBackendClient for L2/L3/L4 saves and subsequent cache hits; only model output is replaced by deterministic examples, so external LLM generation quality is not verified.
