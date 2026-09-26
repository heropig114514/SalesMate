"""Responsibility: Verify anonymous cross-account business access in public lab mode and restored permissions after it is disabled.
Implementation: Use a real HTTP client without cookies or tokens and with CSRF checks in isolated PostgreSQL.
Relationships: Covers laboratory and Sales/CRM/Tool/Chat entry points; synthetic batches are generated in temporary directories without external models or sends.
Directory:
- LaboratoryTests: Public-lab integration verification.
- LaboratoryTests.setUp: Create two non-KGSEED accounts and business records.
- LaboratoryTests.call: Send Tool request without credentials or idempotency key.
- LaboratoryTests.test_anonymous_reads_and_all_list_tools: Anonymous full catalog and cross-account reads.
- LaboratoryTests.test_cross_owner_write_and_internal_confirmation: Anonymous cross-account modification and direct management operations.
- LaboratoryTests.test_identity_selection_and_invalid_token: Public owner selection and invalid-token compatibility.
- LaboratoryTests.test_switch_off_restores_authentication: Restore formal authentication and version checking.
- LaboratoryTests.test_anonymous_chat_preserves_original_owner: Cross-account chat and knowledge context.
- LaboratoryTests.test_seed_crud_and_regular_edit: Anonymous fictional-data maintenance and readable ordinary edits.
- LaboratoryTests.test_agent_context_and_lease_optional: Agent business access without authentication and optional lease.
Variable index:
- BASE: Fixed Tool API prefix.
"""

import tempfile
from pathlib import Path
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.agent_tools.registry import build_registry
from apps.chat.models import KnowledgeEntry, AnswerRequest
from apps.crm import rules
from integrations.company_enrichment import input_version
from rest_framework.exceptions import ValidationError
from apps.crm.access import check_version
from apps.crm.jobs import require_lease
from apps.sales import grouping, models
from apps.sales.experiments import APPROVED_BATCHES, load_batch, table_rows
from apps.sales.management.commands.seed_kg_lab import run_seed

BASE = "/api/v1/agent-tools/"


