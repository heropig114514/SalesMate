"""职责：验证公开实验模式的匿名跨账号业务访问与关闭后的权限恢复。
实现：隔离 PostgreSQL 中使用不带 Cookie/令牌且执行 CSRF 检查的真实 HTTP 客户端。
关联：laboratory、Sales/CRM/Tool/Chat 入口；合成批次在临时目录生成，不执行外部模型或发送。
目录：
- LaboratoryTests：实验开放集成验证。
- LaboratoryTests.setUp：创建非 KGSEED 的两个账号与业务记录。
- LaboratoryTests.call：发送无凭证、无幂等键的工具请求。
- LaboratoryTests.test_anonymous_reads_and_all_list_tools：匿名全目录和跨账号读取。
- LaboratoryTests.test_cross_owner_write_and_internal_confirmation：匿名跨账号修改及直接管理操作。
- LaboratoryTests.test_identity_selection_and_invalid_token：公开选择归属及无效令牌不阻断。
- LaboratoryTests.test_switch_off_restores_authentication：恢复正式认证与版本检查。
- LaboratoryTests.test_anonymous_chat_preserves_original_owner：跨账号聊天及知识上下文。
- LaboratoryTests.test_seed_crud_and_regular_edit：虚构数据匿名维护及普通入口修改后可读。
- LaboratoryTests.test_agent_context_and_lease_optional：Agent 业务免认证、租约可省略。
变量索引：
- BASE：Tool API 固定前缀。
"""

import tempfile
from pathlib import Path
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.agent_tools.registry import build_registry
from apps.chat.models import KnowledgeEntry, AnswerRequest
from apps.crm import rules
from integrations.company_enrichment import input_version
from rest_framework.exceptions import ValidationError
from apps.crm.access import check_version
from apps.crm.jobs import require_lease
from apps.sales import grouping, models
from apps.sales.experiments import APPROVED_BATCHES, load_batch, table_rows
from apps.sales.management.commands.seed_kg_lab import run_seed

BASE = "/api/v1/agent-tools/"


