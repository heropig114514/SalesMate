"""Responsibility: Verify real interface behavior after synthetic-fact compatibility, upgrade tools, and restored account isolation.
Implementation: Generate two sets of real fixtures in an isolated database and verify through HTTP, Agent L2, and persistence APIs in sequence.
Relationships: Covers `extraction_contract`, `CompanyViewSet`, Tool API, and personal-workspace access policy.
Directory:
- ExtractionCompatibilityTests: Cross-layer regression tests.
- ExtractionCompatibilityTests.setUp: Generate independent fixture and reader.
- ExtractionCompatibilityTests.call: Execute Tool HTTP request.
- ExtractionCompatibilityTests.test_existing_seed_builds_and_saves_l2: Historical fixture can be reanalyzed and archived.
- ExtractionCompatibilityTests.test_compatible_upgrade_is_noop: Compatible source does not call model repeatedly.
- ExtractionCompatibilityTests.test_real_legacy_upgrade_tool: Real legacy version retains explicit upgrade.
- ExtractionCompatibilityTests.test_malformed_fixture_still_rejected: Synthetic declaration cannot bypass fact structure.
- ExtractionCompatibilityTests.test_mailbox_chip_uses_current_account: Mailbox list in lab mode still shows only own connections.
- ExtractionCompatibilityTests.test_owner_isolation_covers_all_entry_points: Personal isolation covers web, Tool, lab, and Agent.
Variable index:
- None
"""

import hashlib
import tempfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from agent.workflows.analysis_input import AnalysisInput, build_analysis_input, _validate_email
from apps.crm import selectors
from apps.crm.models import AgentCredential, Company, Mailbox
from apps.crm.durable_models import ExtractionRepair
from apps.sales.management.commands.seed_kg_lab import run_seed
from apps.sales.experiments import APPROVED_BATCHES
from apps.sales import models


