"""Responsibility: Verify shared global insights, private-link isolation, date compatibility, and server-side deduplication.
Implementation: Real Django HTTP, Tool credentials, and isolated database; two employees read across entry points while ownership/token permissions still constrain writes.
Relationships: sales.permissions/serializers/insights and agent_tools; no external sources/models or Agent modifications.
Directory:
- SharedInsightsTests: Shared-read and compatibility-interface acceptance tests.
- SharedInsightsTests.setUp: Create two employees and restricted tool identities.
- SharedInsightsTests.event: Construct explicit event payloads.
- SharedInsightsTests.news: Construct news payloads.
- SharedInsightsTests.call: Call the real tool entry point.
- SharedInsightsTests.test_shared_reads_private_relations_and_writes: Verify sharing and write isolation across modes/entry points.
- SharedInsightsTests.test_anonymous_and_tool_scope: Verify anonymous rejection and minimal tool scope.
- SharedInsightsTests.test_agent_date_protocol_and_explicit_precision: Verify legacy Agent and explicit-date contracts.
- SharedInsightsTests.test_duplicate_sources_archive_and_occurrences: Verify cross-account deduplication, archival, and different event editions.
- SharedInsightsTests.test_database_constraints: Verify bypassing serializers cannot create duplicate collected records.
- InsightMigrationTests: Historical migration and concurrent uniqueness acceptance tests.
- InsightMigrationTests.test_historical_dates_and_duplicate_rejection: Reject silent merging of historical duplicates and correctly mark old date records.
- InsightMigrationTests.test_concurrent_agent_news: Concurrent same-source news writes from different accounts succeed only once.
- InsightMigrationTests.test_concurrent_agent_news.insert: Write test records through a thread-local database connection.
- InsightMigrationTests.test_concurrent_http_conflict: Synchronize cross-account HTTP writes to verify 409 receipts.
- InsightMigrationTests.test_concurrent_http_conflict.synchronized_clean: Establish a deterministic race window after model validation.
- InsightMigrationTests.test_concurrent_http_conflict.create: Perform one authorized HTTP write through an independent connection.
Variable index:
- TOOLS: Allow only news/event tools required by this test.
"""

import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.agent_tools.models import ToolCredential
from apps.sales import grouping, models
from apps.sales.insight_dates import LEGACY_DATE_MARKER

TOOLS = [f"{resource}.{action}" for resource in ("world_news", "world_events") for action in ("list", "get", "create", "update", "archive")] + ["world_insights.get"]


