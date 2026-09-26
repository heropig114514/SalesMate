"""Responsibility: Define stateless company-enrichment versioning and employee-count selection contracts.
Implementation: Canonical JSON digests bind input/data versions; CRM counts take precedence, with experiment counts labeled independently.
Relationships: Shared by Agent L2/L3 and backend persistence validation; no database or network access.
Directory:
- digest: Compute a stable content digest.
- input_version: Version email, CRM, and optional enrichment input.
- employee_size: Select employee count and actual provenance.
- source_refs: Extract independent sources for matched enrichment.
Variable index:
- None
"""

import hashlib
import json


# Function: Compute a stable content digest.
# Inputs: `value` is JSON-serializable content.
# Outputs: A sha256-prefixed hexadecimal string.
# Logic: Sort object keys and remove insignificant whitespace while preserving array order.
# Constraints: Reject noncanonical objects such as timestamps or model instances.
def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


# Function: Version email, CRM, and optional enrichment inputs.
# Inputs: `emails` is email context; `merge_version` is the merge version; `external_version` is the CRM version; `enrichment` is backend enrichment or None.
# Outputs: A stable input key.
# Logic: Extend the original three-element version with a fourth element only when enrichment is explicitly supplied, binding its complete content.
# Constraints: Legacy clients without enrichment retain their original hashes; do not alter CRM external_snapshot_version.
def input_version(emails, merge_version, external_version, enrichment=None):
    members = sorted([item["dedupe_key"], item["extract_prompt_version"], item["extract_status"]] for item in emails)
    parts = [members, merge_version, external_version]
    if enrichment is not None:
        parts.append(enrichment)
    return digest(parts)


# Function: Select employee count and actual provenance.
# Inputs: `business` is validated L2 business_context.
# Outputs: A tuple of count or None and source string.
# Logic: Prefer valid CRM counts, otherwise use matched enrichment counts only; return unknown when no valid count exists.
# Constraints: Never write back to CRM or treat booleans, negative values, or model estimates as counts.
def employee_size(business):
    customer = business.get("customer") or {}
    count = customer.get("employee_count")
    if type(count) is int and count >= 0:
        source = customer.get("employee_count_source")
        return count, source.strip() if isinstance(source, str) and source.strip() else "crm"
    enrichment = business.get("company_enrichment") or {}
    count = enrichment.get("facts", {}).get("employee_count")
    if enrichment.get("status") == "matched" and type(count) is int and count >= 0:
        return count, "synthetic_sample"
    return None, "unknown"


# Function: Extract independent sources for matched enrichment.
# Inputs: `business` is validated L2 business_context.
# Outputs: A set containing zero or one source ID.
# Logic: Register nonempty source_id only from matched objects.
# Constraints: Backend resolution/persistence checks establish provenance; this pure function grants no data permissions.
def source_refs(business):
    enrichment = business.get("company_enrichment") or {}
    source = enrichment.get("source") or {}
    ref = source.get("source_id")
    return {ref} if enrichment.get("status") == "matched" and isinstance(ref, str) and ref else set()
