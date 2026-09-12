"""职责：验证后端基础 HTTP、身份字段和错误边界。
实现：使用 SimpleTestCase 禁止真实数据库访问；就绪分支显式模拟连接，用户接口使用强制认证。
关联：通过 Django 路由调用真实视图和中间件；结果不代表真实登录或 PostgreSQL 联调通过。

目录：
- FoundationTests：在禁止真实数据库访问的条件下检查基础 HTTP 行为。
- FoundationTests.setUp：为每项测试创建独立 HTTP 客户端。
- FoundationTests.test_liveness_is_available_without_database_or_session：验证匿名存活响应及请求 ID 格式。
- FoundationTests.test_request_ids_are_generated_per_request：验证请求 ID 不采信调用方且逐请求生成。
- FoundationTests.test_http_log_does_not_include_query_or_authorization：验证日志不包含查询参数及授权头中的测试秘密。
- FoundationTests.test_readiness_checks_database：验证就绪成功分支执行数据库探测。
- FoundationTests.test_readiness_failure_is_503_and_does_not_expose_connection_details：验证数据库故障响应与日志脱敏。
- FoundationTests.test_current_user_requires_authentication：验证匿名当前用户查询被拒绝并采用统一错误结构。
- FoundationTests.test_current_user_returns_only_public_identity_fields：验证当前用户响应仅包含白名单字段。
- FoundationTests.test_unsupported_method_uses_error_envelope：验证不支持的 HTTP 方法使用统一错误响应。

变量索引：
- 无
"""

from unittest.mock import patch
from uuid import UUID

from django.db import OperationalError
from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User


