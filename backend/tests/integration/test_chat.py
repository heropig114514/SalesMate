"""职责：验证聊天任务事务、证据、隔离和真实 HTTP Agent 适配。
实现：使用隔离 PostgreSQL 与真实 Django API，覆盖结构校验与来源元数据保存；模型和 Worker 环境使用确定性模拟。
关联：apps.chat、既有 sales 消息及 agent.workflows.chat；不调用真实邮箱或百炼。
目录：
- fixture：建立两员工及自有客户会话。
- result_for：构造合法 Agent 回报。
- ChatTests：接口及状态不变量测试。
- ChatTests.setUp：创建隔离夹具与客户端。
- ChatTests.submit：经浏览器 API 提问。
- ChatTests.test_submit_idempotency_and_active_guard：提交幂等及活动约束。
- ChatTests.test_message_and_request_rollback：创建请求失败整体回滚。
- ChatTests.test_claim_history_boundary：领取历史截止和一次性领取。
- ChatTests.test_agent_auth_and_employee_isolation：服务与用户权限隔离。
- ChatTests.test_shared_company_does_not_grant_mail_access：共享业务不升级邮件权限。
- ChatTests.test_context_frozen_budget_and_current_analysis：上下文冻结、容量和画像有效性。
- ChatTests.test_report_idempotency_and_citations：引用快照及终态去重。
- ChatTests.test_invalid_report_rolls_back：Schema 反例不产生消息。
- ChatTests.test_report_accepts_metadata_without_reading_other_snapshots：来源不在快照仍可保存，但不能读取其他请求正文。
- ChatTests.test_report_accepts_arbitrary_error_text：合法错误结构不受固定文案限制。
- ChatTests.test_failure_retry_and_late_report：显式新尝试与迟到结果拒绝。
- ChatTests.test_interrupted_request_recovery：人工恢复确认及旧结果隔离。
- ChatTests.test_no_context_required_for_zero_citation_answer：零引用澄清可完成。
- ChatTests.test_archived_conversation_is_not_claimed：归档任务失败并释放队列。
- ChatTests.test_browser_cannot_forge_assistant：原消息写入口仍禁止助手伪造。
- ChatTests.test_browser_csrf_and_bad_input：会话写入保留 CSRF 和严格字段。
- ChatTests.test_knowledge_import_versions：知识版本不可覆盖与原子回滚。
- ChatTests.test_current_analysis_and_business_sources：有效画像和三类业务证据可引用。
- ChatTests.test_answer_transaction_rollback：引用保存失败回滚助手消息。
- ChatTests.test_worker_report_failure_stops：回报失败停止消费者且不重试。
- ChatTests.test_processing_access_revoked：领取后撤销权限不能读证据或保存结果。
- ChatTests.test_bad_pagination：非法分页返回 400。
- ChatConcurrencyTests：真实数据库并发不变量。
- ChatConcurrencyTests.test_concurrent_claim：两消费者不能领取同一请求。
- ChatConcurrencyTests.test_concurrent_claim.consume：独立连接领取函数。
- ChatConcurrencyTests.test_concurrent_submit：并发提交返回同一原始请求。
- ChatConcurrencyTests.test_concurrent_submit.send：独立连接提交函数。
- ChatConcurrencyTests.test_concurrent_report：重复并发回报只有一条助手消息。
- ChatConcurrencyTests.test_concurrent_report.send：独立连接回报函数。
- ChatHTTPTests：跨真实 HTTP 的 Agent 完整流程。
- ChatHTTPTests.test_agent_real_http_round_trip：真实传输、持久化与引用回读。
变量索引：
- BROWSER：聊天浏览器接口前缀。
- AGENT：固定 Agent 聊天接口前缀。
"""

import hashlib
import json
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections
from django.test import (
    TestCase,
    TransactionTestCase,
    LiveServerTestCase,
    override_settings,
)
from django.utils import timezone
from rest_framework.test import APIClient

from agent.clients.backend_api import DjangoBackendClient
from agent.workflows.chat import (
    process_chat_once,
    parse_answer_context,
    parse_conversation_request,
)
from apps.chat import services
from apps.chat.models import AnswerRequest, Citation, KnowledgeEntry
from apps.crm.models import (
    AgentCredential,
    Company,
    Mailbox,
    Email,
    AnalysisInput,
    Analysis,
)
from apps.crm.durable_models import SnapshotInvalidation
from apps.sales.models import Conversation, Message, Team, Membership, CompanyGrant

BROWSER = "/api/v1/sales/chat/"
AGENT = "/api/v1/agent/chat/"


