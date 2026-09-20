"""职责：验证账号内部数据清空、登录保留、账号隔离及并发和故障边界。
实现：真实 PostgreSQL 事务与会话 HTTP，合成业务数据和临时附件；仅模拟文件删除失败。
关联：accounts.reset、reset_locks、reset_middleware 及各模块外键。
目录：
- AccountResetTests：账户重置集成验收。
- AccountResetTests.setUp：创建两个账号和临时附件根目录。
- AccountResetTests.seed：创建带依赖、重试关系及凭证的合成业务数据。
- AccountResetTests.reset：以真实登录会话调用清理接口。
- AccountResetTests.test_scope_identity_files_sessions_and_tokens：验证清空范围、身份、附件、会话和凭证。
- AccountResetTests.test_idempotency_does_not_delete_new_data：验证较早操作重放不影响新数据。
- AccountResetTests.test_file_failure_can_resume：验证文件失败保持隔离且可显式继续。
- AccountResetTests.test_busy_account_is_unchanged：验证正在执行的工作阻止清理且不修改数据。
- AccountResetTests.test_stale_write_rejected：验证旧页面不能写回。
- AccountResetTests.test_cross_owner_reference_rolls_back：验证共享业务引用不被隐式级联删除。
- AccountResetTests.test_authentication_csrf_and_schema：验证匿名、CSRF 和操作键。
- AccountResetTests.test_shared_membership_is_removed_without_deleting_other_team：验证解除关系不删除他人团队。
- AccountResetTests.test_stale_sales_queue_is_cancelled：验证已清除动作不会执行外部调用。
- AccountResetTests.test_shared_reminders_do_not_repopulate：验证解除负责人关联后不再生成本人通知。
变量索引：
- URL：当前登录账号重置路由。
"""
import hashlib
from pathlib import Path
import tempfile
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import CompanyProfile, SalesSetup, SetupDocument
from apps.accounts.reset import scoped_records
from apps.accounts.reset_locks import account_lock
from apps.accounts.reset_models import AccountReset
from apps.agent_tools.models import ToolCredential, ToolCall, ToolProposal
from apps.chat.models import AnswerRequest, Citation, ToolRead, KnowledgeEntry
from apps.crm import models as crm
from apps.sales import models as sales
from apps.vectors.models import VectorDocument

URL = "/api/v1/accounts/me/reset/"


