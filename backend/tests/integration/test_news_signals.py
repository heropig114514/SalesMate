"""Responsibility: Validate real HTTP, tool contracts, and storage compatibility for public-news sales leads.
Implementation: Use isolated PostgreSQL to test complete/empty payloads, exact amounts, evidence combinations, partial updates, and legacy-data migration.
Relationships: WorldNews, WorldNewsSerializer, and Tool Schema; no news-source calls or Agent changes.
Directory:
- NewsSignalTests: Public-lead interfaces and consistency verification.
- NewsSignalTests.setUp: Create isolated employees, tool identities, and API clients.
- NewsSignalTests.payload: Construct a complete public payload following the collaboration contract.
- NewsSignalTests.call: Call the actual Tool entry point.
- NewsSignalTests.test_full_payload_round_trip_permissions_and_no_crm: Round-trip every field without creating or linking CRM records.
- NewsSignalTests.test_empty_and_legacy_payloads: Compatibility with empty values and old clients omitting fields.
- NewsSignalTests.test_invalid_payloads_are_rejected: Amount, enum, length, and evidence boundaries.
- NewsSignalTests.test_partial_updates_validate_merged_record: Updates must preserve complete evidence combinations.
- NewsSignalTests.test_schema_accepts_empty_enums_and_decimal_strings: Published Tool Schema remains synchronized with the write contract.
- NewsSignalTests.test_database_amount_constraint: Incomplete amount combinations cannot be written even by bypassing the interface.
- NewsSignalMigrationTests: Legacy-record compatibility migration.
- NewsSignalMigrationTests.test_preserves_existing_news: Migrate from 0009 while preserving original text and record revisions.
Variable index:
- SIGNAL_FIELDS: Names of thirteen added fields for individual round-trip checks.
"""

import hashlib
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.agent_tools.models import ToolCredential
from apps.agent_tools.registry import build_registry
from apps.agent_tools.schemas import validate
from apps.crm.models import Company
from apps.sales.models import Opportunity, WorldNews

SIGNAL_FIELDS = ("company_name", "signal_type", "project_name", "demand_description", "potential_sales_need", "opportunity_reason", "time_window", "evidence", "amount", "currency", "amount_type", "amount_scope", "amount_evidence")