# 功能：建立独立聊天测试实体。
# 输入：无外部参数，使用当前测试数据库。
# 输出：员工、另一员工、公司及会话。
# 逻辑：只使用合成账号和邮件，凭证为测试固定值。
# 约束：不读取运行环境凭证，不触发邮箱或模型调用。
def fixture():
    owner = get_user_model().objects.create_user(username="chat-owner")
    other = get_user_model().objects.create_user(username="chat-other")
    company = Company.objects.create(
        owner=owner, name="测试客户", group_key="domain:chat.example"
    )
    conversation = Conversation.objects.create(owner=owner, company=company)
    AgentCredential.objects.create(
        owner=owner,
        name="chat-test",
        digest=hashlib.sha256(b"chat-test-token").hexdigest(),
    )
    mailbox = Mailbox.objects.create(owner=owner, address="seller@chat.example")
    Email.objects.create(
        dedupe_key="seller@chat.example:one",
        mailbox=mailbox,
        company=company,
        sent_at=timezone.now(),
        received_at=timezone.now(),
        direction="inbound",
        payload={"subject": "采购需求", "body_text": "客户需要设备。"},
    )
    return owner, other, company, conversation


# 功能：构造合法最终结果。
# 输入：`request` 回答任务，`citation` 可选严格引用三元组。
# 输出：chat-v2 completed 回报。
# 逻辑：带引用时返回受证据支持的确定语句，否则返回资料不足。
# 约束：仅测试夹具，不代表真实模型能力。
def result_for(request, citation=None):
    return {
        "request_id": str(request.pk),
        "chat_prompt_version": "chat-v2",
        "status": "completed",
        "error": None,
        "assistant_text": (
            "客户需要设备。[1]" if citation else "现有资料不足，无法回答该问题。"
        ),
        "citations": [citation] if citation else [],
    }


