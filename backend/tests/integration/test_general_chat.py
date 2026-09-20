"""职责：验证通用会话的持久化、范围隔离和回答协议。
实现：真实数据库与 APIClient，覆盖省略客户创建、私有访问、草稿和任意合法版本回报。
关联：sales 记录接口、chat 事务与通用 Agent 模式；不调用外部模型。
目录：
- GeneralChatTests：无客户聊天的集成测试。
- GeneralChatTests.setUp：创建无客户员工及认证客户端。
- GeneralChatTests.test_general_round_trip_and_isolation：会话、草稿、提问、上下文和回答的完整链路。
- GeneralChatTests.test_scope_and_immutable_binding：通用与客户筛选及禁止重新绑定。
变量索引：
- RECORDS：销售记录入口。
"""

import uuid
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework.exceptions import ValidationError
from apps.chat import services
from apps.chat.models import KnowledgeEntry
from apps.crm.models import Company
from apps.sales.models import Conversation

RECORDS = "/api/v1/sales/records/"


# 功能：验证通用聊天无需任何客户数据。
# 逻辑：从空账户创建关系记录，跨员工读取应拒绝。
# 约束：使用测试数据库事务，不访问生产数据或模型。
class GeneralChatTests(TestCase):
    # 功能：创建没有客户的两个用户。
    # 输入：无外部参数；使用测试数据库。
    # 输出：owner、other 和浏览器客户端。
    # 逻辑：通过用户模型建立身份，接口使用 DRF 认证。
    # 约束：不绕过业务权限与序列化校验。
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="general-owner")
        self.other = get_user_model().objects.create_user(username="general-other")
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    # 功能：验证零客户账户的完整持久化及权限边界。
    # 输入：无外部参数；本人和其他员工的知识夹具。
    # 输出：成功回答、草稿可回读，越权不可读取或提问。
    # 逻辑：省略 company 创建会话并保存草稿；领取后只冻结本人知识，回报版本仅检查类型与长度。
    # 约束：模型输出为显式合成回执，不验证外部服务。
    def test_general_round_trip_and_isolation(self):
        created = self.client.post(
            RECORDS + "conversations/",
            {"title": "通用会话"},
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        conversation = created.data["id"]
        draft = self.client.post(
            RECORDS + "drafts/",
            {"conversation": conversation, "kind": "chat", "content": "邮件草稿"},
            format="json",
        )
        self.assertEqual(draft.status_code, 201, draft.data)
        KnowledgeEntry.objects.create(
            owner=self.owner,
            source_key="mine",
            version="1",
            title="本人知识",
            content="可用内容",
        )
        KnowledgeEntry.objects.create(
            owner=self.other,
            source_key="other",
            version="1",
            title="他人知识",
            content="不可见内容",
        )
        request, _ = services.submit(
            self.owner,
            {
                "conversation_id": conversation,
                "content": "你好",
                "client_key": str(uuid.uuid4()),
            },
        )
        claimed = services.claim(self.owner)
        self.assertIsNone(claimed["company_id"])
        context = services.context_for(self.owner, request.pk, "internal")
        self.assertEqual(context["customer_context"], [])
        self.assertEqual(len(context["context_items"]), 1)
        self.assertNotIn("不可见", str(context))
        result = {
            "request_id": str(request.pk),
            "chat_prompt_version": "workspace-chat-v1",
            "status": "completed",
            "error": None,
            "assistant_text": "你好，可以一起起草邮件。",
            "citations": [],
        }
        for version in (None, [], {}, "", "v" * 101):
            with self.assertRaises(ValidationError):
                services.save_answer(
                    self.owner, {**result, "chat_prompt_version": version}
                )
        self.assertTrue(services.save_answer(self.owner, result)["saved"])
        self.assertTrue(services.save_answer(self.owner, result)["duplicate"])
        self.assertEqual(
            len(
                self.client.get(
                    RECORDS + f"messages/?conversation={conversation}"
                ).data["results"]
            ),
            2,
        )
        self.assertEqual(
            self.client.get(RECORDS + f"drafts/?conversation={conversation}").data[
                "results"
            ][0]["content"],
            "邮件草稿",
        )
        self.client.force_authenticate(self.other)
        self.assertEqual(
            self.client.get(RECORDS + "conversations/?conversation_scope=general").data[
                "count"
            ],
            0,
        )
        self.assertEqual(
            self.client.get(RECORDS + f"conversations/{conversation}/").status_code, 404
        )
        denied = self.client.post(
            "/api/v1/sales/chat/messages/",
            {
                "conversation_id": conversation,
                "content": "越权",
                "client_key": str(uuid.uuid4()),
            },
            format="json",
        )
        self.assertEqual(denied.status_code, 404)

    # 功能：验证两种会话的列表与不可变绑定。
    # 输入：无外部参数；同用户各一种会话。
    # 输出：筛选互斥，非法筛选与重新绑定被拒绝。
    # 逻辑：通用与客户查询都经过 owner 范围；尝试把通用会话改为客户会话。
    # 约束：不通过重新绑定绕过历史及权限隔离。
    def test_scope_and_immutable_binding(self):
        company = Company.objects.create(
            owner=self.owner, name="客户", group_key="domain:general.example"
        )
        general = Conversation.objects.create(owner=self.owner, company=None)
        specific = Conversation.objects.create(owner=self.owner, company=company)
        for mode, expected in (("general", general), ("customer", specific)):
            response = self.client.get(
                RECORDS + f"conversations/?conversation_scope={mode}"
            )
            self.assertEqual(
                [row["id"] for row in response.data["results"]], [str(expected.pk)]
            )
        self.assertEqual(
            self.client.get(
                RECORDS + "conversations/?conversation_scope=invalid"
            ).status_code,
            400,
        )
        changed = self.client.patch(
            RECORDS + f"conversations/{general.pk}/",
            {"company": str(company.pk)},
            format="json",
            HTTP_IF_MATCH=str(general.revision),
        )
        self.assertEqual(changed.status_code, 400, changed.data)
