"""职责：验证业务工具端到端授权、写入、确认和外部动作边界。
实现：真实隔离 PostgreSQL 与 HTTP 请求；只在外部发送边界模拟 provider。
关联：agent_tools 包及原 crm/sales/chat 服务；不验证真实邮箱授权或发信。
目录：
- AgentToolTests：工具集成验证。
- AgentToolTests.setUp：建立隔离用户及限定 token。
- AgentToolTests.call：通过工具入口请求。
- AgentToolTests.test_catalog_and_all_list_handlers：校验目录与全部资源查询。
- AgentToolTests.test_token_scope_expiry_and_revocation：校验委托权限与期限。
- AgentToolTests.test_session_csrf_and_credential_boundary：校验授权管理与确认权限。
- AgentToolTests.test_idempotence_conflict_and_revisions：校验逻辑写入与版本。
- AgentToolTests.test_private_data_and_mass_assignment：校验私人数据隔离。
- AgentToolTests.test_proposal_frozen_confirmation_and_conflict：校验冻结提案和独立确认。
- AgentToolTests.test_expired_or_revoked_proposal：校验失效授权不可执行。
- AgentToolTests.test_external_actions_prepare_only：验证三种动作只准备。
- AgentToolTests.test_quote_precision_and_parent_revision：验证真实报价计算。
- AgentToolTests.test_knowledge_and_contact_identifiers：校验证据与整数联系人 ID。
- AgentToolTests.test_failed_write_rolls_back_receipt：验证失败回滚。
- AgentToolTests.test_crm_registration_and_sync_scope：验证 CRM 字段与 QQ 同步载荷。
- ConcurrentToolTests：并发回执验证。
- ConcurrentToolTests.invoke_in_thread：独立连接发送调用。
- ConcurrentToolTests.test_two_credentials_share_one_logical_write：验证不同凭证相同键只写一次。
变量索引：
- BASE：工具 API 路径。
"""

import hashlib
import uuid
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from jsonschema import Draft202012Validator
from rest_framework.response import Response
from rest_framework.test import APIClient
from apps.agent_tools.models import ToolCall, ToolCredential, ToolProposal
from apps.agent_tools.registry import build_registry
from apps.chat.models import KnowledgeEntry
from apps.sales import grouping, models

BASE = "/api/v1/agent-tools/"


