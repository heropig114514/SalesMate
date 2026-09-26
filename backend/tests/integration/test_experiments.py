"""Responsibility: Validate cross-account reads under laboratory sharing, maintenance capability flags, exact batch boundaries, ownership, and GET endpoint boundaries.
Implementation: Create two groups of real related fixtures and ordinary non-fixture data in isolated PostgreSQL, using actual HTTP views.
Relationships: sales.experiments, seed_kg_lab, and existing business permissions; does not access external services or write a real database.
Directory:
- ExperimentTests: Laboratory-sharing integration tests.
- ExperimentTests.setUp: Create temporary files, complete fixtures, and a non-owner reader.
- ExperimentTests.url: Generate table paths for the current batch.
- ExperimentTests.test_cross_account_all_tables_and_ownership: All tables are readable and retain ownership.
- ExperimentTests.test_only_exact_batch_rows_are_shared: Other batches and forged prefixes are not authorized.
- ExperimentTests.test_mutations_and_original_write_paths_denied: Reject writes and action execution.
- ExperimentTests.test_anonymous_denied_and_page_available: Anonymous users cannot retrieve data, while the site provides a laboratory entry point.
- ExperimentTests.test_changed_or_missing_records_fail_closed: Content drift and missing content fail explicitly.
- ExperimentTests.test_filters_and_pagination: Search, ownership, and page-number boundaries.
- ExperimentTests.test_export_keeps_lineage_without_credentials: Export complete relationships while excluding credentials.
- ExperimentTests.test_documents_and_attachment_integrity: Documents can be downloaded and corrupted attachments are rejected.
- ExperimentTests.test_removed_manifest_revokes_access: Revoking the manifest immediately withdraws read capability.
Variable index:
- None
"""

import json
import tempfile
from pathlib import Path

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.crm.models import Company
from apps.sales.experiment_writes import WRITE_MODELS
from apps.sales.experiments import APPROVED_BATCHES, TABLES
from apps.sales.management.commands.seed_kg_lab import run_seed, verify_manifest
from apps.sales.models import AuditEvent, Product