# Function: Verify permission boundaries between public facts and private sales information.
# Logic: Session and Tool use real views with laboratory opening and automatic login explicitly disabled; switch modes separately within scenarios.
# Constraints: TestCase isolates the database; no external networking or claim of real Agent integration acceptance.
@override_settings(LAB_OPEN_ACCESS=False, WORKSPACE_OWNER_ONLY=False, LOCAL_DEBUG_AUTO_LOGIN=False)
class SharedInsightsTests(TestCase):
    # Function: Create two employees and tool identities.
    # Inputs: No external arguments.
    # Outputs: Employee, Session, and Tool clients in the instance.
    # Logic: Store token digests and authorize news/event tools only; raw tokens are fixed test data.
    # Constraints: Do not write local development accounts or production credentials.
    def setUp(self):
        self.a = get_user_model().objects.create_user(username="insights-a")
        self.b = get_user_model().objects.create_user(username="insights-b")
        self.human_a, self.human_b = APIClient(), APIClient()
        self.human_a.force_login(self.a)
        self.human_b.force_login(self.b)
        self.tool_a, self.tool_b = APIClient(), APIClient()
        for user, client, token in ((self.a, self.tool_a, "insights-a-token"), (self.b, self.tool_b, "insights-b-token")):
            ToolCredential.objects.create(owner=user, name="test", digest=hashlib.sha256(token.encode()).hexdigest(), allowed_tools=TOOLS, expires_at=timezone.now() + timedelta(hours=1))
            client.credentials(HTTP_AUTHORIZATION="Tool " + token)

    # Function: Construct valid exhibition facts.
    # Inputs: No external arguments.
    # Outputs: An independently mutable event-payload dictionary.
    # Logic: Default to exact times without private links.
    # Constraints: example.org is a test source only and receives no requests.
    def event(self):
        return {"title": "共享展会", "event_type": "exhibition", "country": "SG", "city": "Singapore", "latitude": 1.3, "longitude": 103.8, "starts_at": "2026-10-27T09:00:00+08:00", "ends_at": "2026-10-29T17:00:00+08:00", "source_url": "https://example.org/expo", "description": "公开的展会说明", "data_source": "agent"}

    # Function: Construct valid news facts.
    # Inputs: No external arguments.
    # Outputs: News-creation payload.
    # Logic: Provide sources and exact publication times.
    # Constraints: Do not visit sources or generate model text.
    def news(self):
        return {"title": "共享新闻", "category": "industry", "published_at": "2026-09-24T08:00:00Z", "source_url": "https://example.org/news", "content": "公开行业新闻", "data_source": "agent"}

    # Function: Call the real tool HTTP entry point.
    # Inputs: `client` is the client, `name` the tool, `arguments` its inputs, and `status` the expected HTTP status.
    # Outputs: Response data.
    # Logic: Use an independent idempotency key for every write to verify source deduplication rather than receipt replay.
    # Constraints: Do not mock backend handlers; error receipts also traverse HTTP.
    def call(self, client, name, arguments, status=200):
        payload = {"name": name, "arguments": arguments}
        if name.rsplit(".", 1)[1] not in {"list", "get"}:
            payload["idempotency_key"] = str(uuid.uuid4())
        response = client.post("/api/v1/agent-tools/call/", payload, format="json")
        self.assertEqual(response.status_code, status, response.data)
        return response.data

    # Function: Verify shared facts and private-link isolation at every read entry point.
    # Inputs: No external arguments; A's private opportunity, manual/Agent news/events, and B's identity.
    # Outputs: B reads facts without private IDs, customers, or amounts and cannot edit/archive them.
    # Logic: Loop through ordinary and owner_only modes, reading real lists, details, map, and four Tools; A retains visibility of owned links.
    # Constraints: Do not use experimental mode to prove normal permissions or change customer/opportunity access.
    def test_shared_reads_private_relations_and_writes(self):
        company = grouping.create_company(self.a, "A 私有客户")
        opportunity = models.Opportunity.objects.create(owner=self.a, company=company, title="私有商机", amount="12345", currency="SGD")
        event = self.call(self.tool_a, "world_events.create", {"data": {**self.event(), "opportunity_ids": [str(opportunity.pk)]}})["data"]
        news = self.call(self.tool_a, "world_news.create", {"data": self.news()})["data"]
        manual = self.human_a.post("/api/v1/sales/records/world-events/", {**self.event(), "data_source": "manual", "event_type": "sales"}, format="json")
        self.assertEqual(manual.status_code, 201)
        manual_news = self.human_a.post("/api/v1/sales/records/world-news/", {**self.news(), "data_source": "manual"}, format="json")
        self.assertEqual(manual_news.status_code, 201)
        for owner_only in (False, True):
            with self.subTest(owner_only=owner_only), override_settings(WORKSPACE_OWNER_ONLY=owner_only):
                for resource, record in (("world-events", event), ("world-news", news)):
                    prefix = resource.replace("-", "_")
                    detail = self.human_b.get(f"/api/v1/sales/records/{resource}/{record['id']}/")
                    self.assertEqual(detail.status_code, 200)
                    listing = self.human_b.get(f"/api/v1/sales/records/{resource}/")
                    self.assertEqual(listing.data["count"], 2)
                    tool_detail = self.call(self.tool_b, prefix + ".get", {"id": record["id"]})["data"]
                    tool_list = self.call(self.tool_b, prefix + ".list", {"page_size": 1, "page": 2})["data"]
                    self.assertEqual((tool_list["count"], len(tool_list["results"])), (2, 1))
                    if resource == "world-events":
                        for row in [detail.data, tool_detail, *listing.data["results"], *tool_list["results"]]:
                            self.assertEqual(row["opportunity_ids"], [])
                    denied = self.human_b.patch(f"/api/v1/sales/records/{resource}/{record['id']}/", {"title": "越权"}, format="json", HTTP_IF_MATCH="0")
                    self.assertIn(denied.status_code, (403, 404))
                    self.call(self.tool_b, prefix + ".update", {"id": record["id"], "revision": 0, "data": {"title": "越权"}}, 404 if owner_only else 403)
                    self.call(self.tool_b, prefix + ".archive", {"id": record["id"], "revision": 0, "archived": True}, 404 if owner_only else 403)
                world = self.human_b.get("/api/v1/sales/world/")
                self.assertEqual(world.status_code, 200)
                self.assertEqual(world.data["count"], 2)
                self.assertNotIn("A 私有客户", str(world.data))
                self.assertNotIn(str(opportunity.pk), str(world.data))
                tool_world = self.call(self.tool_b, "world_insights.get", {})["data"]
                self.assertEqual(tool_world["count"], 2)
                self.assertNotIn(str(opportunity.pk), str(tool_world))
                for row in world.data["results"]:
                    self.assertIsNone(row["amount"])
                    self.assertNotIn("map_amounts", row)
                self.assertEqual(self.human_b.get("/api/v1/sales/records/opportunities/").data["count"], 0)
        own = self.call(self.tool_a, "world_events.get", {"id": event["id"]})["data"]
        self.assertEqual(own["opportunity_ids"], [str(opportunity.pk)])
        updated = self.call(self.tool_a, "world_news.update", {"id": news["id"], "revision": 0, "data": {"title": "更新公共资讯"}})["data"]
        self.call(self.tool_a, "world_news.archive", {"id": news["id"], "revision": updated["revision"], "archived": True})
        self.assertEqual(self.call(self.tool_b, "world_news.list", {})["data"]["count"], 1)
        self.assertEqual(self.call(self.tool_b, "world_news.list", {"archived": "all"})["data"]["count"], 2)

    # Function: Verify sharing does not imply anonymous access or unrestricted tool authorization.
    # Inputs: No external arguments; anonymous clients and a list-only Tool identity.
    # Outputs: Reject anonymous business requests and unauthorized tools.
    # Logic: Both normal modes require authentication; credentials seeing shared records still cannot call create without permission.
    # Constraints: Do not change existing laboratory-mode semantics.
    def test_anonymous_and_tool_scope(self):
        anonymous = APIClient()
        for owner_only in (False, True):
            with override_settings(WORKSPACE_OWNER_ONLY=owner_only):
                for path in ("/api/v1/sales/world/", "/api/v1/sales/records/world-news/", "/api/v1/sales/records/world-events/"):
                    self.assertIn(anonymous.get(path).status_code, (401, 403))
                self.assertIn(anonymous.post("/api/v1/agent-tools/call/", {"name": "world_news.list", "arguments": {}}, format="json").status_code, (401, 403))
        ToolCredential.objects.filter(owner=self.b).update(allowed_tools=["world_news.list"])
        self.call(self.tool_b, "world_news.list", {})
        self.call(self.tool_b, "world_news.create", {"data": self.news()}, 403)

    # Function: Verify original Agent payloads display accurate dates without new arguments.
    # Inputs: No external arguments; complete markers, noon boundaries, ordinary timestamps, and inconsistent markers.
    # Outputs: Correct precision and inclusive end dates; invalid protocols are rejected without changing timestamps.
    # Logic: Legacy protocol automatically sets date; explicit dates support UTC midnight; ordinary datetime date projections are null.
    # Constraints: Simulate Agent HTTP payloads only, without running Agents/models.
    def test_agent_date_protocol_and_explicit_precision(self):
        payload = {**self.event(), "starts_at": "2026-10-27T12:00:00Z", "ends_at": "2026-10-30T12:00:00Z", "description": "展会公开说明\n" + LEGACY_DATE_MARKER}
        event = self.call(self.tool_a, "world_events.create", {"data": payload})["data"]
        self.assertEqual((event["time_precision"], event["starts_on"], event["ends_on"]), ("date", "2026-10-27", "2026-10-29"))
        saved = models.WorldEvent.objects.get(pk=event["id"])
        self.assertEqual(saved.starts_at.hour, 12)
        self.assertEqual(saved.ends_at.day, 30)
        self.call(self.tool_a, "world_events.create", {"data": {**payload, "starts_at": "2026-10-27T11:00:00Z"}}, 400)
        timed = self.call(self.tool_a, "world_events.create", {"data": self.event()})["data"]
        self.assertEqual((timed["time_precision"], timed["starts_on"], timed["ends_on"]), ("datetime", None, None))
        explicit = self.call(self.tool_a, "world_events.create", {"data": {**self.event(), "time_precision": "date", "starts_at": "2026-12-31T00:00:00Z", "ends_at": "2027-01-02T00:00:00Z"}})["data"]
        self.assertEqual((explicit["starts_on"], explicit["ends_on"]), ("2026-12-31", "2027-01-01"))

    # Function: Verify source deduplication without expanding ownership or modifying original records.
    # Inputs: No external arguments; same-source cross-account news/events, archived records, and next-edition events at the same URL.
    # Outputs: Duplicates return 409 even after archival; manual records and later editions can be created.
    # Logic: Use different idempotency keys per call; persisted counts and original ownership remain unchanged.
    # Constraints: No concurrency simulation here; separate tests cover concurrency.
    def test_duplicate_sources_archive_and_occurrences(self):
        news = self.call(self.tool_a, "world_news.create", {"data": self.news()})["data"]
        self.call(self.tool_b, "world_news.create", {"data": self.news()}, 409)
        self.call(self.tool_a, "world_news.archive", {"id": news["id"], "revision": 0, "archived": True})
        self.call(self.tool_b, "world_news.create", {"data": self.news()}, 409)
        self.call(self.tool_b, "world_news.create", {"data": {**self.news(), "data_source": "manual"}})
        self.call(self.tool_a, "world_events.create", {"data": self.event()})
        self.call(self.tool_b, "world_events.create", {"data": self.event()}, 409)
        self.call(self.tool_b, "world_events.create", {"data": {**self.event(), "starts_at": "2027-10-27T09:00:00+08:00", "ends_at": "2027-10-29T17:00:00+08:00"}})
        self.assertEqual((models.WorldNews.objects.count(), models.WorldEvent.objects.count()), (2, 2))

    # Function: Verify database constraints prevent duplicates when views are bypassed.
    # Inputs: No external arguments; same-source Agent records owned by different users.
    # Outputs: Direct duplicate ORM writes raise IntegrityError.
    # Logic: Check conditional unique indexes for news/events separately, isolating expected errors in inner transactions.
    # Constraints: Serializer prechecks do not replace database uniqueness verification.
    def test_database_constraints(self):
        for model, data in ((models.WorldNews, self.news()), (models.WorldEvent, self.event())):
            model.objects.create(owner=self.a, **data)
            with self.assertRaises(IntegrityError), transaction.atomic():
                model.objects.create(owner=self.b, **data)


