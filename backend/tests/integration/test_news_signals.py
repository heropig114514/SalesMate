"""职责：验收公共新闻销售线索的真实 HTTP、工具契约和存储兼容。
实现：隔离 PostgreSQL 数据库，测试完整/空载荷、精确金额、证据组合、部分更新及旧数据迁移。
关联：WorldNews、WorldNewsSerializer 和 Tool Schema；不调用新闻来源或修改 Agent。
目录：
- NewsSignalTests：公共线索接口与一致性验证。
- NewsSignalTests.setUp：创建隔离员工、工具身份与 API 客户端。
- NewsSignalTests.payload：构造协作契约的完整公开载荷。
- NewsSignalTests.call：请求实际 Tool 入口。
- NewsSignalTests.test_full_payload_round_trip_permissions_and_no_crm：全部字段往返且不创建或关联 CRM。
- NewsSignalTests.test_empty_and_legacy_payloads：空值和省略字段的旧客户端兼容。
- NewsSignalTests.test_invalid_payloads_are_rejected：金额、枚举、长度和证据边界。
- NewsSignalTests.test_partial_updates_validate_merged_record：更新必须保持完整证据组合。
- NewsSignalTests.test_schema_accepts_empty_enums_and_decimal_strings：实际发布的 Tool Schema 与写入契约同步。
- NewsSignalTests.test_database_amount_constraint：绕过接口也不能写不完整金额组合。
- NewsSignalMigrationTests：旧记录兼容迁移。
- NewsSignalMigrationTests.test_preserves_existing_news：从 0009 迁移并保留原文和旧记录版本。
变量索引：
- SIGNAL_FIELDS：十三个新增字段名，用于逐项往返检查。
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


# 功能：验证公开新闻线索可用且不扩大私人 CRM 权限。
# 逻辑：真实 HTTP 和数据库，关闭实验放开与自动登录。
# 约束：所有公司、证据和来源都是测试夹具，不代表外部 Agent 已验收。
@override_settings(LAB_OPEN_ACCESS=False, WORKSPACE_OWNER_ONLY=False, LOCAL_DEBUG_AUTO_LOGIN=False)
class NewsSignalTests(TestCase):
    # 功能：建立最小权限测试身份。
    # 输入：无外部参数。
    # 输出：实例 owner、reader、http 和 tool。
    # 逻辑：令牌只允许新闻创建/更新/读取；reader 无 owner 写权限。
    # 约束：固定测试 token 不在真实数据库或服务中使用。
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="signal-owner")
        self.reader = get_user_model().objects.create_user(username="signal-reader")
        self.http, self.tool = APIClient(), APIClient()
        self.http.force_login(self.owner)
        ToolCredential.objects.create(owner=self.owner, name="signal-test", digest=hashlib.sha256(b"news-signal-test").hexdigest(), allowed_tools=["world_news." + op for op in ("create", "update", "list", "get")], expires_at=timezone.now() + timedelta(hours=1))
        self.tool.credentials(HTTP_AUTHORIZATION="Tool news-signal-test")

    # 功能：构造一组有原文支撑的新闻事实与独立推断。
    # 输入：无参数。
    # 输出：每次独立的完整字典。
    # 逻辑：固定同一公司、总投资和整项目范围，金额原文包含于 evidence。
    # 约束：example.org 不被实际访问；推断不能解释为确认采购。
    def payload(self):
        return {"title": "测试新闻", "category": "industry", "industry": "半导体", "country": "CN", "published_at": "2026-09-24T08:00:00Z", "source_url": "https://example.org/signal", "summary": "公开摘要", "content": "公开新闻正文", "data_source": "agent", "company_name": "测试制造公司", "signal_type": "new_factory", "project_name": "测试基地", "demand_description": "建设生产线", "potential_sales_need": "可能需要检测设备", "opportunity_reason": "生产线可能需要检测环节", "time_window": "2027 年投产", "evidence": "测试制造公司宣布测试基地总投资人民币 5000 万元，2027 年投产。", "amount": "50000000", "currency": "CNY", "amount_type": "total_investment", "amount_scope": "whole_project", "amount_evidence": "总投资人民币 5000 万元"}

    # 功能：执行真实工具调用。
    # 输入：`name` 工具名称、`arguments` 载荷、`status` 预期 HTTP 状态。
    # 输出：响应字典。
    # 逻辑：写操作附独立幂等键，不绕过 Schema 或序列化器。
    # 约束：仅指向测试客户端，不访问运行中的服务。
    def call(self, name, arguments, status=200):
        body = {"name": name, "arguments": arguments}
        if name.endswith((".create", ".update")):
            body["idempotency_key"] = str(uuid.uuid4())
        response = self.tool.post("/api/v1/agent-tools/call/", body, format="json")
        self.assertEqual(response.status_code, status, response.data)
        return response.data

    # 功能：验证工具写入与所有新闻读取入口的字段一致性。
    # 输入：完整夹具及两个正式员工身份。
    # 输出：十三字段往返、读共享写隔离、CRM 计数不变。
    # 逻辑：先写再通过 Tool/API 列表详情读取，分别检查普通和 owner_only 模式。
    # 约束：共享金额是公开报道金额，不是访问其他员工的商机。
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

    # 功能：接受非线索新闻和旧客户端。
    # 输入：显式空值以及完全省略十三字段的两组新闻。
    # 输出：文本全空，金额为 null，创建成功。
    # 逻辑：分别走 Tool 和 REST，验证金额零值不属于未知。
    # 约束：不自动补充信息或进行回填。
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

    # 功能：拒绝会破坏事实口径或数值精度的输入。
    # 输入：逐个变更金额类型、边界、枚举、长度和证据的载荷。
    # 输出：HTTP 400 且无入库；最大许可值精确保存。
    # 逻辑：Tool 与 REST 都验收；超限不能被浮点或数据库舍入掩盖。
    # 约束：不验证外部文章真实性，仅验证协议内部一致。
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

    # 功能：验证补丁与旧金额证据之间的一致性。
    # 输入：有金额记录及更换证据、清空金额、不相关字段更新。
    # 输出：破坏组合的更新拒绝，完整清空或有效更新成功且 revision 递增。
    # 逻辑：校验包含数据库旧值，防止仅检查传入部分字段的漏洞。
    # 约束：不隐式清空元数据、不修复证据、不重新提取。
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

    # 功能：验证 Agent 通过真实 HTTP 发现的工具 Schema 暴露新增字段。
    # 输入：实际注册表与工具目录 HTTP 输出。
    # 输出：空枚举/完整载荷通过、浮点拒绝；十三字段在公开工具契约中存在。
    # 逻辑：契约从同一序列化器派生，防止 REST 接受而工具入口先拒绝。
    # 约束：不访问外部网络或生成客户端代码。
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

    # 功能：验证数据库拒绝不完整金额状态。
    # 输入：绕过 serializer 的金额/元数据矛盾记录。
    # 输出：IntegrityError，合法新闻不受影响。
    # 逻辑：每个失败放入内层事务，以便后续断言继续执行。
    # 约束：仅对测试数据绕过业务接口，不证明外部证据真实性。
    def test_database_amount_constraint(self):
        payload = self.payload()
        for patch in ({"amount": None}, {"currency": ""}, {"amount": "-1"}):
            with self.subTest(patch=patch), self.assertRaises(IntegrityError), transaction.atomic():
                WorldNews.objects.create(owner=self.owner, **{**payload, **patch})


# 功能：验证真实 PostgreSQL 迁移保留历史资讯。
# 逻辑：回到旧 schema 创建记录，然后前移并恢复所有最新迁移。
# 约束：仅在测试数据库执行，不更新运行中的本地或线上数据。
class NewsSignalMigrationTests(TransactionTestCase):
    # 功能：验证旧记录新增字段为空且正文不变。
    # 输入：0009 中的一条无结构化线索旧记录。
    # 输出：0010 后字段默认值正确、内容/revision/来源不变。
    # 逻辑：使用历史模型创建旧数据；finally 恢复所有应用叶节点。
    # 约束：没有文本提取、外部调用或自动回填。
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
