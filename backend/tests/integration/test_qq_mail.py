"""职责：验证 QQ 接入、持久同步、权限与 Gmail 共存。
实现：隔离 PostgreSQL 真实事务和 HTTP 序列化器，模拟 IMAP/LLM 边界。
关联：qq_views、qq_connection、qq_sync、worker；不读取真实邮箱或调用真实模型。
目录：
- QQMailTests：QQ 跨模块回归测试。
- QQMailTests.setUp：建立隔离员工、加密密钥和模拟 HTTP 与日期查询边界。
- QQMailTests.http_write：将 HTTP 传输送入真实 ingestion。
- QQMailTests.start：领取 QQ 批次。
- QQMailTests.raw：构造有原文证据的标准邮件。
- QQMailTests.test_connect_encrypts_and_never_returns_code：授权验证、加密和响应脱敏。
- QQMailTests.test_invalid_input_or_vault_does_not_connect：非法输入和缺失密钥无网络调用。
- QQMailTests.test_authentication_failure_is_safe_and_atomic：失败不落库且不泄露原始错误。
- QQMailTests.test_disconnect_is_owned_and_preserves_gmail：员工隔离及 Gmail 共存。
- QQMailTests.test_legacy_gmail_claim_skips_qq：旧 CLI 不误领 QQ 队列。
- QQMailTests.test_worker_persists_qq_and_deduplicates_incremental：临时员工身份、Worker、原文、L1 与增量去重。
- QQMailTests.test_failure_requires_explicit_retry：逐封失败隔离及明确重试。
- QQMailTests.test_checkpoint_rolls_back_on_generation_change：UIDVALIDITY 改变不推进游标。
- QQMailTests.test_session_csrf_is_required：连接写操作的 Session/CSRF 边界。
- QQMailTests.test_scope_is_required_and_validated_on_every_request：每次选择、输入与归属边界。
- QQMailTests.test_limits_apply_before_bodies_across_folders：全局封数、日期边界及扩大范围不漏信。
- QQMailTests.test_retry_keeps_original_window：失败重试冻结时间及旧批次拒绝无界扫描。
- QQMailTests.test_single_limits_and_empty_window：单项限制与无匹配邮件。
变量索引：
- TEST_KEY：仅用于测试隔离密文的固定 Fernet 密钥。
"""
import base64
import json
from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from agent.clients.backend_api import DjangoBackendClient
from agent.tools import qq_mail
from apps.crm import gmail_oauth, ingestion, qq_connection, qq_sync, rules
from apps.crm.access import Conflict
from apps.crm.models import Email, GmailCredential, Mailbox, MailboxSyncRun, QQCredential, QQSyncCheckpoint, StoredMessage
from apps.crm.processing import claim_run, finish_run, request_run, retry_run
from apps.crm.access import InvalidState
from apps.crm.worker import run_sync
from apps.sales.integrations import vault

TEST_KEY = base64.urlsafe_b64encode(b"q" * 32).decode("ascii")