# 功能：覆盖聊天后端状态与权限。
# 逻辑：真实 ORM/HTTP 配合模拟失败验证事务边界。
# 约束：TestCase 每例回滚，外部服务不参与。
class ChatTests(TestCase):
    # 功能：准备员工和两种认证客户端。
    # 输入：无外部参数，读取测试框架数据库。
    # 输出：实例夹具。
    # 逻辑：浏览器 force_authenticate，Agent 使用真实服务凭证认证。
    # 约束：不绕过 AgentAuthentication。
    def setUp(self):
        self.owner, self.other, self.company, self.conversation = fixture()
        self.browser = APIClient()
        self.browser.force_authenticate(self.owner)
        self.agent = APIClient()
        self.agent.credentials(HTTP_AUTHORIZATION="Agent chat-test-token")

    # 功能：经公开聊天写入入口提交问题。
    # 输入：`key` 可选幂等 UUID，`content` 问题正文。
    # 输出：成功 HTTP 响应。
    # 逻辑：对状态断言后返回，供边界测试复用。
    # 约束：不直接创建 AnswerRequest。
    def submit(self, key=None, content="客户需要什么？"):
        response = self.browser.post(
            BROWSER + "messages/",
            {
                "conversation_id": str(self.conversation.pk),
                "content": content,
                "client_key": str(key or uuid.uuid4()),
            },
            format="json",
        )
        self.assertIn(response.status_code, (200, 201), response.data)
        return response

    # 功能：验证重复提交不会重复收费或生成任务。
    # 输入：无外部参数，使用同会话同幂等键。
    # 输出：断言同请求、冲突及数据库计数。
    # 逻辑：同内容重传成功，异内容和第二个活动问题均冲突。
    # 约束：验证后端幂等，不模拟浏览器双击行为。
    def test_submit_idempotency_and_active_guard(self):
        key = uuid.uuid4()
        first = self.submit(key)
        second = self.submit(key)
        self.assertEqual(first.data["request_id"], second.data["request_id"])
        for changed_key, content in ((key, "不同问题"), (uuid.uuid4(), "另一个问题")):
            response = self.browser.post(
                BROWSER + "messages/",
                {
                    "conversation_id": str(self.conversation.pk),
                    "content": content,
                    "client_key": str(changed_key),
                },
                format="json",
            )
            self.assertEqual(response.status_code, 409)
        self.assertEqual(Message.objects.count(), 1)
        self.assertEqual(AnswerRequest.objects.count(), 1)

    # 功能：验证消息和任务的原子创建。
    # 输入：无外部参数，模拟任务创建数据库写入异常。
    # 输出：消息与请求计数均为零。
    # 逻辑：内部异常向上冒泡，事务回滚刚写入的用户消息。
    # 约束：仅模拟指定写入边界，不替换数据库事务。
    def test_message_and_request_rollback(self):
        with patch(
            "apps.chat.services.AnswerRequest.objects.create",
            side_effect=RuntimeError("synthetic"),
        ):
            with self.assertRaises(RuntimeError):
                services.submit(
                    self.owner,
                    {
                        "conversation_id": str(self.conversation.pk),
                        "content": "问题",
                        "client_key": str(uuid.uuid4()),
                    },
                )
        self.assertFalse(Message.objects.exists())
        self.assertFalse(AnswerRequest.objects.exists())

    # 功能：验证领取时不会把未来问题混入历史。
    # 输入：无外部参数，建立过去、当前和之后的消息。
    # 输出：严格请求可被 Agent 解析，历史仅包含过去消息。
    # 逻辑：第二次领取为空，recent_history 冻结在数据库。
    # 约束：并发领取另用 TransactionTestCase 验证。
    def test_claim_history_boundary(self):
        Message.objects.create(
            owner=self.owner,
            conversation=self.conversation,
            client_key=uuid.uuid4(),
            role="user",
            content="之前的问题",
        )
        self.submit()
        Message.objects.create(
            owner=self.owner,
            conversation=self.conversation,
            client_key=uuid.uuid4(),
            role="user",
            content="之后的问题",
        )
        response = self.agent.post(AGENT + "requests/claim/", {}, format="json")
        parsed = parse_conversation_request(response.data["request"])
        self.assertEqual(
            parsed["recent_history"], [{"role": "user", "content": "之前的问题"}]
        )
        self.assertIsNone(
            self.agent.post(AGENT + "requests/claim/", {}, format="json").data[
                "request"
            ]
        )

    # 功能：验证所有接口重新核验员工身份。
    # 输入：无外部参数，另一员工使用真实独立 token。
    # 输出：越权查询、上下文和回报为 404，错误认证为 401。
    # 逻辑：服务端不信任任意 request_id 或浏览器会话替代服务凭证。
    # 约束：不暴露任务是否属于其他员工。
    def test_agent_auth_and_employee_isolation(self):
        request_id = self.submit().data["request_id"]
        AgentCredential.objects.create(
            owner=self.other,
            name="other",
            digest=hashlib.sha256(b"other-token").hexdigest(),
        )
        self.agent.credentials(HTTP_AUTHORIZATION="Agent other-token")
        self.assertIsNone(
            self.agent.post(AGENT + "requests/claim/", {}, format="json").data[
                "request"
            ]
        )
        self.assertEqual(
            self.agent.post(
                AGENT + "context/",
                {"request_id": request_id, "scope": "internal"},
                format="json",
            ).status_code,
            404,
        )
        self.assertEqual(
            self.agent.post(
                AGENT + "answers/",
                result_for(AnswerRequest.objects.get(pk=request_id)),
                format="json",
            ).status_code,
            404,
        )
        self.browser.force_authenticate(self.other)
        self.assertEqual(
            self.browser.get(BROWSER + f"requests/{request_id}/").status_code, 404
        )
        anonymous = APIClient()
        self.assertEqual(
            anonymous.post(AGENT + "requests/claim/", {}, format="json").status_code,
            401,
        )

    # 功能：验证业务共享不能读取他人邮箱衍生上下文。
    # 输入：无外部参数，建立团队和客户共享授权。
    # 输出：共享员工创建聊天任务被拒绝。
    # 逻辑：即使会话属于共享员工，客户完整聊天仍要求员工自有公司。
    # 约束：不改变原业务共享规则。
    def test_shared_company_does_not_grant_mail_access(self):
        team = Team.objects.create(owner=self.owner, name="团队")
        Membership.objects.create(
            owner=self.owner, team=team, user=self.other, role="viewer"
        )
        CompanyGrant.objects.create(
            owner=self.owner, company=self.company, team=team, role="viewer"
        )
        conversation = Conversation.objects.create(
            owner=self.other, company=self.company
        )
        self.browser.force_authenticate(self.other)
        response = self.browser.post(
            BROWSER + "messages/",
            {
                "conversation_id": str(conversation.pk),
                "client_key": str(uuid.uuid4()),
                "content": "给我邮件",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 404)

    # 功能：验证证据快照、知识预算与画像版本。
    # 输入：无外部参数，创建陈旧画像、隐藏邮件和真实版本知识。
    # 输出：不含陈旧画像/隐藏正文，后续变更不会修改本请求证据。
    # 逻辑：当前 revision 不匹配的画像缺口明确，知识仍有预算。
    # 约束：使用人工数据库夹具，不证明真实 L3 生成效果。
    def test_context_frozen_budget_and_current_analysis(self):
        snapshot = AnalysisInput.objects.create(
            company=self.company, revision=0, input_version="test", payload={}
        )
        Analysis.objects.create(
            snapshot=snapshot,
            provider="agent",
            prompt_version="test",
            payload={"status": "completed", "detail_view": {"profile": "陈旧画像"}},
        )
        self.company.revision = 1
        self.company.save(update_fields=["revision"])
        KnowledgeEntry.objects.create(
            owner=self.owner,
            source_key="manual",
            version="1",
            title="测试知识",
            content="员工明确提供的测试资料",
        )
        email = Email.objects.first()
        Email.objects.create(
            dedupe_key="hidden",
            mailbox=email.mailbox,
            company=self.company,
            business_classification="non_business",
            payload={"body_text": "隐藏正文"},
            direction="inbound",
            sent_at=timezone.now(),
            received_at=timezone.now(),
        )
        request_id = self.submit().data["request_id"]
        services.claim(self.owner)
        first = services.context_for(self.owner, uuid.UUID(request_id), "internal")
        parse_answer_context(first)
        self.assertNotIn("陈旧画像", json.dumps(first, ensure_ascii=False))
        self.assertNotIn("隐藏正文", json.dumps(first, ensure_ascii=False))
        self.assertEqual(len(first["context_items"]), 1)
        self.assertLessEqual(
            len(first["customer_context"]) + len(first["context_items"]), 12
        )
        email.payload["body_text"] = "更新后的正文"
        email.save(update_fields=["payload"])
        self.assertEqual(
            first, services.context_for(self.owner, uuid.UUID(request_id), "internal")
        )
        self.assertFalse(first["external_available"])
        response = self.agent.post(
            AGENT + "context/",
            {"request_id": request_id, "scope": "external"},
            format="json",
        )
        self.assertEqual(response.status_code, 409)

    # 功能：验证完成结果精确幂等及引用正文来源。
    # 输入：无外部参数，使用真实冻结邮件引用。
    # 输出：唯一助手消息、唯一引用和冲突保护。
    # 逻辑：相同回报重复保存成功，不同正文拒绝；浏览器可读证据。
    # 约束：Agent 不能自报引用正文。
    def test_report_idempotency_and_citations(self):
        self.submit()
        services.claim(self.owner)
        request = AnswerRequest.objects.get()
        context = services.context_for(self.owner, request.pk, "internal")
        citation = {
            key: context["customer_context"][0][key]
            for key in ("source_id", "source_type", "title_or_label")
        }
        payload = result_for(request, citation)
        first = self.agent.post(AGENT + "answers/", payload, format="json")
        self.assertEqual(first.status_code, 200, first.data)
        second = self.agent.post(AGENT + "answers/", payload, format="json")
        self.assertTrue(second.data["duplicate"])
        self.assertEqual(
            first.data["assistant_message_id"], second.data["assistant_message_id"]
        )
        self.assertEqual(Message.objects.filter(role="assistant").count(), 1)
        self.assertEqual(
            Citation.objects.get().content, context["customer_context"][0]["content"]
        )
        payload["assistant_text"] = "其他内容。[1]"
        self.assertEqual(
            self.agent.post(AGENT + "answers/", payload, format="json").status_code, 409
        )
        state = self.browser.get(BROWSER + f"requests/{request.pk}/")
        self.assertEqual(state.data["status"], "completed")
        self.assertEqual(state.data["citations"][0]["position"], 1)

    # 功能：验证非法回报不会产生部分成功记录。
    # 输入：无外部参数，构造缺版本、非法字段、引用类型或长度错误和失败正文。
    # 输出：全部 400 且请求仍 processing。
    # 逻辑：对各反例经真实 HTTP 验证。
    # 约束：只验证 Schema 反例，不把内容限制混入结构测试。
    def test_invalid_report_rolls_back(self):
        self.submit()
        services.claim(self.owner)
        request = AnswerRequest.objects.get()
        missing = result_for(request)
        del missing["chat_prompt_version"]
        cases = [
            missing,
            {**result_for(request), "extra": True},
            result_for(
                request,
                {
                    "source_id": [],
                    "source_type": "customer_email",
                    "title_or_label": "其他员工",
                },
            ),
            {
                **result_for(request),
                "status": "failed",
                "error": {"code": "model_unavailable", "message": "unsafe"},
            },
        ]
        cases.extend(
            [
                {**result_for(request), "assistant_text": 123},
                {**result_for(request), "citations": {}},
                {**result_for(request), "status": "pending"},
                result_for(
                    request,
                    {
                        "source_id": "source",
                        "source_type": "x" * 81,
                        "title_or_label": "过长来源类型",
                    },
                ),
            ]
        )
        for payload in cases:
            self.assertEqual(
                self.agent.post(AGENT + "answers/", payload, format="json").status_code,
                400,
            )
        self.assertFalse(Message.objects.filter(role="assistant").exists())
        self.assertFalse(Citation.objects.exists())
        request.refresh_from_db()
        self.assertEqual(request.status, "processing")

    # 功能：验证内容校验放宽后仍不会跨请求或员工读取证据正文。
    # 输入：无外部参数；本请求、同员工另一请求和其他员工请求各有独立快照。
    # 输出：重复及未登记来源按原顺序保存，仅本请求匹配来源附有正文。
    # 逻辑：使用新版本和不匹配的正文编号经真实认证接口回报，再核对浏览器投影与幂等。
    # 约束：快照为显式测试数据，不代表资料真实性或模型引用质量已验证。
    def test_report_accepts_metadata_without_reading_other_snapshots(self):
        self.submit()
        services.claim(self.owner)
        request = AnswerRequest.objects.get()
        known = {
            "source_id": "known",
            "source_type": "customer_email",
            "title_or_label": "本次来源",
            "content": "本次已提供正文",
        }
        request.context_snapshot = {"customer_context": [known], "context_items": []}
        request.save(update_fields=["context_snapshot"])
        citations = [
            {key: known[key] for key in ("source_id", "source_type", "title_or_label")}
        ]
        for index, owner in enumerate((self.owner, self.other)):
            conversation = Conversation.objects.create(owner=owner)
            other_request, _ = services.submit(
                owner,
                {
                    "conversation_id": str(conversation.pk),
                    "content": "其他问题",
                    "client_key": str(uuid.uuid4()),
                },
            )
            item = {
                "source_id": f"other-request:{index}",
                "source_type": "internal_knowledge",
                "title_or_label": "其他请求来源",
                "content": "不得从其他请求读取的正文",
            }
            other_request.context_snapshot = {"context_items": [item]}
            other_request.save(update_fields=["context_snapshot"])
            citations.append(
                {
                    key: item[key]
                    for key in ("source_id", "source_type", "title_or_label")
                }
            )
        citations.append(citations[0].copy())
        payload = {
            **result_for(request),
            "chat_prompt_version": "workspace-chat-v1",
            "assistant_text": "回答可使用普通方括号 [99]，后端不校验编号或语义。",
            "citations": citations,
        }
        response = self.agent.post(AGENT + "answers/", payload, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(
            self.agent.post(AGENT + "answers/", payload, format="json").data[
                "duplicate"
            ]
        )
        state = self.browser.get(BROWSER + f"requests/{request.pk}/")
        self.assertEqual(
            [row["content"] for row in state.data["citations"]],
            [known["content"], "", "", known["content"]],
        )
        self.assertEqual(
            [row["position"] for row in state.data["citations"]], [1, 2, 3, 4]
        )
        request.refresh_from_db()
        self.assertEqual(request.result, payload)
        self.assertEqual(request.chat_prompt_version, "workspace-chat-v1")

    # 功能：验证错误码和文案只检查结构，合法新错误可持久化。
    # 输入：无外部参数；已领取请求及测试专用错误对象。
    # 输出：失败回报成功且错误按原值回读，不创建助手消息。
    # 逻辑：经 Agent 认证接口提交未知错误码与自定义文案，再核对请求状态。
    # 约束：错误文案由生产方负责脱敏，测试不发送真实服务异常。
    def test_report_accepts_arbitrary_error_text(self):
        self.submit()
        services.claim(self.owner)
        request = AnswerRequest.objects.get()
        payload = {
            **result_for(request),
            "status": "failed",
            "assistant_text": "",
            "error": {"code": "tool_unavailable", "message": "客户工具暂时不可用。"},
        }
        response = self.agent.post(AGENT + "answers/", payload, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        request.refresh_from_db()
        self.assertEqual(request.error, payload["error"])
        self.assertEqual(request.status, "failed")
        self.assertIsNone(request.assistant_message_id)

    # 功能：验证失败尝试保留且重试有新身份。
    # 输入：无外部参数，标准模型失败回报。
    # 输出：重复失败幂等，显式重试唯一，旧完成回报冲突。
    # 逻辑：同用户消息可关联多个请求但不能覆盖原失败记录。
    # 约束：不启动模型或隐式回队。
    def test_failure_retry_and_late_report(self):
        self.submit()
        services.claim(self.owner)
        old = AnswerRequest.objects.get()
        failure = {
            **result_for(old),
            "status": "failed",
            "assistant_text": "",
            "error": {
                "code": "model_unavailable",
                "message": "回答模型暂时不可用，请稍后重试。",
            },
        }
        self.assertIsNone(
            services.save_answer(self.owner, failure)["assistant_message_id"]
        )
        self.assertTrue(services.save_answer(self.owner, failure)["duplicate"])
        first = self.browser.post(
            BROWSER + f"requests/{old.pk}/retry/", {}, format="json"
        )
        second = self.browser.post(
            BROWSER + f"requests/{old.pk}/retry/", {}, format="json"
        )
        self.assertEqual(first.data["request_id"], second.data["request_id"])
        self.assertNotEqual(first.data["request_id"], str(old.pk))
        self.assertEqual(Message.objects.count(), 1)
        self.assertEqual(
            self.agent.post(
                AGENT + "answers/", result_for(old), format="json"
            ).status_code,
            409,
        )

    # 功能：验证中断恢复不会让旧结果覆盖新尝试。
    # 输入：无外部参数，领取后模拟操作者确认中断。
    # 输出：无确认拒绝，有确认终止，旧回报冲突。
    # 逻辑：命令不采用自动超时或复活请求。
    # 约束：只验证命令契约，不模拟系统进程终止。
    def test_interrupted_request_recovery(self):
        self.submit()
        services.claim(self.owner)
        request = AnswerRequest.objects.get()
        with self.assertRaises(CommandError):
            call_command(
                "chat_interrupt", owner=self.owner.username, request_id=str(request.pk)
            )
        call_command(
            "chat_interrupt",
            owner=self.owner.username,
            request_id=str(request.pk),
            confirm_interrupted=True,
            stdout=StringIO(),
        )
        request.refresh_from_db()
        self.assertEqual(request.error["code"], "worker_interrupted")
        self.assertEqual(
            self.agent.post(
                AGENT + "answers/", result_for(request), format="json"
            ).status_code,
            409,
        )

    # 功能：验证无副作用拒绝或澄清不需要证据读取。
    # 输入：无外部参数，合法零引用回答。
    # 输出：成功唯一助手消息，快照保持空。
    # 逻辑：回报服务只对白名单引用要求上下文。
    # 约束：不强制每条自然语言回答都含引用。
    def test_no_context_required_for_zero_citation_answer(self):
        self.submit()
        services.claim(self.owner)
        request = AnswerRequest.objects.get()
        self.assertTrue(services.save_answer(self.owner, result_for(request))["saved"])
        request.refresh_from_db()
        self.assertIsNone(request.context_snapshot)

    # 功能：验证领取前权限失效的请求不被消费。
    # 输入：无外部参数，提交后归档会话。
    # 输出：无任务返回且原 pending 明确失败。
    # 逻辑：归档不是永久堵塞队列或越权访问的理由。
    # 约束：不取消已发送外部操作，本模块只有只读回答。
    def test_archived_conversation_is_not_claimed(self):
        self.submit()
        self.conversation.archived = True
        self.conversation.save(update_fields=["archived"])
        self.assertIsNone(services.claim(self.owner))
        self.assertEqual(AnswerRequest.objects.get().error["code"], "access_revoked")

    # 功能：验证接入不会放开浏览器助手角色。
    # 输入：无外部参数，调用既有通用消息接口伪造 assistant。
    # 输出：400 且无消息创建。
    # 逻辑：受保护聊天回报与普通消息写入保持独立。
    # 约束：不更改原 sales serializer 的只读字段规则。
    def test_browser_cannot_forge_assistant(self):
        response = self.browser.post(
            "/api/v1/sales/records/messages/",
            {
                "conversation": str(self.conversation.pk),
                "role": "assistant",
                "content": "伪造回答",
                "client_key": str(uuid.uuid4()),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    # 功能：验证浏览器写入认证和输入约束。
    # 输入：无外部参数，真实会话登录但省略 CSRF。
    # 输出：403；额外 owner 和非字符串问题为 400。
    # 逻辑：使用 enforce_csrf_checks，不以 force_authenticate 代替 CSRF 测试。
    # 约束：不修改默认安全配置。
    def test_browser_csrf_and_bad_input(self):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.owner)
        data = {
            "conversation_id": str(self.conversation.pk),
            "content": "问题",
            "client_key": str(uuid.uuid4()),
        }
        self.assertEqual(
            client.post(BROWSER + "messages/", data, format="json").status_code, 403
        )
        self.assertEqual(
            self.browser.post(
                BROWSER + "messages/", {**data, "owner": self.other.pk}, format="json"
            ).status_code,
            400,
        )
        self.assertEqual(
            self.browser.post(
                BROWSER + "messages/", {**data, "content": 123}, format="json"
            ).status_code,
            400,
        )

    # 功能：验证知识新版本显式替换与不可覆盖。
    # 输入：无外部参数，临时 UTF-8 JSON 文件。
    # 输出：同版本异内容失败，新版本停用旧版本。
    # 逻辑：使用真实管理命令和数据库事务。
    # 约束：测试资料标记为合成，不导入生产知识。
    def test_knowledge_import_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "knowledge.json"
            row = {
                "source_key": "test",
                "version": "1",
                "title": "合成知识",
                "content": "合成内容",
                "active": True,
            }
            path.write_text(json.dumps([row]), encoding="utf-8")
            call_command(
                "chat_knowledge",
                owner=self.owner.username,
                file=str(path),
                stdout=StringIO(),
            )
            row["content"] = "不同内容"
            path.write_text(json.dumps([row]), encoding="utf-8")
            with self.assertRaises(CommandError):
                call_command(
                    "chat_knowledge",
                    owner=self.owner.username,
                    file=str(path),
                    stdout=StringIO(),
                )
            row["version"] = "2"
            path.write_text(json.dumps([row]), encoding="utf-8")
            call_command(
                "chat_knowledge",
                owner=self.owner.username,
                file=str(path),
                stdout=StringIO(),
            )
            self.assertEqual(
                list(
                    KnowledgeEntry.objects.filter(active=True).values_list(
                        "version", flat=True
                    )
                ),
                ["2"],
            )

    # 功能：验证非空业务资料确实进入独立引用条目。
    # 输入：无外部参数，当前画像、真实业务投影和规则占位反例。
    # 输出：包含邮件、模型画像、工单、报价、订单，规则占位及失效画像排除。
    # 逻辑：新请求首次读取有效版本，后续请求不会重新使用被失效的画像。
    # 约束：不更改 L3 生成参数或交易投影规则。
    def test_current_analysis_and_business_sources(self):
        snapshot = AnalysisInput.objects.create(
            company=self.company, revision=0, input_version="current", payload={}
        )
        Analysis.objects.create(
            snapshot=snapshot,
            provider="agent",
            prompt_version="test",
            payload={
                "status": "completed",
                "detail_view": {"profile": {"facts": "客户需要设备"}},
            },
        )
        Analysis.objects.create(
            snapshot=snapshot,
            provider="rules",
            prompt_version="rules",
            payload={"status": "completed", "detail_view": {"profile": "规则占位"}},
        )
        self.company.tickets = [{"ticket_id": "ticket-one", "status": "open"}]
        self.company.quotes = [
            {
                "quote_id": "quote-one",
                "status": "sent",
                "currency": "SGD",
                "amount": "100",
            }
        ]
        self.company.orders = [
            {
                "order_id": "order-one",
                "status": "confirmed",
                "currency": "SGD",
                "amount": "80",
            }
        ]
        self.company.save(update_fields=["tickets", "quotes", "orders"])
        self.submit()
        services.claim(self.owner)
        request = AnswerRequest.objects.get()
        context = services.context_for(self.owner, request.pk, "internal")
        self.assertEqual(
            {row["source_type"] for row in context["customer_context"]},
            {"customer_email", "customer_analysis", "ticket", "quote", "order"},
        )
        self.assertNotIn("规则占位", json.dumps(context, ensure_ascii=False))
        services.save_answer(self.owner, result_for(request))
        SnapshotInvalidation.objects.create(snapshot=snapshot, reason="test")
        second = self.submit().data["request_id"]
        services.claim(self.owner)
        context = services.context_for(self.owner, uuid.UUID(second), "internal")
        self.assertNotIn(
            "customer_analysis",
            [row["source_type"] for row in context["customer_context"]],
        )

    # 功能：验证助手消息、引用与终态同事务。
    # 输入：无外部参数，模拟引用写入异常。
    # 输出：没有助手消息，请求仍 processing。
    # 逻辑：异常冒泡且数据库回滚先创建的消息。
    # 约束：模拟仅限写入失败，不替换事务机制。
    def test_answer_transaction_rollback(self):
        self.submit()
        services.claim(self.owner)
        request = AnswerRequest.objects.get()
        context = services.context_for(self.owner, request.pk, "internal")
        citation = {
            key: context["customer_context"][0][key]
            for key in ("source_id", "source_type", "title_or_label")
        }
        with patch(
            "apps.chat.services.Citation.objects.bulk_create",
            side_effect=RuntimeError("synthetic"),
        ):
            with self.assertRaises(RuntimeError):
                services.save_answer(self.owner, result_for(request, citation))
        self.assertFalse(Message.objects.filter(role="assistant").exists())
        request.refresh_from_db()
        self.assertEqual(request.status, "processing")

    # 功能：验证未知回报结果停止消费且不重派。
    # 输入：无外部参数，模拟 process_chat_once 返回 report_failed。
    # 输出：CommandError 且只执行一次工作流。
    # 逻辑：共享调度选择员工后，常驻模式遇到回报不确定也立即停止，需要人工查询请求真实状态。
    # 约束：模拟 Agent 执行边界，不声明真实进程或网络故障已复现。
    def test_worker_report_failure_stops(self):
        failure = {
            "request_id": str(uuid.uuid4()),
            "status": "failed",
            "error": {"code": "report_failed"},
        }
        # 不让 Worker 的连接清理关闭 TestCase 外层事务；生产 finally 行为不变。
        with (
            patch(
                "apps.chat.management.commands.chat_worker.next_owner",
                return_value=self.owner,
            ),
            patch("apps.chat.management.commands.chat_worker.scoped_backend"),
            patch("apps.chat.management.commands.chat_worker.connections.close_all"),
            patch(
                "apps.chat.management.commands.chat_worker.process_chat_once",
                return_value=failure,
            ) as process,
        ):
            with self.assertRaises(CommandError):
                call_command("chat_worker")
            process.assert_called_once()

    # 功能：验证领取后的权限撤销。
    # 输入：无外部参数，领取后归档会话。
    # 输出：context/report 均 404，不写助手消息。
    # 逻辑：每次服务调用重新核验绑定和可访问状态。
    # 约束：任务留待明确人工终止，不能绕过权限保存。
    def test_processing_access_revoked(self):
        self.submit()
        services.claim(self.owner)
        request = AnswerRequest.objects.get()
        self.conversation.archived = True
        self.conversation.save(update_fields=["archived"])
        self.assertEqual(
            self.agent.post(
                AGENT + "context/",
                {"request_id": str(request.pk), "scope": "internal"},
                format="json",
            ).status_code,
            404,
        )
        self.assertEqual(
            self.agent.post(
                AGENT + "answers/", result_for(request), format="json"
            ).status_code,
            404,
        )
        self.assertFalse(Message.objects.filter(role="assistant").exists())

    # 功能：验证分页错误不会变为服务端异常。
    # 输入：无外部参数，合法会话及非法 page。
    # 输出：400。
    # 逻辑：视图把数字解析异常映射为协议错误。
    # 约束：不改变共用分页默认值。
    def test_bad_pagination(self):
        response = self.browser.get(
            BROWSER + f"requests/?conversation={self.conversation.pk}&page=not-a-number"
        )
        self.assertEqual(response.status_code, 400)


# 功能：验证独立 PostgreSQL 连接上的并发行为。
# 逻辑：用同步屏障同时触发两个事务，查询真实持久结果。
# 约束：必须在支持行锁的 PostgreSQL 测试库运行，不以模拟锁代替。
class ChatConcurrencyTests(TransactionTestCase):
    # 功能：验证多消费者领取唯一性。
    # 输入：无外部参数，两个独立连接。
    # 输出：恰好一个请求、一个空结果。
    # 逻辑：并发事务竞争员工锁，后取得锁者看到已处理状态。
    # 约束：线程结束关闭连接，避免泄露测试资源。
    def test_concurrent_claim(self):
        owner, _, _, conversation = fixture()
        services.submit(
            owner,
            {
                "conversation_id": str(conversation.pk),
                "client_key": str(uuid.uuid4()),
                "content": "问题",
            },
        )
        barrier = Barrier(2)

        # 功能：在独立连接领取任务。
        # 输入：`index` 并发占位编号。
        # 输出：领取结果。
        # 逻辑：屏障同步后调用真实事务。
        # 约束：finally 关闭本线程连接。
        def consume(index):
            try:
                barrier.wait(timeout=10)
                return services.claim(owner)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(consume, range(2)))
        self.assertEqual(sum(row is not None for row in results), 1)

    # 功能：验证并发重传只有一个消息和请求。
    # 输入：无外部参数，两个连接提交相同幂等键。
    # 输出：相同 request_id 与唯一计数。
    # 逻辑：用户行锁保护消息检查与创建。
    # 约束：不通过修改数据库隔离级别实现测试通过。
    def test_concurrent_submit(self):
        owner, _, _, conversation = fixture()
        data = {
            "conversation_id": str(conversation.pk),
            "client_key": str(uuid.uuid4()),
            "content": "问题",
        }
        barrier = Barrier(2)

        # 功能：独立事务提交同一问题。
        # 输入：`index` 并发占位编号。
        # 输出：创建或复用请求 ID。
        # 逻辑：屏障后调用真实提交服务。
        # 约束：退出时关闭连接。
        def send(index):
            try:
                barrier.wait(timeout=10)
                return services.submit(owner, data)[0].pk
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(send, range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(Message.objects.count(), 1)
        self.assertEqual(AnswerRequest.objects.count(), 1)

    # 功能：验证并发最终回报不会创建两个助手消息。
    # 输入：无外部参数，两个独立连接发送完全相同结果。
    # 输出：一条助手消息，首次和重复响应各一个。
    # 逻辑：结果检查及写入由员工锁串行化。
    # 约束：不模拟数据库锁或隔离级别。
    def test_concurrent_report(self):
        owner, _, _, conversation = fixture()
        request, _ = services.submit(
            owner,
            {
                "conversation_id": str(conversation.pk),
                "client_key": str(uuid.uuid4()),
                "content": "问题",
            },
        )
        services.claim(owner)
        barrier = Barrier(2)
        payload = result_for(request)

        # 功能：独立连接提交相同终态。
        # 输入：`index` 并发占位编号。
        # 输出：真实保存响应。
        # 逻辑：同步开始后竞争同一员工锁。
        # 约束：finally 清理本线程数据库连接。
        def send(index):
            try:
                barrier.wait(timeout=10)
                return services.save_answer(owner, payload)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(send, range(2)))
        self.assertEqual(sorted(row["duplicate"] for row in results), [False, True])
        self.assertEqual(Message.objects.filter(role="assistant").count(), 1)


# 功能：验证真实后端 HTTP 与 Agent 原实现兼容。
# 逻辑：Django 临时服务器、requests 客户端和 PostgreSQL 都真实运行。
# 约束：模型输出模拟，不证明百炼可用或模型回答质量。
class ChatHTTPTests(LiveServerTestCase):
    # 功能：验证领取、上下文、回答和浏览器回读闭环。
    # 输入：无外部参数，临时服务及固定合成证据。
    # 输出：完成状态、真实助手消息及引用；重复回报幂等。
    # 逻辑：原 DjangoBackendClient 和 process_chat_once 经真实 HTTP 执行，模型仅被调用一次。
    # 约束：不连接外部邮件、知识或模型服务。
    @override_settings(
        ALLOWED_HOSTS=["localhost", "127.0.0.1", "testserver"],
        LOCAL_DEBUG_AUTO_LOGIN=False,
    )
    def test_agent_real_http_round_trip(self):
        owner, _, _, conversation = fixture()
        request, _ = services.submit(
            owner,
            {
                "conversation_id": str(conversation.pk),
                "client_key": str(uuid.uuid4()),
                "content": "客户需要什么？",
            },
        )
        backend = DjangoBackendClient(
            self.live_server_url + "/api/v1/agent/", "chat-test-token"
        )
        citation = {
            "source_id": "email:seller@chat.example:one:review:0",
            "source_type": "customer_email",
            "title_or_label": "采购需求",
        }
        provider = Mock(
            return_value=json.dumps(
                {"assistant_text": "客户需要设备。[1]", "citations": [citation]},
                ensure_ascii=False,
            )
        )
        result = process_chat_once(backend=backend, chat_provider=provider)
        self.assertEqual(result["status"], "completed", result)
        provider.assert_called_once()
        request.refresh_from_db()
        self.assertEqual(request.assistant_message.content, "客户需要设备。[1]")
        self.assertTrue(backend.report_answer(result)["duplicate"])
        browser = APIClient()
        browser.force_authenticate(owner)
        state = browser.get(BROWSER + f"requests/{request.pk}/").data
        self.assertEqual(state["citations"][0]["source_id"], citation["source_id"])
        self.assertIsNone(process_chat_once(backend=backend, chat_provider=provider))
