"""Responsibility: Verify source-backed news geography through real REST and Tool contracts.
Implementation: Use isolated database records with explicit evidence; exercise creation, public reads and partial updates.
Relationships: WorldNewsSerializer validates geography; generated Tool schema must expose exactly the same optional fields.
Directory:
- NewsLocationTests: Integration tests without external geocoding.
- NewsLocationTests.setUp: Create an isolated authenticated client.
- NewsLocationTests.test_located_news_round_trip: Persist geography and verify public reads/schema acceptance.
- NewsLocationTests.test_partial_location_is_rejected: Reject incomplete, credentialed and unsubstantiated locations.
- NewsLocationTests.test_legacy_news_stays_unlocated: Keep country-only legacy news readable without fabricated coordinates.
Variable index:
- None
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from jsonschema import validate
from apps.agent_tools.registry import build_registry


# Function: Exercise optional mapped-news geography against real application boundaries.
# Logic: Use the actual serializer, audit service, database and read handlers with fixed source fixtures.
# Constraints: No production data, real source fetching or external geocoder validation.
class NewsLocationTests(TestCase):
    # Function: Initialize isolated user and complete located-news fixture.
    # Inputs: Django test database and test instance.
    # Outputs: Authenticated client and valid news payload in instance state.
    # Logic: Create one user and explicit city/country/coordinate/evidence fields.
    # Constraints: A fixture quote establishes internal contract consistency only.
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="news-map-test")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.payload = dict(title="Photonics expansion", category="industry", published_at="2026-09-20T12:00:00Z", source_url="https://example.org/expansion", content="A photonics facility is expanding.", country="GB", city="Glasgow", latitude=55.86, longitude=-4.25, location_evidence="Manufacturing facility in Glasgow, Scotland", location_source_url="https://example.org/expansion", data_source="manual")

    # Function: Verify storage, readback and generated Tool geography contract.
    # Inputs: Complete source-backed payload from setUp.
    # Outputs: Creation/read/schema assertions with unchanged coordinates and unknown amount.
    # Logic: Validate generated create schema and call real REST create/detail endpoints.
    # Constraints: Amount remains null; location never implies money or CRM association.
    def test_located_news_round_trip(self):
        validate({"data": self.payload}, build_registry()["world_news.create"]["inputSchema"])
        response = self.client.post("/api/v1/sales/records/world-news/", self.payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        detail = self.client.get(f"/api/v1/sales/records/world-news/{response.data['id']}/")
        for field in ("city", "latitude", "longitude", "location_evidence", "location_source_url"):
            self.assertEqual(detail.data[field], self.payload[field])
        self.assertIsNone(detail.data["amount"])

    # Function: Reject incomplete or internally inconsistent news locations.
    # Inputs: Complete fixture varied one field at a time, followed by a partial update.
    # Outputs: 400 assertions and preserved persisted location.
    # Logic: Require paired coordinates, nonempty country, quoted city and public source; revalidate merged patches.
    # Constraints: Do not infer missing fields from the original news source or publisher.
    def test_partial_location_is_rejected(self):
        for patch in ({"longitude": None}, {"country": ""}, {"location_evidence": "Another city"}, {"location_source_url": "https://user:pass@example.org/location"}):
            with self.subTest(patch=patch):
                response = self.client.post("/api/v1/sales/records/world-news/", {**self.payload, **patch}, format="json")
                self.assertEqual(response.status_code, 400, response.data)
        created = self.client.post("/api/v1/sales/records/world-news/", self.payload, format="json").data
        response = self.client.patch(f"/api/v1/sales/records/world-news/{created['id']}/", {"latitude": None}, format="json", HTTP_IF_MATCH=str(created["revision"]))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(f"/api/v1/sales/records/world-news/{created['id']}/").data["latitude"], self.payload["latitude"])

    # Function: Preserve country-only news without a mapped city.
    # Inputs: Legacy-style payload with no optional location fields.
    # Outputs: Successful write with empty city and null coordinates.
    # Logic: Explicit country is independently useful for news and does not force a map location.
    # Constraints: No automatic capital-city or headquarters fallback.
    def test_legacy_news_stays_unlocated(self):
        payload = {key: value for key, value in self.payload.items() if key not in {"city", "latitude", "longitude", "location_evidence", "location_source_url"}}
        response = self.client.post("/api/v1/sales/records/world-news/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["city"], "")
        self.assertIsNone(response.data["latitude"])
