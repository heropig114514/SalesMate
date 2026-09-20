"""职责：验证工作空间升级的创建边界、旧任务退役和历史保留。
实现：使用真实数据库与 API；仅直接造旧记录模拟升级前状态，调用真实 Worker 启动使用的退役服务。
关联：chat.services、chat_worker 启动清理、sales 会话创建服务和邮件草稿准备。
目录：
- WorkspaceChatTests：升级与安全边界测试。
- WorkspaceChatTests.setUp：建立员工及新旧会话。
- WorkspaceChatTests.legacy_request：构造旧版本已存在的请求。
- WorkspaceChatTests.test_legacy_creation_and_retry_rejected：新建和重试不再进入公司聊天。
- WorkspaceChatTests.test_claim_retires_legacy_and_continues：旧 pending 不阻挡工作空间领取。
- WorkspaceChatTests.test_retirement_preserves_history_and_is_idempotent：终止旧活动任务且保留终态历史。
- WorkspaceChatTests.test_workspace_draft_requires_owner_and_explicit_customer：工作空间草稿可准备动作且不可跨员工或旧客户。
变量索引：
- 无
"""

import uuid

from apps.chat import services
from apps.chat.models import AnswerRequest
from apps.crm.access import InvalidState
from apps.crm.models import Company
from apps.sales import actions, models
from django.test import TestCase
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from tests.integration.test_chat import fixture


