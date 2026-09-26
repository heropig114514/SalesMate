"""Responsibility: Verify that existing business lists show shared experiment data across accounts without exposing records outside the manifest.
Implementation: Generate complete fixtures through an isolated database and temporary files, then use Session API to check merging, pagination, filters, counts, and rejected writes.
Relationships: Covers `sales.browse`, original business permissions, and experiment manifest; real frontend interaction is verified separately.
Directory:
- BusinessBrowseTests: Merged-read tests for existing sales pages.
- BusinessBrowseTests.setUp: Establish owner, reader, fictional batch, and counterexample for real permissions.
- BusinessBrowseTests.get: Call merged read and validate status.
- BusinessBrowseTests.test_all_resources_and_foreign_privacy: Business resources are readable, original ownership remains, and external connections are unavailable.
- BusinessBrowseTests.test_mixed_pagination_owner_dedup_and_company_filter: Mixed pagination deduplication and customer-status filtering.
- BusinessBrowseTests.test_no_unlisted_related_data: Shared customer does not bring in newly added ordinary contacts or settings.
- BusinessBrowseTests.test_stats_permissions_and_revocation: Counts, rejected writes, drift, and revocation.
Variable index:
- None
"""

import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.crm.models import Company, Contact
from apps.sales.browse import RESOURCES
from apps.sales.experiments import APPROVED_BATCHES
from apps.sales.management.commands.seed_kg_lab import run_seed, verify_manifest
from apps.sales.models import AuditEvent


