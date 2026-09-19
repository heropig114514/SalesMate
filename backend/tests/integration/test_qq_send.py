"""职责：验证 QQ 发信连接、SMTP 载荷和动作状态边界。
实现：显式启用 QQ 能力，隔离数据库，模拟 SMTP/IMAP 网络；真实执行加密、审批和状态写入。
关联：sales.qq_connection、qq_smtp、actions；不使用开发邮箱凭证。
目录：
- QQSendTests：QQ 发信集成测试。
- QQSendTests.setUp：准备合成连接和邮件草稿。
- QQSendTests.prepare：建立待确认动作。
- QQSendTests.test_connection_only_authenticates：连接加密且不发信。
- QQSendTests.test_connection_rejects_unapproved_fields_and_credentials：输入及认证失败边界。
- QQSendTests.test_connection_permissions_and_pending_actions：身份隔离与换凭证限制。
- QQSendTests.test_session_write_requires_csrf：真实会话禁止无 CSRF 的连接写入。
- QQSendTests.test_confirmation_frozen_mime_and_single_submission：审批、冻结正文及单次提交。
- QQSendTests.test_refused_recipient_sends_no_body：任一拒收不提交正文。
- QQSendTests.test_transport_outcomes_are_not_retried：区分失败与未知并禁止重试。
- QQSendTests.test_quit_failure_preserves_acceptance：清理失败不抹掉接受结果。
- QQSendTests.test_changed_connection_blocks_submission：执行前核对冻结连接版本。
- QQSendTests.test_recipient_validation_and_owner_scope：收件地址和账号权限校验。
- QQSendTests.test_verification_reads_matching_sent_copy_only：核对只读且须匹配唯一副本。
变量索引：
- 无
"""
import smtplib
import uuid
from email import message_from_bytes, policy
from email.message import EmailMessage
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.crm.access import InvalidState
from apps.sales import actions, grouping, integrations, models, qq_smtp


