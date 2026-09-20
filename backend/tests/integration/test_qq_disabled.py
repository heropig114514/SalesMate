"""职责：验证 QQ 临时停用覆盖入口、队列和外部执行，同时保留历史数据及 Gmail。
实现：Gmail 测试批次显式选择最多 20 封（非运行默认值）；隔离 PostgreSQL、真实服务及 HTTP 视图；网络调用用 Mock 证明未发生。
关联：common.mail_features、CRM 调度、销售动作及运行时能力接口。
目录：
- QQDisabledTests：QQ 默认关闭集成验证。
- QQDisabledTests.setUp：准备个人 QQ/Gmail 邮箱与客户端。
- QQDisabledTests.test_connections_fail_before_network：连接在网络前拒绝。
- QQDisabledTests.test_queue_pauses_qq_and_keeps_gmail：新请求拒绝，既有队列暂停，Gmail 仍可领取。
- QQDisabledTests.test_history_and_credentials_remain：历史原文和凭证继续保留。
- QQDisabledTests.test_sales_actions_cannot_send_or_verify：批准、准备、执行及核对无法访问 QQ。
- QQDisabledTests.test_capabilities_and_restoration：能力发现与恢复配置一致。
变量索引：
- 无
"""
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from common.mail_features import QQMailDisabled
from apps.agent_tools.registry import build_registry
from apps.crm import ingestion, rules
from apps.crm.dispatch import next_owner
from apps.crm.models import Company, Email, GmailCredential, Mailbox, MailboxSyncRun, QQCredential
from apps.crm.processing import claim_run, request_run
from apps.crm.qq_connection import authorization_code
from apps.sales import actions
from apps.sales.models import ToolAction


