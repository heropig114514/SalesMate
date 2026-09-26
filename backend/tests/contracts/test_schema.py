"""Responsibility: Verify that the currently generated OpenAPI schema matches the versioned contract.
Implementation: Generate and validate the schema, then compare parsed YAML structures to avoid comparing formatting or text layout alone.
Relationships: Reads `contracts/openapi.yaml` under the software root and uses Django configuration and drf-spectacular; does not call a real business database.

Directory:
- SchemaTests: Check that the generated API contract matches the saved version.
- SchemaTests.test_generated_schema_matches_versioned_contract: Verify schema validity and agreement with the versioned contract.

Variable index:
- None
"""

import yaml
from django.conf import settings
from django.test import SimpleTestCase
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.validation import validate_schema


# Function: Check that the generated API contract matches the saved version.
# Logic: Inherit from `SimpleTestCase` and read the project contract file for a structural comparison.
# Constraints: Do not create a database; depends on current Django configuration and installed schema-tool versions.
class SchemaTests(SimpleTestCase):
    # Function: Verify schema validity and agreement with the versioned contract.
    # Inputs: No external parameters; reads the contract file below the `settings.BASE_DIR` software root.
    # Outputs: Returns `None`; the test fails when validation fails, the file is unreadable, or structures differ.
    # Logic: Publicly generate the full schema, perform specification validation, then compare it with safe-loaded YAML.
    # Constraints: Do not modify the contract file; passing proves only that current generated structures agree, not that business interfaces are integrated.
    def test_generated_schema_matches_versioned_contract(self):
        generated = SchemaGenerator().get_schema(request=None, public=True)
        validate_schema(generated)
        contract = settings.BASE_DIR / "contracts" / "openapi.yaml"
        self.assertEqual(yaml.safe_load(contract.read_text(encoding="utf-8")), generated)