# 功能：覆盖独立 QQ 发信的状态与载荷契约。
# 逻辑：显式启用 QQ，仅替换网络传输，其余业务和数据库走真实实现。
# 约束：不读取真实授权码，不发送邮件；模拟成功不证明外部服务可用。
@override_settings(QQ_MAIL_ENABLED=True)
class QQSendTests(TestCase):
    # 功能：建立合成测试数据和固定 SMTP 成功响应。
    # 输入：无外部参数；使用一次性测试密钥。
    # 输出：user、company、connection、draft、client、smtp 实例状态。
    # 逻辑：每例独立建库，所有 SMTP 构造都被 Mock 替换。
    # 约束：补丁和设置在测试结束清理。
    def setUp(self):
        setting = override_settings(SALESMATE_VAULT_KEY=Fernet.generate_key().decode())
        setting.enable()
        self.addCleanup(setting.disable)
        self.user = get_user_model().objects.create_user(username="qq-send-test")
        self.company = grouping.create_company(self.user, "QQ 发信测试客户")
        self.connection = models.Connection.objects.create(owner=self.user, provider="qq", account="sender@qq.com", encrypted_credentials=integrations.encrypt_credentials({"authorization_code": "abcdefghijklmnop"}))
        conversation = models.Conversation.objects.create(owner=self.user, company=self.company)
        self.draft = models.Draft.objects.create(owner=self.user, conversation=conversation, kind="email", subject="报价确认", content="第一行\n第二行", recipients=["buyer@example.com", "buyer2@example.com"])
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        patcher = patch("apps.sales.qq_smtp.smtplib.SMTP_SSL")
        self.factory = patcher.start()
        self.addCleanup(patcher.stop)
        self.smtp = self.factory.return_value
        self.smtp.ehlo.return_value = (250, b"hello")
        self.smtp.mail.return_value = (250, b"ok")
        self.smtp.rcpt.return_value = (250, b"ok")
        self.smtp.data.return_value = (250, b"accepted")

    # 功能：建立当前草稿对应的待确认动作。
    # 输入：读取当前测试实例的员工、客户、连接、草稿。
    # 输出：ToolAction。
    # 逻辑：走正式动作创建服务，冻结数据但不批准。
    # 约束：不会执行 SMTP。
    def prepare(self):
        return actions.create_action(self.user, {"company": str(self.company.pk), "tool": "qq.send", "parameters": {"connection_id": str(self.connection.pk), "draft_id": str(self.draft.pk)}, "idempotency_key": str(uuid.uuid4())})

    # 功能：验证连接只执行 TLS 认证并保存密文。
    # 输入：合成账号授权码 POST。
    # 输出：201，无凭证回显和发信动作。
    # 逻辑：核对固定主机、证书校验及未调用 SMTP 信封和正文。
    # 约束：SMTP 被模拟，不证明真实账号可认证。
    def test_connection_only_authenticates(self):
        response = self.client.post("/api/v1/sales/connections/qq/", {"address": "new@qq.com", "authorization_code": "abcdefghijklmnop"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotIn("abcdefghijklmnop", str(response.data))
        self.assertNotIn("encrypted_credentials", response.data)
        connection = models.Connection.objects.get(pk=response.data["id"])
        self.assertNotIn("abcdefghijklmnop", connection.encrypted_credentials)
        self.assertEqual(integrations.credentials_for(connection), {"authorization_code": "abcdefghijklmnop"})
        self.assertEqual(self.factory.call_args.args, ("smtp.qq.com", 465))
        self.assertTrue(self.factory.call_args.kwargs["context"].check_hostname)
        self.smtp.login.assert_called_once_with("new@qq.com", "abcdefghijklmnop")
        self.smtp.mail.assert_not_called()
        self.smtp.data.assert_not_called()
        self.assertFalse(models.ToolAction.objects.exists())

    # 功能：验证输入白名单和认证拒绝的持久化边界。
    # 输入：额外服务器字段、非法地址、错误授权码及模拟认证错误。
    # 输出：格式错误 400、认证失败 409，无新增连接或服务端秘密回显。
    # 逻辑：分别在序列化和 SMTP 认证处失败。
    # 约束：没有真实登录尝试。
    def test_connection_rejects_unapproved_fields_and_credentials(self):
        data = {"address": "new@qq.com", "authorization_code": "abcdefghijklmnop"}
        for change in ({"host": "example.com"}, {"address": "x@example.com"}, {"authorization_code": "wrong"}):
            with self.subTest(change=change):
                response = self.client.post("/api/v1/sales/connections/qq/", {**data, **change}, format="json")
                self.assertEqual(response.status_code, 400)
        self.factory.assert_not_called()
        self.smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"private-server-detail")
        response = self.client.post("/api/v1/sales/connections/qq/", data, format="json")
        self.assertEqual(response.status_code, 409, response.data)
        self.assertNotIn("private-server-detail", str(response.data))
        self.assertEqual(models.Connection.objects.count(), 1)

    # 功能：验证登录权限及未完成动作阻止更换授权。
    # 输入：未登录、关联待确认动作及不同员工的连接请求。
    # 输出：匿名拒绝、同员工冲突、另一员工独立保存。
    # 逻辑：请求真实 API，核对 owner 及原连接版本。
    # 约束：仅模拟认证，不发送。
    def test_connection_permissions_and_pending_actions(self):
        data = {"address": "sender@qq.com", "authorization_code": "abcdefghijklmnop"}
        self.client.force_authenticate(None)
        self.assertIn(self.client.post("/api/v1/sales/connections/qq/", data, format="json").status_code, (401, 403))
        self.factory.assert_not_called()
        self.client.force_authenticate(self.user)
        self.prepare()
        self.assertEqual(self.client.post("/api/v1/sales/connections/qq/", data, format="json").status_code, 409)
        other = get_user_model().objects.create_user(username="another-sender")
        self.client.force_authenticate(other)
        response = self.client.post("/api/v1/sales/connections/qq/", data, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(models.Connection.objects.get(pk=response.data["id"]).owner, other)
        self.smtp.data.assert_not_called()

    # 功能：验证 QQ 发信连接继承 Session CSRF 防护。
    # 输入：已登录真实会话，不提交 CSRF 令牌。
    # 输出：403，SMTP 未调用。
    # 逻辑：启用 APIClient 的 CSRF 强制校验，不使用 force_authenticate。
    # 约束：测试不会向外部提交账号。
    def test_session_write_requires_csrf(self):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.post("/api/v1/sales/connections/qq/", {"address": "sender@qq.com", "authorization_code": "abcdefghijklmnop"}, format="json")
        self.assertEqual(response.status_code, 403)
        self.factory.assert_not_called()

    # 功能：验证 QQ 草稿冻结、确认门槛和成功后的单次提交。
    # 输入：准备后的草稿被编辑，再显式批准原动作。
    # 输出：旧中文内容、完整收件人与稳定 Message-ID 被提交一次。
    # 逻辑：解析真实 MIME 字节并检查 SMTP 信封和 CRLF。
    # 约束：传输被替换，不代表邮件已投递。
    def test_confirmation_frozen_mime_and_single_submission(self):
        action = self.prepare()
        self.assertEqual(actions.run_action(action.pk), "pending_confirmation")
        self.factory.assert_not_called()
        self.draft.content = "后来修改的正文"
        self.draft.save()
        action = actions.decide_action(action, self.user, action.revision, "approved")
        self.assertEqual(actions.run_action(action.pk), "succeeded")
        self.assertEqual(actions.run_action(action.pk), "succeeded")
        self.smtp.data.assert_called_once()
        raw = self.smtp.data.call_args.args[0]
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        message = message_from_bytes(raw, policy=policy.default)
        self.assertEqual(message["Subject"], "报价确认")
        self.assertEqual(message["From"], "sender@qq.com")
        self.assertEqual(message["Message-ID"], f"<{action.pk}@salesmate.local>")
        self.assertEqual(message.get_content().replace("\r\n", "\n").strip(), "第一行\n第二行")
        self.assertEqual([call.args[0] for call in self.smtp.rcpt.call_args_list], self.draft.recipients)
        action.refresh_from_db()
        self.assertEqual(action.result["submission_status"], "smtp_accepted")

    # 功能：拒绝部分收件人时避免向已获准地址发送正文。
    # 输入：第二个 RCPT 返回 550。
    # 输出：动作 failed，DATA 从未调用。
    # 逻辑：所有收件人确认后才允许提交，失败会关闭会话。
    # 约束：不自动移除拒收地址或重发。
    def test_refused_recipient_sends_no_body(self):
        action = self.prepare()
        actions.decide_action(action, self.user, action.revision, "approved")
        self.smtp.rcpt.side_effect = [(250, b"ok"), (550, b"no")]
        self.assertEqual(actions.run_action(action.pk), "failed")
        self.smtp.data.assert_not_called()
        self.smtp.quit.assert_called_once()

    # 功能：区分明确拒绝、提交前故障和正文提交中断。
    # 输入：认证失败、DATA 550、DATA 超时与 DATA 连接中断。
    # 输出：前两者 failed，后两者 uncertain，重复执行均无额外提交。
    # 逻辑：逐例创建批准动作，仅注入对应网络异常。
    # 约束：未知结果不能被自动认定未发送。
    def test_transport_outcomes_are_not_retried(self):
        for phase, error, expected in [("login", smtplib.SMTPAuthenticationError(535, b"no"), "failed"), ("data", smtplib.SMTPDataError(550, b"no"), "failed"), ("data", TimeoutError(), "uncertain"), ("data", smtplib.SMTPServerDisconnected(), "uncertain")]:
            with self.subTest(phase=phase, error=type(error).__name__):
                self.smtp.reset_mock()
                self.smtp.login.side_effect = self.smtp.data.side_effect = None
                getattr(self.smtp, phase).side_effect = error
                action = self.prepare()
                actions.decide_action(action, self.user, action.revision, "approved")
                self.assertEqual(actions.run_action(action.pk), expected)
                count = self.smtp.data.call_count
                self.assertEqual(actions.run_action(action.pk), expected)
                self.assertEqual(self.smtp.data.call_count, count)

    # 功能：验证 DATA 已确认后 QUIT 中断仍保留成功。
    # 输入：成功响应后模拟 QUIT 断开。
    # 输出：动作 succeeded，底层 close 仍执行。
    # 逻辑：清理与提交结果独立。
    # 约束：不会为清理故障重新发送。
    def test_quit_failure_preserves_acceptance(self):
        action = self.prepare()
        actions.decide_action(action, self.user, action.revision, "approved")
        self.smtp.quit.side_effect = smtplib.SMTPServerDisconnected()
        self.assertEqual(actions.run_action(action.pk), "succeeded")
        self.smtp.close.assert_called_once()

    # 功能：验证冻结连接被修改后不能执行。
    # 输入：已批准动作的连接版本递增。
    # 输出：failed 且不连接外部。
    # 逻辑：执行前身份版本校验拒绝旧快照。
    # 约束：不隐式改为使用新连接。
    def test_changed_connection_blocks_submission(self):
        action = self.prepare()
        actions.decide_action(action, self.user, action.revision, "approved")
        self.connection.revision += 1
        self.connection.save()
        self.assertEqual(actions.run_action(action.pk), "failed")
        self.factory.assert_not_called()

    # 功能：验证收件人格式、重复地址及提供方与员工归属。
    # 输入：损坏草稿地址，以及他人连接或 Gmail 连接。
    # 输出：400 或 404，不生成动作或连接 SMTP。
    # 逻辑：通过 API 真实参数验证和异常转换。
    # 约束：不测试真实地址投递能力。
    def test_recipient_validation_and_owner_scope(self):
        data = {"company": str(self.company.pk), "tool": "qq.send", "parameters": {"connection_id": str(self.connection.pk), "draft_id": str(self.draft.pk)}, "idempotency_key": str(uuid.uuid4())}
        for recipients in (["invalid"], ["x@example.com\r\nBCC: a@example.com"], ["x@example.com"] * 2, ["中文@example.com"]):
            self.draft.recipients = recipients
            self.draft.save()
            self.assertEqual(self.client.post("/api/v1/sales/records/actions/", data, format="json").status_code, 400)
        self.connection.provider = "gmail"
        self.connection.save()
        self.assertEqual(self.client.post("/api/v1/sales/records/actions/", data, format="json").status_code, 404)
        self.connection.provider = "qq"
        self.connection.owner = get_user_model().objects.create_user(username="other-owner")
        self.connection.save()
        self.assertEqual(self.client.post("/api/v1/sales/records/actions/", data, format="json").status_code, 404)
        self.factory.assert_not_called()

    # 功能：验证未知动作只读核对唯一且完整匹配的发送头部。
    # 输入：空搜索、错主题及正确副本，包括服务端补充的发件显示名。
    # 输出：前两者保留 uncertain，匹配后 succeeded；从不连接 SMTP。
    # 逻辑：模拟 IMAP UID 查询和 BODY.PEEK 返回，真实执行核对与状态更新。
    # 约束：不 APPEND、重发或把未找到当作发送失败。
    def test_verification_reads_matching_sent_copy_only(self):
        action = self.prepare()
        action.status = "uncertain"
        action.save()
        client = Mock()
        with patch("apps.sales.qq_smtp.qq_mail.connect", return_value=client), patch("apps.sales.qq_smtp.qq_mail.folders", return_value=("INBOX", "Sent Messages")), patch("apps.sales.qq_smtp.qq_mail.select_folder", return_value=123), patch("apps.sales.qq_smtp.qq_mail.disconnect"):
            client.uid.return_value = ("OK", [b""])
            with self.assertRaises(InvalidState):
                actions.verify_action(action, self.user, action.revision)
            for subject in ("wrong", action.parameters["subject"]):
                header = EmailMessage(policy=policy.SMTP)
                header["Message-ID"] = f"<{action.pk}@salesmate.local>"
                header["From"] = "Sender <sender@qq.com>"
                header["To"] = ", ".join(action.parameters["to"])
                header["Subject"] = subject
                client.uid.side_effect = [("OK", [b"42"]), ("OK", [(b"7 (UID 42 BODY[HEADER.FIELDS] {100}", header.as_bytes())])]
                if subject == "wrong":
                    with self.assertRaises(InvalidState):
                        actions.verify_action(action, self.user, action.revision)
                    action.refresh_from_db()
                    self.assertEqual(action.status, "uncertain")
                else:
                    result = actions.verify_action(action, self.user, action.revision)
                    self.assertEqual(result.status, "succeeded")
                    self.assertEqual(result.result["submission_status"], "confirmed_in_sent")
            self.assertIn("BODY.PEEK", client.uid.call_args.args[2])
            client.append.assert_not_called()
        self.factory.assert_not_called()