# Function: Verify public-news leads are usable without expanding private CRM permissions.
# Logic: Real HTTP and database with public laboratory access and automatic login disabled.
# Constraints: All companies, evidence, and sources are fixtures, not acceptance evidence for external Agents.
@override_settings(LAB_OPEN_ACCESS=False, WORKSPACE_OWNER_ONLY=False, LOCAL_DEBUG_AUTO_LOGIN=False)
class NewsSignalTests(TestCase):
    # Function: Create least-privilege test identities.
    # Inputs: No external arguments.
    # Outputs: Instance owner, reader, http, and tool.
    # Logic: The token permits only news creation/update/read; reader has no owner write permission.
    # Constraints: Fixed test tokens are not used in real databases or services.
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="signal-owner")
        self.reader = get_user_model().objects.create_user(username="signal-reader")
        self.http, self.tool = APIClient(), APIClient()
        self.http.force_login(self.owner)
        ToolCredential.objects.create(owner=self.owner, name="signal-test", digest=hashlib.sha256(b"news-signal-test").hexdigest(), allowed_tools=["world_news." + op for op in ("create", "update", "list", "get")], expires_at=timezone.now() + timedelta(hours=1))
        self.tool.credentials(HTTP_AUTHORIZATION="Tool news-signal-test")

    # Function: Construct news facts supported by original text and separate inferences.
    # Inputs: No arguments.
    # Outputs: A fresh complete dictionary per call.
    # Logic: Fix the company, total investment, and whole-project scope; amount text appears in evidence.
    # Constraints: example.org is not accessed; inference does not establish confirmed procurement.
    def payload(self):
        return {"title": "测试新闻", "category": "industry", "industry": "半导体", "country": "CN", "published_at": "2026-09-24T08:00:00Z", "source_url": "https://example.org/signal", "summary": "公开摘要", "content": "公开新闻正文", "data_source": "agent", "company_name": "测试制造公司", "signal_type": "new_factory", "project_name": "测试基地", "demand_description": "建设生产线", "potential_sales_need": "可能需要检测设备", "opportunity_reason": "生产线可能需要检测环节", "time_window": "2027 年投产", "evidence": "测试制造公司宣布测试基地总投资人民币 5000 万元，2027 年投产。", "amount": "50000000", "currency": "CNY", "amount_type": "total_investment", "amount_scope": "whole_project", "amount_evidence": "总投资人民币 5000 万元"}

    # Function: Execute a real tool call.
    # Inputs: `name` is the tool name, `arguments` its payload, and `status` the expected HTTP status.
    # Outputs: Response dictionary.
    # Logic: Writes carry independent idempotency keys without bypassing schemas or serializers.
    # Constraints: Use only the test client, not running services.
    def call(self, name, arguments, status=200):
        body = {"name": name, "arguments": arguments}
        if name.endswith((".create", ".update")):
            body["idempotency_key"] = str(uuid.uuid4())
        response = self.tool.post("/api/v1/agent-tools/call/", body, format="json")
        self.assertEqual(response.status_code, status, response.data)
        return response.data

    # Function: Verify field consistency across tool writes and every news-read entry point.
    # Inputs: Complete fixtures and two ordinary employee identities.
    # Outputs: Thirteen-field round-trip, shared reads with isolated writes, and unchanged CRM counts.
    # Logic: Write first, then read Tool/API lists and details, checking ordinary and owner_only modes separately.
    # Constraints: Shared amounts are publicly reported amounts, not access to other employees' opportunities.
    def test_full_payload_round_trip_permissions_and_no_crm(self):
        counts = (Company.objects.count(), Opportunity.objects.count())
        payload = self.payload()
        created = self.call("world_news.create", {"data": payload})["data"]
        expected = {field: payload[field] for field in SIGNAL_FIELDS}
        expected["amount"] = "50000000.000000"
        self.assertEqual({field: created[field] for field in SIGNAL_FIELDS}, expected)
        self.assertEqual(str(WorldNews.objects.get(pk=created["id"]).amount), expected["amount"])
        self.assertEqual((Company.objects.count(), Opportunity.objects.count()), counts)
        for operation, arguments in (("get", {"id": created["id"]}), ("list", {})):
            data = self.call("world_news." + operation, arguments)["data"]
            row = data if operation == "get" else data["results"][0]
            self.assertEqual({field: row[field] for field in SIGNAL_FIELDS}, expected)
        self.http.force_login(self.reader)
        for owner_only in (False, True):
            with self.settings(WORKSPACE_OWNER_ONLY=owner_only):
                for path in ("/api/v1/sales/records/world-news/", f'/api/v1/sales/records/world-news/{created["id"]}/'):
                    response = self.http.get(path)
                    self.assertEqual(response.status_code, 200)
                    row = response.data["results"][0] if "results" in response.data else response.data
                    self.assertEqual({field: row[field] for field in SIGNAL_FIELDS}, expected)
                response = self.http.patch(f'/api/v1/sales/records/world-news/{created["id"]}/', {"company_name": "改写"}, format="json", HTTP_IF_MATCH=str(created["revision"]))
                self.assertIn(response.status_code, (403, 404))

    # Function: Accept non-lead news and old clients.
    # Inputs: Two news payloads with explicit empty values or all thirteen fields omitted.
    # Outputs: All text is empty, amount is null, and creation succeeds.
    # Logic: Exercise both Tool and REST and verify zero amounts are not unknown.
    # Constraints: Do not enrich or backfill automatically.
    def test_empty_and_legacy_payloads(self):
        payload = self.payload()
        payload.update({field: None if field == "amount" else "" for field in SIGNAL_FIELDS})
        row = self.call("world_news.create", {"data": payload})["data"]
        self.assertEqual({field: row[field] for field in SIGNAL_FIELDS}, {field: payload[field] for field in SIGNAL_FIELDS})
        legacy = {key: value for key, value in payload.items() if key not in SIGNAL_FIELDS}
        legacy["source_url"] += "/legacy"
        response = self.http.post("/api/v1/sales/records/world-news/", legacy, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(response.data["amount"])
        self.assertEqual(response.data["company_name"], "")
        payload = self.payload()
        payload.update(source_url="https://example.org/zero", amount="0", evidence="预算 CNY 0", amount_evidence="预算 CNY 0")
        self.assertEqual(self.call("world_news.create", {"data": payload})["data"]["amount"], "0.000000")

    # Function: Reject inputs that violate factual scope or numeric precision.
    # Inputs: Payloads individually altering amount type, bounds, enums, length, and evidence.
    # Outputs: HTTP 400 without persistence; the maximum allowed value saves exactly.
    # Logic: Validate Tool and REST; floating point or database rounding cannot conceal excess values.
    # Constraints: Verify internal contract consistency, not external article truthfulness.
    def test_invalid_payloads_are_rejected(self):
        patches = [{"amount": value} for value in (1, 1.25, "-1", "1e3", "NaN", "1.1234567", "1" * 25, None)]
        patches += [{"currency": ""}, {"currency": "XXX"}, {"amount_type": "sales_value"}, {"amount_scope": "city"}, {"amount_evidence": "不在原文中"}, {"evidence": ""}, {"amount_evidence": " "}, {"signal_type": "unknown"}, {"company_name": "x" * 241}, {"demand_description": "x" * 501}, {"evidence": "x" * 601}, {"amount_evidence": "x" * 401}]
        for patch in patches:
            with self.subTest(patch=patch):
                data = {**self.payload(), **patch}
                self.call("world_news.create", {"data": data}, 400)
                response = self.http.post("/api/v1/sales/records/world-news/", data, format="json")
                self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(WorldNews.objects.count(), 0)
        data = self.payload()
        data["amount"] = "999999999999999999999999.123456"
        row = self.call("world_news.create", {"data": data})["data"]
        self.assertEqual(row["amount"], data["amount"])

    # Function: Verify patch consistency with existing amount evidence.
    # Inputs: A record with an amount, replacement evidence, amount clearing, and unrelated-field updates.
    # Outputs: Reject broken combinations; complete clearing or valid updates succeed and increment revision.
    # Logic: Validation includes stored values, preventing checks limited to submitted partial fields.
    # Constraints: Do not implicitly clear metadata, repair evidence, or re-extract.
    def test_partial_updates_validate_merged_record(self):
        row = self.call("world_news.create", {"data": self.payload()})["data"]
        for patch in ({"amount": None}, {"currency": ""}, {"evidence": "另一段新闻"}):
            self.call("world_news.update", {"id": row["id"], "revision": row["revision"], "data": patch}, 400)
        row = self.call("world_news.update", {"id": row["id"], "revision": row["revision"], "data": {"summary": "更新摘要"}})["data"]
        self.assertEqual(row["amount"], "50000000.000000")
        cleared = {field: None if field == "amount" else "" for field in ("amount", "currency", "amount_type", "amount_scope", "amount_evidence")}
        row = self.call("world_news.update", {"id": row["id"], "revision": row["revision"], "data": cleared})["data"]
        self.assertIsNone(row["amount"])
        self.assertEqual(row["currency"], "")

    # Function: Verify tool schemas discovered by the Agent over real HTTP expose new fields.
    # Inputs: Actual registry and HTTP tool-catalog output.
    # Outputs: Empty enums and complete payloads pass, floats fail, and all thirteen fields appear in the public tool contract.
    # Logic: Derive the contract from the same serializer to prevent tools rejecting inputs accepted by REST.
    # Constraints: No external network access or client-code generation.
    def test_schema_accepts_empty_enums_and_decimal_strings(self):
        schema = build_registry()["world_news.create"]["inputSchema"]
        validate({"data": self.payload()}, schema)
        empty = {**self.payload(), **{field: None if field == "amount" else "" for field in SIGNAL_FIELDS}}
        validate({"data": empty}, schema)
        self.assertFalse(schema["properties"]["data"]["additionalProperties"])
        with self.assertRaises(ValidationError):
            validate({"data": {**self.payload(), "amount": 1.25}}, schema)
        response = self.tool.get("/api/v1/agent-tools/catalog/?page_size=100")
        self.assertEqual(response.status_code, 200)
        published = next(tool for tool in response.data["tools"] if tool["name"] == "world_news.create")
        self.assertEqual(published["inputSchema"], schema)
        self.assertTrue(set(SIGNAL_FIELDS).issubset(schema["properties"]["data"]["properties"]))

    # Function: Verify the database rejects incomplete amount states.
    # Inputs: Amount/metadata conflicts inserted while bypassing the serializer.
    # Outputs: IntegrityError without affecting valid news.
    # Logic: Place each failure in an inner transaction so later assertions can run.
    # Constraints: Bypass business interfaces only for test data; this does not establish external evidence truthfulness.
    def test_database_amount_constraint(self):
        payload = self.payload()
        for patch in ({"amount": None}, {"currency": ""}, {"amount": "-1"}):
            with self.subTest(patch=patch), self.assertRaises(IntegrityError), transaction.atomic():
                WorldNews.objects.create(owner=self.owner, **{**payload, **patch})


# Function: Verify real PostgreSQL migration preserves historical news.
# Logic: Return to the old schema, create records, migrate forward, and restore all latest migrations.
# Constraints: Run only in the test database without updating local or online runtime data.
class NewsSignalMigrationTests(TransactionTestCase):
    # Function: Verify new fields are empty on old records and bodies remain unchanged.
    # Inputs: One legacy 0009 record without structured lead fields.
    # Outputs: After 0010, defaults are correct and content/revision/source remain unchanged.
    # Logic: Create old data through historical models; restore all application leaf migrations in finally.
    # Constraints: No text extraction, external calls, or automatic backfill.
    def test_preserves_existing_news(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        old_target = [("sales", "0009_shared_insights")]
        executor.migrate(old_target)
        old = executor.loader.project_state(old_target).apps
        try:
            user = get_user_model().objects.create_user(username="news-signal-migration")
            row = old.get_model("sales", "WorldNews").objects.create(owner_id=user.pk, title="旧新闻", category="industry", content="旧正文有金额但不应自动解析", published_at=timezone.now(), source_url="https://example.org/old", revision=3)
            MigrationExecutor(connection).migrate([("sales", "0010_news_signal_fields")])
            updated = WorldNews.objects.get(pk=row.pk)
            self.assertEqual((updated.content, updated.revision, updated.source_url), (row.content, 3, row.source_url))
            for field in SIGNAL_FIELDS:
                self.assertEqual(getattr(updated, field), None if field == "amount" else "")
        finally:
            MigrationExecutor(connection).migrate(latest)
