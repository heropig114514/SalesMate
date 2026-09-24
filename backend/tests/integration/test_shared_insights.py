"""职责：验证共享全球资讯、私人关联隔离、日期兼容和服务端去重。
实现：真实 Django HTTP、Tool 凭证与隔离数据库；两个员工跨入口读取，写入仍受 owner 和令牌授权限制。
关联：sales.permissions/serializers/insights 与 agent_tools；不调用外部来源或模型，不修改 Agent。
目录：
- SharedInsightsTests：共享读取和兼容接口验收。
- SharedInsightsTests.setUp：建立两个员工及限定工具身份。
- SharedInsightsTests.event：构造明确活动载荷。
- SharedInsightsTests.news：构造新闻载荷。
- SharedInsightsTests.call：调用真实工具入口。
- SharedInsightsTests.test_shared_reads_private_relations_and_writes：验证多模式多入口共享与写隔离。
- SharedInsightsTests.test_anonymous_and_tool_scope：验证匿名拒绝和最小工具范围。
- SharedInsightsTests.test_agent_date_protocol_and_explicit_precision：验证旧 Agent 和显式日期契约。
- SharedInsightsTests.test_duplicate_sources_archive_and_occurrences：验证跨账号去重、归档和不同届次。
- SharedInsightsTests.test_database_constraints：验证绕过序列化器也不能创建重复采集记录。
- InsightMigrationTests：历史数据迁移和并发唯一性验收。
- InsightMigrationTests.test_historical_dates_and_duplicate_rejection：拒绝静默合并历史重复，正确标记旧日期记录。
- InsightMigrationTests.test_concurrent_agent_news：不同账号同时写同源新闻仅成功一次。
- InsightMigrationTests.test_concurrent_agent_news.insert：在线程独立连接中写测试记录。
- InsightMigrationTests.test_concurrent_http_conflict：同步跨账号 HTTP 写入验证 409 回执。
- InsightMigrationTests.test_concurrent_http_conflict.synchronized_clean：在模型校验后建立确定竞争窗口。
- InsightMigrationTests.test_concurrent_http_conflict.create：独立连接执行一次已授权 HTTP 写入。
变量索引：
- TOOLS：仅允许本测试所需资讯和活动工具。
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


# 功能：验证公开事实与私人销售信息的权限边界。
# 逻辑：Session 与 Tool 走实际视图，显式关闭实验开放与自动登录；模式在场景中独立切换。
# 约束：数据库由 TestCase 隔离，外部网络不调用，不代表真实 Agent 已联调。
@override_settings(LAB_OPEN_ACCESS=False, WORKSPACE_OWNER_ONLY=False, LOCAL_DEBUG_AUTO_LOGIN=False)
class SharedInsightsTests(TestCase):
    # 功能：建立两位员工及工具身份。
    # 输入：无外部参数。
    # 输出：实例中的员工、Session 和 Tool 客户端。
    # 逻辑：令牌以摘要保存，仅授权资讯活动工具；令牌原文仅为固定测试数据。
    # 约束：不写本地开发账号或生产凭证。
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

    # 功能：构造合法展会事实。
    # 输入：无外部参数。
    # 输出：可独立修改的活动载荷字典。
    # 逻辑：默认确切时间，不附私人关联。
    # 约束：example.org 仅为测试来源，不发请求。
    def event(self):
        return {"title": "共享展会", "event_type": "exhibition", "country": "SG", "city": "Singapore", "latitude": 1.3, "longitude": 103.8, "starts_at": "2026-10-27T09:00:00+08:00", "ends_at": "2026-10-29T17:00:00+08:00", "source_url": "https://example.org/expo", "description": "公开的展会说明", "data_source": "agent"}

    # 功能：构造合法新闻事实。
    # 输入：无外部参数。
    # 输出：新闻创建载荷。
    # 逻辑：提供来源与确切发布时间。
    # 约束：不访问来源或生成模型文本。
    def news(self):
        return {"title": "共享新闻", "category": "industry", "published_at": "2026-09-24T08:00:00Z", "source_url": "https://example.org/news", "content": "公开行业新闻", "data_source": "agent"}

    # 功能：调用真实工具 HTTP 入口。
    # 输入：`client` 客户端、`name` 工具、`arguments` 参数、`status` 预期 HTTP 状态。
    # 输出：响应数据。
    # 逻辑：每次写入使用独立幂等键，以验证来源去重而非幂等重放。
    # 约束：不模拟后端处理器；错误回执同样经过 HTTP。
    def call(self, client, name, arguments, status=200):
        payload = {"name": name, "arguments": arguments}
        if name.rsplit(".", 1)[1] not in {"list", "get"}:
            payload["idempotency_key"] = str(uuid.uuid4())
        response = client.post("/api/v1/agent-tools/call/", payload, format="json")
        self.assertEqual(response.status_code, status, response.data)
        return response.data

    # 功能：验证共享事实和私人关系隔离覆盖所有读入口。
    # 输入：无外部参数；A 的私有商机、人工/Agent 新闻活动及 B 身份。
    # 输出：B 可读事实但看不到私有 ID、客户和金额，也不能修改或归档。
    # 逻辑：循环普通和 owner_only 模式，实际读取列表、详情、地图及四个 Tool；A 仍能看到自己的关联。
    # 约束：不使用实验模式证明正式权限；不修改客户或商机权限。
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
                    self.assertEqual((row["amounts"], row["map_amounts"], row["customers"]), ({}, {}, []))
                self.assertEqual(self.human_b.get("/api/v1/sales/records/opportunities/").data["count"], 0)
        own = self.call(self.tool_a, "world_events.get", {"id": event["id"]})["data"]
        self.assertEqual(own["opportunity_ids"], [str(opportunity.pk)])
        updated = self.call(self.tool_a, "world_news.update", {"id": news["id"], "revision": 0, "data": {"title": "更新公共资讯"}})["data"]
        self.call(self.tool_a, "world_news.archive", {"id": news["id"], "revision": updated["revision"], "archived": True})
        self.assertEqual(self.call(self.tool_b, "world_news.list", {})["data"]["count"], 1)
        self.assertEqual(self.call(self.tool_b, "world_news.list", {"archived": "all"})["data"]["count"], 2)

    # 功能：确认共享不等于匿名访问或无限工具授权。
    # 输入：无外部参数；匿名客户端和仅 list 授权的 Tool 身份。
    # 输出：匿名业务请求及未授权工具被拒绝。
    # 逻辑：两种正式模式均保留认证；凭证即使可见共享记录也不能调用 create。
    # 约束：不改变实验模式既有语义。
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

    # 功能：验证原 Agent 载荷无需新参数即可准确显示日期。
    # 输入：无外部参数；完整标记、中午边界、普通时刻及不一致标记样本。
    # 输出：正确精度与包含末日的日期；错误协议拒绝且不改时间。
    # 逻辑：旧协议自动设置 date，显式日期支持 UTC 午夜；普通 datetime 日期投影为 null。
    # 约束：只模拟 Agent HTTP 载荷，不运行 Agent 或模型。
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

    # 功能：验证来源去重而非扩大 owner 或修改原记录。
    # 输入：无外部参数；跨账号相同新闻与活动、已归档记录、同 URL 下一届活动。
    # 输出：重复 409，归档后仍 409，人工记录和下一届活动允许创建。
    # 逻辑：各次调用使用不同幂等键，数据库数量及原 owner 不变。
    # 约束：不模拟并发；并发由独立测试覆盖。
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

    # 功能：验证数据库层防止绕过视图产生重复。
    # 输入：无外部参数；不同 owner 的同源 Agent 记录。
    # 输出：直接 ORM 重复写入抛 IntegrityError。
    # 逻辑：分别验证新闻和活动的条件唯一索引，内层事务隔离预期错误。
    # 约束：不以序列化器预检查代替数据库唯一性验证。
    def test_database_constraints(self):
        for model, data in ((models.WorldNews, self.news()), (models.WorldEvent, self.event())):
            model.objects.create(owner=self.a, **data)
            with self.assertRaises(IntegrityError), transaction.atomic():
                model.objects.create(owner=self.b, **data)


# 功能：验证迁移历史兼容和 PostgreSQL 实际并发。
# 逻辑：使用可提交事务的隔离测试数据库，历史迁移测试始终恢复当前 schema。
# 约束：只作用于 Django 测试库，不迁移实际开发数据库。
@override_settings(LAB_OPEN_ACCESS=False, WORKSPACE_OWNER_ONLY=False, LOCAL_DEBUG_AUTO_LOGIN=False)
class InsightMigrationTests(TransactionTestCase):
    # 功能：验证旧记录精度迁移及重复阻断。
    # 输入：无外部参数；0008 历史状态中的旧 Agent 日期活动与重复新闻。
    # 输出：重复时迁移失败且记录保留，人工移除测试重复后迁移成功且日期被标记。
    # 逻辑：真正执行前后迁移；finally 恢复执行前所有应用的最新 schema，失败数据仅限测试库。
    # 约束：生产迁移从不删除重复，此处删除的是测试自行创建的重复夹具。
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
            migrated = models.WorldEvent.objects.get(pk=sample.pk)
            self.assertEqual((migrated.time_precision, migrated.starts_at.hour, migrated.ends_at.day), ("date", 12, 30))
        finally:
            if duplicate is not None:
                duplicate.delete()
            MigrationExecutor(connection).migrate(latest)

    # 功能：验证不同账号并发采集只创建一条新闻。
    # 输入：无外部参数；两个测试账号、同步屏障和独立数据库连接。
    # 输出：一个成功、一个唯一约束冲突，最终一条记录。
    # 逻辑：线程同时提交相同来源的直接数据库写入，覆盖客户端分页去重无法保证的竞争窗口。
    # 约束：必须在 PostgreSQL 执行，不用 SQLite 代替并发证据；线程结束关闭连接。
    def test_concurrent_agent_news(self):
        self.assertEqual(connection.vendor, "postgresql")
        users = [get_user_model().objects.create_user(username=f"concurrent-{number}") for number in range(2)]
        barrier = Barrier(2)

        # 功能：从独立连接尝试插入同源测试新闻。
        # 输入：`owner_id` 测试账号主键；隐式读取同步屏障。
        # 输出：created 或 conflict；其他异常向测试传播。
        # 逻辑：同步后一次写入；只捕获预期唯一约束错误。
        # 约束：不重试，每个线程始终关闭自己的连接。
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

    # 功能：验证跨账号竞争通过正式 Tool API 返回 409。
    # 输入：无外部参数；两个限权凭证与实际 HTTP 处理器。
    # 输出：一条 completed 回执、一个 409，数据库仅一条新闻。
    # 逻辑：只在 full_clean 完成后用屏障安排并发，所有认证、序列化、事务和索引均真实执行。
    # 约束：屏障仅控制测试时序，不模拟数据库结果；不重试失败调用。
    def test_concurrent_http_conflict(self):
        barrier = Barrier(2)
        original = models.WorldNews.full_clean
        tokens = ["concurrent-http-a", "concurrent-http-b"]
        for token in tokens:
            user = get_user_model().objects.create_user(username=token)
            ToolCredential.objects.create(owner=user, name="test", digest=hashlib.sha256(token.encode()).hexdigest(), allowed_tools=["world_news.create"], expires_at=timezone.now() + timedelta(hours=1))

        # 功能：使两次调用均完成预检后竞争真实数据库唯一索引。
        # 输入：`instance` 模型实例、`args` 和 `kwargs` 原 full_clean 参数。
        # 输出：无；原校验异常正常传播。
        # 逻辑：保留原校验，在写入前同步线程。
        # 约束：仅 patch 此测试的模型方法，不模拟校验结果。
        def synchronized_clean(instance, *args, **kwargs):
            original(instance, *args, **kwargs)
            barrier.wait(timeout=10)

        # 功能：执行一位员工的真实 HTTP 写入。
        # 输入：`token` 固定测试凭证。
        # 输出：HTTP 状态码。
        # 逻辑：不同连接、不同幂等键提交同源新闻，末尾关闭连接。
        # 约束：不捕获未预期异常，不使用生产身份。
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