# Function: Verify passwordless lab business flow.
# Logic: Test configuration enables mode; framework restores configuration and rolls back data afterwards.
# Constraints: Do not call public internet or real external services.
@override_settings(LAB_OPEN_ACCESS=True, LAB_DEFAULT_USER="algorithm-lab", LOCAL_DEBUG_AUTO_LOGIN=False)
class LaboratoryTests(TestCase):
    # Function: Establish cross-account data.
    # Inputs: Isolated test database.
    # Outputs: Instance state for two users, customer, product, knowledge, and anonymous client.
    # Logic: Ordinary records carry no KGSEED marker, verifying open scope is not limited to fictional batches.
    # Constraints: Enable real CSRF checks and do not use force_authenticate.
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="lab-owner")
        self.other = get_user_model().objects.create_user(username="lab-other")
        self.company = grouping.create_company(self.owner, "Ordinary private customer")
        self.product = models.Product.objects.create(owner=self.owner, sku="ordinary", name="Original", currency="USD", unit_price="2.00")
        self.knowledge = KnowledgeEntry.objects.create(owner=self.owner, title="Shared fact", content="Cross account fact", version="1")
        self.client = APIClient(enforce_csrf_checks=True)

    # Function: Execute anonymous Tool request.
    # Inputs: `name` tool name, `arguments` input, and `expected` HTTP status.
    # Outputs: Response data.
    # Logic: Send no authentication, version, or idempotency headers and assert real HTTP result.
    # Constraints: Failure assertion retains response for diagnosis.
    def call(self, name, arguments, expected=200):
        response = self.client.post(BASE + "call/", {"name": name, "arguments": arguments}, format="json")
        self.assertEqual(response.status_code, expected, response.data)
        return response.data

    # Function: Verify anonymous read entry points and full Tool catalog.
    # Inputs: Ordinary records and registry for two accounts.
    # Outputs: Visible company, product, and knowledge; every record list is requestable.
    # Logic: Iterate fixed registered resources; web browse, CRM, and Tool use real views.
    # Constraints: Do not interpret successful protocol reads as model-inference quality.
    def test_anonymous_reads_and_all_list_tools(self):
        for path in ("/api/v1/session/", "/api/v1/sales/directory/", "/api/v1/companies/", "/api/v1/mailboxes/", BASE + "catalog/"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response["X-Lab-Open-Access"], "true")
        self.assertTrue(self.client.get("/api/v1/session/").data["lab_open_access"])
        self.assertEqual(self.call("products.list", {})["data"]["count"], 1)
        self.assertIn("Shared fact", str(self.call("knowledge.search", {})))
        for name, spec in build_registry().items():
            if spec["kind"] == "record_list":
                with self.subTest(tool=name):
                    self.call(name, {})

    # Function: Verify anonymous modification and direct management operations.
    # Inputs: Another account's product and new team name.
    # Outputs: Writes succeed, original ownership remains, audit retains lab actor, and internal proposals need no confirmation.
    # Logic: Omit If-Match, revision, and idempotency key, then perform archive.
    # Constraints: Actual external sending flow is outside this test.
    def test_cross_owner_write_and_internal_confirmation(self):
        response = self.client.patch(f"/api/v1/sales/records/products/{self.product.pk}/", {"name": "Public edit"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.product.refresh_from_db()
        self.assertEqual((self.product.name, self.product.owner_id), ("Public edit", self.owner.pk))
        result = self.call("products.update", {"id": str(self.product.pk), "data": {"name": "Tool edit"}})
        self.assertIn("call_id", result)
        self.call("products.archive", {"id": str(self.product.pk), "archived": True})
        self.assertEqual(self.call("teams.create", {"data": {"name": "Public team"}})["status"], "completed")
        setting = self.company.business_settings
        self.call("customer_settings.update", {"id": str(setting.pk), "data": {"notes": "Public customer edit"}})
        self.assertTrue(models.AuditEvent.objects.filter(actor__username="algorithm-lab", owner=self.owner).exists())

    # Function: Verify passwordless owner selection and compatibility with invalid old token.
    # Inputs: X-Lab-User and invalid Authorization.
    # Outputs: Selected account owns new record; invalid old credential does not reject business access.
    # Logic: Create product through public identity header; after clearing identity return to default lab account.
    # Constraints: Only lab mode accepts this header and does not create login Session.
    def test_identity_selection_and_invalid_token(self):
        self.client.credentials(HTTP_X_LAB_USER=self.other.username, HTTP_AUTHORIZATION="Tool expired-or-invalid")
        result = self.call("products.create", {"data": {"sku": "selected", "name": "Selected", "currency": "USD", "unit_price": "1.00"}})
        self.assertEqual(models.Product.objects.get(pk=result["data"]["id"]).owner_id, self.other.pk)
        self.client.credentials(HTTP_AUTHORIZATION="invalid")
        self.assertEqual(self.client.get(BASE + "catalog/").status_code, 200)

    # Function: Verify the unified disable switch.
    # Inputs: Anonymous and logged-in requests from the same client.
    # Outputs: Anonymous access rejects, cross-account resources are hidden, and missing version is not accepted.
    # Logic: After runtime mode switch, use original authentication and scope without rebuilding test client.
    # Constraints: Production deployment must restart service when switching environment variables.
    def test_switch_off_restores_authentication(self):
        self.assertEqual(self.client.get(BASE + "catalog/").status_code, 200)
        with override_settings(LAB_OPEN_ACCESS=False):
            self.assertIn(self.client.get(BASE + "catalog/").status_code, (401, 403))
            self.assertIn(self.client.get("/api/v1/companies/").status_code, (401, 403))
            self.client.force_login(self.other)
            response = self.client.get(f"/api/v1/sales/records/products/{self.product.pk}/")
            self.assertEqual(response.status_code, 404)
            with self.assertRaises(ValidationError):
                check_version(None, 0)

    # Function: Verify cross-account conversation and knowledge evidence.
    # Inputs: Another account's workspace conversation.
    # Outputs: Anonymous submission retains original ownership; Agent context sees another account's knowledge.
    # Logic: Use real submit, claim, and context APIs; public identity header selects queue for claim.
    # Constraints: Do not run model or change established knowledge budget.
    def test_anonymous_chat_preserves_original_owner(self):
        conversation = models.Conversation.objects.create(owner=self.other, title="Existing workspace")
        response = self.client.post("/api/v1/sales/chat/messages/", {"conversation_id": str(conversation.pk), "client_key": str(uuid.uuid4()), "content": "Shared fact"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        answer = AnswerRequest.objects.get(conversation=conversation)
        self.assertEqual(answer.owner_id, self.other.pk)
        self.client.credentials(HTTP_X_LAB_USER=self.other.username)
        response = self.client.post("/api/v1/agent/chat/requests/claim/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.client.credentials()
        response = self.client.post("/api/v1/agent/chat/context/", {"request_id": str(answer.pk), "scope": "internal"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn("Cross account fact", str(response.data))

    # Function: Verify anonymous CRUD for fictional batch and compatibility with ordinary entry point.
    # Inputs: Complete small 44-table batch and maintenance request without expected version.
    # Outputs: Creation, modification, and deletion succeed; ordinary edits do not invalidate lab read entry point.
    # Logic: Maintain and delete new product through lab entry point, then ordinarily modify existing synthetic product.
    # Constraints: Temporary attachments clean on exit and real database is unaffected.
    def test_seed_crud_and_regular_edit(self):
        with tempfile.TemporaryDirectory() as folder, override_settings(BASE_DIR=Path(folder)):
            run_seed(self.owner, APPROVED_BATCHES[0], 2)
            location = {"batch": APPROVED_BATCHES[0], "model": "sales.Product"}
            created = self.call("experiments.create", {**location, "data": {"sku": "lab-new", "name": "New", "currency": "USD", "unit_price": "1.00"}})["data"]
            self.call("experiments.update", {**location, "pk": created["pk"], "data": {"name": "Updated"}})
            self.call("experiments.delete", {**location, "pk": created["pk"]})
            row = table_rows(load_batch(APPROVED_BATCHES[0]), "sales.Product")[0]
            self.call("products.update", {"id": row["pk"], "data": {"name": "Ordinary edit"}})
            rows = table_rows(load_batch(APPROVED_BATCHES[0]), "sales.Product")
            updated = next(item for item in rows if item["pk"] == row["pk"])
            self.assertEqual(updated["fields"]["name"], "Ordinary edit")
            self.assertNotEqual(updated["fingerprint"], row["fingerprint"])
            self.assertEqual(self.client.get("/api/v1/sales/browse/directory/").status_code, 200)

    # Function: Verify Agent context and omitted lease.
    # Inputs: Anonymous client and another account's company.
    # Outputs: Context and L2 save both return HTTP 200; omitted version and lease pass; OAuth-key claiming still requires machine credential.
    # Logic: Access real Agent context and directly check boundaries of public lease function and credential-output endpoint.
    # Constraints: Build L2 with actual rules, do not mock database save, and do not call external model.
    def test_agent_context_and_lease_optional(self):
        response = self.client.get("/api/v1/agent/context/", {"company_id": str(self.company.pk)})
        self.assertEqual(response.status_code, 200, response.data)
        context = response.data
        group = self.client.get("/api/v1/agent/grouping/", {"company_id": str(self.company.pk)}).data
        document = rules.build_input(group, context)
        document["business_context"]["company_enrichment"] = context["company_enrichment"]
        document["input_version"] = input_version(context["emails"], document["merge_version"], context["external_snapshot_version"], context["company_enrichment"])
        saved = self.client.post("/api/v1/agent/analysis-inputs/", document, format="json")
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertIsNone(require_lease(self.company, None, None))
        check_version(None, self.company.revision)
        response = self.client.post("/api/v1/agent/mailbox-syncs/claim/", {"limit": 1}, format="json")
        self.assertEqual(response.status_code, 401)
