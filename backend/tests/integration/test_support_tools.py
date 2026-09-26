"""Responsibility: Validate software-support tools' real HTTP, permission, transaction, and persistence contracts.
Implementation: Isolated PostgreSQL and ordinary-user Tool tokens; profiles/files do not require browser Sessions, and external networking is not called.
Relationships: agent_tools.support/presets, sales.insights, and accounts.onboarding; no validation of models, live news, or real sending.
Directory:
- SupportToolTests: Software-support tool integration scenarios.
- SupportToolTests.setUp: Create the owner, another user, and restricted tokens.
- SupportToolTests.call: Call through HTTP and verify status.
- SupportToolTests.product: Return complete reference-product input.
- SupportToolTests.test_profiles_and_legacy_catalog: Profile persistence and stable historical entry identifiers.
- SupportToolTests.test_catalog_crud_links_and_conflicts: Individual CRUD, link isolation, and revision conflicts.
- SupportToolTests.test_documents_roundtrip_and_references: Session-free uploads, chunked reads, and referenced-file deletion protection.
- SupportToolTests.test_existing_attachment_reads: Real storage reads and integrity validation for existing business attachments.
- SupportToolTests.test_events_news_crud_and_filters: Event/news storage, filtering, editing, and archival.
- SupportToolTests.test_invalid_insights_and_private_relations: Invalid-data rejection, public-news reads, and cross-account link isolation.
- SupportToolTests.test_permission_presets_and_token_boundaries: Batch authorization and frozen token scope.
Variable index:
- BASE: Tool HTTP path.
"""

import base64
import hashlib
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import SalesSetup, SetupDocument
from apps.agent_tools.models import ToolCredential, ToolCall, ToolProposal
from apps.agent_tools.registry import build_registry
from apps.sales import models, grouping

BASE = "/api/v1/agent-tools/"


