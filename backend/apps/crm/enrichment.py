"""Responsibility: Supply shared experiment data to authorized company analysis and determine whether a snapshot remains valid.
Implementation: Personal isolation never reads shared experiment data; a batch resolution shares one verified manifest and exactly matches each company, without cross-request caching.
Relationships: selectors supplies context, results rereads on save, and Agent merges unchanged; no additional Tool credentials are required.
Directory:
- resolve: Resolve approved experiment data for the current company.
- resolve_many: Verify one manifest and resolve multiple companies in the same read-only transaction.
- match_company: Exactly match a single company with verified data.
- snapshot_current: Check whether snapshot enrichment still agrees with current sources.
Variable index:
- logger: Diagnostic logger that records only company, batch, and status.
"""

import logging

from django.db import connection, transaction
from rest_framework.exceptions import NotFound

from common.laboratory import owner_only
from apps.sales import experiments
from integrations.company_enrichment import digest
from .access import Conflict

logger = logging.getLogger("salesmate.enrichment")


# Function: Resolve approved experiment data for the current company.
# Inputs: `company` is a Company the caller is authorized to read.
# Outputs: Independent object containing status, match_basis, source, facts, and enrichment_version.
# Logic: Delegate to a single-element batch resolution, retaining read-only repeatable-read transactions and matching rules for independent reads.
# Constraints: Read approved manifests only; missing batches or integrity failures explicitly return unavailable and record a reason, while ordinary no-match returns not_found; does not modify CRM.
def resolve(company):
    return resolve_many([company])[company.pk]


# Function: Read and verify an approved manifest once and resolve data for multiple companies.
# Inputs: `companies` is a sequence of companies the caller is authorized to access.
# Outputs: Complete resolution results indexed by company primary key; an empty sequence returns an empty dictionary.
# Logic: The outermost transaction uses read-only repeatable read and nested calls retain the caller transaction. Manifest verification retains its original function and results match each company independently.
# Constraints: Does not cache across calls or omit fingerprint or approval checks; manifest failure makes every result explicitly unavailable and logs safely per company.
@transaction.atomic
def resolve_many(companies):
    companies = list(companies)
    if not companies:
        return {}
    if len(connection.atomic_blocks) == 1:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    rows = []
    failure = None
    for batch in (() if owner_only() else experiments.APPROVED_BATCHES):
        try:
            rows.extend(experiments.table_rows(experiments.load_batch(batch), "crm.Company"))
        except (NotFound, Conflict) as error:
            failure = "batch_unavailable" if isinstance(error, NotFound) else "integrity_error"
            for company in companies:
                logger.warning("company_enrichment_unavailable company_id=%s batch=%s reason=%s", company.pk, batch, failure)
    return {company.pk: match_company(company, rows, failure) for company in companies}


# Function: Generate company enrichment from data verified in this resolution.
# Inputs: `company` is the target company; `rows` is the approved manifest's current projection; `failure` is a manifest error code or None.
# Outputs: Independent dictionary with source, facts, and digest version.
# Logic: Prefer exact domain, then a batch-marked exact full name; ambiguity rejects a unique match and employee count and industry retain type validation.
# Constraints: Used only for the current resolution call, does not read the database or alter source data, and failure does not fall back to ordinary no-match.
def match_company(company, rows, failure):
    domains = {str(value).strip().casefold().rstrip(".") for value in company.domains if value}
    candidates = [row for row in rows if domains.intersection(
        str(value).strip().casefold().rstrip(".") for value in row["fields"]["domains"] if value)]
    basis = "exact_domain"
    if not candidates and any(batch in (company.name or "") for batch in experiments.APPROVED_BATCHES):
        basis = "exact_name"
        candidates = [row for row in rows if (company.name or "").strip().casefold() == (row["fields"]["name"] or "").strip().casefold()
                      and (not domains or not row["fields"]["domains"])]
    result = {"status": "unavailable" if failure else "not_found", "match_basis": None, "source": None, "facts": {}}
    if failure:
        result["reason"] = failure
    elif len(candidates) > 1:
        result["status"] = "ambiguous"
    elif len(candidates) == 1:
        row = candidates[0]
        customer = row["fields"]["customer"]
        count = customer.get("employee_count")
        industry = customer.get("industry_from_crm")
        result.update(status="matched", match_basis=basis, source={
            "source_id": f"experiment:{row['batch']}:crm.Company:{row['pk']}",
            "batch": row["batch"], "model": "crm.Company", "record_pk": row["pk"],
            "synthetic": True, "owner": row["owner"], "fingerprint": row["fingerprint"],
        }, facts={"employee_count": count if type(count) is int and count >= 0 else None,
                  "industry": industry if isinstance(industry, str) else None})
    result["enrichment_version"] = digest(result)
    return result


# Function: Check whether snapshot enrichment still agrees with current sources.
# Inputs: `snapshot` is AnalysisInput, `company` is its company, and `current` can reuse data resolved in this call.
# Outputs: True when it remains usable, otherwise False.
# Logic: Snapshots with enrichment compare the complete object, covering content changes, ambiguity, new matches, and approval revocation.
# Constraints: Retain the original protocol when legacy clients omit enrichment; do not delete historical snapshots or trigger model calls automatically.
def snapshot_current(snapshot, company, current=None):
    business = snapshot.payload.get("business_context", {})
    if "company_enrichment" not in business:
        return True
    return business["company_enrichment"] == (resolve(company) if current is None else current)