# 功能：验证工具边界。
# 逻辑：所有业务断言基于隔离数据库及真实工具 HTTP 入口。
# 约束：不运行外部发送服务，不连接真实邮箱。
class AgentToolTests(TestCase):
    # 功能：建立上下文。
    # 输入：无外部参数。
    # 输出：用户、公司、token 客户端与 Session 客户端。
    # 逻辑：token 明确列出测试目录中的工具。
    # 约束：合成 token 仅用于测试数据库。
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="tools-user")
        self.other = get_user_model().objects.create_user(username="tools-other")
        self.company = grouping.create_company(self.user, "工具客户")
        self.foreign = grouping.create_company(self.other, "私人客户")
        self.credential = ToolCredential.objects.create(
            owner=self.user,
            name="tests",
            digest=hashlib.sha256(b"synthetic-tools-token").hexdigest(),
            allowed_tools=list(build_registry()),
            expires_at=timezone.now() + timedelta(hours=1),
        )
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Tool synthetic-tools-token")
        self.human = APIClient()
        self.human.force_login(self.user)

    # 功能：调用工具。
    # 输入：`name`、`arguments`、`key` 可选写入 UUID、`expected` HTTP 状态。
    # 输出：回执数据。
    # 逻辑：调用真实认证路径并检查状态。
    # 约束：调用者显式传递幂等键。
    def call(self, name, arguments, key=None, expected=200):
        data = {"name": name, "arguments": arguments}
        if key is not None:
            data["idempotency_key"] = key
        response = self.client.post(BASE + "call/", data, format="json")
        self.assertEqual(response.status_code, expected, response.data)
        return response.data

    # 功能：验证可发现工具均有可执行列表适配。
    # 输入：注册表和空业务数据。
    # 输出：全部 Schema 合法、列表查询成功。
    # 逻辑：逐一执行每类资源及特殊只读入口。
    # 约束：日历需连接，独立外部测试覆盖。
    def test_catalog_and_all_list_handlers(self):
        registry = build_registry()
        self.assertGreaterEqual(len(registry), 120)
        for name, spec in registry.items():
            Draft202012Validator.check_schema(spec["inputSchema"])
            if spec["kind"] == "record_list":
                with self.subTest(tool=name):
                    self.call(name, {})
        for name, args in [
            ("customers.search", {}),
            ("sales.overview", {}),
            ("sales.audit", {}),
            ("people.find", {"username": self.user.username}),
            ("mailboxes.list", {}),
            ("emails.list", {}),
            ("knowledge.search", {}),
        ]:
            self.call(name, args)
        first = self.client.get(BASE + "catalog/", {"page_size": 100}).data
        second = self.client.get(BASE + "catalog/", {"page_size": 100, "page": 2}).data
        self.assertEqual(len(first["tools"]) + len(second["tools"]), len(registry))
        self.assertNotIn("kind", first["tools"][0])
        for forbidden in (
            "actions.approve",
            "sql.execute",
            "credentials.create",
            "actions.execute",
        ):
            self.call(forbidden, {}, expected=404)

    # 功能：验证凭证的有限授权。
    # 输入：受限、到期、撤销和停用授权。
    # 输出：目录缩小且越权、失效均拒绝。
    # 逻辑：使用真实 Authorization 头。
    # 约束：不以目录隐藏替代执行权限检查。
    def test_token_scope_expiry_and_revocation(self):
        self.credential.allowed_tools = ["customers.search"]
        self.credential.save()
        self.assertEqual(self.client.get(BASE + "catalog/").data["count"], 1)
        self.call("products.list", {}, expected=403)
        self.credential.expires_at = timezone.now() - timedelta(seconds=1)
        self.credential.save()
        self.call("customers.search", {}, expected=401)
        self.credential.expires_at = timezone.now() + timedelta(hours=1)
        self.credential.revoked_at = timezone.now()
        self.credential.save()
        self.call("customers.search", {}, expected=401)
        self.credential.revoked_at = None
        self.credential.save()
        self.user.is_active = False
        self.user.save()
        self.call("customers.search", {}, expected=401)
        self.client.credentials(HTTP_AUTHORIZATION="Agent synthetic-tools-token")
        self.assertEqual(self.client.get(BASE + "catalog/").status_code, 401)

    # 功能：验证 Session-only 和 CSRF。
    # 输入：token 客户端及真实 Session，无 CSRF/有 CSRF 请求。
    # 输出：token 不能授权，Session 写入要求 CSRF，摘要不回显。
    # 逻辑：不使用 force_authenticate 绕过浏览器认证。
    # 约束：原始 token 仅在创建响应出现。
    def test_session_csrf_and_credential_boundary(self):
        payload = {
            "name": "limited",
            "allowed_tools": ["customers.search"],
            "expires_in_hours": 1,
        }
        self.assertEqual(
            self.client.post(BASE + "credentials/", payload, format="json").status_code,
            403,
        )
        browser = APIClient(enforce_csrf_checks=True)
        browser.force_login(self.user)
        self.assertEqual(
            browser.post(BASE + "credentials/", payload, format="json").status_code, 403
        )
        browser.cookies["csrftoken"] = "a" * 32
        created = browser.post(
            BASE + "credentials/", payload, format="json", HTTP_X_CSRFTOKEN="a" * 32
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created["Cache-Control"], "no-store")
        self.assertNotIn(
            created.data["token"], str(browser.get(BASE + "credentials/").data)
        )
        self.assertNotIn("digest", str(browser.get(BASE + "credentials/").data))
        self.assertEqual(
            browser.delete(
                BASE + f"credentials/{created.data['id']}/", HTTP_X_CSRFTOKEN="a" * 32
            ).status_code,
            204,
        )

    # 功能：验证幂等和乐观锁。
    # 输入：相同键重放、不同内容、过期 revision。
    # 输出：只创建一次，冲突返回 409。
    # 逻辑：创建客户和跟进后修改版本。
    # 约束：重放为历史回执，不再次执行业务。
    def test_idempotence_conflict_and_revisions(self):
        key = str(uuid.uuid4())
        first = self.call("customers.create", {"name": "新增"}, key)
        second = self.call("customers.create", {"name": "新增"}, key)
        self.assertTrue(second["replayed"])
        self.assertEqual(first["data"], second["data"])
        self.call("customers.create", {"name": "不同"}, key, 409)
        self.call("customers.create", {"name": "没有键"}, expected=400)
        self.call("customers.search", {}, key, 400)
        item = self.call(
            "follow_ups.create",
            {
                "data": {
                    "company": str(self.company.pk),
                    "title": "跟进",
                    "due_at": "2026-10-01T12:00:00+08:00",
                }
            },
            str(uuid.uuid4()),
        )["data"]
        args = {
            "id": item["id"],
            "revision": item["revision"],
            "data": {"title": "已更新"},
        }
        self.call("follow_ups.update", args, str(uuid.uuid4()))
        self.call("follow_ups.update", args, str(uuid.uuid4()), 409)
        self.assertEqual(models.FollowUp.objects.get(pk=item["id"]).title, "已更新")

    # 功能：验证私人数据和字段边界。
    # 输入：跨用户公司、知识与额外 owner 字段。
    # 输出：越权查询拒绝、列表不泄露、写入拒绝。
    # 逻辑：不伪造 actor，仅改变输入 ID。
    # 约束：知识库 fixture 不代表真实检索质量。
    def test_private_data_and_mass_assignment(self):
        self.call(
            "customers.context", {"company_id": str(self.foreign.pk)}, expected=404
        )
        names = [
            row["name"] for row in self.call("customers.search", {})["data"]["results"]
        ]
        self.assertNotIn(self.foreign.name, names)
        self.call(
            "products.create",
            {
                "data": {
                    "sku": "bad",
                    "name": "bad",
                    "currency": "USD",
                    "unit_price": "1.00",
                    "owner": self.other.pk,
                }
            },
            str(uuid.uuid4()),
            400,
        )
        entry = KnowledgeEntry.objects.create(
            owner=self.other,
            source_key="private",
            version="v1",
            title="private",
            content="secret",
        )
        self.call("knowledge.get", {"id": str(entry.pk)}, expected=404)
        self.assertEqual(self.call("knowledge.search", {})["data"]["count"], 0)

    # 功能：验证提案冻结与一次确认。
    # 输入：归档提案、篡改请求、工具身份、旧版本。
    # 输出：批准前不改业务，批准只执行一次。
    # 逻辑：通过真实 Session 决策，版本冲突保持 pending。
    # 约束：测试客户端关闭 CSRF 的部分仅验证业务；CSRF 有独立测试。
    def test_proposal_frozen_confirmation_and_conflict(self):
        item = self.call(
            "products.create",
            {
                "data": {
                    "sku": "sku",
                    "name": "产品",
                    "currency": "USD",
                    "unit_price": "2.00",
                }
            },
            str(uuid.uuid4()),
        )["data"]
        args = {"id": item["id"], "revision": item["revision"], "archived": True}
        proposal = self.call("products.archive", args, str(uuid.uuid4()))["proposal"]
        path = proposal["decision_path"]
        self.assertFalse(models.Product.objects.get(pk=item["id"]).archived)
        self.assertEqual(
            self.client.post(path, {"decision": "approve"}, format="json").status_code,
            403,
        )
        self.assertEqual(
            self.human.post(
                path, {"decision": "approve", "arguments": {}}, format="json"
            ).status_code,
            400,
        )
        first = self.human.post(path, {"decision": "approve"}, format="json")
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(
            self.call("proposals.get", {"id": proposal["id"]})["data"]["status"],
            "approved",
        )
        self.assertTrue(models.Product.objects.get(pk=item["id"]).archived)
        self.assertEqual(
            self.human.post(path, {"decision": "approve"}, format="json").data,
            first.data,
        )
        self.assertEqual(
            self.human.post(path, {"decision": "cancel"}, format="json").status_code,
            409,
        )
        stale = self.call("products.archive", args, str(uuid.uuid4()))["proposal"]
        self.assertEqual(
            self.human.post(
                stale["decision_path"], {"decision": "approve"}, format="json"
            ).status_code,
            409,
        )
        self.assertEqual(ToolProposal.objects.get(pk=stale["id"]).status, "pending")

    # 功能：验证确认时授权仍有效。
    # 输入：过期提案和撤销授权。
    # 输出：批准均失败且业务未执行。
    # 逻辑：先创建冻结提案，再改变状态。
    # 约束：不执行原操作，确认接口仍使用真实 Session。
    def test_expired_or_revoked_proposal(self):
        proposal = self.call(
            "teams.create", {"data": {"name": "协作"}}, str(uuid.uuid4())
        )["proposal"]
        ToolProposal.objects.filter(pk=proposal["id"]).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertEqual(
            self.human.post(
                proposal["decision_path"], {"decision": "approve"}, format="json"
            ).status_code,
            409,
        )
        self.credential.revoked_at = timezone.now()
        self.credential.save()
        ToolProposal.objects.filter(pk=proposal["id"]).update(
            expires_at=timezone.now() + timedelta(hours=1)
        )
        self.assertEqual(
            self.human.post(
                proposal["decision_path"], {"decision": "approve"}, format="json"
            ).status_code,
            403,
        )
        self.assertFalse(models.Team.objects.exists())
        self.assertEqual(
            self.human.post(
                proposal["decision_path"], {"decision": "cancel"}, format="json"
            ).status_code,
            200,
        )

    # 功能：验证 Gmail、QQ、日历均只准备。
    # 输入：合成不可用连接和真实草稿。
    # 输出：冻结待确认动作，无 provider 执行。
    # 逻辑：精确字段走原动作服务；重复调用重用回执。
    # 约束：模拟发送函数只是断言未调用，不证明发送可用。
    def test_external_actions_prepare_only(self):
        conversation = self.call(
            "conversations.create",
            {"data": {"company": str(self.company.pk)}},
            str(uuid.uuid4()),
        )["data"]
        draft = self.call(
            "drafts.create",
            {
                "data": {
                    "conversation": conversation["id"],
                    "kind": "email",
                    "subject": "报价",
                    "content": "请审阅",
                    "recipients": ["buyer@example.com"],
                }
            },
            str(uuid.uuid4()),
        )["data"]
        with patch("apps.sales.actions.execute_provider") as provider:
            for kind in ("gmail", "qq", "calendar"):
                connection = models.Connection.objects.create(
                    owner=self.user,
                    provider=kind,
                    account="seller@example.com",
                    encrypted_credentials="not-real",
                )
                params = {"connection_id": str(connection.pk), "draft_id": draft["id"]}
                if kind == "calendar":
                    params = {
                        "connection_id": str(connection.pk),
                        "calendar_id": "primary",
                        "title": "会议",
                        "description": "",
                        "start": "2026-10-01T10:00:00+08:00",
                        "end": "2026-10-01T11:00:00+08:00",
                        "attendees": ["buyer@example.com"],
                        "send_updates": "all",
                    }
                result = self.call(
                    "actions.prepare_" + kind,
                    {"company": str(self.company.pk), "parameters": params},
                    str(uuid.uuid4()),
                )
                self.assertEqual(result["data"]["status"], "pending_confirmation")
                self.assertEqual(result["status"], "confirmation_required")
            provider.assert_not_called()
        self.assertEqual(models.ToolAction.objects.count(), 3)

    # 功能：验证金额与父单据版本。
    # 输入：3 × 12.35 − 0.05 报价明细。
    # 输出：总额 37.00、父版本递增。
    # 逻辑：通过 tools 创建并重新读取。
    # 约束：不批准或发送报价。
    def test_quote_precision_and_parent_revision(self):
        quote = self.call(
            "quotes.create",
            {
                "data": {
                    "company": str(self.company.pk),
                    "number": "TOOLS-Q",
                    "currency": "USD",
                }
            },
            str(uuid.uuid4()),
        )["data"]
        self.call(
            "quote_lines.create",
            {
                "data": {
                    "quote": quote["id"],
                    "description": "设备",
                    "quantity": "3",
                    "unit_price": "12.35",
                    "discount": "0.05",
                }
            },
            str(uuid.uuid4()),
        )
        after = self.call("quotes.get", {"id": quote["id"]})["data"]
        self.assertEqual(after["total"], "37.00")
        self.assertGreater(after["revision"], quote["revision"])

    # 功能：验证知识引用与联系人主键。
    # 输入：合成知识与联系人。
    # 输出：来源标识和版本保持，整数 ID 可编辑。
    # 逻辑：创建后重新读取公司版本。
    # 约束：知识搜索是关键词匹配。
    def test_knowledge_and_contact_identifiers(self):
        entry = KnowledgeEntry.objects.create(
            owner=self.user,
            source_key="manual",
            version="v1",
            title="设备",
            content="检测方案",
        )
        data = self.call("knowledge.search", {"q": "检测"})["data"]["results"][0]
        self.assertEqual(data["source_id"], f"knowledge:{entry.pk}")
        self.assertEqual(data["version"], "v1")
        self.call(
            "contacts.save",
            {
                "company_id": str(self.company.pk),
                "revision": self.company.revision,
                "data": {"email": "buyer@example.com", "name": "联系人"},
            },
            str(uuid.uuid4()),
        )
        self.company.refresh_from_db()
        contact = self.company.contacts.get(email="buyer@example.com")
        self.call(
            "contacts.save",
            {
                "company_id": str(self.company.pk),
                "revision": self.company.revision,
                "data": {"id": contact.pk, "email": contact.email, "name": "新名字"},
            },
            str(uuid.uuid4()),
        )

    # 功能：验证失败不留下成功回执。
    # 输入：无权写入其他用户公司。
    # 输出：数据库拒绝并回滚 ToolCall。
    # 逻辑：真实序列化关系权限失败。
    # 约束：失败不自动重试。
    def test_failed_write_rolls_back_receipt(self):
        key = str(uuid.uuid4())
        self.call(
            "tickets.create",
            {"data": {"company": str(self.foreign.pk), "title": "越权"}},
            key,
            400,
        )
        self.assertFalse(ToolCall.objects.filter(key=key).exists())
        self.assertFalse(models.Ticket.objects.exists())

    # 功能：验证 CRM 与同步适配契约。
    # 输入：真实 CRM 登记及带范围同步提案。
    # 输出：公司资料保存，确认时完整传递范围。
    # 逻辑：仅模拟邮箱请求边界，检验参数不被吞掉。
    # 约束：不证明邮箱同步已经完成。
    def test_crm_registration_and_sync_scope(self):
        self.call(
            "customers.register",
            {
                "company_id": str(self.company.pk),
                "revision": self.company.revision,
                "data": {
                    "company_name": "登记客户",
                    "industry_from_crm": "unknown",
                    "employee_count": None,
                    "employee_count_source": None,
                },
            },
            str(uuid.uuid4()),
        )
        self.company.refresh_from_db()
        self.assertEqual(self.company.crm_status, "registered")
        args = {"mailbox_id": str(uuid.uuid4()), "sync_options": {"max_messages": 5}}
        proposal = self.call("mailboxes.sync", args, str(uuid.uuid4()))["proposal"]
        with patch(
            "apps.agent_tools.dispatch.crm.MailboxViewSet.request_sync",
            return_value=Response({"status": "queued"}, status=202),
        ) as sync:
            result = self.human.post(
                proposal["decision_path"], {"decision": "approve"}, format="json"
            )
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(
            sync.call_args.args[0].data, {"sync_options": {"max_messages": 5}}
        )
        self.assertEqual(result.data["result"]["status"], "accepted")


