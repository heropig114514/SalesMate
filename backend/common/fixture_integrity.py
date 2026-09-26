"""Responsibility: Calculate persistent-row fingerprints for experiment fixtures so import, read-only sharing, and cleanup use the same verification.
Implementation: Uses actual database fields only; normalizes binary values and vectors, sorts keys, and calculates SHA-256.
Relationships: sales.seed_kg_lab persists fingerprints; sales.experiments validates them before returning cross-account content.
Directory:
- fingerprint: Calculates the digest of one persistent row's content.
Variable index:
- None
"""

import hashlib
import json


# Function: Calculate a content fingerprint for a database row.
# Inputs: `instance` is an ORM instance read from the database.
# Outputs: SHA-256 hexadecimal string.
# Logic: Uses IDs for foreign keys, hex for binary values, lists for vectors, and string normalization for other non-JSON types.
# Constraints: Compatible with the existing kg-business-fixture-v1 manifest; the digest detects content drift but does not prove source authenticity.
def fingerprint(instance):
    values = {}
    for field in instance._meta.concrete_fields:
        value = getattr(instance, field.attname)
        if isinstance(value, (bytes, memoryview)):
            value = bytes(value).hex()
        elif hasattr(value, "tolist"):
            value = value.tolist()
        values[field.attname] = value
    encoded = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str, allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()