# Function: Verify historical migration compatibility and actual PostgreSQL concurrency.
# Logic: Use an isolated database with committable transactions; historical-migration tests always restore the current schema.
# Constraints: Operate only on Django test databases, not actual development databases.
@override_settings(LAB_OPEN_ACCESS=False, WORKSPACE_OWNER_ONLY=False, LOCAL_DEBUG_AUTO_LOGIN=False)
class InsightMigrationTests(TransactionTestCase):
    # Function: Verify legacy-record precision migration and duplicate blocking.
    # Inputs: No external arguments; legacy Agent date events and duplicate news in historical state 0008.
    # Outputs: Duplicates cause migration failure without deletion; after manually removing test duplicates, migration succeeds and dates are marked.
    # Logic: Run backward/forward migrations genuinely; finally restore every application's latest pre-test schema. Failure data stays in the test database.
    # Constraints: Production migrations never delete duplicates; deleted rows here are duplicate fixtures created by the test.
    def test_historical_dates_and_duplicate_rejection(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        previous = [("sales", "0008_development_support")]
        current = [("sales", "0009_shared_insights")]
        executor.migrate(previous)
        old = executor.loader.project_state(previous).apps
        user = get_user_model().objects.create_user(username="migration-insights")
        news = old.get_model("sales", "WorldNews")
        event = old.get_model("sales", "WorldEvent")
        duplicate = None
        try:
            data = {"owner_id": user.pk, "title": "test", "category": "industry", "published_at": timezone.now(), "source_url": "https://example.org/repeated", "content": "public", "data_source": "agent"}
            news.objects.create(**data)
            duplicate = news.objects.create(**data)
            sample = event.objects.create(owner_id=user.pk, title="date", event_type="exhibition", country="SG", city="Singapore", latitude=1, longitude=103, starts_at="2026-10-27T12:00:00Z", ends_at="2026-10-30T12:00:00Z", source_url="https://example.org/migration", data_source="agent", description=LEGACY_DATE_MARKER)
            with self.assertRaisesRegex(RuntimeError, "重复 Agent 来源"):
                MigrationExecutor(connection).migrate(current)
            self.assertEqual(news.objects.count(), 2)
            duplicate.delete()
            duplicate = None
            MigrationExecutor(connection).migrate(current)
            migrated = MigrationExecutor(connection).loader.project_state(current).apps.get_model("sales", "WorldEvent").objects.get(pk=sample.pk)
            self.assertEqual((migrated.time_precision, migrated.starts_at.hour, migrated.ends_at.day), ("date", 12, 30))
        finally:
            if duplicate is not None:
                duplicate.delete()
            MigrationExecutor(connection).migrate(latest)

    # Function: Verify concurrent collection by different accounts creates only one news record.
    # Inputs: No external arguments; two test accounts, a barrier, and independent database connections.
    # Outputs: One success, one unique-constraint conflict, and one final record.
    # Logic: Threads simultaneously submit same-source direct writes, covering races that client-side paginated deduplication cannot prevent.
    # Constraints: Run on PostgreSQL, not SQLite as concurrency evidence; close connections when threads finish.
    def test_concurrent_agent_news(self):
        self.assertEqual(connection.vendor, "postgresql")
        users = [get_user_model().objects.create_user(username=f"concurrent-{number}") for number in range(2)]
        barrier = Barrier(2)

        # Function: Attempt same-source test-news insertion on an independent connection.
        # Inputs: `owner_id` is the test-account primary key; implicitly reads the synchronization barrier.
        # Outputs: created or conflict; other exceptions propagate to the test.
        # Logic: Write once after synchronization and catch only expected unique-constraint errors.
        # Constraints: No retry; each thread always closes its connection.
        def insert(owner_id):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                models.WorldNews.objects.create(owner_id=owner_id, title="concurrent", category="industry", published_at=timezone.now(), source_url="https://example.org/concurrent", content="public", data_source="agent")
                return "created"
            except IntegrityError:
                return "conflict"
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(insert, [user.pk for user in users]))
        self.assertCountEqual(outcomes, ["created", "conflict"])
        self.assertEqual(models.WorldNews.objects.filter(source_url="https://example.org/concurrent").count(), 1)

    # Function: Verify cross-account races return 409 through the formal Tool API.
    # Inputs: No external arguments; two restricted credentials and actual HTTP handlers.
    # Outputs: One completed receipt, one 409, and one persisted news record.
    # Logic: Only schedule concurrency with a barrier after full_clean; authentication, serialization, transactions, and indexes execute normally.
    # Constraints: The barrier controls timing without mocking database results; failed calls are not retried.
    def test_concurrent_http_conflict(self):
        barrier = Barrier(2)
        original = models.WorldNews.full_clean
        tokens = ["concurrent-http-a", "concurrent-http-b"]
        for token in tokens:
            user = get_user_model().objects.create_user(username=token)
            ToolCredential.objects.create(owner=user, name="test", digest=hashlib.sha256(token.encode()).hexdigest(), allowed_tools=["world_news.create"], expires_at=timezone.now() + timedelta(hours=1))

        # Function: Let both calls finish preflight before contending on the real unique index.
        # Inputs: `instance` is the model instance; `args`/`kwargs` are original full_clean arguments.
        # Outputs: None; original validation exceptions propagate normally.
        # Logic: Retain original validation and synchronize threads before writing.
        # Constraints: Patch only this test's model method without mocking validation results.
        def synchronized_clean(instance, *args, **kwargs):
            original(instance, *args, **kwargs)
            barrier.wait(timeout=10)

        # Function: Perform a real HTTP write for one employee.
        # Inputs: `token` is a fixed test credential.
        # Outputs: HTTP status code.
        # Logic: Submit same-source news with distinct connections/idempotency keys, then close connections.
        # Constraints: Do not catch unexpected exceptions or use production identities.
        def create(token):
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION="Tool " + token)
                response = client.post("/api/v1/agent-tools/call/", {"name": "world_news.create", "idempotency_key": str(uuid.uuid4()), "arguments": {"data": {"title": "parallel", "category": "industry", "content": "public", "source_url": "https://example.org/http-concurrent", "published_at": "2026-09-24T08:00:00Z", "data_source": "agent"}}}, format="json")
                return response.status_code
            finally:
                connection.close()

        with patch.object(models.WorldNews, "full_clean", synchronized_clean), ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(create, tokens))
        self.assertCountEqual(statuses, [200, 409])
        self.assertEqual(models.WorldNews.objects.filter(source_url="https://example.org/http-concurrent").count(), 1)