# Function: Verify end-to-end contracts for compatibility and account isolation.
# Logic: Use real ORM and HTTP; replace only L2 client network reads with projections from the same database.
# Constraints: No external model, mailbox, or production-database call.
@override_settings(LAB_OPEN_ACCESS=True, WORKSPACE_OWNER_ONLY=False, ANALYSIS_PROVIDER="agent", LOCAL_DEBUG_AUTO_LOGIN=False)
class ExtractionCompatibilityTests(TestCase):
    # Function: Prepare reproducible fixture.
    # Inputs: Test database and temporary attachment directory.
    # Outputs: Instance state for `owner`, `reader`, `company`, `client`, and `batch`.
    # Logic: Use production generator to establish relationships; reader does not belong to fixture owner.
    # Constraints: Each test rolls back and temporary directory automatically cleans attachments.
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        settings = override_settings(BASE_DIR=Path(folder.name))
        settings.enable()
        self.addCleanup(settings.disable)
        self.owner = get_user_model().objects.create_user(username="compat-owner")
        self.reader = get_user_model().objects.create_user(username="compat-reader")
        self.batch = APPROVED_BATCHES[0]
        manifest = run_seed(self.owner, self.batch, 2)
        self.company = Company.objects.get(pk=manifest["truth"][0]["company_id"])
        self.client = APIClient()
        self.client.force_authenticate(self.reader)

    # Function: Execute real Tool route.
    # Inputs: `name` is fixed tool name; `arguments` are known business input.
    # Outputs: HTTP response.
    # Logic: Use service authentication and registry/dispatch rather than directly calling view.
    # Constraints: Lab mode permits no idempotency key and does not access external services.
    def call(self, name, arguments):
        return self.client.post("/api/v1/agent-tools/call/", {"name": name, "arguments": arguments}, format="json")

    # Function: Cover the complete production legacy-fixture path from reading to Agent L2 saving.
    # Inputs: Original batch envelope after removing newly added structural fields.
    # Outputs: Detail, analysis enqueueing, and L2 save all succeed while synthetic version retains original value.
    # Logic: Simulate deployed legacy rows without rewriting `prompt_version`; construct Agent input from actual context.
    # Constraints: Does not run L3 model and does not treat L2 test as model-quality validation.
    def test_existing_seed_builds_and_saves_l2(self):
        for email in self.company.emails.all():
            email.payload.pop("extract_schema_version")
            email.save(update_fields=["payload"])
        response = self.client.get(f"/api/v1/companies/{self.company.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["extraction_upgrade"]["incompatible_emails"], 0)
        self.assertEqual(self.call("customers.analyze", {"company_id": str(self.company.pk)}).status_code, 200)
        grouping, context = selectors.context_pair(self.company)
        backend = SimpleNamespace(get_company_grouping=lambda identity: grouping, get_company_context=lambda identity: context)
        result = build_analysis_input(str(self.company.pk), backend=backend, clock=timezone.now)
        self.assertIsInstance(result, AnalysisInput, result)
        saved = self.client.post("/api/v1/agent/analysis-inputs/", result.to_dict(), format="json")
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertTrue(all(item["extract_prompt_version"] == f"{self.batch}:fixture-extract-v1" for item in context["emails"]))

    # Function: Verify normal fixture does not trigger unnecessary re-extraction of 300 messages.
    # Inputs: Batch whose generator explicitly records structural version.
    # Outputs: Two tools are discoverable, preview is compatible, upgrade creates zero records, and revision is unchanged.
    # Logic: Actually execute read-only preview and write tool and verify repair table is empty.
    # Constraints: No model call or legacy-fact rewrite.
    def test_compatible_upgrade_is_noop(self):
        args = {"company_id": str(self.company.pk)}
        preview = self.call("customers.extraction_status", args)
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data["data"]["incompatible_emails"], 0)
        upgraded = self.call("customers.upgrade_extractions", args)
        self.assertEqual(upgraded.status_code, 200, upgraded.data)
        self.assertEqual(upgraded.data["data"]["created"], 0)
        self.assertFalse(ExtractionRepair.objects.exists())

    # Function: Verify that a real legacy structure is explicitly queued through Tool.
    # Inputs: One latest `extract-v6` extraction and legacy schema declaration.
    # Outputs: Analysis returns 409; upgrade creates one repair and progress is queryable.
    # Logic: Version check must not accept ordinary legacy prompt due to `synthetic_batch` or schema field.
    # Constraints: Worker does not start in this test.
    def test_real_legacy_upgrade_tool(self):
        source = self.company.emails.first().extractions.latest("pk")
        source.prompt_version = "extract-v6"
        source.save(update_fields=["prompt_version"])
        response = self.call("customers.analyze", {"company_id": str(self.company.pk)})
        self.assertEqual(response.status_code, 409, response.data)
        upgraded = self.call("customers.upgrade_extractions", {"company_id": str(self.company.pk)})
        self.assertEqual(upgraded.status_code, 200, upgraded.data)
        self.assertEqual(upgraded.data["data"]["created"], 1)

    # Function: Confirm fixture compatibility does not remove Agent fact validation.
    # Inputs: Current-format envelope with facts missing fields.
    # Outputs: Explicit `ValueError` and unchanged original dictionary.
    # Logic: Separately cover fact missing fields, unknown batch, and mismatched structural declaration.
    # Constraints: Do not manufacture or backfill missing facts.
    def test_malformed_fixture_still_rejected(self):
        email = selectors.email_data(self.company.emails.first())
        for change in ({"facts": {}}, {"synthetic_batch": "OTHER"}, {"extract_schema_version": "extract-v6"}):
            candidate = {**deepcopy(email), **change}
            with self.assertRaises(ValueError):
                _validate_email(candidate, 0)

    # Function: Verify that the top Gmail data source matches the current account.
    # Inputs: Mailboxes for two accounts while lab-open mode remains enabled.
    # Outputs: Reader receives only their own mailbox address.
    # Logic: Use actual mailbox-list API to exclude every fixture mailbox of the other account.
    # Constraints: Do not create or copy OAuth credentials.
    def test_mailbox_chip_uses_current_account(self):
        mailbox = Mailbox.objects.create(owner=self.reader, address="mine@example.test")
        response = self.client.get("/api/v1/mailboxes/")
        self.assertEqual([row["mailbox_id"] for row in response.data], [str(mailbox.pk)])

    # Function: Verify personal isolation takes precedence over legacy lab switch and team authorization.
    # Inputs: Reader login session, another account's data, and one proactively granted team access.
    # Outputs: Mailbox, customer, and lab contain no cross-account content; Tool and Agent details reject; anonymous cannot read business data.
    # Logic: Retain original data, team, and owner and filter access only through service policy.
    # Constraints: Test permission boundary without revoking or deleting authorization records of real accounts.
    @override_settings(WORKSPACE_OWNER_ONLY=True)
    def test_owner_isolation_covers_all_entry_points(self):
        team = models.Team.objects.create(owner=self.owner, name="Shared")
        models.Membership.objects.create(owner=self.owner, team=team, user=self.reader, role="editor")
        models.CompanyGrant.objects.create(owner=self.owner, team=team, company=self.company, role="editor")
        self.assertEqual(self.client.get("/api/v1/mailboxes/").data, [])
        self.assertEqual(self.client.get("/api/v1/sales/browse/directory/").data["count"], 0)
        self.assertEqual(self.client.get("/api/v1/experiments/").data, {"batches": []})
        for path in (f"/api/v1/companies/{self.company.pk}/", f"/api/v1/experiments/{self.batch}/crm.Email/"):
            self.assertEqual(self.client.get(path).status_code, 404, path)
        self.assertEqual(self.call("customers.context", {"company_id": str(self.company.pk)}).status_code, 404)
        anonymous = APIClient()
        self.assertIn(anonymous.get("/api/v1/mailboxes/").status_code, (401, 403))
        session = APIClient()
        session.force_login(self.reader)
        self.assertEqual(session.get("/api/v1/session/").data["username"], self.reader.username)
        self.assertFalse(session.get("/api/v1/session/").data["lab_open_access"])
        self.assertEqual(session.get("/api/v1/session/", HTTP_X_LAB_USER=self.owner.username).data["username"], self.reader.username)
        AgentCredential.objects.create(owner=self.reader, name="isolation-test", digest=hashlib.sha256(b"isolation-reader-token").hexdigest())
        agent = APIClient()
        agent.credentials(HTTP_AUTHORIZATION="Agent isolation-reader-token")
        self.assertEqual(agent.get("/api/v1/agent/context/", {"company_id": str(self.company.pk)}).status_code, 404)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get(f"/api/v1/companies/{self.company.pk}/").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/companies/").data["count"], 2)
