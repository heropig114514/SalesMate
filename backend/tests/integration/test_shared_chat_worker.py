"""职责：回归验证无固定服务令牌的新员工也能获得聊天回答。
实现：真实数据库、HTTP 认证及共享命令；只在模型边界使用模拟输出。
关联：chat_worker、crm.dispatch.scoped_backend 和 Agent process_chat_once。
目录：
- SharedChatWorkerTests：多员工聊天调度验收。
- SharedChatWorkerTests.setUp：创建两位未配置服务凭证的员工及待回答请求。
- SharedChatWorkerTests.test_round_robin_and_inactive：轮转发现与停用隔离。
- SharedChatWorkerTests.test_command_answers_both_owners：真实命令处理两位员工且撤销凭证。
- SharedChatWorkerTests.test_command_answers_both_owners.execute：在模型边界提供合成回答。
- SharedChatWorkerTests.test_identity_cannot_access_other_request：临时身份不能读取另一员工请求。
变量索引：
- 无
"""

import json
import os
import uuid
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import LiveServerTestCase, override_settings

from agent.clients.backend_api import BackendRequestError
from agent.workflows.chat import process_chat_once
from apps.chat import services
from apps.chat.management.commands.chat_worker import next_owner
from apps.crm.dispatch import scoped_backend
from apps.crm.models import AgentCredential
from apps.sales.models import Conversation


# 功能：验证聊天调度覆盖所有有效员工且保留权限边界。
# 逻辑：两个零客户、无服务凭证员工通过同一常驻命令的单轮入口处理。
# 约束：只连本地测试 HTTP；模型模拟不作为真实模型验收证据。
@override_settings(ALLOWED_HOSTS=["localhost", "127.0.0.1", "testserver"])
class SharedChatWorkerTests(LiveServerTestCase):
    # 功能：复现旧固定员工 Worker 遗漏的新用户任务。
    # 输入：无外部参数；隔离数据库和 LiveServer 地址。
    # 输出：owners、requests 及自动恢复的连接环境。
    # 逻辑：两个账户各自提交 ping，不配置永久 AgentCredential；环境故意放入无效旧身份。
    # 约束：不读取真实凭证，不预先领取请求。
    def setUp(self):
        self.owners = [
            get_user_model().objects.create_user(username=f"shared-chat-{i}")
            for i in range(2)
        ]
        self.requests = []
        for owner in self.owners:
            conversation = Conversation.objects.create(owner=owner)
            request, _ = services.submit(
                owner,
                {
                    "conversation_id": str(conversation.pk),
                    "client_key": str(uuid.uuid4()),
                    "content": "ping",
                },
            )
            self.requests.append(request)
        environment = patch.dict(
            os.environ,
            {
                "SALESMATE_BACKEND_AGENT_URL": self.live_server_url + "/api/v1/agent/",
                "SALESMATE_AGENT_SERVICE_TOKEN": "unused-old-user-token",
                "SALESMATE_MAILBOX_ID": "unused-old-mailbox",
                "NO_PROXY": "localhost,127.0.0.1",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)

    # 功能：验证轮转公平性、停用排除及非 pending 请求不再被选择。
    # 输入：两个保持 pending 的请求及员工状态变更。
    # 输出：先后选择两位员工，到末尾回绕；停用和 processing 均不被调度。
    # 逻辑：发现阶段只读队列，不需要预配置令牌。
    # 约束：不自动重置 processing，也不触发模型。
    def test_round_robin_and_inactive(self):
        first, second = self.owners
        self.assertEqual(next_owner().pk, first.pk)
        self.assertEqual(next_owner(first.pk).pk, second.pk)
        self.assertEqual(next_owner(second.pk).pk, first.pk)
        first.is_active = False
        first.save(update_fields=["is_active"])
        self.assertEqual(next_owner().pk, second.pk)
        services.claim(second)
        self.assertIsNone(next_owner())

    # 功能：验证真实命令为不同员工各处理一次问题。
    # 输入：两条 pending 请求、模拟模型和真实 HTTP。
    # 输出：两条 completed、答案正确、无临时凭证残留，旧环境身份不变。
    # 逻辑：模型返回工作空间 action=answer；两次 --once 各处理一个任务，第三次空队列不调用模型。
    # 约束：模型之外的领取、上下文、保存和认证均执行真实实现。
    def test_command_answers_both_owners(self):
        provider = Mock(
            return_value=json.dumps({"action": "answer", "assistant_text": "pong", "citations": []})
        )

        # 功能：仅替换模型调用以保持测试确定性。
        # 输入：`backend` 为命令创建的独立员工客户端。
        # 输出：原工作流回报结果。
        # 逻辑：领取及回报访问真实测试服务，只有 provider 使用 Mock。
        # 约束：不绕过认证，不模拟持久化。
        def execute(backend):
            return process_chat_once(backend=backend, chat_provider=provider)

        with patch(
            "apps.chat.management.commands.chat_worker.process_chat_once",
            side_effect=execute,
        ):
            for _ in range(3):
                call_command("chat_worker", once=True)
        self.assertEqual(provider.call_count, 2)
        for request in self.requests:
            request.refresh_from_db()
            self.assertEqual(request.status, "completed")
            self.assertEqual(request.assistant_message.content, "pong")
        self.assertFalse(AgentCredential.objects.exists())
        self.assertEqual(
            os.environ["SALESMATE_AGENT_SERVICE_TOKEN"], "unused-old-user-token"
        )
        self.assertEqual(os.environ["SALESMATE_MAILBOX_ID"], "unused-old-mailbox")

    # 功能：验证共享进程不会授予跨用户请求访问权。
    # 输入：第一员工临时客户端和第二员工的请求 ID。
    # 输出：只领取本人的请求，跨用户上下文 404，凭证离开上下文后撤销。
    # 逻辑：真实 AgentAuthentication 与 request_for 共同验证隔离。
    # 约束：不调用模型；对方请求保持 pending。
    def test_identity_cannot_access_other_request(self):
        with scoped_backend(self.owners[0]) as backend:
            claimed = backend.claim_answer_request()
            self.assertEqual(claimed["request_id"], str(self.requests[0].pk))
            with self.assertRaises(BackendRequestError) as error:
                backend.get_answer_context(str(self.requests[1].pk), "internal")
            self.assertEqual(error.exception.status_code, 404)
        self.assertFalse(AgentCredential.objects.exists())
        self.requests[1].refresh_from_db()
        self.assertEqual(self.requests[1].status, "pending")