# 功能：验证禁用配置的所有外部边界。
# 逻辑：固定 QQ 关闭，每例使用独立数据库事务。
# 约束：不连接真实邮箱，不删除业务数据，不验证第三方授权有效性。
@override_settings(QQ_MAIL_ENABLED=False, ANALYSIS_PROVIDER="agent")
class QQDisabledTests(TestCase):
    # 功能：建立个人邮箱与登录客户端。
    # 输入：无外部参数，使用隔离测试库。
    # 输出：user、qq、credential、client 实例状态。
    # 逻辑：QQ 密文故意不可解密，确保禁用判断发生在解密前。
    # 约束：不使用真实邮箱凭证。
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="qq-disabled")
        self.qq = Mailbox.objects.create(owner=self.user, address="fixture@qq.com")
        self.credential = QQCredential.objects.create(mailbox=self.qq, encrypted_code="not-a-secret")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    # 功能：验证连接入口不访问网络。
    # 输入：合成 QQ 地址、授权码和同步范围。
    # 输出：两个入口返回 503，授权记录数量不变。
    # 逻辑：Mock IMAP/SMTP 连接并断言没有调用。
    # 约束：只覆盖已认证请求；匿名权限沿用现有测试。
    def test_connections_fail_before_network(self):
        with patch("apps.crm.qq_connection.qq_mail.connect") as imap, patch("apps.sales.qq_smtp.smtplib.SMTP_SSL") as smtp:
            data = {"address": "fixture@qq.com", "authorization_code": "abcdefghijklmnop"}
            response = self.client.post("/api/v1/mailboxes/qq-connect/", {**data, "sync_options": {"max_messages": 1}}, format="json")
            self.assertEqual(response.status_code, 503)
            response = self.client.post("/api/v1/sales/connections/qq/", data, format="json")
            self.assertEqual(response.status_code, 503)
            imap.assert_not_called()
            smtp.assert_not_called()
        with self.assertRaises(QQMailDisabled):
            authorization_code(self.credential)
        self.assertEqual(QQCredential.objects.count(), 1)

    # 功能：验证 QQ 队列暂停但不阻塞 Gmail。
    # 输入：较早 QQ 排队记录及同账户较晚 Gmail 记录。
    # 输出：QQ 原队列不变；调度器仅在有 Gmail 时选择该员工。
    # 逻辑：先验证 QQ-only 无待办，再添加 Gmail 并实际领取。 Gmail 批次显式提供 20 封范围，QQ 开关不改变其可领取性。
    # 约束：不执行任何邮箱网络请求。
    def test_queue_pauses_qq_and_keeps_gmail(self):
        pending = MailboxSyncRun.objects.create(mailbox=self.qq)
        with self.assertRaises(QQMailDisabled):
            request_run(self.user, self.qq.pk, sync_options={"max_messages": 1})
        self.assertIsNone(claim_run(self.user))
        self.assertIsNone(next_owner("sync"))
        gmail = Mailbox.objects.create(owner=self.user, address="fixture@gmail.com")
        GmailCredential.objects.create(mailbox=gmail, credentials={})
        gmail_run = request_run(self.user, gmail.pk, sync_options={"max_messages": 20})
        self.assertEqual(next_owner("sync").pk, self.user.pk)
        self.assertEqual(claim_run(self.user).pk, gmail_run.pk)
        pending.refresh_from_db()
        self.assertEqual(pending.status, "queued")

    # 功能：验证历史仍可查询。
    # 输入：模拟已入库的 QQ 来源邮件。
    # 输出：已保存邮件接口返回 200 且包含邮件；邮箱和凭证保留。
    # 逻辑：通过正式入库服务准备历史，不执行同步。
    # 约束：原文属于合成测试，不代表真实 QQ 采集。
    def test_history_and_credentials_remain(self):
        payload = rules.extract_email(self.qq, "buyer@example.com", "采购询价", "需求：采购设备")
        payload["source"] = "qq_real"
        ingestion.submit_emails(self.user, [payload])
        response = self.client.get(f"/api/v1/mailboxes/{self.qq.pk}/email-reviews/?status=saved")
        self.assertEqual(response.status_code, 200)
        self.assertIn(payload["dedupe_key"], str(response.data))
        self.assertEqual(Email.objects.count(), 1)
        self.assertTrue(QQCredential.objects.filter(pk=self.credential.pk).exists())

    # 功能：验证存量发信任务不会突破开关。
    # 输入：合成 QQ 待确认、已批准和结果未知动作。
    # 输出：准备/批准/核对拒绝；已批准执行记为 failed 且没有外部调用。
    # 逻辑：构造冻结任务并检查 execute_provider 未触发，再验证用户仍可取消旧提案。
    # 约束：不改变 Gmail 动作审批及失败语义。
    def test_sales_actions_cannot_send_or_verify(self):
        company = Company.objects.create(owner=self.user, group_key="manual:qq-disabled")
        action = ToolAction.objects.create(owner=self.user, company=company, tool="qq.send", parameters={}, idempotency_key=uuid.uuid4())
        with self.assertRaises(QQMailDisabled):
            actions.validate_parameters(self.user, company, "qq.send", {})
        with self.assertRaises(QQMailDisabled):
            actions.decide_action(action, self.user, action.revision, "approved")
        actions.decide_action(action, self.user, action.revision, "cancelled")
        action.status = "approved"
        action.save(update_fields=["status"])
        with patch("apps.sales.actions.execute_provider") as provider:
            self.assertEqual(actions.run_action(action.pk), "failed")
            provider.assert_not_called()
        action.status = "uncertain"
        action.save(update_fields=["status"])
        with self.assertRaises(QQMailDisabled):
            actions.verify_action(action, self.user, action.revision)

    # 功能：验证客户端能力和恢复行为。
    # 输入：关闭配置及显式启用的局部设置。
    # 输出：运行时与工具目录一致；开启后原 QQ 队列可领取。
    # 逻辑：不删除任务，恢复仅改变可用性。
    # 约束：未证明真实 SMTP/IMAP 可达。
    def test_capabilities_and_restoration(self):
        self.assertFalse(self.client.get("/api/v1/demo/runtime/").data["qq_enabled"])
        self.assertNotIn("actions.prepare_qq", build_registry())
        pending = MailboxSyncRun.objects.create(mailbox=self.qq)
        with override_settings(QQ_MAIL_ENABLED=True):
            self.assertIn("actions.prepare_qq", build_registry())
            self.assertEqual(claim_run(self.user).pk, pending.pk)