# 功能：验收保留登录身份的内部数据重置。
# 逻辑：每个用例使用真实提交和独立账号；重置操作仅作用于隔离测试数据库。
# 约束：不调用真实邮件、模型或外部服务，不操作用户已有附件。
@override_settings(LOCAL_DEBUG_AUTO_LOGIN=False)
class AccountResetTests(TransactionTestCase):
    # 功能：建立真实登录会话和两个账号。
    # 输入：测试框架隐式生命周期。
    # 输出：user、other、client、key、root 实例状态。
    # 逻辑：临时目录限制附件副作用；force_login 保留正常 SessionAuthentication 路径。
    # 约束：不使用 force_authenticate 绕过中间件或 CSRF 身份解析。
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="reset-owner", password="SyntheticPassword123", email="login@example.test")
        self.other = get_user_model().objects.create_user(username="reset-other", password="OtherPassword123")
        self.client = APIClient()
        self.client.force_login(self.user)
        self.key = str(uuid.uuid4())
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        config = override_settings(BASE_DIR=self.root)
        config.enable()
        self.addCleanup(config.disable)

    # 功能：创建跨模块依赖和实际附件。
    # 输入：`owner` 为数据所属测试账号。
    # 输出：客户对象；数据库中保存分析、聊天、交易、工具和资料记录。
    # 逻辑：包含 PROTECT 外键、聊天重试自引用、向量和二进制文档，验证真实删除依赖。
    # 约束：凭证均为合成摘要；不产生外部调用。
    def seed(self, owner):
        now = timezone.now()
        company = crm.Company.objects.create(owner=owner, group_key="customer.test")
        mailbox = crm.Mailbox.objects.create(owner=owner, address=f"u{owner.pk}@example.test", sync_state={"cached": True})
        crm.GmailCredential.objects.create(mailbox=mailbox, credentials={"synthetic": True})
        crm.AgentCredential.objects.create(owner=owner, digest=hashlib.sha256(str(owner.pk).encode()).hexdigest())
        contact = crm.Contact.objects.create(company=company, email="contact@example.test")
        email = crm.Email.objects.create(dedupe_key=str(mailbox.pk) + ":m1", mailbox=mailbox, company=company, contact=contact, payload={"body": "synthetic"}, sent_at=now, received_at=now, direction="inbound")
        extraction = crm.Extraction.objects.create(email=email, prompt_version="test", status="completed", facts={})
        snapshot = crm.AnalysisInput.objects.create(company=company, revision=0, input_version="test", payload={})
        analysis = crm.Analysis.objects.create(snapshot=snapshot, prompt_version="test", payload={}, provider="agent")
        crm.Score.objects.create(analysis=analysis, payload={}, score_version="test", value=10)
        crm.SnapshotSource.objects.create(snapshot=snapshot, email=email, extraction=extraction, review_revision=0)
        crm.SnapshotInvalidation.objects.create(snapshot=snapshot, reason="test")
        crm.ExtractionRepair.objects.create(email=email, source=extraction, review_revision=0)
        crm.Job.objects.create(company=company, trigger="test", revision=0)
        run = crm.MailboxSyncRun.objects.create(mailbox=mailbox)
        crm.EmailProcessingJob.objects.create(run=run, gmail_message_id="m1", dedupe_key=email.pk, company=company)
        crm.StoredMessage.objects.create(mailbox=mailbox, message_id="m1", raw={"body": "synthetic"})
        crm.SyncCheckpoint.objects.create(mailbox=mailbox, cursor="test")
        CompanyProfile.objects.create(owner=owner, company_name="Seller")
        SalesSetup.objects.create(owner=owner, personal={"name": "Synthetic"}, completed=True)
        SetupDocument.objects.create(owner=owner, name="test.txt", content_type="text/plain", content=b"synthetic")
        sales.SellerProfile.objects.create(owner=owner, profile={"test": True})
        sales.CompanySettings.objects.create(owner=owner, company=company, primary_contact=contact)
        product = sales.Product.objects.create(owner=owner, sku="sku", name="test", currency="USD", unit_price=1)
        quote = sales.Quote.objects.create(owner=owner, company=company, number="Q1", currency="USD")
        sales.QuoteLine.objects.create(owner=owner, quote=quote, product=product, quantity=1, unit_price=1)
        order = sales.SalesOrder.objects.create(owner=owner, company=company, number="O1", currency="USD", quote=quote)
        sales.OrderLine.objects.create(owner=owner, order=order, product=product, quantity=1, unit_price=1)
        follow = sales.FollowUp.objects.create(owner=owner, company=company, title="test", due_at=now)
        sales.Notification.objects.create(owner=owner, follow_up=follow, source_revision=0, title="test")
        conversation = sales.Conversation.objects.create(owner=owner)
        message = sales.Message.objects.create(owner=owner, conversation=conversation, content="test", client_key=uuid.uuid4())
        failed = AnswerRequest.objects.create(owner=owner, conversation=conversation, user_message=message, status="failed")
        request = AnswerRequest.objects.create(owner=owner, conversation=conversation, user_message=message, retry_of=failed)
        Citation.objects.create(request=request, position=0, source_id="test", source_type="test", title_or_label="test", content="test")
        ToolRead.objects.create(request=request, tool="test", arguments={}, result={}, evidence_items=[])
        KnowledgeEntry.objects.create(owner=owner, source_key="test", version="1", title="test", content="test")
        sales.Draft.objects.create(owner=owner, conversation=conversation, kind="chat", content="test")
        credential = ToolCredential.objects.create(owner=owner, name="test", digest=hashlib.sha256(f"tool{owner.pk}".encode()).hexdigest(), allowed_tools=[], expires_at=now + timezone.timedelta(hours=1))
        ToolProposal.objects.create(owner=owner, credential=credential, tool="test", arguments={}, expires_at=credential.expires_at)
        ToolCall.objects.create(owner=owner, key=uuid.uuid4(), tool="test", input_hash="test", result={})
        VectorDocument.objects.create(owner=owner, namespace="test", source="test", model="test", dimensions=2, content="test", content_hash="test", embedding=[1, 0])
        key = f"{owner.pk}/test.txt"
        path = self.root / "private_uploads" / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic", encoding="utf-8")
        sales.Attachment.objects.create(owner=owner, company=company, name="test.txt", storage_key=key, content_type="text/plain", size=9, sha256="test")
        return company

    # 功能：调用账号重置 API。
    # 输入：`key` 为可选幂等键，默认当前操作。
    # 输出：HTTP 响应。
    # 逻辑：使用真实登录 cookie，不传账号或公司 ID。
    # 约束：测试客户端未启用 CSRF 的用例只验证会话身份，CSRF 由专门用例覆盖。
    def reset(self, key=None):
        return self.client.post(URL, {}, format="json", HTTP_IDEMPOTENCY_KEY=key or self.key)

    # 功能：验证本人业务清空、他人数据和登录身份保留。
    # 输入：两套完整合成业务和两份当前账号会话。
    # 输出：所有本人查询为空，其他账号计数不变、密码哈希不变、当前登录仍有效。
    # 逻辑：检查文件、文档、向量、凭证、旧会话 OAuth 缓存和 HTTP 缓存响应头。
    # 约束：只清理内部授权记录，不声称撤销第三方平台账户。
    def test_scope_identity_files_sessions_and_tokens(self):
        self.seed(self.user)
        self.seed(self.other)
        counts = {model._meta.label: query.count() for model, query in scoped_records(self.other)}
        second = APIClient()
        second.force_login(self.user)
        session = second.session
        session["oauth_state"] = "synthetic-sensitive-state"
        session.save()
        self.user.refresh_from_db()
        identity = get_user_model().objects.filter(pk=self.user.pk).values().get()
        response = self.reset()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["Clear-Site-Data"], '"cache"')
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(identity, get_user_model().objects.filter(pk=self.user.pk).values().get())
        for model, query in scoped_records(self.user):
            self.assertFalse(query.exists(), model._meta.label)
        self.assertEqual(counts, {model._meta.label: query.count() for model, query in scoped_records(self.other)})
        self.assertFalse((self.root / "private_uploads" / str(self.user.pk) / "test.txt").exists())
        self.assertTrue((self.root / "private_uploads" / str(self.other.pk) / "test.txt").exists())
        self.assertNotIn("oauth_state", second.session)
        self.assertIn("_auth_user_id", second.session)
        self.assertEqual(self.client.get("/api/v1/accounts/me/").status_code, 200)
        stale_agent = APIClient()
        stale_agent.credentials(HTTP_AUTHORIZATION="Agent " + str(self.user.pk))
        self.assertIn(stale_agent.post("/api/v1/agent/chat/requests/claim/", {}).status_code, [401, 403])

    # 功能：验证网络重放不会再次清空重置后新创建的数据。
    # 输入：两次独立操作键和之后的新公司。
    # 输出：重放任意旧键后新公司仍存在，数据版本保持不变。
    # 逻辑：先完成两次清空，再重放第一键，覆盖不能只记录最近一次键的问题。
    # 约束：不自动重试 HTTP 请求。
    def test_idempotency_does_not_delete_new_data(self):
        self.assertEqual(self.reset().status_code, 200)
        self.assertEqual(self.reset(str(uuid.uuid4())).status_code, 200)
        company = crm.Company.objects.create(owner=self.user, group_key="new.test")
        response = self.reset()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["generation"], 2)
        self.assertTrue(crm.Company.objects.filter(pk=company.pk).exists())

    # 功能：验证数据库已清空但文件失败时不会误报成功。
    # 输入：合成附件及一次 OSError。
    # 输出：503 后 cleaning 为真，正常业务 409；显式重试后文件清理且版本不再递增。
    # 逻辑：故障仅注入文件边界，数据库使用真实提交。
    # 约束：不把失败自动转为成功或后台重试。
    def test_file_failure_can_resume(self):
        self.seed(self.user)
        with patch("apps.accounts.reset.clean_files", side_effect=OSError("synthetic")):
            self.assertEqual(self.reset().status_code, 503)
        state = AccountReset.objects.get(owner=self.user)
        self.assertTrue(state.cleaning)
        self.assertTrue(state.pending_files)
        self.assertEqual(self.client.get("/api/v1/mailboxes/").status_code, 409)
        identity = self.client.get("/api/v1/accounts/me/")
        self.assertEqual(identity.status_code, 200)
        self.assertEqual(identity["X-Account-Reset-Status"], "cleaning")
        self.assertEqual(self.reset().status_code, 200)
        state.refresh_from_db()
        self.assertFalse(state.cleaning)
        self.assertEqual(state.generation, 1)
        self.assertEqual(state.pending_files, [])

    # 功能：验证共享工作锁与清空独占锁的真实跨连接互斥。
    # 输入：本人和另一账号的持久业务记录。
    # 输出：本人工作期间 409 且数据不变；他人持锁不影响本人清空。
    # 逻辑：使用真实 PostgreSQL advisory lock，无锁行为模拟。
    # 约束：不阻塞等待任务，也不停止其他账号工作。
    def test_busy_account_is_unchanged(self):
        company = crm.Company.objects.create(owner=self.user, group_key="test")
        with account_lock(self.user.pk):
            self.assertEqual(self.reset().status_code, 409)
        self.assertTrue(crm.Company.objects.filter(pk=company.pk).exists())
        with account_lock(self.other.pk):
            self.assertEqual(self.reset().status_code, 200)

    # 功能：验证旧页面写请求被版本隔离。
    # 输入：数据版本为零的旧请求。
    # 输出：清空后 409，响应提供最新版本。
    # 逻辑：完整 HTTP 中间件先检查版本再进入业务视图。
    # 约束：不通过新增业务内容校验达到拒绝效果。
    def test_stale_write_rejected(self):
        self.assertEqual(self.reset().status_code, 200)
        response = self.client.patch("/api/v1/accounts/onboarding/", {"completed": True}, format="json", HTTP_X_ACCOUNT_DATA_VERSION="0")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "account_data_reset")
        self.assertEqual(response["X-Account-Data-Version"], "1")
        self.assertFalse(SalesSetup.objects.filter(owner=self.user).exists())

    # 功能：验证他人拥有的业务记录不被删除或级联修改。
    # 输入：他人的工单引用本人客户。
    # 输出：整体事务回滚，客户与工单均保留。
    # 逻辑：由真实数据库外键发现未纳入删除集合的共享引用。
    # 约束：不存在关闭外键或删除他人业务的降级路径。
    def test_cross_owner_reference_rolls_back(self):
        company = self.seed(self.user)
        ticket = sales.Ticket.objects.create(owner=self.other, company=company, title="shared")
        self.assertEqual(self.reset().status_code, 409)
        self.assertTrue(crm.Company.objects.filter(pk=company.pk).exists())
        self.assertTrue(sales.Ticket.objects.filter(pk=ticket.pk).exists())
        self.assertTrue(sales.Attachment.objects.filter(owner=self.user).exists())
        self.assertEqual(AccountReset.objects.get(owner=self.user).generation, 0)

    # 功能：验证仅当前浏览器登录账号能够发起重置。
    # 输入：匿名请求、缺 CSRF 的会话、非法操作键。
    # 输出：匿名和缺 CSRF 403，非法键 400。
    # 逻辑：真实 SessionAuthentication 与 Django CSRF 防护。
    # 约束：不请求额外密码或要求业务字段。
    def test_authentication_csrf_and_schema(self):
        self.assertEqual(APIClient().post(URL, {}).status_code, 403)
        csrf_client = APIClient(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(URL, {}, HTTP_IDEMPOTENCY_KEY=self.key).status_code, 403)
        self.assertEqual(self.reset("invalid").status_code, 400)

    # 功能：验证账号清空只解除参与他人团队的关联。
    # 输入：他人拥有的团队和本人参与关系。
    # 输出：成员关系删除，团队保留。
    # 逻辑：成员是关联记录，不沿关联删除团队。
    # 约束：不改变其他成员权限。
    def test_shared_membership_is_removed_without_deleting_other_team(self):
        team = sales.Team.objects.create(owner=self.other, name="shared")
        membership = sales.Membership.objects.create(owner=self.other, team=team, user=self.user, role="viewer")
        self.assertEqual(self.reset().status_code, 200)
        self.assertFalse(sales.Membership.objects.filter(pk=membership.pk).exists())
        self.assertTrue(sales.Team.objects.filter(pk=team.pk).exists())

    # 功能：验证清空后残留队列键不会执行外部动作或终止工作循环。
    # 输入：曾获批准而后被清空的合成动作。
    # 输出：cancelled，外部 provider 未调用。
    # 逻辑：真实删除记录后调用后台入口，覆盖先取队列再清空的时间窗口。
    # 约束：不执行真实网络请求。
    def test_stale_sales_queue_is_cancelled(self):
        from apps.sales.actions import run_action
        company = crm.Company.objects.create(owner=self.user, group_key="test")
        action = sales.ToolAction.objects.create(owner=self.user, company=company, tool="gmail.send", parameters={}, status="approved", idempotency_key=uuid.uuid4())
        self.assertEqual(self.reset().status_code, 200)
        with patch("apps.sales.actions.execute_provider") as provider:
            self.assertEqual(run_action(action.pk), "cancelled")
        provider.assert_not_called()

    # 功能：验证他人拥有的跟进不在清空后再次写入本人的通知。
    # 输入：他人跟进的负责人设为本人，已有到期通知。
    # 输出：跟进保留，负责人解除，本人通知持续为空。
    # 逻辑：清空后再次运行真实提醒扫描，核对账号隔离和后台读写。
    # 约束：仅解除关联，不删除他人跟进内容。
    def test_shared_reminders_do_not_repopulate(self):
        from apps.sales.services import notify_due
        company = crm.Company.objects.create(owner=self.other, group_key="test")
        follow = sales.FollowUp.objects.create(owner=self.other, company=company, assigned_to=self.user, title="shared", due_at=timezone.now())
        sales.Notification.objects.create(owner=self.user, follow_up=follow, source_revision=0, title="shared")
        self.assertEqual(self.reset().status_code, 200)
        follow.refresh_from_db()
        self.assertIsNone(follow.assigned_to)
        notify_due()
        self.assertFalse(sales.Notification.objects.filter(owner=self.user).exists())
