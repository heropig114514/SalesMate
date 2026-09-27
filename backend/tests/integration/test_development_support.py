"""Responsibility: Verify database placeholders, global aggregation, opportunity results, and development permissions.
Implementation: Use isolated PostgreSQL to verify shared public events, private opportunity isolation, and authentication and ownership in both formal and fixture modes; do not connect to models or external services.
Relationships: Covers `sales.world`, `algorithm_views`, `seed_development_support`, and existing Tool authorization.
Directory:
- DevelopmentSupportTests: End-to-end support-layer tests.
- DevelopmentSupportTests.setUp: Create isolated accounts and fictional batch.
- DevelopmentSupportTests.test_seed_is_idempotent_and_does_not_rewrite: Confirm explicit placeholders remain stable.
- DevelopmentSupportTests.test_world_does_not_use_crm_amounts: Verify private CRM values cannot populate event source amounts.
- DevelopmentSupportTests.test_partial_results_context_and_latest_score: Verify minimum submission and context.
- DevelopmentSupportTests.test_formal_public_events_keep_private_business: Verify public events are shared while opportunities remain isolated.
- DevelopmentSupportTests.test_lab_tools_require_identity_and_versions: Verify owner Tool writes retain authentication and versions.
- DevelopmentSupportTests.test_essential_boundaries_remain: Verify essential boundaries.
Variable index:
- None
"""

import uuid
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from apps.sales.models import Opportunity, OpportunityPriority, WorldEvent
from apps.sales.management.commands.seed_development_support import seed_support