# Function: Verify merged lists and original permission boundaries under a real user Session.
# Logic: An ordinary account does not join the importer's team, so original business lists are empty while new browse lists show approved fixtures.
# Constraints: Use test database and temporary attachments only and do not call external services.
class BusinessBrowseTests(TestCase):
    # Function: Establish shared fixtures for two accounts and two scenarios.
    # Inputs: Test database and temporary directory.
    # Outputs: Instance attributes `owner`, `reader`, `manifest`, `private`, and `client`.
    # Logic: Use a private customer with the same prefix as an exact manifest counterexample without granting team permissions.
    # Constraints: Roll back database and clean temporary files at test end.
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="business-browse-")
        self.addCleanup(folder.cleanup)
        settings = override_settings(BASE_DIR=Path(folder.name), LOCAL_DEBUG_AUTO_LOGIN=False)
        settings.enable()
        self.addCleanup(settings.disable)
        self.owner = get_user_model().objects.create_user(username="browse-owner")
        self.reader = get_user_model().objects.create_user(username="browse-reader")
        self.manifest = run_seed(self.owner, APPROVED_BATCHES[0], 2)
        self.private = Company.objects.create(owner=self.owner, name=APPROVED_BATCHES[0] + " private", group_key="private")
        self.client = APIClient()
        self.client.force_login(self.reader)

    # Function: Read one merged page.
    # Inputs: `resource` is a business resource and `params` are optional query parameters.
    # Outputs: Successful response data.
    # Logic: Call the actual route through Session middleware and retain original response for assertion diagnostics on failure.
    # Constraints: Do not bypass view authentication or schema.
    def get(self, resource, params=None):
        response = self.client.get(f"/api/v1/sales/browse/{resource}/", params or {})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    # Function: Verify that every mapped business table is visible to an ordinary other account.
    # Inputs: Complete fixtures, unshared customer, and logged-in reader.
    # Outputs: Each resource count equals the manifest; original business remains isolated, external connections and anonymous requests are rejected.
    # Logic: Check experiment source and Boolean maintenance markers per resource, and query private primary key to verify no leakage.
    # Constraints: Do not interpret Session tests as browser visual verification.
    def test_all_resources_and_foreign_privacy(self):
        self.assertEqual(self.client.get("/api/v1/sales/directory/").data["count"], 0)
        for resource, model in RESOURCES.items():
            data = self.get(resource, {"archived": "all"})
            self.assertEqual(data["count"], self.manifest["table_counts"][model], resource)
            self.assertTrue(all(isinstance(row["experiment"]["read_only"], bool) and row["experiment"]["synthetic"] for row in data["results"]))
            self.assertEqual({row["experiment"]["owner"]["id"] for row in data["results"]}, {self.owner.pk})
        self.assertEqual(self.get("directory", {"company": str(self.private.pk)})["count"], 0)
        self.assertEqual(self.client.get("/api/v1/sales/browse/connections/").status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get("/api/v1/sales/browse/directory/").status_code, 403)

    # Function: Verify consistent pagination, deduplication, and filtering when ordinary and shared records mix.
    # Inputs: Reader-owned customer, importer account, and verified customer and ticket from the same batch.
    # Outputs: Ordinary records precede shared records with no duplicate primary keys; importer sees each fixture once.
    # Logic: Check across pagination boundaries page by page and filter tickets through customer ID and status.
    # Constraints: Do not make a shared customer referenceable with ordinary write permissions.
    def test_mixed_pagination_owner_dedup_and_company_filter(self):
        own = Company.objects.create(owner=self.reader, name="my real customer", group_key="own")
        first = self.get("directory", {"page_size": 2})
        second = self.get("directory", {"page_size": 2, "page": 2})
        self.assertEqual((first["count"], first["shared_count"]), (3, 2))
        self.assertEqual(first["results"][0]["id"], str(own.pk))
        self.assertNotIn("experiment", first["results"][0])
        self.assertEqual(len({row["id"] for row in first["results"] + second["results"]}), 3)
        company = self.manifest["truth"][0]["company_id"]
        tickets = self.get("tickets", {"company": company})
        self.assertEqual(tickets["count"], 1)
        status = tickets["results"][0]["status"]
        self.assertEqual(self.get("tickets", {"company": company, "status": status})["count"], 1)
        self.client.force_login(self.owner)
        owner_data = self.get("directory")
        self.assertEqual((owner_data["count"], owner_data["shared_count"]), (3, 2))

    # Function: Verify that reverse relationships of a shared customer cannot bring in records outside the manifest.
    # Inputs: An ordinary contact pointing to a shared customer but absent from the manifest.
    # Outputs: Shared customer contains only original manifest contacts and no newly added sensitive email.
    # Logic: Actually create the foreign-key association and read through merged directory.
    # Constraints: Construct ordinary unshared data only in the test database and do not modify original fixture rows.
    def test_no_unlisted_related_data(self):
        company = self.manifest["truth"][0]["company_id"]
        Contact.objects.create(company_id=company, name="private new contact", email="private@example.test")
        row = self.get("directory", {"company": company})["results"][0]
        self.assertNotIn("private@example.test", str(row))
        self.assertTrue(row["contacts"])
        self.assertEqual(verify_manifest(self.manifest), self.manifest["table_counts"])

    # Function: Verify merged overview, read-only methods, and integrity failure.
    # Inputs: An ordinary account with no original permissions, then simulated record drift and manifest revocation after a complete batch.
    # Outputs: Customer count is 2 with experiment count marked, write entry point rejects, drift returns 409, and count becomes zero after revocation.
    # Logic: Check list and overview, then use original write path to verify no permission expansion.
    # Constraints: Change fixtures only within test transactions; errors must not degrade to partial success.
    def test_stats_permissions_and_revocation(self):
        data = self.get("overview")
        self.assertEqual((data["customers"], data["shared_counts"]["customers"]), (2, 2))
        self.assertEqual(data["confirmed_order_net"], {})
        for method in ("post", "patch", "delete"):
            self.assertEqual(getattr(self.client, method)("/api/v1/sales/browse/directory/", {}, format="json").status_code, 405)
        company = self.manifest["truth"][0]["company_id"]
        self.assertEqual(self.client.get(f"/api/v1/companies/{company}/").status_code, 404)
        Company.objects.filter(pk=company).update(name="modified")
        self.assertEqual(self.client.get("/api/v1/sales/browse/directory/").status_code, 409)
        AuditEvent.objects.filter(event="kg_synthetic_batch_v1", object_id=APPROVED_BATCHES[0]).delete()
        self.assertEqual(self.get("directory")["count"], 0)
