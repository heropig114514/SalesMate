"""职责：验收软件辅助工具的真实 HTTP、权限、事务与持久化契约。
实现：隔离 PostgreSQL、普通用户 Tool token；资料及文件不依赖浏览器 Session，外部网络不调用。
关联：agent_tools.support/presets、sales.insights、accounts.onboarding；不验证模型、实时资讯或真实发信。
目录：
- SupportToolTests：软件辅助工具集成场景。
- SupportToolTests.setUp：建立本人、其他用户和限定令牌。
- SupportToolTests.call：通过 HTTP 调用并核对状态。
- SupportToolTests.product：返回完整参考产品输入。
- SupportToolTests.test_profiles_and_legacy_catalog：资料保存和旧条目稳定标识。
- SupportToolTests.test_catalog_crud_links_and_conflicts：逐条 CRUD、关联隔离和版本冲突。
- SupportToolTests.test_documents_roundtrip_and_references：无 Session 上传、分块读取与引用删除保护。
- SupportToolTests.test_existing_attachment_reads：已有业务附件的真实存储读取和完整性校验。
- SupportToolTests.test_events_news_crud_and_filters：事件资讯存储、筛选、修改和归档。
- SupportToolTests.test_invalid_insights_and_private_relations：坏数据拒绝、公共资讯读取及跨账号关联隔离。
- SupportToolTests.test_permission_presets_and_token_boundaries：批量授权和令牌范围固定。
变量索引：
- BASE：工具 HTTP 路径。
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


# 功能：验证可调用的数据能力。
# 逻辑：HTTP 请求经过真实工具认证和业务服务，数据库由 Django 隔离。
# 约束：测试令牌仅合成；不连接任何外部提供方。
class SupportToolTests(TestCase):
    # 功能：建立测试身份。
    # 输入：无外部参数。
    # 输出：本人及其他用户、Tool 客户端和仅供授权管理的 Session 客户端。
    # 逻辑：授权明确包含当前工具名，全部业务调用使用 token。
    # 约束：不创建生产凭证。
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="support-user")
        self.other = get_user_model().objects.create_user(username="support-other")
        self.credential = ToolCredential.objects.create(owner=self.user, name="test", digest=hashlib.sha256(b"support-token").hexdigest(), allowed_tools=list(build_registry()), expires_at=timezone.now() + timedelta(hours=1))
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Tool support-token")
        self.human = APIClient()
        self.human.force_login(self.user)

    # 功能：调用真实工具接口。
    # 输入：`name` 工具、`args` 参数、`expected` HTTP 状态、`key` 可选明确幂等键。
    # 输出：成功的回执或预期错误响应。
    # 逻辑：写工具默认生成本次测试操作的键，显式键用于重放验证；非 DRF 错误只报告状态，避免回显上传正文。
    # 约束：不替换业务处理器或认证。
    def call(self, name, args, expected=200, key=None):
        data = {"name": name, "arguments": args}
        if build_registry()[name]["executionMode"] != "read":
            data["idempotency_key"] = key or str(uuid.uuid4())
        response = self.client.post(BASE + "call/", data, format="json")
        self.assertEqual(response.status_code, expected, getattr(response, "data", response.status_code))
        return response.data

    # 功能：创建参考产品夹具。
    # 输入：无外部参数。
    # 输出：符合现有资料录入契约的字典。
    # 逻辑：使用明确币种和未知价格，不补正式报价。
    # 约束：仅返回数据，不创建业务记录。
    def product(self):
        return {"name": "检测设备", "category": "A", "specifications": ["尺寸 10 cm"], "price_min": None, "price_max": None, "currency": "SGD", "scenarios": ["量测"], "document_id": None}

    # 功能：验证原资料接口和历史目录兼容性。
    # 输入：无 ID 的旧产品列表和修改请求。
    # 输出：GET 不写数据库，删除首行后其他 ID 保持稳定，旧 revision 明确冲突。
    # 逻辑：通过 MCP 共用 HTTP 路径先读再写，对比实际持久化记录。
    # 约束：不触发模型调用。
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

    # 功能：验证单条目录操作和链接权限。
    # 输入：本人及他人的交易产品、重复请求和过期版本。
    # 输出：条目 CRUD、版本与幂等正确，其他账号链接无法保存。
    # 逻辑：执行真实调用并检查是否意外创建交易记录或确认提案。
    # 约束：参考目录只关联显式选择的产品。
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

    # 功能：验证无 Session 的完整文件和方案链路。
    # 输入：UTF-8、达到 5 MiB 上限的 TXT、PDF、坏编码及外部账号文件。
    # 输出：分块可还原原文，上限文件可上传，超限或普通工具的大载荷被拒绝；引用文件不可删除，未引用文件可删除且幂等。
    # 逻辑：真实上传、关联、分块和删除，验证没有浏览器 Cookie。
    # 约束：PDF 只验证原始字节读取，不声称已解析文本。
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

    # 功能：验证既有附件无需 Session 链接即可读取。
    # 输入：临时私有存储中的真实文件和篡改后的内容。
    # 输出：正文可读，完整性错误及越权被拒绝。
    # 逻辑：仅替换测试存储根目录，保留原读取、权限和 SHA-256 校验。
    # 约束：临时目录由测试管理，不写生产附件。
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

    # 功能：验证活动资讯接口与 MCP 使用同一数据。
    # 输入：明确来源、时间、分类的合成活动和资讯。
    # 输出：查询、修改、归档、恢复和时间筛选正确。
    # 逻辑：写入采用 Tool 身份，再从原业务 API 查询验证持久化。
    # 约束：无实时资讯抓取；不计算价值或优先级。
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

    # 功能：验证公共资讯读取及时间、来源和私人商机关联边界。
    # 输入：无时区时间、危险链接、倒置窗口以及其他员工记录。
    # 输出：公共资讯可跨账号读取；无效输入和私有商机关联被拒绝且不留成功回执。
    # 逻辑：真实 JSON Schema、序列化器及 owner 查询联动。
    # 约束：只共享公共资讯，客户和商机关联维持隔离。
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

    # 功能：验证一次授权后直接调用与固定范围。
    # 输入：明确选定的 read_only/data_management 模板和受限 Tool token。
    # 输出：读取无需重复确认、写入仅获授权时成功，工具令牌不能自发授权。
    # 逻辑：通过 Session 创建模板凭证，再用新 Tool token 请求；检查新工具不会扩大已发范围。
    # 约束：不取消现有提案和真实外部动作确认。
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