# Function: Verify the added support layer rather than algorithm calculation.
# Logic: Use database placeholders and real APIs with explicit mode overrides to prevent local configuration from affecting conclusions.
# Constraints: No real customer data, credentials, or network.
@override_settings(DEBUG=True, LAB_OPEN_ACCESS=False, WORKSPACE_OWNER_ONLY=False, LOCAL_DEBUG_AUTO_LOGIN=False)
class DevelopmentSupportTests(TestCase):
    # Function: Establish two isolated accounts.
    # Inputs: No external parameters.
    # Outputs: Test users, client, and batch.
    # Logic: Owner imports placeholders; other account verifies unauthorized access.
    # Constraints: Uses Django test database.
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="support-lab")
        self.other = get_user_model().objects.create_user(username="support-other")
        self.manifest = seed_support(self.user)
        self.client = APIClient()
        self.client.force_login(self.user)
        self.opportunity = Opportunity.objects.get(pk=self.manifest["opportunities"][0])

    # Function: Check repeated placeholder execution and retention of existing edits.
    # Inputs: Existing import manifest and modified event title.
    # Outputs: Records are not duplicated and dates and edits are not rewritten.
    # Logic: Repeatedly call the real initialization function.
    # Constraints: Do not mistake placeholders for live news.
    def test_seed_is_idempotent_and_does_not_rewrite(self):
        event = WorldEvent.objects.get(pk=self.manifest["events"][0])
        event.title = "人工已编辑"
        event.save()
        self.assertEqual(seed_support(self.user), self.manifest)
        event.refresh_from_db()
        self.assertEqual(event.title, "人工已编辑")
        self.assertEqual(WorldEvent.objects.count(), 8)

    # Function: Verify map amounts and private-event permissions.
    # Inputs: Duplicate association at the same coordinates and another-currency opportunity for the same customer.
    # Outputs: Null source amounts despite linked CRM values, and correct country count.
    # Logic: Call the real world API.
    # Constraints: Do not verify geography services or real transactions.
    def test_world_does_not_use_crm_amounts(self):
        event = WorldEvent.objects.get(pk=self.manifest["events"][1])
        event.opportunity_ids.append(str(self.opportunity.pk))
        usd = Opportunity.objects.create(owner=self.user, company=self.opportunity.company, title="美元商机", currency="USD", amount="20", status="proposal")
        event.opportunity_ids.append(str(usd.pk))
        event.save()
        response = self.client.get("/api/v1/sales/world/?country=SG")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)
        self.assertIsNone(response.data["results"][0]["amount"])
        self.assertNotIn("map_amounts", response.data["results"][0])
        self.assertEqual(next(row for row in response.data["countries"] if row["code"] == "SG")["event_count"], 2)

    # Function: Verify that opportunity results can be submitted without complete explanation.
    # Inputs: Explicit opportunity, score, and custom signal type.
    # Outputs: Automatic company ownership, latest result in list, and original material in context.
    # Logic: Create through existing CRUD, then query board and context.
    # Constraints: No scoring algorithm executes and company-level score is unchanged.
    def test_partial_results_context_and_latest_score(self):
        response = self.client.post("/api/v1/sales/records/opportunity-priorities/", {"opportunity": str(self.opportunity.pk), "priority_score": 99}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(str(response.data["company"]), str(self.opportunity.company_id))
        signal = self.client.post("/api/v1/sales/records/opportunity-signals/", {"opportunity": str(self.opportunity.pk), "signal_type": "CUSTOM_ALGORITHM_SIGNAL", "signal_value": {"new": "shape"}}, format="json")
        self.assertEqual(signal.status_code, 201, signal.data)
        board = self.client.get("/api/v1/sales/priority-board/")
        self.assertEqual(board.status_code, 200, board.data)
        self.assertEqual(board.data["results"][0]["priority"]["priority_score"], 99)
        context = self.client.get(f"/api/v1/sales/opportunity-context/{self.opportunity.pk}/")
        self.assertEqual(context.status_code, 200, context.data)
        self.assertEqual(len(context.data["signals"]), 2)
        self.assertTrue(context.data["seller_context"]["sales_setup"]["products"])

    # Function: Verify shared public events and formal-business isolation.
    # Inputs: No external parameters; anonymous and another logged-in account in the fixture.
    # Outputs: Anonymous access is rejected; logged-in worker sees public events but not private associations or context.
    # Logic: Use real Session requests to check event counts, field projections, and opportunity permissions together.
    # Constraints: Do not mock permissions or expand customer or opportunity reads.
    def test_formal_public_events_keep_private_business(self):
        other = APIClient()
        self.assertEqual(other.get("/api/v1/sales/world/").status_code, 403)
        other.force_login(self.other)
        world = other.get("/api/v1/sales/world/").data
        self.assertEqual(world["count"], 8)
        for row in world["results"]:
            self.assertEqual(row["opportunity_ids"], [])
            self.assertIsNone(row["amount"])
        self.assertEqual(other.get(f"/api/v1/sales/opportunity-context/{self.opportunity.pk}/").status_code, 404)

    # Function: Preserve owner tool writes without weakening authentication or concurrency.
    # Inputs: Anonymous, foreign, and owner Session clients; synthetic opportunity and event.
    # Outputs: Unauthorized access rejects, owner writes succeed with idempotency/version, and private context stays isolated.
    # Logic: Keep fixture source allowances while testing real authorization and original result creation.
    # Constraints: No provider, model, or external action executes.
    @override_settings(LAB_OPEN_ACCESS=True, WORKSPACE_OWNER_ONLY=False)
    def test_lab_tools_require_identity_and_versions(self):
        client = APIClient()
        payload = {"name": "opportunity_priorities.create", "arguments": {"data": {"opportunity": str(self.opportunity.pk), "priority_score": 70}}, "idempotency_key": str(uuid.uuid4())}
        self.assertEqual(client.post("/api/v1/agent-tools/call/", payload, format="json").status_code, 401)
        client.force_login(self.other)
        context = {"name": "opportunity_context.get", "arguments": {"opportunity_id": str(self.opportunity.pk)}}
        self.assertEqual(client.post("/api/v1/agent-tools/call/", context, format="json").status_code, 404)
        client.force_login(self.user)
        response = client.post("/api/v1/agent-tools/call/", payload, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        record = OpportunityPriority.objects.get(pk=response.data["data"]["id"])
        self.assertEqual(record.owner_id, self.user.pk)
        event = WorldEvent.objects.get(pk=self.manifest["events"][0])
        values = {"description": "已由算法修改", "opportunity_ids": [str(self.opportunity.pk)], "data_source": "agent", "source_url": ""}
        url = f"/api/v1/sales/records/world-events/{event.pk}/"
        self.assertEqual(client.patch(url, values, format="json").status_code, 400)
        updated = client.patch(url, values, format="json", HTTP_IF_MATCH=str(event.revision))
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(client.post("/api/v1/agent-tools/call/", context, format="json").status_code, 200)

    # Function: Retain essential numeric and source safety boundaries.
    # Inputs: Out-of-range score and unsafe link.
    # Outputs: 400 and no erroneous result write.
    # Logic: Open mode still applies data boundaries that can be displayed and stored.
    # Constraints: Do not verify algorithm formulas.
    @override_settings(LAB_OPEN_ACCESS=True, WORKSPACE_OWNER_ONLY=False)
    def test_essential_boundaries_remain(self):
        response = self.client.post("/api/v1/sales/records/opportunity-priorities/", {"opportunity": str(self.opportunity.pk), "priority_score": 101}, format="json")
        self.assertEqual(response.status_code, 400)
        event = WorldEvent.objects.get(pk=self.manifest["events"][0])
        response = self.client.patch(f"/api/v1/sales/records/world-events/{event.pk}/", {"source_url": "javascript:alert(1)"}, format="json")
        self.assertEqual(response.status_code, 400)
