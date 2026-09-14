"""职责：验证简易注册、登录衔接和新账号的数据隔离。
实现：真实测试数据库与启用 CSRF 校验的客户端覆盖账号写入、重复用户名及权限边界。
关联：accounts.registration、CRM SessionView 和 sales 客户目录；不调用 Gmail 或模型服务。
目录：
- RegistrationTests：覆盖匿名注册和普通会话边界。
- RegistrationTests.setUp：准备 CSRF 客户端与合成注册资料。
- RegistrationTests.submit：通过真实 HTTP 视图提交注册载荷。
- RegistrationTests.test_register_login_logout_and_empty_workspace：验证注册后登录、空工作空间和重新登录。
- RegistrationTests.test_csrf_and_privileged_fields_rejected：验证 CSRF 与权限字段不能绕过。
- RegistrationTests.test_invalid_username_and_password_rejected：验证既有用户名及密码规则。
- RegistrationTests.test_duplicate_and_normalized_username_rejected：验证规范化重名不覆盖旧账号。
- RegistrationTests.test_database_duplicate_is_validation_error：模拟预检查竞争后验证真实唯一约束处理。
- RegistrationTests.test_authenticated_registration_keeps_identity：验证已登录身份不会被注册请求替换。
变量索引：
- 无
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient


# 功能：验证无需外部验证服务的普通账号注册。
# 逻辑：关闭调试自动登录，使用真实密码哈希、数据库和 Session 进行请求。
# 约束：仅使用隔离测试库和合成数据，不消耗模型额度或发送邮件。
@override_settings(DEBUG=False, LOCAL_DEBUG_AUTO_LOGIN=False)
class RegistrationTests(TestCase):
    # 功能：准备匿名浏览器与合法注册数据。
    # 输入：无外部参数；读取 Django 测试运行环境。
    # 输出：client、csrf、payload 实例状态。
    # 逻辑：先访问 session/ 取得真实 CSRF Cookie，再构造只含两个字段的请求。
    # 约束：不强制认证，不跳过密码验证。
    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.csrf = self.client.get("/api/v1/session/").data["csrf_token"]
        self.payload = {"username": "new-sales-user", "password": "Quartz-Bridge-86!"}

    # 功能：提交带有效 CSRF 的注册请求。
    # 输入：`payload` 为本场景的注册 JSON。
    # 输出：HTTP 响应对象。
    # 逻辑：复用当前客户端 Cookie 与 csrf 实例状态。
    # 约束：调用方在 Session 轮换后需更新 csrf，不隐式刷新或重试。
    def submit(self, payload):
        return self.client.post("/api/v1/accounts/register/", payload, format="json", HTTP_X_CSRFTOKEN=self.csrf)

    # 功能：验证从注册到业务使用及重新登录的闭环。
    # 输入：匿名客户端、合法注册资料和另一用户拥有的客户。
    # 输出：201、普通权限、哈希密码、数据隔离与重新登录断言。
    # 逻辑：另一用户先创建客户，新用户注册后只能创建和读取自己的客户，注销后用同一密码登录。
    # 约束：另一用户的 force_login 仅构造隔离前提；待测注册和登录均通过真实接口。
    def test_register_login_logout_and_empty_workspace(self):
        other = get_user_model().objects.create_user(username="existing-owner")
        other_client = APIClient()
        other_client.force_login(other)
        self.assertEqual(other_client.post("/api/v1/sales/directory/", {"name": "Other private customer"}, format="json").status_code, 201)
        response = self.submit(self.payload)
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["authenticated"])
        self.assertNotIn(self.payload["password"], str(response.data))
        user = get_user_model().objects.get(username=self.payload["username"])
        self.assertTrue(user.check_password(self.payload["password"]))
        self.assertNotEqual(user.password, self.payload["password"])
        self.assertFalse(user.is_staff or user.is_superuser)
        self.assertEqual(self.client.get("/api/v1/accounts/me/").data["id"], user.pk)
        self.assertEqual(self.client.get("/api/v1/sales/directory/").data["count"], 0)
        token = response.data["csrf_token"]
        created = self.client.post("/api/v1/sales/directory/", {"name": "My first customer"}, format="json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(created.status_code, 201)
        self.assertEqual(self.client.get("/api/v1/sales/directory/").data["count"], 1)
        self.assertEqual(self.client.delete("/api/v1/session/", HTTP_X_CSRFTOKEN=token).status_code, 204)
        self.assertEqual(self.client.get("/api/v1/sales/directory/").status_code, 403)
        token = self.client.get("/api/v1/session/").data["csrf_token"]
        logged_in = self.client.post("/api/v1/session/", self.payload, format="json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(logged_in.status_code, 200)
        self.assertEqual(self.client.get("/api/v1/sales/directory/").data["count"], 1)

    # 功能：验证注册保留 CSRF 并拒绝客户端提升权限。
    # 输入：缺失 CSRF 请求及带 is_staff/is_superuser 的合成请求。
    # 输出：403/400 和无新增用户的断言。
    # 逻辑：分别经真实中间件和字段校验拒绝，检查数据库未写入。
    # 约束：不把错误响应本身当作无副作用的证据。
    def test_csrf_and_privileged_fields_rejected(self):
        self.assertEqual(self.client.post("/api/v1/accounts/register/", self.payload, format="json").status_code, 403)
        for field in ["is_staff", "is_superuser"]:
            with self.subTest(field=field):
                self.assertEqual(self.submit({**self.payload, field: True}).status_code, 400)
        self.assertEqual(get_user_model().objects.count(), 0)

    # 功能：验证注册输入采用已有账号规则。
    # 输入：非对象载荷、缺失字段、非法用户名、弱密码及超长密码场景。
    # 输出：每个场景返回 400 且无账号写入。
    # 逻辑：通过完整接口执行模型用户名与 Django 密码校验。
    # 约束：没有添加邮箱、手机或验证码校验；不改变全局密码规则。
    def test_invalid_username_and_password_rejected(self):
        for changes in [{"username": ""}, {"username": "bad name"}, {"password": "12345678"}, {"password": "short"}, {"password": "x" * 129}]:
            with self.subTest(changes=changes):
                self.assertEqual(self.submit({**self.payload, **changes}).status_code, 400)
        self.assertEqual(self.submit({"username": "missing-password"}).status_code, 400)
        self.assertEqual(self.submit([self.payload]).status_code, 400)
        self.assertEqual(get_user_model().objects.count(), 0)

    # 功能：验证重复注册与 Unicode 规范化重名处理。
    # 输入：已有 sales 用户和普通/全角用户名请求。
    # 输出：400、单一用户及旧密码保持不变的断言。
    # 逻辑：两种输入均映射到已有用户名，不允许重置或接管账号。
    # 约束：不改变既有用户名大小写语义。
    def test_duplicate_and_normalized_username_rejected(self):
        user = get_user_model().objects.create_user(username="sales", password="Original-Secret-82!")
        for username in ["sales", "ｓａｌｅｓ"]:
            self.assertEqual(self.submit({**self.payload, "username": username}).status_code, 400)
        user.refresh_from_db()
        self.assertTrue(user.check_password("Original-Secret-82!"))
        self.assertEqual(get_user_model().objects.count(), 1)

    # 功能：验证预检查之后出现重名时仍返回明确校验错误。
    # 输入：`validate_username` 为模拟已通过的预检查；数据库内真实存在同名用户。
    # 输出：400、无注册会话且用户数量未变的断言。
    # 逻辑：只模拟预检查结果，由真实数据库触发唯一约束和原子事务回滚。
    # 约束：模拟竞争窗口，不声称验证了多进程并发调度。
    @patch("apps.accounts.registration.RegistrationSerializer.validate_username")
    def test_database_duplicate_is_validation_error(self, validate_username):
        get_user_model().objects.create_user(username=self.payload["username"])
        validate_username.return_value = self.payload["username"]
        self.assertEqual(self.submit(self.payload).status_code, 400)
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertNotIn("_auth_user_id", self.client.session)

    # 功能：验证注册不能替换已登录用户的身份。
    # 输入：已建立的用户会话和另一个注册用户名。
    # 输出：409、原会话 ID 和单一用户数量断言。
    # 逻辑：先真实注册并刷新 CSRF，再向同一接口提交新用户名。
    # 约束：不注销用户，不创建第二个账号。
    def test_authenticated_registration_keeps_identity(self):
        response = self.submit(self.payload)
        self.assertEqual(response.status_code, 201)
        self.csrf = response.data["csrf_token"]
        original_id = self.client.session["_auth_user_id"]
        self.assertEqual(self.submit({**self.payload, "username": "replacement"}).status_code, 409)
        self.assertEqual(self.client.session["_auth_user_id"], original_id)
        self.assertEqual(get_user_model().objects.count(), 1)
