"""职责：验证图谱HTTP工具与MCP共享的授权、来源幂等和事务边界。
实现：真实隔离PostgreSQL和Tool认证；仅模型生成用合成响应替代。
关联：agent_tools.graph/services 与 knowledge_graph；MCP传输另有SDK协议测试。
目录：
- GraphToolTests：图谱工具数据库集成测试。
- GraphToolTests.setUp：创建限定凭证及两个独立身份。
- GraphToolTests.call：经真实工具HTTP入口调用。
- GraphToolTests.test_source_idempotence_and_retraction：来源重放、冲突、撤回与查询。
- GraphToolTests.test_model_outside_transaction：确认模型边界无数据库事务。
- GraphToolTests.test_model_outside_transaction.generate：返回有原文证据的合成候选。
- GraphToolTests.test_authorization_and_owner_scope：拒绝越权和质量无关的权限逃逸。
- GraphToolTests.test_invalid_inputs_and_failed_model：契约拒绝与失败不写库。
变量索引：
- 无
"""
import hashlib
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from apps.agent_tools.models import ToolCredential, ToolCall
from apps.agent_tools.registry import build_registry
from apps.knowledge_graph.models import Episode
from apps.knowledge_graph.sync import sync_owner


# 功能：验证图谱工具权限和可重放输入。
# 逻辑：TransactionTestCase允许检查真实事务边界；所有数据均为合成。
# 约束：模型模拟只验证接口集成，不能证明实际权重抽取质量。
@override_settings(LAB_OPEN_ACCESS=False, LOCAL_DEBUG_AUTO_LOGIN=False)
class GraphToolTests(TransactionTestCase):
    # 功能：准备身份和专用凭证。
    # 输入：隔离测试数据库及已迁移图谱捕获。
    # 输出：owner、other、client、credential、payload实例状态。
    # 逻辑：凭证仅包含graph工具；同步空图以便验证按身份读取。
    # 约束：不读取业务数据库或外部模型。
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="graph-tools")
        self.other = get_user_model().objects.create_user(username="graph-other")
        self.credential = ToolCredential.objects.create(owner=self.owner, name="graph-test",
            digest=hashlib.sha256(b"synthetic-graph-token").hexdigest(),
            allowed_tools=[name for name in build_registry() if name.startswith("graph.")],
            expires_at=timezone.now() + timedelta(hours=1))
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Tool synthetic-graph-token")
        self.payload = {"source_key": "partial", "observed_at": timezone.now().isoformat(),
                        "records": [{"key": "c", "schema": "crm.company", "fields": {"name": "Synthetic Acme"}}]}
        sync_owner(self.owner.pk)
        sync_owner(self.other.pk)

    # 功能：调用经过认证的HTTP工具入口。
    # 输入：`name` 工具名、`args` 参数、`status` 预期HTTP状态，默认200。
    # 输出：响应JSON。
    # 逻辑：固定路径，检查真实状态与错误信封。
    # 约束：不绕过认证或直接调用内部分派。
    def call(self, name, args, status=200):
        response = self.client.post("/api/v1/agent-tools/call/", {"name": name, "arguments": args}, format="json")
        self.assertEqual(response.status_code, status, response.data)
        return response.data

    # 功能：验证结构化建图、重复来源及撤回。
    # 输入：部分字段观察及相同来源键。
    # 输出：唯一来源、当前图谱、冲突和重复撤回断言。
    # 逻辑：完整走HTTP，确认来源写不生成外层ToolCall并能回查事实血缘。
    # 约束：保持缺失业务字段，不创建CRM订单或联系人。
    def test_source_idempotence_and_retraction(self):
        first = self.call("graph.ingest", self.payload)["data"]
        self.assertTrue(first["sync"]["current"])
        self.assertEqual(self.call("graph.ingest", self.payload)["data"]["id"], first["id"])
        self.assertEqual(Episode.objects.count(), 1)
        self.assertEqual(ToolCall.objects.count(), 0)
        self.call("graph.ingest", {**self.payload, "records": [{"key": "c", "schema": "crm.company", "fields": {"name": "Different"}}]}, 409)
        self.assertEqual(len(self.call("graph.schema", {})["data"]["schemas"]), 48)
        self.assertTrue(self.call("graph.status", {})["data"]["current"])
        self.assertTrue(self.call("graph.entities", {})["data"]["results"])
        facts = self.call("graph.facts", {})["data"]["results"]
        self.call("graph.lineage", {"fact_id": facts[0]["id"]})
        self.call("graph.episodes", {})
        self.call("graph.episode", {"episode_id": first["id"]})
        for _ in range(2):
            self.assertTrue(self.call("graph.retract", {"episode_id": first["id"]})["data"]["retracted"])

    # 功能：验证自然语言推理发生在数据库事务外。
    # 输入：简单文本和模拟模型回调。
    # 输出：事务状态与已保存来源断言。
    # 逻辑：回调实际检查connection状态；空事实仅用于事务契约验证。
    # 约束：不把模拟输出解释为模型准确率。
    def test_model_outside_transaction(self):
        # 功能：提供确定性模型边界并核验事务。
        # 输入：`prompt` 为服务实际构造的消息。
        # 输出：合法实体候选与模拟审计。
        # 逻辑：在调用时断言无事务，实体由输入原文支持。
        # 约束：不执行真实推理。
        def generate(prompt):
            self.assertFalse(connection.in_atomic_block)
            self.assertTrue(prompt)
            return {"entities": [], "facts": []}, {"test_only": True}
        with patch("apps.knowledge_graph.episodes.generate", side_effect=generate):
            self.call("graph.ingest", {"source_key": "text", "observed_at": self.payload["observed_at"], "text": "No business facts."})
        self.assertEqual(Episode.objects.count(), 1)

    # 功能：验证凭证范围、他人来源和owner注入。
    # 输入：外部身份来源及收窄授权。
    # 输出：404、400、403断言。
    # 逻辑：先创建自己的来源，再从其他身份查询；目录和执行均受白名单约束。
    # 约束：越权与不存在返回相同404。
    def test_authorization_and_owner_scope(self):
        episode = self.call("graph.ingest", self.payload)["data"]["id"]
        self.credential.owner = self.other
        self.credential.save(update_fields=["owner"])
        self.call("graph.episode", {"episode_id": episode}, 404)
        self.call("graph.retract", {"episode_id": episode}, 404)
        self.call("graph.ingest", {**self.payload, "owner": self.owner.pk}, 400)
        self.credential.allowed_tools = ["graph.schema"]
        self.credential.save(update_fields=["allowed_tools"])
        self.call("graph.ingest", self.payload, 403)

    # 功能：验证非法信封和生成失败不会污染观察层。
    # 输入：二选一冲突、传输幂等UUID及模型异常。
    # 输出：400/502和零来源断言。
    # 逻辑：先契约校验，再在单次模型边界模拟失败。
    # 约束：不得重试、修复或静默回退。
    def test_invalid_inputs_and_failed_model(self):
        self.call("graph.ingest", {**self.payload, "text": "extra"}, 400)
        response = self.client.post("/api/v1/agent-tools/call/", {"name": "graph.ingest", "arguments": self.payload,
            "idempotency_key": "00000000-0000-4000-8000-000000000001"}, format="json")
        self.assertEqual(response.status_code, 400)
        with patch("apps.knowledge_graph.episodes.generate", side_effect=RuntimeError("synthetic failure")) as generate:
            self.call("graph.ingest", {"source_key": "fail", "observed_at": self.payload["observed_at"], "text": "Sample"}, 502)
            self.assertEqual(generate.call_count, 1)
        self.assertFalse(Episode.objects.exists())