# 功能：在禁止真实数据库访问的条件下检查基础 HTTP 行为。
# 逻辑：APIClient 驱动实际视图与中间件，数据库分支用 patch，身份字段测试用内存 User。
# 约束：不覆盖真实数据库、会话登录或邮件业务；模拟数据仅用于测试。
class FoundationTests(SimpleTestCase):
    """No database is allowed: test the HTTP boundary independently of provisioning."""

    # 功能：为每项测试创建独立 HTTP 客户端。
    # 输入：无外部参数；由 unittest 生命周期调用。
    # 输出：返回 None，初始化 self.client。
    # 逻辑：每次新建 APIClient，避免前一测试的强制认证状态泄露。
    # 约束：不建立真实数据库连接或用户记录。
    def setUp(self):
        self.client = APIClient()

    # 功能：验证匿名存活响应及请求 ID 格式。
    # 输入：无外部参数；使用 setUp 创建的匿名客户端。
    # 输出：返回 None；状态、正文或 UUID 版本不符时测试失败。
    # 逻辑：请求 health-live，核对固定字段与 UUID4 响应头。
    # 约束：SimpleTestCase 禁止真实数据库调用；不证明外部依赖就绪。
    def test_liveness_is_available_without_database_or_session(self):
        response = self.client.get(reverse("health-live"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "service": "salesmate-backend"})
        self.assertEqual(UUID(response["X-Request-ID"]).version, 4)

    # 功能：验证请求 ID 不采信调用方且逐请求生成。
    # 输入：无外部参数；使用匿名客户端。
    # 输出：返回 None；ID 被复用或来自请求头时断言失败。
    # 逻辑：连续发送携带指定 ID 和未携带 ID 的请求，比较响应头。
    # 约束：只验证两次样本，不构成对所有 UUID 唯一性的数学证明。
    def test_request_ids_are_generated_per_request(self):
        first = self.client.get(reverse("health-live"), HTTP_X_REQUEST_ID="caller-controlled")
        second = self.client.get(reverse("health-live"))
        self.assertNotEqual(first["X-Request-ID"], "caller-controlled")
        self.assertNotEqual(first["X-Request-ID"], second["X-Request-ID"])

    # 功能：验证日志不包含查询参数及授权头中的测试秘密。
    # 输入：无外部参数；使用显式虚构的令牌字符串。
    # 输出：返回 None；泄露指定字符串或缺少 request_id 时断言失败。
    # 逻辑：捕获 salesmate.http 日志，调用存活接口并检查关联 ID 与敏感值。
    # 约束：仅覆盖查询和授权头这两种输入位置，不证明任意路径或其他日志均无敏感信息。
    def test_http_log_does_not_include_query_or_authorization(self):
        with self.assertLogs("salesmate.http", level="INFO") as logs:
            response = self.client.get(
                reverse("health-live") + "?token=private-query",
                HTTP_AUTHORIZATION="Bearer private-header",
            )
        text = "\n".join(logs.output)
        self.assertIn(response["X-Request-ID"], text)
        self.assertNotIn("private-query", text)
        self.assertNotIn("private-header", text)

    # 功能：验证就绪成功分支执行数据库探测。
    # 输入：`connections` 为 patch 注入的连接注册表 mock。
    # 输出：返回 None；状态、正文或 SELECT 1 调用次数不符时失败。
    # 逻辑：模拟游标返回 (1,)，通过 HTTP 调用就绪视图并核对单次执行。
    # 约束：替换的是真实连接入口，不验证 PostgreSQL 服务或迁移。
    @patch("common.views.connections")
    def test_readiness_checks_database(self, connections):
        cursor = connections["default"].cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (1,)
        response = self.client.get(reverse("health-ready"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "database": "ok"})
        cursor.execute.assert_called_once_with("SELECT 1")

    # 功能：验证数据库故障响应与日志脱敏。
    # 输入：`connections` 为 patch 注入的连接注册表 mock。
    # 输出：返回 None；503、错误类型或敏感值排除断言不符时失败。
    # 逻辑：让 cursor 抛出带虚构秘密的 OperationalError，捕获健康日志并检查响应。
    # 约束：模拟连接失败，不连接真实数据库；不穷举所有 DatabaseError 子类。
    @patch("common.views.connections")
    def test_readiness_failure_is_503_and_does_not_expose_connection_details(self, connections):
        connections["default"].cursor.side_effect = OperationalError("password=private-database-secret")
        with self.assertLogs("salesmate.health", level="ERROR") as logs:
            response = self.client.get(reverse("health-ready"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable", "database": "unavailable"})
        self.assertNotIn("private-database-secret", str(response.content) + str(logs.output))
        self.assertIn("OperationalError", "\n".join(logs.output))

    # 功能：验证匿名当前用户查询被拒绝并采用统一错误结构。
    # 输入：无外部参数；使用未认证客户端。
    # 输出：返回 None；403、错误代码或请求 ID 不符时失败。
    # 逻辑：访问 accounts:me 并比较响应正文与响应头的 request_id。
    # 约束：验证默认权限边界，不验证真实登录凭据或团队权限。
    def test_current_user_requires_authentication(self):
        response = self.client.get(reverse("accounts:me"))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "not_authenticated")
        self.assertEqual(response.json()["request_id"], response["X-Request-ID"])

    # 功能：验证当前用户响应仅包含白名单字段。
    # 输入：无外部参数；创建带虚构敏感字段的内存 User。
    # 输出：返回 None；响应字段或值不符合预期时断言失败。
    # 逻辑：force_authenticate 跳过真实登录，将完整响应与四字段字典比较。
    # 约束：不保存用户，不验证密码或会话；新客户端由 setUp 隔离认证状态。
    def test_current_user_returns_only_public_identity_fields(self):
        user = User(id=7, username="sales-rep", password="private-hash", email="private@example.com")
        self.client.force_authenticate(user)
        response = self.client.get(reverse("accounts:me"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"id": 7, "username": "sales-rep", "first_name": "", "last_name": ""},
        )

    # 功能：验证不支持的 HTTP 方法使用统一错误响应。
    # 输入：无外部参数；使用匿名客户端。
    # 输出：返回 None；405 或 method_not_allowed 错误代码不符时失败。
    # 逻辑：向只实现 GET 的存活接口发送 POST，检查异常处理器输出。
    # 约束：仅验证该路由的方法拒绝，不涵盖未知异常的 500 页面。
    def test_unsupported_method_uses_error_envelope(self):
        response = self.client.post(reverse("health-live"), {}, format="json")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json()["error"]["code"], "method_not_allowed")
