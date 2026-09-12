"""职责：验证外部工具的真实适配器载荷与授权失败边界。
实现：使用隔离数据库和模拟 Google SDK，检查 MIME、时间、通知、令牌加密及核对。
关联：apps.sales.actions/calendar/integrations；模拟成功不代表外部账号已完成授权。
目录：
- ExternalTests：外部工具及连接测试。
- ExternalTests.setUp：建立合成身份和连接。
- ExternalTests.test_gmail_adapter_exact_content：校验 MIME 与单次执行。
- ExternalTests.test_calendar_adapter_explicit_notifications：校验事件 ID、时间与通知。
- ExternalTests.test_calendar_validation_requires_timezone_and_notification：拒绝隐式会议参数。
- ExternalTests.test_credentials_encrypted_and_scopes_verified：校验加密和真实保存 scope。
- ExternalTests.test_oauth_pkce_reuses_verifier：校验 OAuth 往返的 PKCE。
- ExternalTests.test_calendar_reads_are_owner_scoped：验证只读日历授权范围。
- ExternalTests.test_verification_cannot_create_or_send：核对仅查询已有事件。
- ExternalTests.test_bad_preflight_never_calls_provider：校验执行前失败无外部业务调用。
- ExternalTests.test_authenticated_write_requires_csrf：验证实际 Session 的 CSRF。
变量索引：
- 无
"""

import base64
from email import message_from_bytes
from unittest.mock import Mock, patch
import uuid

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.crm.access import InvalidState
from apps.sales import actions, calendar, grouping, integrations, models