# Function: Verify callable data capabilities.
# Logic: HTTP requests traverse real tool authentication and business services; Django isolates the database.
# Constraints: Tokens are synthetic; no external providers are contacted.
class SupportToolTests(TestCase):
    # Function: Create test identities.
    # Inputs: No external arguments.
    # Outputs: Owner/other users, a Tool client, and a Session client used only for authorization administration.
    # Logic: Authorization explicitly includes current tool names; all business calls use tokens.
    # Constraints: Do not create production credentials.
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="support-user")
        self.other = get_user_model().objects.create_user(username="support-other")
        self.credential = ToolCredential.objects.create(owner=self.user, name="test", digest=hashlib.sha256(b"support-token").hexdigest(), allowed_tools=list(build_registry()), expires_at=timezone.now() + timedelta(hours=1))
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Tool support-token")
        self.human = APIClient()
        self.human.force_login(self.user)

    # Function: Call the real tool interface.
    # Inputs: `name` identifies the tool, `args` contains inputs, `expected` is HTTP status, and `key` is an optional explicit idempotency key.
    # Outputs: Successful receipt or expected error response.
    # Logic: Write tools generate a key for this test operation by default; explicit keys verify replay. Non-DRF errors report status only, avoiding uploaded-body echoes.
    # Constraints: Do not replace business handlers or authentication.
    def call(self, name, args, expected=200, key=None):
        data = {"name": name, "arguments": args}
        if build_registry()[name]["executionMode"] != "read":
            data["idempotency_key"] = key or str(uuid.uuid4())
        response = self.client.post(BASE + "call/", data, format="json")
        self.assertEqual(response.status_code, expected, getattr(response, "data", response.status_code))
        return response.data

    # Function: Create a reference-product fixture.
    # Inputs: No external arguments.
    # Outputs: Dictionary satisfying the existing profile-entry contract.
    # Logic: Use explicit currency and unknown price without inventing a formal quote.
    # Constraints: Return data only; create no business records.
    def product(self):
        return {"name": "检测设备", "category": "A", "specifications": ["尺寸 10 cm"], "price_min": None, "price_max": None, "currency": "SGD", "scenarios": ["量测"], "document_id": None}

    # Function: Verify original profile-interface and historical-catalog compatibility.
    # Inputs: Legacy product lists without IDs and modification requests.
    # Outputs: GET writes nothing; deleting the first row preserves other IDs, and stale revisions conflict explicitly.
    # Logic: Read then write through the HTTP path shared by MCP and compare persisted records.
    # Constraints: Do not trigger model calls.
    def test_profiles_and_legacy_catalog(self):
        self.assertEqual(self.call("sales_setup.get", {})["data"]["revision"], 0)
        self.assertFalse(SalesSetup.objects.filter(owner=self.user).exists())
        self.call("company_profile.update", {"revision": 0, "data": {"company_name": "本公司"}})
        self.assertEqual(self.call("company_profile.get", {})["data"]["company_name"], "本公司")
        self.call("company_profile.update", {"revision": 0, "data": {"company_name": "冲突"}}, 409)
        row = SalesSetup.objects.create(owner=self.user, products=[self.product(), {**self.product(), "name": "第二项"}])
        first = self.call("setup_products.list", {})["data"]["results"]
        row.refresh_from_db()
        self.assertNotIn("id", row.products[0])
        self.call("setup_products.delete", {"revision": 0, "id": first[0]["id"]})
        second = self.call("setup_products.list", {})["data"]["results"]
        self.assertEqual(second[0]["id"], first[1]["id"])
        self.assertEqual(self.call("seller_profile.get", {})["data"]["revision"], 0)

    # Function: Verify individual catalog operations and link permissions.
    # Inputs: Owned/foreign transaction products, repeated requests, and expired versions.
    # Outputs: Correct entry CRUD, revisions, and idempotency; links to other accounts cannot save.
    # Logic: Execute real calls and check for unintended transaction records or confirmation proposals.
    # Constraints: Reference catalogs link only explicitly selected products.
    def test_catalog_crud_links_and_conflicts(self):
        foreign = models.Product.objects.create(owner=self.other, sku="other", name="其他", currency="SGD", unit_price=1)
        mine = models.Product.objects.create(owner=self.user, sku="mine", name="目录", currency="SGD", unit_price=2)
        self.call("setup_products.create", {"revision": 0, "data": {**self.product(), "linked_product_id": str(foreign.pk)}}, 400)
        key = str(uuid.uuid4())
        args = {"revision": 0, "data": {**self.product(), "linked_product_id": str(mine.pk)}}
        created = self.call("setup_products.create", args, key=key)
        self.assertTrue(self.call("setup_products.create", args, key=key)["replayed"])
        item = created["data"]["item"]
        self.assertEqual(self.call("setup_products.get", {"id": item["id"]})["data"]["item"]["linked_product_id"], str(mine.pk))
        self.call("setup_products.update", {"id": item["id"], "revision": 0, "data": {"name": "旧版"}}, 409)
        updated = self.call("setup_products.update", {"id": item["id"], "revision": 1, "data": {"name": "更新"}})["data"]
        self.assertEqual(updated["item"]["id"], item["id"])
        self.assertEqual(self.call("setup_products.list", {"q": "更新", "page_size": 1})["data"]["count"], 1)
        self.call("setup_products.delete", {"id": item["id"], "revision": 2})
        self.call("setup_products.get", {"id": item["id"]}, 404)
        self.assertEqual(models.Product.objects.count(), 2)
        self.assertEqual(ToolProposal.objects.count(), 0)

    # Function: Verify the complete file/solution path without Session.
    # Inputs: UTF-8, TXT at the 5 MiB limit, PDF, invalid encoding, and other accounts' files.
    # Outputs: Chunks reconstruct original text; limit-sized files upload, while oversized files/ordinary-tool payloads fail. Referenced files cannot be deleted; unreferenced deletion succeeds idempotently.
    # Logic: Real upload, linking, chunking, and deletion with no browser Cookie.
    # Constraints: PDF checks cover raw-byte reading only, not text parsing.
    def test_documents_roundtrip_and_references(self):
        content = "第一行\n第二行🙂".encode()
        doc = self.call("setup_documents.upload", {"name": "方案.txt", "content_base64": base64.b64encode(content).decode()})["data"]
        self.assertFalse(self.client.cookies)
        part = self.call("setup_documents.read", {"id": doc["id"], "format": "base64", "offset": 0, "limit": 5})["data"]
        tail = self.call("setup_documents.read", {"id": doc["id"], "format": "base64", "offset": part["next_offset"], "limit": 100})["data"]
        self.assertEqual(base64.b64decode(part["content"]) + base64.b64decode(tail["content"]), content)
        self.assertIsNone(tail["next_offset"])
        text = self.call("setup_documents.read", {"id": doc["id"], "format": "text", "offset": 0, "limit": 4})["data"]
        self.assertEqual(text["content"], "第一行\n")
        solution = self.call("solutions.create", {"revision": 0, "data": {"name": "方案", "document_id": doc["id"]}})["data"]["item"]
        self.call("setup_documents.delete", {"id": doc["id"]}, 409)
        self.assertEqual(len(self.call("setup_documents.get", {"id": doc["id"]})["data"]["references"]), 1)
        self.call("solutions.delete", {"revision": 1, "id": solution["id"]})
        key = str(uuid.uuid4())
        self.call("setup_documents.delete", {"id": doc["id"]}, key=key)
        self.assertTrue(self.call("setup_documents.delete", {"id": doc["id"]}, key=key)["replayed"])
        other = SetupDocument.objects.create(owner=self.other, name="private.txt", content_type="text/plain", content=b"secret")
        self.call("setup_documents.read", {"id": str(other.pk), "format": "text", "offset": 0, "limit": 10}, 404)
        self.call("setup_documents.upload", {"name": "bad.txt", "content_base64": "!!!"}, 400)
        pdf = self.call("setup_documents.upload", {"name": "test.pdf", "content_base64": base64.b64encode(b"%PDF-1.4\n").decode()})["data"]
        self.call("setup_documents.read", {"id": pdf["id"], "format": "text", "offset": 0, "limit": 10}, 400)
        large = self.call("setup_documents.upload", {"name": "large.txt", "content_base64": base64.b64encode(b"a" * (5 * 1024 * 1024)).decode()})["data"]
        self.assertEqual(self.call("setup_documents.get", {"id": large["id"]})["data"]["size"], 5 * 1024 * 1024)
        self.call("setup_documents.upload", {"name": "oversize.txt", "content_base64": base64.b64encode(b"a" * (6 * 1024 * 1024)).decode()}, 400)
        self.call("company_profile.update", {"revision": 0, "data": {"company_name": "a" * (3 * 1024 * 1024)}}, 400)

    # Function: Verify existing attachments can be read without Session download links.
    # Inputs: Real files in temporary private storage and subsequently tampered contents.
    # Outputs: Readable contents; integrity errors and unauthorized access are rejected.
    # Logic: Replace only the test storage root while preserving reads, permissions, and SHA-256 checks.
    # Constraints: Tests manage temporary directories without writing production attachments.
    def test_existing_attachment_reads(self):
        company = grouping.create_company(self.user, "客户")
        with tempfile.TemporaryDirectory() as folder, override_settings(BASE_DIR=Path(folder)):
            path = Path(folder) / "private_uploads" / str(self.user.pk) / "file"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"hello")
            record = models.Attachment.objects.create(owner=self.user, company=company, name="file.txt", storage_key=f"{self.user.pk}/file", content_type="text/plain", size=5, sha256=hashlib.sha256(b"hello").hexdigest())
            args = {"id": str(record.pk), "format": "text", "offset": 0, "limit": 10}
            self.assertEqual(self.call("files.read", args)["data"]["content"], "hello")
            path.write_bytes(b"wrong")
            self.call("files.read", args, 400)

    # Function: Verify event/news interfaces and MCP use the same data.
    # Inputs: Synthetic events/news with explicit sources, times, and categories.
    # Outputs: Correct queries, modifications, archival, restoration, and time filtering.
    # Logic: Write as a Tool identity, then query original business APIs to verify persistence.
    # Constraints: No live-news collection or value/priority calculation.
    def test_events_news_crud_and_filters(self):
        args = {"title": "行业展会", "event_type": "exhibition", "country": "SG", "city": "Singapore", "latitude": 1.3, "longitude": 103.8, "starts_at": "2026-10-01T09:00:00+08:00", "ends_at": "2026-10-02T18:00:00+08:00", "source_url": "https://example.com/event"}
        created = self.call("world_events.create", {"data": args})["data"]
        self.assertEqual(self.call("world_events.list", {"country": "SG", "from": "2026-10-01T00:00:00+08:00", "to": "2026-10-03T00:00:00+08:00"})["data"]["count"], 1)
        self.assertEqual(self.call("world_events.list", {"country": "DE"})["data"]["count"], 0)
        updated = self.call("world_events.update", {"id": created["id"], "revision": created["revision"], "data": {"city": "Updated"}})["data"]
        archived = self.call("world_events.archive", {"id": created["id"], "revision": updated["revision"], "archived": True})["data"]
        self.assertEqual(self.call("world_events.list", {})["data"]["count"], 0)
        self.call("world_events.archive", {"id": created["id"], "revision": archived["revision"], "archived": False})
        news = self.call("world_news.create", {"data": {"title": "消息", "category": "industry", "published_at": "2026-09-21T00:00:00Z", "source_url": "https://example.com/news", "content": "原文"}})["data"]
        response = self.human.get("/api/v1/sales/records/world-news/", {"category": "industry"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["results"][0]["id"], news["id"])
        self.assertEqual(ToolProposal.objects.count(), 0)

    # Function: Verify public-news reads and time, source, and private-opportunity-link boundaries.
    # Inputs: Timezone-free timestamps, unsafe links, reversed windows, and other employees' records.
    # Outputs: Public news is readable across accounts; invalid inputs/private-opportunity links are rejected without successful receipts.
    # Logic: Real JSON Schema, serializers, and owner queries operate together.
    # Constraints: Share public news only; customer/opportunity links remain isolated.
    def test_invalid_insights_and_private_relations(self):
        data = {"title": "消息", "category": "price", "published_at": "2026-09-21T00:00:00Z", "source_url": "https://example.com/news", "content": "内容"}
        for field, value in (("source_url", "javascript:alert(1)"), ("published_at", "2026-09-21T00:00:00"), ("owner", self.other.pk)):
            self.call("world_news.create", {"data": {**data, field: value}}, 400)
        self.assertEqual(ToolCall.objects.count(), 0)
        self.assertFalse(models.WorldNews.objects.exists())
        foreign = models.WorldNews.objects.create(owner=self.other, **data)
        self.assertEqual(self.call("world_news.get", {"id": str(foreign.pk)})["data"]["id"], str(foreign.pk))
        self.call("world_news.list", {"from": "2026-10-01T00:00:00Z", "to": "2026-09-01T00:00:00Z"}, 400)
        company = grouping.create_company(self.other, "他人客户")
        opportunity = models.Opportunity.objects.create(owner=self.other, company=company, title="商机", currency="SGD")
        event = {"title": "活动", "event_type": "sales", "country": "SG", "city": "SG", "latitude": 1, "longitude": 103, "starts_at": "2026-10-01T00:00:00Z", "ends_at": "2026-10-02T00:00:00Z", "source_url": "https://example.com/event", "opportunity_ids": [str(opportunity.pk)]}
        self.call("world_events.create", {"data": event}, 400)

    # Function: Verify direct calls after one authorization and frozen scope.
    # Inputs: Explicitly selected read_only/data_management presets and restricted Tool tokens.
    # Outputs: Reads require no repeated confirmation, writes succeed only when authorized, and tool tokens cannot authorize themselves.
    # Logic: Create preset credentials through Session, then call with the new Tool token; verify new tools cannot expand issued scope.
    # Constraints: Do not remove existing proposal or real external-action confirmation.
    def test_permission_presets_and_token_boundaries(self):
        presets = self.client.get(BASE + "permission-presets/").data["presets"]
        self.assertIn("setup_documents.read", presets["read_only"]["allowed_tools"])
        self.assertNotIn("setup_products.create", presets["read_only"]["allowed_tools"])
        self.assertIn("world_events.archive", presets["data_management"]["allowed_tools"])
        self.assertNotIn("customers.merge", presets["data_management"]["allowed_tools"])
        response = self.human.post(BASE + "credentials/", {"name": "reader", "preset": "read_only", "expires_in_hours": 1}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.client.credentials(HTTP_AUTHORIZATION="Tool " + response.data["token"])
        self.call("company_profile.get", {})
        self.call("company_profile.update", {"revision": 0, "data": {"company_name": "不允许"}}, 403)
        self.assertEqual(self.client.post(BASE + "credentials/", {"name": "escalate", "preset": "data_management", "expires_in_hours": 1}, format="json").status_code, 403)
        self.assertEqual(self.human.post(BASE + "credentials/", {"name": "ambiguous", "preset": "read_only", "allowed_tools": ["sales_setup.get"], "expires_in_hours": 1}, format="json").status_code, 400)
        saved = ToolCredential.objects.get(pk=response.data["id"])
        self.assertEqual(saved.allowed_tools, presets["read_only"]["allowed_tools"])
