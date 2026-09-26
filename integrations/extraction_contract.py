"""Responsibility: Distinguish L1 fact-schema versions from synthetic-data generation provenance.
Implementation: Ordinary extraction uses prompt versions; explicitly synthetic envelopes use independent schema versions with audited historical-batch compatibility.
Relationships: Shared by backend upgrade preflight and Agent L2; existing Agent validators still validate fact fields.
Directory:
- compatible_extraction: Determine whether an envelope declares the current fact schema.
Variable index:
- LEGACY_FIXTURE_SCHEMAS: Exact mapping from audited historical synthetic versions to actual schema versions.
"""

LEGACY_FIXTURE_SCHEMAS = {"KGSEED_20260921_01:fixture-extract-v1": "extract-v7"}


# Function: Determine whether ordinary or synthetic extraction uses the specified schema.
# Inputs: `email` is an email protocol dictionary; `target` is the consumer-supported fact version.
# Outputs: Boolean; prompt versions and facts remain unchanged.
# Logic: Compare ordinary prompt versions directly; synthetic versions must match the batch and use explicit schema declarations or audited historical mappings.
# Constraints: Neither authorization nor fact validation; unknown legacy versions cannot impersonate current versions by adding a schema declaration.
def compatible_extraction(email, target):
    version = email.get("extract_prompt_version")
    if version == target:
        return True
    batch = email.get("synthetic_batch")
    if not isinstance(batch, str) or not batch or version != f"{batch}:fixture-extract-v1":
        return False
    schema = email.get("extract_schema_version", LEGACY_FIXTURE_SCHEMAS.get(version))
    return schema == target
