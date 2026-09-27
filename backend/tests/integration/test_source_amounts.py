"""Responsibility: Verify Agent source amounts cross Tool, storage, and public read boundaries.
Implementation: Mock only model output; run real Agent evidence checks, tool schema, serializers, database and world projection.
Relationships: Exercises agent.world_insights and the sales/agent_tools HTTP services against isolated PostgreSQL.
Directory:
- SourceAmountTests: Verify source amount transport independently of CRM.
- SourceAmountTests.setUp: Create a signed-in test account and client.
- SourceAmountTests.test_event_amount_round_trip: Preserve event fee, qualifier and evidence through real Tool writes.
- SourceAmountTests.test_news_federal_award: Preserve a sourced federal grant and its upper bound.
- SourceAmountTests.test_bare_currency_and_invalid_evidence: Reject ambiguous symbols and fabricated quotations at the Agent boundary.
- SourceAmountTests.test_zero_and_partial_update: Distinguish zero from unknown and reject inconsistent clearing.
Variable index:
- None
"""

import json
import uuid
from datetime import datetime, timezone
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from agent.world_insights import Candidate, extract_event_amount, summarize_news, _source_amount


# Function: Exercise business interfaces with isolated records and deterministic model responses.
# Logic: Tool calls use explicit laboratory test mode while production data and external providers remain untouched.
# Constraints: This suite validates contracts, not live model accuracy or source authenticity.
@override_settings(LAB_OPEN_ACCESS=True, LOCAL_DEBUG_AUTO_LOGIN=False)
class SourceAmountTests(TestCase):
    # Function: Establish a test identity.
    # Inputs: No external parameters; isolated Django test database.
    # Outputs: Authenticated API client and account in instance state.
    # Logic: Reuse normal Session authentication.
    # Constraints: No production credentials or networking.
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="source-amount-test")
        self.client = APIClient()
        self.client.force_login(self.user)

    # Function: Verify event fee transport and public projection.
    # Inputs: A fixed source quote and mocked JSON model result.
    # Outputs: Assertions on persisted REST and world responses.
    # Logic: Agent extracts an exact EUR fee, then the real Tool handler validates and stores it.
    # Constraints: No CRM opportunity supplies the tested amount.
    def test_event_amount_round_trip(self):
        quote = "Photonics Expo registration fee is EUR 250 per attendee."
        parsed = dict(amount="250", currency="EUR", amount_type="registration_fee", amount_scope="other", amount_qualifier="exact", evidence=quote, amount_evidence="EUR 250")
        candidate = Candidate("event", "Photonics", "Photonics Expo", "https://example.org/expo", quote)
        monetary = extract_event_amount(candidate, quote, lambda *args, **kwargs: json.dumps(parsed))
        payload = dict(title=candidate.title, event_type="exhibition", country="IT", city="Milan", latitude=45.46, longitude=9.19, starts_at="2026-10-13T00:00:00Z", ends_at="2026-10-15T00:00:00Z", time_precision="date", source_url=candidate.url, description=quote, data_source="agent", **monetary)
        response = self.client.post("/api/v1/agent-tools/call/", {"name": "world_events.create", "idempotency_key": str(uuid.uuid4()), "arguments": {"data": payload}}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        record = response.data["data"]
        for data in (record, self.client.get("/api/v1/sales/world/").data["results"][0], self.client.get(f'/api/v1/sales/records/world-events/{record["id"]}/').data):
            self.assertEqual(data["amount"], "250.000000")
            self.assertEqual(data["amount_qualifier"], "exact")
            self.assertEqual(data["amount_evidence"], "EUR 250")
            self.assertNotIn("map_amounts", data)

    # Function: Verify a government-issued dollar grant does not lose its bound.
    # Inputs: A named US federal issuer and a mocked complete news model result.
    # Outputs: Agent payload and persisted amount assertions.
    # Logic: Match amount and issuer within the same verbatim quote, then use the normal news endpoint.
    # Constraints: An arbitrary dollar sign or location alone is not accepted as USD.
    def test_news_federal_award(self):
        quote = "The U.S. Department of Commerce announced an R&D award of up to $1 billion to Anderon."
        parsed = dict(relevant=True, category="industry", industry="Semiconductors", country="", country_evidence="", summary="A federal R&D grant was announced.", content="The award supports semiconductor research.", company_name="Anderon", signal_type="expansion", evidence=quote, amount="1000000000", currency="USD", amount_type="grant", amount_scope="whole_project", amount_evidence=quote, amount_qualifier="up_to")
        candidate = Candidate("news", "Semiconductors", "Anderon grant", "https://example.org/grant", quote, datetime(2026, 9, 16, tzinfo=timezone.utc))
        payload = summarize_news(candidate, quote, lambda *args, **kwargs: json.dumps(parsed))
        response = self.client.post("/api/v1/sales/records/world-news/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["amount"], "1000000000.000000")
        self.assertEqual(response.data["amount_qualifier"], "up_to")

    # Function: Verify ambiguous money cannot become a supported source amount.
    # Inputs: A dollar-only source and a fabricated EUR quote.
    # Outputs: Both results remain unknown.
    # Logic: Invoke real evidence and currency checks without a network/model call.
    # Constraints: Null is not proof that the original source lacks a monetary statement.
    def test_bare_currency_and_invalid_evidence(self):
        data = dict(amount="250", currency="USD", amount_type="registration_fee", amount_scope="other", amount_qualifier="exact", evidence="Registration costs $250.", amount_evidence="$250")
        self.assertIsNone(_source_amount(data, data["evidence"])["amount"])
        data.update(currency="EUR", amount_evidence="EUR 250")
        self.assertIsNone(_source_amount(data, data["evidence"])["amount"])

    # Function: Preserve an explicit zero fee and reject incomplete amount clearing.
    # Inputs: A real zero-valued event payload in an isolated database.
    # Outputs: Zero persists; clearing only the number fails validation.
    # Logic: Reuse an explicit EUR 0 source and perform a partial update through REST.
    # Constraints: No implicit conversion of missing values to zero.
    def test_zero_and_partial_update(self):
        payload = dict(title="Free optics expo", event_type="exhibition", country="IT", city="Milan", latitude=45.46, longitude=9.19, starts_at="2026-10-13T00:00:00Z", ends_at="2026-10-15T00:00:00Z", source_url="https://example.org/free", evidence="Entry fee: EUR 0.", amount="0", currency="EUR", amount_type="registration_fee", amount_scope="other", amount_qualifier="exact", amount_evidence="EUR 0")
        response = self.client.post("/api/v1/sales/records/world-events/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["amount"], "0.000000")
        update = self.client.patch(f'/api/v1/sales/records/world-events/{response.data["id"]}/', {"amount": None}, format="json")
        self.assertEqual(update.status_code, 400)