# 功能：覆盖旧契约退役和工作空间草稿权限。
# 逻辑：真实 ORM 与服务执行，旧数据显式构造而非经已停用创建路径。
# 约束：不访问生产库或发送邮件，TestCase 回滚测试记录。
class WorkspaceChatTests(TestCase):
    # 功能：创建员工、工作空间及公司绑定历史会话。
    # 输入：无参数，使用隔离测试数据库。
    # 输出：实例夹具和员工认证客户端。
    # 逻辑：复用公开测试夹具，直接造旧会话模拟升级前数据。
    # 约束：不改写系统用户或实际外部凭证。
    def setUp(self):
        self.owner, self.other, self.company, self.workspace = fixture()
        self.legacy = models.Conversation.objects.create(
            owner=self.owner, company=self.company
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    # 功能：构造升级前的公司绑定请求。
    # 输入：`status` 为待验证状态，默认 pending。
    # 输出：持久化 AnswerRequest。
    # 逻辑：保留原公司、会话及用户消息关系，终态 result 用于比较退役前后。
    # 约束：只用作历史数据夹具，不绕过生产入口创建新任务。
    def legacy_request(self, status="pending"):
        message = models.Message.objects.create(
            owner=self.owner,
            conversation=self.legacy,
            role="user",
            content="这个客户怎么样？",
            client_key=uuid.uuid4(),
        )
        return AnswerRequest.objects.create(
            owner=self.owner,
            conversation=self.legacy,
            company=self.company,
            user_message=message,
            status=status,
            result={"historical": True} if status == "completed" else None,
        )

    # 功能：验证所有新聊天仅能由工作空间会话创建。
    # 输入：无参数；浏览器创建载荷与旧失败任务。
    # 输出：公司会话创建为 400，旧会话提交和重试拒绝，历史仍可读。
    # 逻辑：检查拒绝操作没有创建用户消息或后继任务。
    # 约束：不删除或重新绑定旧会话，工作空间显式 null 仍可创建。
    def test_legacy_creation_and_retry_rejected(self):
        created = self.client.post(
            "/api/v1/sales/records/conversations/",
            {"company": str(self.company.pk)},
            format="json",
        )
        self.assertEqual(created.status_code, 400)
        self.assertEqual(
            self.client.post(
                "/api/v1/sales/records/conversations/", {"company": None}, format="json"
            ).status_code,
            201,
        )
        old = self.legacy_request("failed")
        with self.assertRaises(InvalidState):
            services.submit(
                self.owner,
                {
                    "conversation_id": str(self.legacy.pk),
                    "content": "继续",
                    "client_key": str(uuid.uuid4()),
                },
            )
        with self.assertRaises(InvalidState):
            services.retry(self.owner, old.pk)
        self.assertEqual(models.Message.objects.count(), 1)
        self.assertEqual(services.request_for(self.owner, old.pk).pk, old.pk)

    # 功能：验证运行期遗留任务不会阻塞领取队列。
    # 输入：无参数；旧公司 pending 和新的工作空间请求。
    # 输出：只返回五字段工作空间请求，旧请求明确失败且不生成助手消息。
    # 逻辑：同员工锁内跳过旧任务并继续寻找可执行任务。
    # 约束：不隐式转移问题、不自动重新调用模型。
    def test_claim_retires_legacy_and_continues(self):
        old = self.legacy_request()
        current, _ = services.submit(
            self.owner,
            {
                "conversation_id": str(self.workspace.pk),
                "content": "查找客户",
                "client_key": str(uuid.uuid4()),
            },
        )
        claimed = services.claim(self.owner)
        self.assertEqual(claimed["request_id"], str(current.pk))
        self.assertNotIn("company_id", claimed)
        old.refresh_from_db()
        self.assertEqual(old.status, "failed")
        self.assertEqual(old.error["code"], "workspace_chat_required")
        self.assertIsNotNone(old.finished_at)
        self.assertIsNone(old.assistant_message_id)

    # 功能：验证 Worker 启动的清理只结束旧活动任务。
    # 输入：无参数；pending、processing、completed 历史和新工作空间任务。
    # 输出：旧活动任务结束，工作空间及历史结果不变；第二次运行无额外变化。
    # 逻辑：调用 Worker 使用的退役服务两次，核对终态幂等及工作空间不受影响。
    # 约束：不改变数据库结构，不调用模型，不把旧任务转为新的 pending。
    def test_retirement_preserves_history_and_is_idempotent(self):
        pending = self.legacy_request()
        self.legacy = models.Conversation.objects.create(
            owner=self.owner, company=self.company
        )
        processing = self.legacy_request("processing")
        completed = self.legacy_request("completed")
        current, _ = services.submit(
            self.owner,
            {
                "conversation_id": str(self.workspace.pk),
                "content": "你好",
                "client_key": str(uuid.uuid4()),
            },
        )
        self.assertEqual(services.retire_legacy_requests(), 2)
        pending.refresh_from_db()
        finished = pending.finished_at
        self.assertEqual(services.retire_legacy_requests(), 0)
        for old in (pending, processing):
            old.refresh_from_db()
            self.assertEqual(old.status, "failed")
        self.assertEqual(pending.finished_at, finished)
        completed.refresh_from_db()
        current.refresh_from_db()
        self.assertEqual(completed.result, {"historical": True})
        self.assertEqual(completed.status, "completed")
        self.assertEqual(current.status, "pending")
        self.assertEqual(models.Message.objects.count(), 4)

    # 功能：验证工作空间草稿用于明确客户的动作，归属限制仍生效。
    # 输入：无参数；本人连接和邮件草稿、他人会话及其他客户历史会话。
    # 输出：本人工作空间草稿成功冻结，其他员工或错误客户草稿拒绝。
    # 逻辑：调用真实参数准备服务，仅改变草稿所在会话作为反例。
    # 约束：不执行 provider、不批准动作、无真实连接凭证。
    def test_workspace_draft_requires_owner_and_explicit_customer(self):
        connector = models.Connection.objects.create(
            owner=self.owner,
            provider="gmail",
            account="synthetic@example.com",
            encrypted_credentials="not-a-token",
        )
        draft = models.Draft.objects.create(
            owner=self.owner,
            conversation=self.workspace,
            kind="email",
            subject="测试",
            content="合成正文",
            recipients=["buyer@example.com"],
        )
        parameters = {"connection_id": str(connector.pk), "draft_id": str(draft.pk)}
        self.assertEqual(
            actions.validate_parameters(
                self.owner, self.company, "gmail.send", parameters
            )["body"],
            draft.content,
        )
        other_company = Company.objects.create(
            owner=self.owner, name="其他客户", group_key="domain:other.example"
        )
        for conversation in (
            models.Conversation.objects.create(owner=self.other),
            models.Conversation.objects.create(owner=self.owner, company=other_company),
        ):
            draft.conversation = conversation
            draft.save(update_fields=["conversation"])
            with self.assertRaises(ValidationError):
                actions.validate_parameters(
                    self.owner, self.company, "gmail.send", parameters
                )