# 功能：验证免登录实验业务链路。
# 逻辑：模式通过测试配置开启，测试后框架恢复配置并回滚数据。
# 约束：不调用公网或真实外部服务。
@override_settings(LAB_OPEN_ACCESS=True, LAB_DEFAULT_USER="algorithm-lab", LOCAL_DEBUG_AUTO_LOGIN=False)
class LaboratoryTests(TestCase):
    # 功能：建立跨账号数据。
    # 输入：隔离测试数据库。
    # 输出：两个用户、客户、产品、知识及匿名客户端实例状态。
    # 逻辑：所有普通记录不使用 KGSEED 标记，验证开放范围不限虚构批次。
    # 约束：启用真实 CSRF 检查，不使用 force_authenticate。
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="lab-owner")
        self.other = get_user_model().objects.create_user(username="lab-other")
        self.company = grouping.create_company(self.owner, "Ordinary private customer")
        self.product = models.Product.objects.create(owner=self.owner, sku="ordinary", name="Original", currency="USD", unit_price="2.00")
        self.knowledge = KnowledgeEntry.objects.create(owner=self.owner, title="Shared fact", content="Cross account fact", version="1")
        self.client = APIClient(enforce_csrf_checks=True)

    # 功能：执行匿名工具请求。
    # 输入：`name` 工具名、`arguments` 输入、`expected` 预期 HTTP 状态。
    # 输出：响应数据。
    # 逻辑：不发送认证、版本或幂等头，断言真实 HTTP 结果。
    # 约束：异常断言保留响应以定位失败。
    def call(self, name, arguments, expected=200):
        response = self.client.post(BASE + "call/", {"name": name, "arguments": arguments}, format="json")
        self.assertEqual(response.status_code, expected, response.data)
        return response.data

    # 功能：验证匿名读入口和完整工具目录。
    # 输入：两账号的普通记录及注册表。
    # 输出：可见公司、产品、知识；每种记录列表均可请求。
    # 逻辑：遍历固定注册资源；网页 browse、CRM 和 Tool 使用实际视图。
    # 约束：不把协议读取成功解释为模型推理质量。
    def test_anonymous_reads_and_all_list_tools(self):
        for path in ("/api/v1/session/", "/api/v1/sales/directory/", "/api/v1/companies/", "/api/v1/mailboxes/", BASE + "catalog/"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response["X-Lab-Open-Access"], "true")
        self.assertTrue(self.client.get("/api/v1/session/").data["lab_open_access"])
        self.assertEqual(self.call("products.list", {})["data"]["count"], 1)
        self.assertIn("Shared fact", str(self.call("knowledge.search", {})))
        for name, spec in build_registry().items():
            if spec["kind"] == "record_list":
                with self.subTest(tool=name):
                    self.call(name, {})

    # 功能：验证匿名修改和直接管理操作。
    # 输入：其他账号产品和新团队名称。
    # 输出：写入成功，原归属不变，审计保留实验操作者，内部提案无需确认。
    # 逻辑：省略 If-Match、revision 和幂等键，随后执行归档。
    # 约束：实际外部发信流程不参与测试。
    def test_cross_owner_write_and_internal_confirmation(self):
        response = self.client.patch(f"/api/v1/sales/records/products/{self.product.pk}/", {"name": "Public edit"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.product.refresh_from_db()
        self.assertEqual((self.product.name, self.product.owner_id), ("Public edit", self.owner.pk))
        result = self.call("products.update", {"id": str(self.product.pk), "data": {"name": "Tool edit"}})
        self.assertIn("call_id", result)
        self.call("products.archive", {"id": str(self.product.pk), "archived": True})
        self.assertEqual(self.call("teams.create", {"data": {"name": "Public team"}})["status"], "completed")
        setting = self.company.business_settings
        self.call("customer_settings.update", {"id": str(setting.pk), "data": {"notes": "Public customer edit"}})
        self.assertTrue(models.AuditEvent.objects.filter(actor__username="algorithm-lab", owner=self.owner).exists())

    # 功能：验证无需密码的归属选择和无效旧令牌兼容。
    # 输入：X-Lab-User 与无效 Authorization。
    # 输出：所选账号成为新记录 owner，无效旧凭证不拒绝业务访问。
    # 逻辑：通过公开身份头新增产品，清除身份后回到默认实验账号。
    # 约束：仅实验模式接受该头，不产生登录 Session。
    def test_identity_selection_and_invalid_token(self):
        self.client.credentials(HTTP_X_LAB_USER=self.other.username, HTTP_AUTHORIZATION="Tool expired-or-invalid")
        result = self.call("products.create", {"data": {"sku": "selected", "name": "Selected", "currency": "USD", "unit_price": "1.00"}})
        self.assertEqual(models.Product.objects.get(pk=result["data"]["id"]).owner_id, self.other.pk)
        self.client.credentials(HTTP_AUTHORIZATION="invalid")
        self.assertEqual(self.client.get(BASE + "catalog/").status_code, 200)

    # 功能：验证统一关闭开关。
    # 输入：同一客户端的匿名及登录请求。
    # 输出：匿名被拒绝，跨账号资源不可见，缺失版本不被接受。
    # 逻辑：运行时切换模式后走原认证与 scope，不依赖重建测试客户端。
    # 约束：正式部署切换环境变量须重启服务。
    def test_switch_off_restores_authentication(self):
        self.assertEqual(self.client.get(BASE + "catalog/").status_code, 200)
        with override_settings(LAB_OPEN_ACCESS=False):
            self.assertIn(self.client.get(BASE + "catalog/").status_code, (401, 403))
            self.assertIn(self.client.get("/api/v1/companies/").status_code, (401, 403))
            self.client.force_login(self.other)
            response = self.client.get(f"/api/v1/sales/records/products/{self.product.pk}/")
            self.assertEqual(response.status_code, 404)
            with self.assertRaises(ValidationError):
                check_version(None, 0)

    # 功能：验证跨账号会话和知识证据。
    # 输入：其他账号工作空间会话。
    # 输出：匿名提交保留原归属，Agent 上下文可见另一账号知识。
    # 逻辑：通过真实提交、领取、上下文三个接口，领取通过公开身份头选择队列。
    # 约束：不运行模型，不改变既定知识预算。
    def test_anonymous_chat_preserves_original_owner(self):
        conversation = models.Conversation.objects.create(owner=self.other, title="Existing workspace")
        response = self.client.post("/api/v1/sales/chat/messages/", {"conversation_id": str(conversation.pk), "client_key": str(uuid.uuid4()), "content": "Shared fact"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        answer = AnswerRequest.objects.get(conversation=conversation)
        self.assertEqual(answer.owner_id, self.other.pk)
        self.client.credentials(HTTP_X_LAB_USER=self.other.username)
        response = self.client.post("/api/v1/agent/chat/requests/claim/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.client.credentials()
        response = self.client.post("/api/v1/agent/chat/context/", {"request_id": str(answer.pk), "scope": "internal"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn("Cross account fact", str(response.data))

    # 功能：验证虚构批次的匿名 CRUD 和普通入口兼容。
    # 输入：完整小型 44 表批次、无 expected 的维护请求。
    # 输出：新增、修改、删除成功；普通修改不使实验读入口失效。
    # 逻辑：新建产品经实验入口维护并删除，再普通修改既有合成产品。
    # 约束：临时附件在退出时清理，真实数据库不受影响。
    def test_seed_crud_and_regular_edit(self):
        with tempfile.TemporaryDirectory() as folder, override_settings(BASE_DIR=Path(folder)):
            run_seed(self.owner, APPROVED_BATCHES[0], 2)
            location = {"batch": APPROVED_BATCHES[0], "model": "sales.Product"}
            created = self.call("experiments.create", {**location, "data": {"sku": "lab-new", "name": "New", "currency": "USD", "unit_price": "1.00"}})["data"]
            self.call("experiments.update", {**location, "pk": created["pk"], "data": {"name": "Updated"}})
            self.call("experiments.delete", {**location, "pk": created["pk"]})
            row = table_rows(load_batch(APPROVED_BATCHES[0]), "sales.Product")[0]
            self.call("products.update", {"id": row["pk"], "data": {"name": "Ordinary edit"}})
            rows = table_rows(load_batch(APPROVED_BATCHES[0]), "sales.Product")
            updated = next(item for item in rows if item["pk"] == row["pk"])
            self.assertEqual(updated["fields"]["name"], "Ordinary edit")
            self.assertNotEqual(updated["fingerprint"], row["fingerprint"])
            self.assertEqual(self.client.get("/api/v1/sales/browse/directory/").status_code, 200)

    # 功能：验证 Agent 上下文及省略租约。
    # 输入：匿名客户端和外账号公司。
    # 输出：上下文和 L2 保存均 HTTP 200；省略版本及租约通过；OAuth 密钥领取仍要求机器凭证。
    # 逻辑：访问真实 Agent 上下文，直接核验公共租约函数与凭据输出端点边界。
    # 约束：使用实际规则构建 L2，不模拟数据库保存，不调用外部模型。
    def test_agent_context_and_lease_optional(self):
        response = self.client.get("/api/v1/agent/context/", {"company_id": str(self.company.pk)})
        self.assertEqual(response.status_code, 200, response.data)
        context = response.data
        group = self.client.get("/api/v1/agent/grouping/", {"company_id": str(self.company.pk)}).data
        document = rules.build_input(group, context)
        document["business_context"]["company_enrichment"] = context["company_enrichment"]
        document["input_version"] = input_version(context["emails"], document["merge_version"], context["external_snapshot_version"], context["company_enrichment"])
        saved = self.client.post("/api/v1/agent/analysis-inputs/", document, format="json")
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertIsNone(require_lease(self.company, None, None))
        check_version(None, self.company.revision)
        response = self.client.post("/api/v1/agent/mailbox-syncs/claim/", {"limit": 1}, format="json")
        self.assertEqual(response.status_code, 401)