# 功能：验证工具适配器的授权与载荷不变量。
# 逻辑：用 Mock 替代 SDK 网络，数据库仍真实隔离。
# 约束：测试不得向任何真实收件人或日历写入。
@override_settings(SALESMATE_AUTO_RUN_AGENT=False)
class ExternalTests(TestCase):
    # 功能：建立测试用连接和会话。
    # 输入：无外部参数。
    # 输出：user、company、connection 和 client 实例状态。
    # 逻辑：连接使用显式无效密文，测试须模拟凭证边界才能执行。
    # 约束：不会读取开发环境现有凭证。
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="external-tester")
        self.company = grouping.create_company(self.user, "外部工具测试客户")
        self.connection = models.Connection.objects.create(
            owner=self.user,
            provider="calendar",
            account="calendar@example.com",
            encrypted_credentials="not-a-real-token",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    # 功能：验证 Gmail 适配器发送确切的冻结正文。
    # 输入：合成 ToolAction 和 Mock Gmail SDK。
    # 输出：解码 MIME 的收件人、主题、正文和确定 Message-ID 均匹配。
    # 逻辑：检查真实 execute_provider 构造载荷及 num_retries=0。
    # 约束：不执行网络请求。
    def test_gmail_adapter_exact_content(self):
        action = models.ToolAction(
            id=uuid.uuid4(),
            tool="gmail.send",
            parameters={
                "account": "seller@example.com",
                "to": ["buyer@example.com"],
                "subject": "Test quote",
                "body": "Line one\nLine two",
            },
        )
        api = Mock()
        api.users.return_value.messages.return_value.send.return_value.execute.return_value = {
            "id": "sent-id"
        }
        with patch("apps.sales.actions.build", return_value=api):
            result = actions.execute_provider(action, Mock())
        sender = api.users.return_value.messages.return_value.send
        message = message_from_bytes(
            base64.urlsafe_b64decode(sender.call_args.kwargs["body"]["raw"])
        )
        self.assertEqual(message["To"], "buyer@example.com")
        self.assertEqual(message["Subject"], "Test quote")
        self.assertEqual(message["Message-ID"], f"<{action.pk}@salesmate.local>")
        self.assertEqual(
            message.get_payload(decode=True).decode().strip(), "Line one\nLine two"
        )
        sender.return_value.execute.assert_called_once_with(num_retries=0)
        self.assertEqual(result["message_id"], "sent-id")

    # 功能：验证日历创建不猜测通知方式。
    # 输入：明确时区、参会人和 send_updates 的动作。
    # 输出：稳定事件 ID、原时间字符串和通知选项进入 SDK。
    # 逻辑：直接检查 insert 参数与单次 execute。
    # 约束：不创建真实会议。
    def test_calendar_adapter_explicit_notifications(self):
        action = models.ToolAction(
            id=uuid.uuid4(),
            tool="calendar.create",
            parameters={
                "calendar_id": "primary",
                "title": "Meeting",
                "description": "Test",
                "start": "2026-09-14T10:00:00+08:00",
                "end": "2026-09-14T11:00:00+08:00",
                "attendees": ["buyer@example.com"],
                "send_updates": "all",
            },
        )
        api = Mock()
        api.events.return_value.insert.return_value.execute.return_value = {
            "id": action.pk.hex
        }
        with patch("apps.sales.actions.build", return_value=api):
            actions.execute_provider(action, Mock())
        arguments = api.events.return_value.insert.call_args.kwargs
        self.assertEqual(arguments["sendUpdates"], "all")
        self.assertEqual(arguments["body"]["id"], action.pk.hex)
        self.assertEqual(
            arguments["body"]["start"]["dateTime"], action.parameters["start"]
        )
        api.events.return_value.insert.return_value.execute.assert_called_once_with(
            num_retries=0
        )

    # 功能：验证缺时区和缺通知选项都明确失败。
    # 输入：部分不完整的会议准备请求。
    # 输出：400 且未创建动作。
    # 逻辑：逐个修复参数，最终完整计划可以保存但仍待确认。
    # 约束：不调用 provider。
    def test_calendar_validation_requires_timezone_and_notification(self):
        parameters = {
            "connection_id": str(self.connection.pk),
            "calendar_id": "primary",
            "title": "Meeting",
            "description": "",
            "start": "2026-09-14T10:00:00",
            "end": "2026-09-14T11:00:00",
            "attendees": [],
            "send_updates": "none",
        }
        data = {
            "company": str(self.company.pk),
            "tool": "calendar.create",
            "parameters": parameters,
            "idempotency_key": str(uuid.uuid4()),
        }
        self.assertEqual(
            self.client.post(
                "/api/v1/sales/records/actions/", data, format="json"
            ).status_code,
            400,
        )
        parameters.update(
            start=parameters["start"] + "+08:00", end=parameters["end"] + "+08:00"
        )
        parameters.pop("send_updates")
        self.assertEqual(
            self.client.post(
                "/api/v1/sales/records/actions/", data, format="json"
            ).status_code,
            400,
        )
        parameters["send_updates"] = "none"
        result = self.client.post("/api/v1/sales/records/actions/", data, format="json")
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data["status"], "pending_confirmation")

    # 功能：验证数据库仅保存密文且不能凭目标 scope 伪造授权。
    # 输入：合成 token JSON 与一次性测试 Fernet 密钥。
    # 输出：解密后可构造未过期凭证；实际 scope 不足时明确拒绝。
    # 逻辑：检查原 JSON scopes，不把构造器入参当授权证据。
    # 约束：不会刷新或使用这些合成令牌访问外部。
    def test_credentials_encrypted_and_scopes_verified(self):
        document = {
            "token": "fake-access",
            "refresh_token": "fake-refresh",
            "client_id": "fake-client",
            "client_secret": "fake-secret",
            "token_uri": "https://oauth2.googleapis.com/token",
            "expiry": "2099-01-01T00:00:00Z",
            "scopes": integrations.SCOPES["calendar"],
        }
        with override_settings(SALESMATE_VAULT_KEY=Fernet.generate_key().decode()):
            self.connection.encrypted_credentials = integrations.encrypt_credentials(
                document
            )
            self.assertNotIn("fake-access", self.connection.encrypted_credentials)
            self.assertEqual(
                integrations.credentials_for(self.connection).token, "fake-access"
            )
            document["scopes"] = []
            self.connection.encrypted_credentials = integrations.encrypt_credentials(
                document
            )
            with self.assertRaises(InvalidState):
                integrations.credentials_for(self.connection)

    # 功能：验证 OAuth 发起和回调使用同一 PKCE verifier。
    # 输入：模拟 Flow、Google profile 和普通 Session 字典。
    # 输出：发起要求自动生成 verifier，回调传回同一 verifier 并保存密文。
    # 逻辑：真实 integrations.begin/finish 执行，只有外部边界被替换。
    # 约束：不会执行真实 OAuth 交换。
    @override_settings(
        GOOGLE_OAUTH_CLIENT_ID="fake-client", GOOGLE_OAUTH_CLIENT_SECRET="fake-secret"
    )
    def test_oauth_pkce_reuses_verifier(self):
        flow = Mock(code_verifier="test-verifier")
        flow.authorization_url.return_value = (
            "https://accounts.google.com/test",
            "test-state",
        )
        flow.credentials.to_json.return_value = '{"token": "fake"}'
        api = Mock()
        api.users.return_value.getProfile.return_value.execute.return_value = {
            "emailAddress": "sender@example.com"
        }
        request = Mock(
            session={},
            query_params={"state": "test-state", "code": "test-code"},
            user=self.user,
        )
        with (
            override_settings(SALESMATE_VAULT_KEY=Fernet.generate_key().decode()),
            patch(
                "apps.sales.integrations.Flow.from_client_config", return_value=flow
            ) as factory,
            patch("apps.sales.integrations.build", return_value=api),
        ):
            integrations.begin(
                request, "gmail", "http://testserver/api/v1/sales/oauth/"
            )
            self.assertTrue(factory.call_args.kwargs["autogenerate_code_verifier"])
            connection = integrations.finish(request)
            self.assertEqual(factory.call_args.kwargs["code_verifier"], "test-verifier")
            self.assertNotIn("fake", connection.encrypted_credentials)
            self.assertNotIn(integrations.SESSION_KEY, request.session)

    # 功能：验证日历读取身份和只读 SDK 方法。
    # 输入：owner 连接、明确日期窗口和另一用户。
    # 输出：owner 读取空事件页，另一用户 404。
    # 逻辑：模拟 events.list，禁止创建调用。
    # 约束：不读取真实日程。
    def test_calendar_reads_are_owner_scoped(self):
        parameters = {
            "connection_id": str(self.connection.pk),
            "calendar_id": "primary",
            "start": "2026-09-14T00:00:00+08:00",
            "end": "2026-09-15T00:00:00+08:00",
        }
        api = Mock()
        api.events.return_value.list.return_value.execute.return_value = {"items": []}
        with (
            patch("apps.sales.calendar.credentials_for"),
            patch("apps.sales.calendar.build", return_value=api),
        ):
            self.assertEqual(
                calendar.read_calendar(self.user, "events", parameters)["results"], []
            )
        api.events.return_value.insert.assert_not_called()
        self.client.force_authenticate(
            get_user_model().objects.create_user(username="outsider")
        )
        self.assertEqual(
            self.client.get("/api/v1/sales/calendar/events/", parameters).status_code,
            404,
        )

    # 功能：验证未知结果核对只查询外部已有事件。
    # 输入：uncertain 日历动作和模拟同 ID 事件。
    # 输出：标记 succeeded，insert 从未调用。
    # 逻辑：核对后版本递增，后续 worker 不执行。
    # 约束：此测试不证明外部事件实际存在。
    def test_verification_cannot_create_or_send(self):
        action = models.ToolAction.objects.create(
            owner=self.user,
            company=self.company,
            tool="calendar.create",
            parameters={
                "connection_id": str(self.connection.pk),
                "calendar_id": "primary",
            },
            status="uncertain",
            idempotency_key=uuid.uuid4(),
        )
        api = Mock()
        api.events.return_value.get.return_value.execute.return_value = {
            "id": action.pk.hex,
            "status": "confirmed",
        }
        with (
            patch("apps.sales.actions.credentials_for"),
            patch("apps.sales.actions.build", return_value=api),
        ):
            self.assertEqual(
                actions.verify_action(action, self.user, 0).status, "succeeded"
            )
        api.events.return_value.insert.assert_not_called()
        self.assertEqual(actions.run_action(action.pk), "succeeded")

    # 功能：验证连接已停用时不会调用业务 provider。
    # 输入：approved 动作和已归档连接。
    # 输出：failed，execute_provider 未调用。
    # 逻辑：真实 preflight 在外部业务调用前拒绝连接。
    # 约束：没有隐式重新授权或重试。
    def test_bad_preflight_never_calls_provider(self):
        self.connection.archived = True
        self.connection.save()
        action = models.ToolAction.objects.create(
            owner=self.user,
            company=self.company,
            tool="calendar.create",
            parameters={
                "connection_id": str(self.connection.pk),
                "account": self.connection.account,
            },
            status="approved",
            idempotency_key=uuid.uuid4(),
        )
        with patch("apps.sales.actions.execute_provider") as execute:
            self.assertEqual(actions.run_action(action.pk), "failed")
            execute.assert_not_called()

    # 功能：验证真实会话写请求必须带 CSRF。
    # 输入：强制登录 Session 的 APIClient，未使用 force_authenticate。
    # 输出：无令牌 403，有合法 cookie/header 后创建成功。
    # 逻辑：测试浏览器实际使用的认证与 CSRF 路径。
    # 约束：不使用测试绕过认证来证明 CSRF 有效。
    def test_authenticated_write_requires_csrf(self):
        browser = APIClient(enforce_csrf_checks=True)
        browser.force_login(self.user)
        browser.get("/api/v1/session/")
        path = "/api/v1/sales/records/teams/"
        self.assertEqual(
            browser.post(path, {"name": "CSRF team"}, format="json").status_code, 403
        )
        response = browser.post(
            path,
            {"name": "CSRF team"},
            format="json",
            HTTP_X_CSRFTOKEN=browser.cookies["csrftoken"].value,
        )
        self.assertEqual(response.status_code, 201, response.data)