# 功能：验证跨连接的幂等行为。
# 逻辑：TransactionTestCase 允许两个线程各自提交。
# 约束：使用隔离 PostgreSQL，不可用单线程 Mock 代替。
class ConcurrentToolTests(TransactionTestCase):
    # 功能：发送并发请求。
    # 输入：`token`、`key`、`barrier` 同步起点。
    # 输出：HTTP 状态和回执。
    # 逻辑：每线程独立客户端和数据库连接。
    # 约束：退出时关闭该线程连接，避免测试数据库清理阻塞。
    def invoke_in_thread(self, token, key, barrier):
        close_old_connections()
        try:
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION="Tool " + token)
            barrier.wait(timeout=10)
            response = client.post(
                BASE + "call/",
                {
                    "name": "customers.create",
                    "arguments": {"name": "并发客户"},
                    "idempotency_key": key,
                },
                format="json",
            )
            return response.status_code, response.data
        finally:
            close_old_connections()

    # 功能：验证两个委托不会重复创建客户或倒序死锁。
    # 输入：同用户、不同凭证、相同幂等键。
    # 输出：两请求成功，其中一个重放，只有一个回执。
    # 逻辑：线程同时进入真实 HTTP 与数据库事务。
    # 约束：测试不保证任意业务的所有并发交错，覆盖本工具锁顺序。
    def test_two_credentials_share_one_logical_write(self):
        user = get_user_model().objects.create_user(username="concurrent-tools")
        for token in ("parallel-one", "parallel-two"):
            ToolCredential.objects.create(
                owner=user,
                name=token,
                digest=hashlib.sha256(token.encode()).hexdigest(),
                allowed_tools=["customers.create"],
                expires_at=timezone.now() + timedelta(hours=1),
            )
        key, barrier = str(uuid.uuid4()), Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self.invoke_in_thread, token, key, barrier)
                for token in ("parallel-one", "parallel-two")
            ]
            results = [future.result(timeout=30) for future in futures]
        self.assertEqual([status for status, data in results], [200, 200], results)
        self.assertEqual(
            sorted(data["replayed"] for status, data in results), [False, True]
        )
        self.assertEqual(results[0][1]["data"], results[1][1]["data"])
        self.assertEqual(ToolCall.objects.filter(owner=user, key=key).count(), 1)
