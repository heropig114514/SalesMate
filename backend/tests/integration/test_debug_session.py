"""职责：验证本地免登录工作台的会话与访问边界。
实现：使用真实测试数据库和启用 CSRF 检查的浏览器客户端，显式覆盖本地配置。
关联：覆盖 SessionView 的调试入口，不连接 Gmail 或团队 Agent。
目录：
- DebugSessionTests：验证自动会话、配置边界和既有身份。
- DebugSessionTests.setUp：创建普通调试用户和浏览器客户端。
- DebugSessionTests.test_auto_session_keeps_csrf_and_agent_auth：验证免登录可读业务但写入仍需 CSRF，Agent 仍需凭证。
- DebugSessionTests.test_disabled_or_nonlocal_requires_login：验证关闭开关、关闭 DEBUG 或非回环地址不会自动登录。
- DebugSessionTests.test_unavailable_user_is_explicit_error：验证缺失、停用或管理员账号不会自动登录。
- DebugSessionTests.test_existing_identity_is_preserved：验证已有登录身份不被替换。
变量索引：
- 无
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient


# 功能：验证自动会话、配置边界和既有身份。
# 逻辑：本类显式启用本地自动登录，各场景使用独立测试事务。
# 约束：测试账号和邮箱均为合成数据，不使用本机 demo 的真实凭证。
@override_settings(DEBUG=True, LOCAL_DEBUG_AUTO_LOGIN=True, LOCAL_DEBUG_USER="debug-user")
class DebugSessionTests(TestCase):
    # 功能：创建普通调试用户和浏览器客户端。
    # 输入：测试框架初始化的实例状态，无外部参数。
    # 输出：user 与 client 实例状态。
    # 逻辑：创建无可用密码的普通用户，由待测自动会话建立身份。
    # 约束：所有写请求执行真实 CSRF 校验。
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="debug-user")
        self.client = APIClient(enforce_csrf_checks=True)

    # 功能：验证免登录可读业务但写入仍需 CSRF，Agent 仍需凭证。
    # 输入：匿名本地客户端和已存在的调试用户。
    # 输出：会话持久化、业务读取及受保护写入的断言。
    # 逻辑：GET 建立真实会话后使用同一 cookie 读取业务并尝试两类写入。
    # 约束：不强制认证客户端，不模拟认证服务。
    def test_auto_session_keeps_csrf_and_agent_auth(self):
        response = self.client.get("/api/v1/session/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["authenticated"])
        self.assertTrue(response.data["debug_auto_login"])
        self.assertEqual(response.data["username"], "debug-user")
        self.assertEqual(self.client.get("/api/v1/companies/").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/session/").data["username"], "debug-user")
        payload = {"address": "debug@internal.example"}
        self.assertEqual(self.client.post("/api/v1/mailboxes/", payload, format="json").status_code, 403)
        token = self.client.cookies["csrftoken"].value
        self.assertEqual(self.client.post("/api/v1/mailboxes/", payload, format="json", HTTP_X_CSRFTOKEN=token).status_code, 201)
        self.assertEqual(self.client.post("/api/v1/agent/emails/", [], format="json", HTTP_X_CSRFTOKEN=token).status_code, 401)

    # 功能：验证关闭开关、关闭 DEBUG 或非回环地址不会自动登录。
    # 输入：三个显式禁用条件及全新匿名客户端。
    # 输出：匿名状态和业务访问被拒绝的断言。
    # 逻辑：逐个覆盖单项边界，不能依赖其他边界碰巧阻止自动登录。
    # 约束：不信任可伪造的 X-Forwarded-For 回环地址。
    def test_disabled_or_nonlocal_requires_login(self):
        for config, remote in [({"LOCAL_DEBUG_AUTO_LOGIN": False}, "127.0.0.1"),
                               ({"DEBUG": False}, "127.0.0.1"), ({}, "192.0.2.1")]:
            with self.subTest(config=config, remote=remote), override_settings(**config):
                client = APIClient()
                response = client.get("/api/v1/session/", REMOTE_ADDR=remote, HTTP_X_FORWARDED_FOR="127.0.0.1")
                self.assertFalse(response.data["authenticated"])
                self.assertFalse(response.data["debug_auto_login"])
                self.assertEqual(client.get("/api/v1/companies/").status_code, 403)

    # 功能：验证缺失、停用或管理员账号不会自动登录。
    # 输入：不满足调试用户前提的数据库状态。
    # 输出：409 明确错误、无新增用户和无认证会话的断言。
    # 逻辑：逐个修改用户约束，缺失配置单独通过设置覆盖验证。
    # 约束：不隐式创建账号或改选其他身份。
    def test_unavailable_user_is_explicit_error(self):
        for values in [{"is_active": False}, {"is_staff": True}, {"is_superuser": True}]:
            with self.subTest(values=values):
                get_user_model().objects.filter(pk=self.user.pk).update(is_active=True, is_staff=False, is_superuser=False)
                get_user_model().objects.filter(pk=self.user.pk).update(**values)
                self.assertEqual(self.client.get("/api/v1/session/").status_code, 409)
                self.assertNotIn("_auth_user_id", self.client.session)
        with override_settings(LOCAL_DEBUG_USER="missing"):
            self.assertEqual(self.client.get("/api/v1/session/").status_code, 409)
        self.assertEqual(get_user_model().objects.count(), 1)

    # 功能：验证已有登录身份不被替换。
    # 输入：以另一普通用户建立的真实 Django Session。
    # 输出：会话用户名和会话用户 ID 保持一致的断言。
    # 逻辑：force_login 仅建立测试前提，随后执行真实 SessionView。
    # 约束：本场景不验证密码认证，正常登录由既有 CRM 测试覆盖。
    def test_existing_identity_is_preserved(self):
        other = get_user_model().objects.create_user(username="existing-user")
        self.client.force_login(other)
        response = self.client.get("/api/v1/session/")
        self.assertEqual(response.data["username"], "existing-user")
        self.assertEqual(self.client.session["_auth_user_id"], str(other.pk))
