"""职责：验证工作空间聊天只读接口、稳定证据与原业务权限的完整后端链路。
实现：真实 PostgreSQL、认证 HTTP 和业务处理器，故障及并发等待仅在明确边界模拟。
关联：chat.tool_reads/tool_views、agent_tools 注册表和 chat.services；不调用外部模型或邮箱。
目录：
- ChatToolTests：请求绑定工具服务集成测试。
- ChatToolTests.setUp：创建两个员工、通用聊天与 Agent 身份。
- ChatToolTests.read：发送一次请求绑定工具调用。
- ChatToolTests.test_catalog_and_schema：目录隔离、Schema 一致及分页参数。
- ChatToolTests.test_search_context_answer_round_trip：搜索、两公司详情、上下文兼容与引用持久化。
- ChatToolTests.test_search_pagination_and_empty：分页完整性与空结果证据。
- ChatToolTests.test_shared_search_does_not_grant_detail：共享目录可见但私人详情拒绝，聊天仍可完成。
- ChatToolTests.test_schema_and_tool_whitelist：身份注入、错误参数、写入及确认工具拒绝。
- ChatToolTests.test_auth_request_and_terminal_boundaries：凭证隔离、请求归属和终态拒绝。
- ChatToolTests.test_read_versions_are_immutable：重复读取不覆盖旧证据。
- ChatToolTests.test_failure_rolls_back_and_hides_internal_error：异常回滚、安全错误和状态保持。
- ChatToolTests.test_registry_mode_is_rechecked：注册模式变更后发现和执行均拒绝。
- ChatToolConcurrencyTests：工具读取与最终回报的串行化测试。
- ChatToolConcurrencyTests.test_answer_waits_for_inflight_read：在途读取完成后才允许结束请求。
- ChatToolConcurrencyTests.test_answer_waits_for_inflight_read.blocked_execute：在真实处理器前设置同步屏障。
- ChatToolConcurrencyTests.test_answer_waits_for_inflight_read.read：独立数据库连接执行工具服务。
- ChatToolConcurrencyTests.test_answer_waits_for_inflight_read.answer：独立数据库连接保存最终回答。
变量索引：
- BASE：Agent 聊天服务前缀。
"""

import copy
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

from apps.agent_tools import services as tool_services
from apps.agent_tools.dispatch import execute
from apps.agent_tools.registry import build_registry
from apps.chat import services, tool_reads
from apps.chat.models import AnswerRequest, ToolRead
from apps.crm.models import Company, Extraction
from apps.sales.models import CompanyGrant, Conversation, Membership, Team
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from tests.integration.test_chat import fixture, result_for

BASE = "/api/v1/agent/chat/"