# 功能：贯通 QQ 连接及后台处理的真实业务边界。
# 逻辑：测试数据库和账户隔离，外部 IMAP、模型和 HTTP 网络被模拟。
# 约束：通过不等于真实 QQ 授权或部署已验证。
@override_settings(ANALYSIS_PROVIDER="agent", SALESMATE_VAULT_KEY=TEST_KEY, LOCAL_DEBUG_AUTO_LOGIN=False)
class QQMailTests(TransactionTestCase):
    # 功能：准备独立用户、QQ 连接和 HTTP 客户端。
    # 输入：无外部参数；测试框架调用。
    # 输出：测试实例状态，无外部网络。
    # 逻辑：用测试密钥加密虚构授权码，真实 DjangoBackendClient 的网络方法接入 ingestion。
    # 约束：不使用工作区账号或密钥。
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="qq-owner", password="test-password")
        self.other = get_user_model().objects.create_user(username="qq-other", password="test-password")
        self.mailbox = Mailbox.objects.create(owner=self.owner, address="demo@qq.com")
        QQCredential.objects.create(mailbox=self.mailbox, encrypted_code=vault().encrypt(b"abcdefghijklmnop").decode("ascii"))
        self.browser = APIClient()
        self.browser.force_authenticate(self.owner)
        self.backend = DjangoBackendClient("http://test.invalid/api/v1/agent/", "test-token", mailbox_id=str(self.mailbox.pk))
        self.backend._request = Mock(side_effect=self.http_write)
        dates = patch.object(qq_mail, "message_dates", side_effect=lambda client, uids: {uid: timezone.now() - timedelta(days=1, seconds=uid) for uid in uids})
        dates.start()
        self.addCleanup(dates.stop)

    # 功能：在网络边界内执行真实持久化。
    # 输入：`method` 为 HTTP 方法；`path` 为接口路径；`kwargs` 含 JSON 载荷。
    # 输出：真实业务响应及空头字典。
    # 逻辑：严格只允许 emails/ 写入，校验 QQ 来源和身份。
    # 约束：不访问任何 HTTP 服务器。
    def http_write(self, method, path, **kwargs):
        self.assertEqual((method, path), ("POST", "emails/"))
        self.assertTrue(all(item["source"] == "qq_real" for item in kwargs["json"]))
        return ingestion.submit_emails(self.owner, kwargs["json"]), {}

    # 功能：创建并领取一次 QQ 同步。
    # 输入：无外部参数；当前测试邮箱。
    # 输出：带租约的持久批次。
    # 逻辑：复用实际 request_run 与 claim_run。
    # 约束：不跳过权限与活动批次约束。
    def start(self):
        request_run(self.owner, self.mailbox.pk, sync_options={"recent_days": 7, "max_messages": 20})
        return claim_run(self.owner)

    # 功能：构造供真实 L1 消费的虚构原文。
    # 输入：`value` 为 QQ 消息 ID。
    # 输出：标准原文及用作模型模拟结果的事实字段。
    # 逻辑：规则夹具产生可定位证据，模型边界返回这些固定事实。
    # 约束：仅测试使用规则，不增加运行时模型回退。
    def raw(self, value):
        document = rules.extract_email(self.mailbox, "buyer@customer.example", "设备询价", "需求：设备\n数量：2 台", value)
        document["eligible_body_text"] = document["body_text"]
        document["headers"] = {}
        return document

    # 功能：验证连接后密文持久且响应不含授权码。
    # 输入：无外部参数；模拟登录和文件夹验证成功，显式选择最近 7 天最多 20 封。
    # 输出：202、新批次、安全标志、可解密密文。
    # 逻辑：真实 API、序列化及排队服务，网络用 Mock 替代。
    # 约束：不声称虚构账号真实有效。
    def test_connect_encrypts_and_never_returns_code(self):
        with patch.object(qq_mail, "connect", return_value=Mock()), patch.object(qq_mail, "folders", return_value=["INBOX", "Sent Messages"]), patch.object(qq_mail, "select_folder", return_value=10), patch.object(qq_mail, "disconnect"):
            response = self.browser.post("/api/v1/mailboxes/qq-connect/", {"address": "DEMO@qq.com", "authorization_code": "abcdefghijklmnop", "sync_options": {"recent_days": 7, "max_messages": 20}}, format="json")
        self.assertEqual(response.status_code, 202, response.data)
        self.assertTrue(response.data["qq_authorized"])
        self.assertFalse(response.data["gmail_authorized"])
        self.assertNotIn("abcdefghijklmnop", json.dumps(response.data))
        credential = QQCredential.objects.get(mailbox=self.mailbox)
        self.assertNotEqual(credential.encrypted_code, "abcdefghijklmnop")
        self.assertEqual(qq_connection.authorization_code(credential), "abcdefghijklmnop")
        self.assertEqual(MailboxSyncRun.objects.get().status, "queued")
        self.assertNotIn("encrypted_code", json.dumps(self.browser.get("/api/v1/mailboxes/").data))

    # 功能：验证错误输入及缺失密钥不发起外部连接。
    # 输入：无外部参数；未知字段、非 QQ 地址、短密码与空 vault。
    # 输出：400 或 409，网络调用数为零。
    # 逻辑：严格 API 校验和加密配置先于网络。
    # 约束：不生成替代密钥或接受任意服务器。
    def test_invalid_input_or_vault_does_not_connect(self):
        with patch.object(qq_mail, "connect") as connect:
            for data in [{"address": "a@gmail.com", "authorization_code": "abcdefghijklmnop", "sync_options": {"recent_days": 7, "max_messages": 20}}, {"address": "a@qq.com", "authorization_code": "password"}, {"address": "a@qq.com", "authorization_code": "abcdefghijklmnop", "host": "127.0.0.1"}]:
                self.assertEqual(self.browser.post("/api/v1/mailboxes/qq-connect/", data, format="json").status_code, 400)
            with override_settings(SALESMATE_VAULT_KEY=""):
                self.assertEqual(self.browser.post("/api/v1/mailboxes/qq-connect/", {"address": "a@qq.com", "authorization_code": "abcdefghijklmnop", "sync_options": {"recent_days": 7, "max_messages": 20}}, format="json").status_code, 409)
            connect.assert_not_called()

    # 功能：验证认证失败安全且无半完成连接。
    # 输入：无外部参数；模拟传输层受控认证错误。
    # 输出：409 且新邮箱、密文和批次均未创建。
    # 逻辑：连接验证在数据库写入之前。
    # 约束：不把失败展示为已连接。
    def test_authentication_failure_is_safe_and_atomic(self):
        with patch.object(qq_mail, "connect", side_effect=qq_mail.QQMailError("QQ 认证失败")):
            response = self.browser.post("/api/v1/mailboxes/qq-connect/", {"address": "new@qq.com", "authorization_code": "abcdefghijklmnop", "sync_options": {"recent_days": 7, "max_messages": 20}}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertFalse(Mailbox.objects.filter(address="new@qq.com").exists())
        self.assertFalse(MailboxSyncRun.objects.exists())

    # 功能：验证删除隔离且保留 Gmail 和历史。
    # 输入：无外部参数；另一员工、活动批次和独立 Gmail 连接。
    # 输出：跨员工 404、活动时 409，结束后可移除且保留 Gmail。
    # 逻辑：依次覆盖所有权、活动状态和最终删除。
    # 约束：不删除邮箱主体及同步历史。
    def test_disconnect_is_owned_and_preserves_gmail(self):
        gmail = Mailbox.objects.create(owner=self.owner, address="demo@gmail.com")
        GmailCredential.objects.create(mailbox=gmail, credentials={"token": "test"})
        self.browser.force_authenticate(self.other)
        self.assertEqual(self.browser.delete(f"/api/v1/mailboxes/{self.mailbox.pk}/qq-authorization/").status_code, 404)
        self.browser.force_authenticate(self.owner)
        run = self.start()
        self.assertEqual(self.browser.delete(f"/api/v1/mailboxes/{self.mailbox.pk}/qq-authorization/").status_code, 409)
        finish_run(run.pk, run.lease_token, {"status": "completed"})
        response = self.browser.delete(f"/api/v1/mailboxes/{self.mailbox.pk}/qq-authorization/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["qq_authorized"])
        self.assertTrue(GmailCredential.objects.filter(mailbox=gmail).exists())
        self.assertTrue(MailboxSyncRun.objects.filter(pk=run.pk).exists())

    # 功能：防止旧 Gmail CLI 消费 QQ 任务。
    # 输入：无外部参数；QQ 排在 Gmail 前面的队列。
    # 输出：领取结果仅含 Gmail，QQ 仍排队。
    # 逻辑：通过原公开领取函数检查 provider 过滤。
    # 约束：Google 凭证响应中不出现 QQ 密文。
    def test_legacy_gmail_claim_skips_qq(self):
        qq_run = request_run(self.owner, self.mailbox.pk, sync_options={"recent_days": 7, "max_messages": 20})
        gmail = Mailbox.objects.create(owner=self.owner, address="demo@gmail.com")
        GmailCredential.objects.create(mailbox=gmail, credentials={"token": "test"})
        request_run(self.owner, gmail.pk)
        claims = gmail_oauth.claim_mailbox_syncs(self.owner, 10)
        self.assertEqual([item["mailbox_id"] for item in claims], [str(gmail.pk)])
        qq_run.refresh_from_db()
        self.assertEqual(qq_run.status, "queued")

    # 功能：验证 Worker 选择 QQ 路径并增量去重。
    # 输入：无外部参数；模拟两个文件夹 UID 及单封有证据邮件。
    # 输出：真实原文/邮件/抽取持久，二次同步无重复 LLM 调用。
    # 逻辑：仅替换临时身份的 HTTP 客户端和外部边界，Worker、载荷及事务执行真实代码。
    # 约束：确认没有 Gmail SDK 调用；不触发真实 L3 模型。
    def test_worker_persists_qq_and_deduplicates_incremental(self):
        value = qq_mail.message_id("INBOX", 10, 1)
        raw = self.raw(value)
        request_run(self.owner, self.mailbox.pk, sync_options={"recent_days": 7, "max_messages": 20})
        with patch("apps.crm.dispatch.django_backend_from_environment", return_value=self.backend), patch("apps.crm.worker.create_service_from_authorization") as gmail, patch.object(qq_mail, "connect", return_value=Mock()), patch.object(qq_mail, "disconnect"), patch.object(qq_mail, "folders", return_value=["INBOX", "Sent Messages"]), patch.object(qq_mail, "select_folder", return_value=10), patch.object(qq_mail, "list_uids", side_effect=lambda client, after, since=None: [1] if after == 0 else []), patch.object(qq_mail, "read_email", return_value=raw) as read, patch("apps.crm.durable_sync.bailian_extraction_provider", return_value=json.dumps(raw["facts"])) as model:
            # 本测试的 Sent Messages 保持空箱，INBOX 首次有一封。
            with patch.object(qq_mail, "list_uids", side_effect=[[1], []]):
                self.assertTrue(run_sync(self.owner))
            self.assertEqual(Email.objects.get().payload["source"], "qq_real")
            self.assertTrue(StoredMessage.objects.get().raw)
            self.assertEqual(MailboxSyncRun.objects.get().status, "completed")
            self.assertEqual(model.call_count, 1)
            request_run(self.owner, self.mailbox.pk, sync_options={"recent_days": 7, "max_messages": 20})
            with patch.object(qq_mail, "list_uids", return_value=[]):
                self.assertTrue(run_sync(self.owner))
            self.assertEqual(model.call_count, 1)
            self.assertEqual(read.call_count, 1)
            self.assertEqual(Email.objects.count(), 1)
            gmail.assert_not_called()

    # 功能：验证逐封失败保留且只在用户明确操作后重试。
    # 输入：无外部参数；一封读取成功，一封暂时读取失败。
    # 输出：partial、普通同步不重试失败、显式重试完成。
    # 逻辑：真实处理表记录状态，后续使用相同 ID 恢复。
    # 约束：成功邮件不重复调用模型。
    def test_failure_requires_explicit_retry(self):
        good, bad = [qq_mail.message_id("INBOX", 10, uid) for uid in [1, 2]]
        raw = self.raw(good)
        run = self.start()
        with patch.object(qq_mail, "folders", return_value=["INBOX", "Sent Messages"]), patch.object(qq_mail, "select_folder", return_value=10), patch("apps.crm.durable_sync.bailian_extraction_provider", return_value=json.dumps(raw["facts"])) as model:
            with patch.object(qq_mail, "list_uids", side_effect=[[1, 2], []]), patch.object(qq_mail, "read_email", side_effect=[raw, qq_mail.QQMailError("测试读取失败")]):
                result = qq_sync.sync_persisted(run, Mock(), self.backend)
            finish_run(run.pk, run.lease_token, result)
            run.refresh_from_db()
            self.assertEqual(run.status, "partial")
            next_run = self.start()
            with patch.object(qq_mail, "list_uids", return_value=[]), patch.object(qq_mail, "read_email") as read:
                finish_run(next_run.pk, next_run.lease_token, qq_sync.sync_persisted(next_run, Mock(), self.backend))
                read.assert_not_called()
            retry_run(self.owner, run.pk)
            retry = claim_run(self.owner)
            self.assertEqual(retry.message_ids, [bad])
            with patch.object(qq_mail, "read_email", return_value=self.raw(bad)):
                finish_run(retry.pk, retry.lease_token, qq_sync.sync_persisted(retry, Mock(), self.backend))
            self.assertEqual(Email.objects.count(), 2)
            self.assertEqual(model.call_count, 2)

    # 功能：验证代次变化不会破坏原检查点。
    # 输入：无外部参数；已保存代次后尝试提交不同代次。
    # 输出：Conflict、原游标不变、新消息未登记。
    # 逻辑：真实事务回滚验证。
    # 约束：不自动切换全量重扫。
    def test_checkpoint_rolls_back_on_generation_change(self):
        run = self.start()
        qq_sync.checkpoint(run, "INBOX", 10, [1])
        with self.assertRaises(Conflict):
            qq_sync.checkpoint(run, "INBOX", 11, [2])
        self.assertEqual(QQSyncCheckpoint.objects.get().folders["INBOX"], {"uidvalidity": 10, "last_uid": 1})
        self.assertEqual(StoredMessage.objects.count(), 1)

    # 功能：验证浏览器必须提供 Session 和 CSRF。
    # 输入：无外部参数；未登录与已登录但无 CSRF 的客户端。
    # 输出：两个请求均被拒绝，无 IMAP 调用。
    # 逻辑：使用真实 SessionAuthentication，不使用 force_authenticate。
    # 约束：不以 API 测试的认证捷径证明 CSRF 有效。
    def test_session_csrf_is_required(self):
        browser = APIClient(enforce_csrf_checks=True)
        data = {"address": "demo@qq.com", "authorization_code": "abcdefghijklmnop", "sync_options": {"recent_days": 7, "max_messages": 20}}
        with patch.object(qq_mail, "connect") as connect:
            self.assertEqual(browser.post("/api/v1/mailboxes/qq-connect/", data, format="json").status_code, 403)
            browser.force_login(self.owner)
            self.assertEqual(browser.post("/api/v1/mailboxes/qq-connect/", data, format="json").status_code, 403)
            connect.assert_not_called()

    # 功能：验证每次 QQ 请求都必须明确选择范围。
    # 输入：无外部参数；空范围、非法值、越权账号及正常双限制请求。
    # 输出：非法输入 400、越权 404、有效请求 202，活动批次拒绝覆盖。
    # 逻辑：执行真实 HTTP 校验与数据库排队，检查窗口保存与 Gmail 空请求兼容。
    # 约束：不访问真实 IMAP；省略范围不会复用上次选择。
    def test_scope_is_required_and_validated_on_every_request(self):
        url = f"/api/v1/mailboxes/{self.mailbox.pk}/request-sync/"
        for data in [{}, {"sync_options": {}}, {"sync_options": {"recent_days": None, "max_messages": None}},
                     {"sync_options": {"recent_days": 0}}, {"sync_options": {"max_messages": -1}},
                     {"sync_options": {"max_messages": 1.5}}, {"sync_options": {"recent_days": 10**12}},
                     {"sync_options": {"max_messages": 1, "until": "2026-01-01"}}]:
            with self.subTest(data=data):
                self.assertEqual(self.browser.post(url, data, format="json").status_code, 400)
        self.assertFalse(MailboxSyncRun.objects.exists())
        self.browser.force_authenticate(self.other)
        self.assertEqual(self.browser.post(url, {"sync_options": {"max_messages": 1}}, format="json").status_code, 404)
        self.browser.force_authenticate(self.owner)
        response = self.browser.post(url, {"sync_options": {"recent_days": 7, "max_messages": 2}}, format="json")
        self.assertEqual(response.status_code, 202, response.data)
        run = MailboxSyncRun.objects.get()
        self.assertEqual(response.data["sync_options"], run.sync_options)
        self.assertEqual(run.sync_options["max_messages"], 2)
        self.assertEqual(self.browser.post(url, {"sync_options": {"max_messages": 5}}, format="json").status_code, 409)
        run.status = "completed"
        run.save(update_fields=["status"])
        self.assertEqual(self.browser.post(url, {}, format="json").status_code, 400)
        gmail = Mailbox.objects.create(owner=self.owner, address="demo@gmail.com")
        GmailCredential.objects.create(mailbox=gmail, credentials={"token": "test"})
        self.assertEqual(self.browser.post(f"/api/v1/mailboxes/{gmail.pk}/request-sync/", {}, format="json").status_code, 202)

    # 功能：验证范围筛选先于原文读取及模型调用，并覆盖全部目标文件夹。
    # 输入：无外部参数；模拟跨文件夹新旧邮件、边界时间、未来日期和范围外 pending。
    # 输出：双限制最多两封，后续天数扩展可发现低 UID 老邮件，范围外原文不读取。
    # 逻辑：真实同步及 L1/入库流程，仅模拟 IMAP 和模型；完成记录不占后续封数。
    # 约束：不能把每页封数当作批次封数，也不能用最大 UID 跳过未选择消息。
    def test_limits_apply_before_bodies_across_folders(self):
        now = timezone.now()
        inbox_dates = {1: now - timedelta(days=8), 2: now - timedelta(days=2), 3: now - timedelta(days=7), 4: now + timedelta(seconds=1)}
        sent_dates = {1: now - timedelta(days=1)}
        old = qq_mail.message_id("INBOX", 10, 1)
        StoredMessage.objects.create(mailbox=self.mailbox, message_id=old)
        facts = json.dumps(self.raw(old)["facts"])
        with patch("apps.crm.qq_scope.timezone.now", return_value=now):
            request_run(self.owner, self.mailbox.pk, sync_options={"recent_days": 7, "max_messages": 2})
        run = claim_run(self.owner)
        with patch.object(qq_mail, "folders", return_value=["INBOX", "Sent Messages"]), patch.object(qq_mail, "select_folder", return_value=10), patch.object(qq_mail, "read_email", side_effect=lambda client, value: self.raw(value)) as read, patch("apps.crm.durable_sync.bailian_extraction_provider", return_value=facts) as model:
            with patch.object(qq_mail, "list_uids", side_effect=[[1, 2, 3, 4], [1]]), patch.object(qq_mail, "message_dates", side_effect=[inbox_dates, sent_dates]):
                finish_run(run.pk, run.lease_token, qq_sync.sync_persisted(run, Mock(), self.backend))
            self.assertEqual([call.args[1] for call in read.call_args_list], [qq_mail.message_id("Sent Messages", 10, 1), qq_mail.message_id("INBOX", 10, 2)])
            self.assertEqual(model.call_count, 2)
            self.assertFalse(StoredMessage.objects.get(message_id=old).raw)
            request_run(self.owner, self.mailbox.pk, sync_options={"recent_days": 30, "max_messages": 2})
            next_run = claim_run(self.owner)
            with patch.object(qq_mail, "list_uids", side_effect=[[1, 2, 3], [1]]), patch.object(qq_mail, "message_dates", side_effect=[{key: value for key, value in inbox_dates.items() if key != 4}, sent_dates]):
                finish_run(next_run.pk, next_run.lease_token, qq_sync.sync_persisted(next_run, Mock(), self.backend))
            self.assertEqual([call.args[1] for call in read.call_args_list[2:]], [qq_mail.message_id("INBOX", 10, 3), old])
            self.assertEqual(Email.objects.count(), 4)
            self.assertEqual(model.call_count, 4)

    # 功能：验证重试不扩大最初选定的时间窗口。
    # 输入：无外部参数；无逐封任务的失败批次与旧版空范围批次。
    # 输出：新重试保留原快照，旧版无范围扫描被拒绝。
    # 逻辑：通过真实 retry_run 和持久化对比范围，避免重试时间推进。
    # 约束：显式失败 ID 的既有重试测试继续覆盖逐封恢复。
    def test_retry_keeps_original_window(self):
        run = self.start()
        finish_run(run.pk, run.lease_token, {"status": "failed"})
        retried = retry_run(self.owner, run.pk)
        self.assertEqual(retried.sync_options, run.sync_options)
        retried.status, retried.sync_options = "failed", {}
        retried.save(update_fields=["status", "sync_options"])
        with self.assertRaises(InvalidState):
            retry_run(self.owner, retried.pk)

    # 功能：验证只填天数或只填封数的语义及空结果。
    # 输入：无外部参数；固定窗口边界及两封历史邮件的模拟元数据。
    # 输出：仅天数保留边界，仅封数保留最新，无匹配批次零正文读取。
    # 逻辑：真实范围选择先比较精确日期，再按全局数量筛选。
    # 约束：IMAP 元数据模拟不证明实际外部服务已验证。
    def test_single_limits_and_empty_window(self):
        now = timezone.now()
        for options, dates, expected in [({"recent_days": 7}, {1: now - timedelta(days=7), 2: now - timedelta(days=7, seconds=1)}, [1]),
                                         ({"max_messages": 1}, {1: now - timedelta(days=70), 2: now - timedelta(days=60)}, [2]),
                                         ({"recent_days": 1}, {1: now - timedelta(days=2)}, [])]:
            with patch("apps.crm.qq_scope.timezone.now", return_value=now):
                request_run(self.owner, self.mailbox.pk, sync_options=options)
            run = claim_run(self.owner)
            with patch.object(qq_mail, "folders", return_value=["INBOX", "Sent Messages"]), patch.object(qq_mail, "select_folder", return_value=10), patch.object(qq_mail, "list_uids", side_effect=[list(dates), []]), patch.object(qq_mail, "message_dates", return_value=dates), patch.object(qq_mail, "read_email") as read:
                selected = qq_sync.select_messages(run, Mock())
                self.assertEqual([item[3] for item in selected], expected)
                read.assert_not_called()
            finish_run(run.pk, run.lease_token, {"status": "completed"})