# Function: Validate positive and negative cases for laboratory-data authorization.
# Logic: Each test uses an isolated transaction and temporary attachment directory; the ordinary reader differs from the importer.
# Constraints: Does not mock database authorization or projections; all samples are fictional and no Worker runs.
class ExperimentTests(TestCase):
    # Function: Create an independent laboratory context.
    # Inputs: No external inputs; reads the test database and temporary directory.
    # Outputs: Instance state includes owner, reader, manifest, base, client, and related values.
    # Logic: Actually generate two sets of 44-table data and create an out-of-manifest company as an unauthorized counterexample.
    # Constraints: The framework rolls back data and deletes temporary files after the test.
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="kg-read-test-")
        self.addCleanup(self.folder.cleanup)
        self.settings_context = override_settings(BASE_DIR=Path(self.folder.name), LOCAL_DEBUG_AUTO_LOGIN=False)
        self.settings_context.enable()
        self.addCleanup(self.settings_context.disable)
        self.owner = get_user_model().objects.create_user(username="experiment-owner")
        self.reader = get_user_model().objects.create_user(username="experiment-reader")
        self.manifest = run_seed(self.owner, APPROVED_BATCHES[0], 2)
        self.private = Company.objects.create(owner=self.owner, name="KGSEED_20260921_01 private lookalike", group_key="private")
        self.base = f"/api/v1/experiments/{APPROVED_BATCHES[0]}/"
        self.client = APIClient()
        self.client.force_authenticate(self.reader)

    # Function: Generate table paths for the current batch.
    # Inputs: `label` is the model name.
    # Outputs: Relative URL string.
    # Logic: Used only by tests to construct known paths.
    # Constraints: Does not initiate HTTP calls.
    def url(self, label):
        return self.base + label + "/"

    # Function: Validate that all 44 tables are cross-account readable and ownership values remain unchanged.
    # Inputs: Fixtures created by setUp and a second ordinary account.
    # Outputs: Assert table count, row count per table, ownership, and original record fingerprints.
    # Logic: Read every table through the actual API and confirm every record is marked writable in reality and fictional.
    # Constraints: Does not equate ordinary permission testing with real browser visual verification.
    def test_cross_account_all_tables_and_ownership(self):
        summary = self.client.get("/api/v1/experiments/").data["batches"][0]
        self.assertEqual(len(summary["tables"]), 44)
        self.assertEqual(summary["total"], 120)
        for label in TABLES:
            response = self.client.get(self.url(label))
            self.assertEqual(response.status_code, 200, (label, response.data))
            self.assertEqual(response.data["count"], self.manifest["table_counts"][label])
            self.assertTrue(all(row["synthetic"] and row["read_only"] == (label not in WRITE_MODELS) for row in response.data["results"]))
        companies = self.client.get(self.url("crm.Company")).data["results"]
        self.assertEqual({row["owner"]["username"] for row in companies}, {self.owner.username})
        self.assertEqual(verify_manifest(self.manifest), self.manifest["table_counts"])

    # Function: Validate that out-of-manifest data, other batches, and credential tables are not exposed.
    # Inputs: An ordinary company with a batch-like name and an unapproved path.
    # Outputs: The exact out-of-manifest company does not appear; unknown batches and models return 404.
    # Logic: Query forged data by primary key, covering the fact that field names and name prefixes cannot replace authorization.
    # Constraints: Does not broaden read scope through regular expressions.
    def test_only_exact_batch_rows_are_shared(self):
        response = self.client.get(self.url("crm.Company"), {"pk": str(self.private.pk)})
        self.assertEqual(response.data["count"], 0)
        for label in ("crm.GmailCredential", "crm.AgentCredential", "agent_tools.ToolCredential", "sales.Connection", "auth.Permission"):
            self.assertEqual(self.client.get(self.url(label)).status_code, 404)
        self.assertEqual(self.client.get("/api/v1/experiments/KGSEED_20260921_02/crm.Company/").status_code, 404)
        self.assertEqual(self.client.get(f"/api/v1/companies/{self.private.pk}/").status_code, 404)

    # Function: Validate that both laboratory endpoints and existing cross-account write endpoints reject changes.
    # Inputs: Fixture primary key visible to another account.
    # Outputs: Write methods return 405; original business editing and analysis return 404; fixture fingerprints remain unchanged.
    # Logic: Covers ordinary records, order actions, and model-analysis entries.
    # Constraints: Does not use a real external action provider.
    def test_mutations_and_original_write_paths_denied(self):
        for method in ("post", "patch", "put", "delete"):
            response = getattr(self.client, method)(self.url("sales.Product"), {}, format="json")
            self.assertEqual(response.status_code, 405)
        product = next(row["pk"] for row in self.manifest["rows"] if row["model"] == "sales.Product")
        company = self.manifest["truth"][0]["company_id"]
        self.assertEqual(self.client.patch(f"/api/v1/sales/records/products/{product}/", {"name": "changed"}, format="json").status_code, 404)
        self.assertEqual(self.client.post(f"/api/v1/companies/{company}/analyze/").status_code, 404)
        self.assertEqual(verify_manifest(self.manifest), self.manifest["table_counts"])

    # Function: Validate anonymous rejection while the site can provide a laboratory page.
    # Inputs: Unauthenticated APIClient and an ordinary authenticated account.
    # Outputs: API returns 403; the HTML page contains a read-only list and script entry point.
    # Logic: The page shell may be public, but content cannot be read without API authentication.
    # Constraints: Does not treat the page response as completed browser-script testing.
    def test_anonymous_denied_and_page_available(self):
        anonymous = APIClient()
        self.assertEqual(anonymous.get("/api/v1/experiments/").status_code, 403)
        self.assertEqual(anonymous.get(self.base + "export/").status_code, 403)
        # The temporary BASE_DIR in tests affects only fixture files; template loaders still use the real template directory initialized by configuration.
        page = self.client.get("/experiments/")
        self.assertContains(page, "experiment-rows")
        self.assertContains(page, "experiments.js")

    # Function: Validate that content is no longer returned when a manifest row changes or is missing.
    # Inputs: Modify one product in the test transaction, then delete a notification that has no external dependency.
    # Outputs: Both table reads return 409 and their responses omit the replaced body.
    # Logic: Separately cover summary and quantity changes without automatically refreshing fingerprints.
    # Constraints: Test modifications are rolled back by the transaction and do not alter original generation parameters.
    def test_changed_or_missing_records_fail_closed(self):
        product = Product.objects.filter(owner=self.owner).first()
        Product.objects.filter(pk=product.pk).update(name="new-private-content")
        response = self.client.get(self.url("sales.Product"))
        self.assertEqual(response.status_code, 409)
        self.assertNotIn(b"new-private-content", response.content)
        apps.get_model("sales.Notification").objects.filter(owner=self.owner).first().delete()
        self.assertEqual(self.client.get(self.url("sales.Notification")).status_code, 409)

    # Function: Validate filtering and page-number contracts.
    # Inputs: Ownership username, text, and invalid pagination parameters.
    # Outputs: Correct filtering, stable pagination, and controlled 400 response.
    # Logic: Filter the authorized row set before pagination; do not search the entire database.
    # Constraints: Validate request parameters only; do not modify model parameters or data splits.
    def test_filters_and_pagination(self):
        result = self.client.get(self.url("crm.Company"), {"owner": self.owner.username, "page_size": 1}).data
        self.assertEqual(result["count"], 2)
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(self.client.get(self.url("crm.Company"), {"owner": self.reader.username}).data["count"], 0)
        self.assertEqual(self.client.get(self.url("crm.Company"), {"q": "private lookalike"}).data["count"], 0)
        for params in ({"page": 0}, {"page_size": 201}, {"page": "bad"}):
            self.assertEqual(self.client.get(self.url("crm.Company"), params).status_code, 400)

    # Function: Validate that complete export retains relationships and synthetic declarations while excluding login credentials.
    # Inputs: Complete-export request for the current batch.
    # Outputs: 44 tables, 120 rows, exact lineage targets, and download headers.
    # Logic: Check that the emails and extraction primary keys targeted by source edges both appear in the export.
    # Constraints: Vectors and scores remain fixture values and do not claim real model performance.
    def test_export_keeps_lineage_without_credentials(self):
        response = self.client.get(self.base + "export/")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(sum(len(rows) for rows in data["records"].values()), 120)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("no-store", response["Cache-Control"])
        for row in data["records"]["accounts.User"]:
            self.assertNotIn("password", row["fields"])
        emails = {row["pk"] for row in data["records"]["crm.Email"]}
        extractions = {row["pk"] for row in data["records"]["crm.Extraction"]}
        for source in data["records"]["crm.SnapshotSource"]:
            self.assertIn(str(source["fields"]["email_id"]), emails)
            self.assertIn(str(source["fields"]["extraction_id"]), extractions)
        self.assertTrue(data["synthetic"])
        self.assertEqual(data["scenario_links"], self.manifest["truth"])

    # Function: Validate that two file types are readable and attachment content is verified.
    # Inputs: Approved document and attachment primary keys; temporarily corrupt one fixture attachment.
    # Outputs: A normal download contains original content, a corrupted file returns 409, and an out-of-manifest primary key returns 404.
    # Logic: File reading must verify both the model-row fingerprint and file digest.
    # Constraints: Operate only on the temporary test directory and never access shared server files.
    def test_documents_and_attachment_integrity(self):
        for label in ("accounts.SetupDocument", "sales.Attachment"):
            pk = next(row["pk"] for row in self.manifest["rows"] if row["model"] == label)
            response = self.client.get(self.url(label) + pk + "/download/")
            self.assertEqual(response.status_code, 200)
            self.assertTrue(b"".join(response.streaming_content))
            self.assertIn("attachment", response["Content-Disposition"])
        attachment = apps.get_model("sales.Attachment").objects.get(pk=pk)
        (Path(self.folder.name) / "private_uploads" / attachment.storage_key).write_bytes(b"tampered")
        self.assertEqual(self.client.get(self.url("sales.Attachment") + pk + "/download/").status_code, 409)
        self.assertEqual(self.client.get(self.url("sales.Attachment") + "not-a-uuid/download/").status_code, 404)

    # Function: Validate that revoking a manifest immediately stops cross-account reads.
    # Inputs: First mark the manifest as file-cleanup in progress, then delete the release-manifest event for the current batch.
    # Outputs: Cleanup in progress returns 409; after manifest deletion the directory is empty and original table paths return 404.
    # Logic: Each request rereads the manifest and cleanup state rather than using a possibly stale authorization cache.
    # Constraints: Delete the manifest only in the test transaction; the real command performs actual cleanup.
    def test_removed_manifest_revokes_access(self):
        entry = AuditEvent.objects.get(event="kg_synthetic_batch_v1", object_id=APPROVED_BATCHES[0])
        entry.changes["cleanup_state"] = "files_pending"
        entry.save(update_fields=["changes"])
        self.assertEqual(self.client.get(self.url("crm.Company")).status_code, 409)
        AuditEvent.objects.filter(event="kg_synthetic_batch_v1", object_id=APPROVED_BATCHES[0]).delete()
        self.assertEqual(self.client.get("/api/v1/experiments/").data["batches"], [])
        self.assertEqual(self.client.get(self.url("crm.Company")).status_code, 404)