# 功能：验证只读工具服务与现有业务查询的对接。
# 逻辑：使用真实员工服务凭证、通用请求及数据库记录，HTTP 请求不绕过认证。
# 约束：每例测试回滚，不接入模型、真实客户或外部业务服务。
class ChatToolTests(TestCase):
    # 功能：建立未绑定公司的处理请求。
    # 输入：无外部参数；复用隔离聊天夹具。
    # 输出：员工、两家自有公司、一家他人公司、请求和 HTTP 客户端。
    # 逻辑：补全邮件 payload 与入库必有的 Extraction，再从通用会话提交并领取，不传 company_id。
    # 约束：凭证仅用于测试数据库，其他员工公司初始不可见。
    def setUp(self):
        self.owner, self.other, self.company, _ = fixture()
        email = self.company.emails.get()
        email.payload = {
            **email.payload,
            "dedupe_key": email.pk,
            "direction": email.direction,
            "sent_at": email.sent_at.isoformat(),
            "received_at": email.received_at.isoformat(),
            "source": "manual_test",
        }
        email.save(update_fields=["payload"])
        Extraction.objects.create(
            email=email,
            prompt_version="synthetic-extraction",
            status="failed",
            facts=None,
            error="测试夹具未运行模型抽取",
        )
        self.second = Company.objects.create(
            owner=self.owner, name="第二客户", group_key="manual:second"
        )
        self.foreign = Company.objects.create(
            owner=self.other, name="共享客户", group_key="manual:foreign"
        )
        conversation = Conversation.objects.create(owner=self.owner)
        self.request, _ = services.submit(
            self.owner,
            {
                "conversation_id": str(conversation.pk),
                "content": "比较客户",
                "client_key": str(uuid.uuid4()),
            },
        )
        services.claim(self.owner)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Agent chat-test-token")

    # 功能：发送一次真实认证的只读查询。
    # 输入：`name` 为工具名，`arguments` 为参数，`request_id` 可显式覆盖以验证隔离。
    # 输出：原始 HTTP 响应。
    # 逻辑：默认绑定本例处理请求，保留错误供调用者断言。
    # 约束：不自动重试或补写公司参数。
    def read(self, name, arguments, request_id=None):
        return self.client.post(
            BASE + "tool-reads/",
            {
                "request_id": str(request_id or self.request.pk),
                "name": name,
                "arguments": arguments,
            },
            format="json",
        )

    # 功能：验证工具目录准确反映当前白名单与原始参数契约。
    # 输入：处理请求、分页参数和无效查询变体。
    # 输出：八个明确获准且执行模式准确的工具，原 Schema 相等，错误参数为 400。
    # 逻辑：遍历分页并对照真实业务注册表。
    # 约束：仅三种实验维护允许写入，也不要求预选公司。
    def test_catalog_and_schema(self):
        response = self.client.get(
            BASE + "tools/", {"request_id": str(self.request.pk)}
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response.data["count"], 8)
        self.assertEqual(
            {row["name"] for row in response.data["tools"]},
            tool_reads.ALLOWED_TOOLS,
        )
        registry = build_registry()
        for row in response.data["tools"]:
            self.assertEqual(row["inputSchema"], registry[row["name"]]["inputSchema"])
            self.assertEqual(row["executionMode"], "write" if row["name"] in {"experiments.create", "experiments.update", "experiments.delete"} else "read")
        page = self.client.get(
            BASE + "tools/",
            {"request_id": str(self.request.pk), "page": 2, "page_size": 1},
        )
        self.assertEqual((page.data["count"], len(page.data["tools"])), (8, 1))
        for query in (
            {},
            {"page": "x"},
            {"page": 0},
            {"page_size": 101},
            {"owner_id": self.other.pk},
        ):
            params = {"request_id": str(self.request.pk), **query} if query else {}
            self.assertEqual(self.client.get(BASE + "tools/", params).status_code, 400)
        self.assertEqual(
            self.client.get(
                BASE + f"tools/?request_id={self.request.pk}&page=1&page=2"
            ).status_code,
            400,
        )

    # 功能：验证原上下文、两公司工具来源和最终浏览器引用共同工作。
    # 输入：原快照、客户搜索及两个详情读取。
    # 输出：业务数据与原工具相同，来源可区分，浏览器仅接收实际引用正文。
    # 逻辑：完整通过 HTTP 回报新提示词版本，保存后查询 Agent 状态。
    # 约束：不把测试合成回答解释为真实模型已完成工具编排。
    def test_search_context_answer_round_trip(self):
        context = services.context_for(self.owner, self.request.pk, "internal")
        search = self.read("customers.search", {"q": "客户"})
        self.assertEqual(search.status_code, 200, search.data)
        self.assertEqual(
            search.data["data"],
            tool_services.invoke(self.owner, None, "customers.search", {"q": "客户"})[
                "data"
            ],
        )
        items = []
        for company in (self.company, self.second):
            detail = self.read("customers.context", {"company_id": str(company.pk)})
            self.assertEqual(detail.status_code, 200, detail.data)
            item = detail.data["evidence_items"][0]
            self.assertEqual(json.loads(item["content"]), detail.data["data"])
            self.assertIn(str(company.pk), item["source_id"])
            items.append(item)
        self.assertNotEqual(items[0]["source_id"], items[1]["source_id"])
        self.assertEqual(
            services.context_for(self.owner, self.request.pk, "internal"), context
        )
        result = {
            **result_for(self.request),
            "chat_prompt_version": "workspace-chat-v1",
            "citations": [
                {
                    key: item[key]
                    for key in ("source_id", "source_type", "title_or_label")
                }
                for item in items
            ],
        }
        saved = self.client.post(BASE + "answers/", result, format="json")
        self.assertEqual(saved.status_code, 200, saved.data)
        status = self.client.get(BASE + f"requests/{self.request.pk}/")
        self.assertEqual(status.data["status"], "completed")
        self.assertEqual(
            [row["content"] for row in status.data["citations"]],
            [item["content"] for item in items],
        )
        self.assertNotIn("tool_reads", status.data)
        self.assertNotIn("context_snapshot", status.data)
        self.assertEqual(ToolRead.objects.filter(request=self.request).count(), 3)
        browser = APIClient()
        browser.force_authenticate(self.owner)
        public = browser.get(f"/api/v1/sales/chat/requests/{self.request.pk}/")
        self.assertEqual(public.data["citations"], status.data["citations"])

    # 功能：验证分页数据不被裁剪成假全量，空搜索仍有可用结果证据。
    # 输入：两家可见客户、每页一条及不存在的关键词。
    # 输出：总数为二、页码准确，空查询返回 completed/count=0。
    # 逻辑：逐页检查结果与页级来源，确保他人公司不出现在结果中。
    # 约束：只验证后端分页，不声称 Agent 已遍历所有页。
    def test_search_pagination_and_empty(self):
        seen = []
        for page in (1, 2):
            response = self.read("customers.search", {"page": page, "page_size": 1})
            self.assertEqual(response.status_code, 200, response.data)
            data = response.data["data"]
            self.assertEqual(
                (data["count"], data["page"], data["page_size"]), (2, page, 1)
            )
            seen.extend(row["id"] for row in data["results"])
        self.assertEqual(set(seen), {str(self.company.pk), str(self.second.pk)})
        empty = self.read("customers.search", {"q": "不存在的客户关键词"})
        self.assertEqual(empty.data["status"], "completed")
        self.assertEqual(empty.data["data"]["results"], [])
        self.assertEqual(
            json.loads(empty.data["evidence_items"][0]["content"])["count"], 0
        )

    # 功能：验证共享搜索范围不扩大私人详情访问。
    # 输入：另一员工向当前员工团队共享公司。
    # 输出：搜索有结果、详情 404 且 scope=tool，仍能保存正常回答。
    # 逻辑：使用既有团队授权模型与真实客户处理器。
    # 约束：失败不登记证据，不将请求变为 failed。
    def test_shared_search_does_not_grant_detail(self):
        team = Team.objects.create(owner=self.other, name="共享团队")
        Membership.objects.create(
            owner=self.other, team=team, user=self.owner, role="viewer"
        )
        CompanyGrant.objects.create(
            owner=self.other, company=self.foreign, team=team, role="viewer"
        )
        search = self.read("customers.search", {"q": "共享客户"})
        self.assertEqual(search.data["data"]["count"], 1)
        detail = self.read("customers.context", {"company_id": str(self.foreign.pk)})
        self.assertEqual(detail.status_code, 404, detail.data)
        self.assertEqual(detail.data["error"]["scope"], "tool")
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, "processing")
        self.assertFalse(ToolRead.objects.filter(tool="customers.context").exists())
        saved = self.client.post(
            BASE + "answers/",
            {**result_for(self.request), "assistant_text": "详情不可用。"},
            format="json",
        )
        self.assertEqual(saved.status_code, 200)

    # 功能：验证工具白名单及参数 Schema，不能通过身份参数越权。
    # 输入：额外身份/幂等字段、缺少定位参数、错误分页和非白名单名称。
    # 输出：结构错误 400，工具范围错误 403，未产生读取记录。
    # 逻辑：覆盖写工具、确认工具及未开放的其他只读工具。
    # 约束：不通过取消 company_id 定位要求来猜测客户。
    def test_schema_and_tool_whitelist(self):
        for name in (
            "customers.create",
            "customers.analyze",
            "mailboxes.sync",
            "knowledge.get",
            "nonexistent",
        ):
            self.assertEqual(self.read(name, {}).status_code, 403)
        for name, args in (
            ("customers.context", {}),
            ("customers.context", {"company_id": "bad"}),
            ("customers.search", {"owner_id": self.other.pk}),
            ("customers.search", {"page_size": 101}),
        ):
            response = self.read(name, args)
            self.assertEqual(response.status_code, 400, response.data)
            self.assertEqual(response.data["error"]["scope"], "tool")
        payload = {
            "request_id": str(self.request.pk),
            "name": "customers.search",
            "arguments": {},
        }
        for key, value in (
            ("owner_id", self.other.pk),
            ("idempotency_key", str(uuid.uuid4())),
        ):
            response = self.client.post(
                BASE + "tool-reads/", {**payload, key: value}, format="json"
            )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.data["error"]["scope"], "request")
        self.assertFalse(ToolRead.objects.exists())

    # 功能：验证凭证、请求归属和生命周期边界。
    # 输入：无凭证、Tool 凭证、Session、其他员工请求及本请求终态。
    # 输出：认证失败 401、越权 404、终态读取 409，状态查询仍可读本人终态。
    # 逻辑：分别通过真实认证和请求查询，不将公司是否绑定作为访问前提。
    # 约束：不会复活终态或恢复已撤销会话。
    def test_auth_request_and_terminal_boundaries(self):
        payload = {
            "request_id": str(self.request.pk),
            "name": "customers.search",
            "arguments": {},
        }
        denied = APIClient()
        self.assertEqual(
            denied.post(BASE + "tool-reads/", payload, format="json").status_code, 401
        )
        denied.force_login(self.owner)
        self.assertEqual(
            denied.post(BASE + "tool-reads/", payload, format="json").status_code, 401
        )
        denied.credentials(HTTP_AUTHORIZATION="Tool chat-test-token")
        self.assertEqual(
            denied.post(BASE + "tool-reads/", payload, format="json").status_code, 401
        )
        conversation = Conversation.objects.create(owner=self.other)
        foreign, _ = services.submit(
            self.other,
            {
                "conversation_id": str(conversation.pk),
                "content": "他人",
                "client_key": str(uuid.uuid4()),
            },
        )
        for request_id in (foreign.pk, uuid.uuid4()):
            response = self.read("customers.search", {}, request_id)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.data["error"]["scope"], "request")
            self.assertEqual(
                self.client.get(BASE + f"requests/{request_id}/").status_code, 404
            )
        for state in ("pending", "completed", "failed"):
            AnswerRequest.objects.filter(pk=self.request.pk).update(status=state)
            self.assertEqual(self.read("customers.search", {}).status_code, 409)
            self.assertEqual(
                self.client.get(
                    BASE + "tools/", {"request_id": str(self.request.pk)}
                ).status_code,
                409,
            )
        self.assertEqual(
            self.client.get(BASE + f"requests/{self.request.pk}/").status_code, 200
        )
        self.assertFalse(ToolRead.objects.exists())

    # 功能：验证同公司多次读取的来源稳定且不会覆盖旧结果。
    # 输入：第一次详情读取、公司资料修改及第二次读取。
    # 输出：来源标识不同，旧记录与旧引用仍保留首次正文。
    # 逻辑：再次查询后选旧来源回报，不要求预先调用 chat/context。
    # 约束：测试修改为数据库夹具操作，聊天工具本身不执行写入。
    def test_read_versions_are_immutable(self):
        first = self.read(
            "customers.context", {"company_id": str(self.company.pk)}
        ).data
        Company.objects.filter(pk=self.company.pk).update(name="后来修改的名称")
        second = self.read(
            "customers.context", {"company_id": str(self.company.pk)}
        ).data
        self.assertNotEqual(first["read_id"], second["read_id"])
        self.assertEqual(
            ToolRead.objects.get(pk=first["read_id"]).evidence_items,
            first["evidence_items"],
        )
        self.assertNotIn("后来修改", first["evidence_items"][0]["content"])
        item = first["evidence_items"][0]
        services.save_answer(
            self.owner,
            result_for(
                self.request,
                {
                    key: item[key]
                    for key in ("source_id", "source_type", "title_or_label")
                },
            ),
        )
        self.assertEqual(self.request.citations.get().content, item["content"])
        self.request.refresh_from_db()
        self.assertIsNone(self.request.context_snapshot)

    # 功能：验证查询或证据保存异常不留下半成功状态且不泄露内部细节。
    # 输入：工具执行异常及证据写入边界抛出的异常。
    # 输出：HTTP 500、无证据记录、请求保持 processing，异常文本不出现在响应。
    # 逻辑：分别模拟调用边界与保存事务失败。
    # 约束：不模拟成功，不把异常解释为真实服务已验证。
    def test_failure_rolls_back_and_hides_internal_error(self):
        for target in (
            "apps.chat.tool_reads.execute",
            "apps.chat.tool_reads.ToolRead.objects.create",
        ):
            with patch(target, side_effect=RuntimeError("synthetic-private-detail")):
                response = self.read("customers.search", {})
            self.assertEqual(response.status_code, 500, response.data)
            self.assertNotIn("synthetic-private-detail", str(response.data))
            self.assertEqual(response["Cache-Control"], "no-store")
            self.assertFalse(ToolRead.objects.exists())
            self.request.refresh_from_db()
            self.assertEqual(self.request.status, "processing")

    # 功能：验证白名单工具后来变为写模式时不会被继续开放。
    # 输入：仅在测试中替换注册表的 customers.search executionMode。
    # 输出：目录移除该工具，调用被 403 拒绝且处理器未执行。
    # 逻辑：发现保留其他四个 read 及三个实验 write 工具，执行再次复核 customers.search 的实时 read 模式。
    # 约束：模拟只涉及注册表，不修改真实业务代码或数据库。
    def test_registry_mode_is_rechecked(self):
        registry = copy.deepcopy(build_registry())
        registry["customers.search"]["executionMode"] = "write"
        with (
            patch("apps.chat.tool_reads.build_registry", return_value=registry),
            patch("apps.agent_tools.services.build_registry", return_value=registry),
            patch("apps.chat.tool_reads.execute") as handler,
        ):
            catalog = self.client.get(
                BASE + "tools/", {"request_id": str(self.request.pk)}
            )
            self.assertEqual(
                [item["name"] for item in catalog.data["tools"]], sorted(tool_reads.ALLOWED_TOOLS - {"customers.search"})
            )
            self.assertEqual(self.read("customers.search", {}).status_code, 403)
            handler.assert_not_called()


