"""Responsibility: Verify database placeholders, global aggregation, opportunity results, and development permissions.
Implementation: Use isolated PostgreSQL to verify shared public events, private opportunity isolation, and formal/lab-mode differences; do not connect to models or external services.
Relationships: Covers `sales.world`, `algorithm_views`, `seed_development_support`, and existing Tool authorization.
Directory:
- DevelopmentSupportTests: End-to-end support-layer tests.
- DevelopmentSupportTests.setUp: Create isolated accounts and fictional batch.
- DevelopmentSupportTests.test_seed_is_idempotent_and_does_not_rewrite: Confirm explicit placeholders remain stable.
- DevelopmentSupportTests.test_world_deduplicates_and_keeps_currencies: Verify duplicate opportunities and currencies.
- DevelopmentSupportTests.test_partial_results_context_and_latest_score: Verify minimum submission and context.
- DevelopmentSupportTests.test_formal_public_events_keep_private_business: Verify public events are shared while opportunities remain isolated.
- DevelopmentSupportTests.test_lab_tools_omit_credentials_versions_and_source: Verify relaxed rules.
- DevelopmentSupportTests.test_essential_boundaries_remain: Verify essential boundaries.
Variable index:
- None
"""

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
    # Outputs: Deduplicated amounts, separate currencies, and correct country count.
    # Logic: Call the real world API.
    # Constraints: Do not verify geography services or real transactions.
    def test_world_deduplicates_and_keeps_currencies(self):
        event = WorldEvent.objects.get(pk=self.manifest["events"][1])
        event.opportunity_ids.append(str(self.opportunity.pk))
        usd = Opportunity.objects.create(owner=self.user, company=self.opportunity.company, title="美元商机", currency="USD", amount="20", status="proposal")
        event.opportunity_ids.append(str(usd.pk))
        event.save()
        response = self.client.get("/api/v1/sales/world/?country=SG")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(response.data["results"][0]["map_amounts"], {"SGD": "145000.00", "USD": "20.00"})
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
            self.assertEqual((row["opportunity_ids"], row["customers"], row["amounts"], row["map_amounts"]), ([], [], {}, {}))
        self.assertEqual(other.get(f"/api/v1/sales/opportunity-context/{self.opportunity.pk}/").status_code, 404)

    # Function: Verify lab mode omits credentials, versions, and sources.
    # Inputs: Anonymous Tool HTTP calls, publicly selected identity, and cross-account opportunity association.
    # Outputs: Reads and writes succeed without fabricated ownership.
    # Logic: Explicitly override lab settings while retaining business services and data validation.
    # Constraints: Do not execute external sends or calendar actions.
    @override_settings(LAB_OPEN_ACCESS=True, WORKSPACE_OWNER_ONLY=False)
    def test_lab_tools_omit_credentials_versions_and_source(self):
        client = APIClient()
        client.credentials(HTTP_X_LAB_USER=self.other.username)
        response = client.post("/api/v1/agent-tools/call/", {"name": "opportunity_priorities.create", "arguments": {"data": {"opportunity": str(self.opportunity.pk), "priority_score": 70}}}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        record = OpportunityPriority.objects.get(pk=response.data["data"]["id"])
        self.assertEqual(record.owner_id, self.user.pk)
        event = WorldEvent.objects.get(pk=self.manifest["events"][0])
        updated = client.patch(f"/api/v1/sales/records/world-events/{event.pk}/", {"description": "已由算法修改", "opportunity_ids": [str(self.opportunity.pk)], "data_source": "agent", "source_url": ""}, format="json")
        self.assertEqual(updated.status_code, 200, updated.data)
        context = client.post("/api/v1/agent-tools/call/", {"name": "opportunity_context.get", "arguments": {"opportunity_id": str(self.opportunity.pk)}}, format="json")
        self.assertEqual(context.status_code, 200, context.data)

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