# 功能：验证真实数据库中工具读取和保存回答不能交错破坏终态边界。
# 逻辑：使用独立连接、线程屏障及原业务查询，不模拟数据库行锁。
# 约束：要求支持 select_for_update 的 PostgreSQL，不用于推断其他数据库并发行为。
class ChatToolConcurrencyTests(TransactionTestCase):
    # 功能：验证读取持锁时最终回报等待，结束后拒绝新读取。
    # 输入：无外部参数；真实处理请求及受控在途读取。
    # 输出：读取先登记，回答后完成，终态新读取被拒绝。
    # 逻辑：在线程内独立连接，事件只延迟业务处理器，不代替事务锁。
    # 约束：等待均有限且 finally 释放，失败不遗留线程或数据库连接。
    def test_answer_waits_for_inflight_read(self):
        owner, _, _, conversation = fixture()
        request, _ = services.submit(
            owner,
            {
                "conversation_id": str(conversation.pk),
                "content": "查询",
                "client_key": str(uuid.uuid4()),
            },
        )
        services.claim(owner)
        entered, release, answer_started = Event(), Event(), Event()

        # 功能：在取得请求锁后阻塞读取，便于验证另一事务确实等待。
        # 输入：`args`/`kwargs` 为原 execute 调用参数。
        # 输出：原业务响应。
        # 逻辑：通知主线程并等待释放后调用真实处理器。
        # 约束：最多等待 10 秒，不模拟查询结果。
        def blocked_execute(*args, **kwargs):
            entered.set()
            if not release.wait(10):
                raise TimeoutError("测试未释放在途读取")
            return execute(*args, **kwargs)

        # 功能：用独立连接读取工具。
        # 输入：闭包中的 owner 和 request。
        # 输出：成功工具回执。
        # 逻辑：线程前后清理连接，调用真实事务服务。
        # 约束：不复用测试主线程连接。
        def read():
            close_old_connections()
            try:
                return tool_reads.read_tool(
                    owner,
                    {
                        "request_id": str(request.pk),
                        "name": "customers.search",
                        "arguments": {},
                    },
                )
            finally:
                close_old_connections()

        # 功能：用独立连接回报最终回答。
        # 输入：闭包中的 owner、request 与 answer_started 事件。
        # 输出：保存回执。
        # 逻辑：通知开始后调用真实保存服务，结束时清理连接。
        # 约束：不修改请求锁或工具状态。
        def answer():
            close_old_connections()
            try:
                answer_started.set()
                return services.save_answer(owner, result_for(request))
            finally:
                close_old_connections()

        with (
            patch("apps.chat.tool_reads.execute", side_effect=blocked_execute),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            reading = pool.submit(read)
            try:
                self.assertTrue(entered.wait(5))
                answering = pool.submit(answer)
                self.assertTrue(answer_started.wait(5))
                self.assertFalse(answering.done())
            finally:
                release.set()
            self.assertEqual(reading.result(timeout=10)["status"], "completed")
            self.assertTrue(answering.result(timeout=10)["saved"])
        request.refresh_from_db()
        self.assertEqual(request.status, "completed")
        self.assertEqual(request.tool_reads.count(), 1)
        from apps.crm.access import InvalidState

        with self.assertRaises(InvalidState):
            tool_reads.read_tool(
                owner,
                {
                    "request_id": str(request.pk),
                    "name": "customers.search",
                    "arguments": {},
                },
            )
