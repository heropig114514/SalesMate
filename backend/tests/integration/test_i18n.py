"""职责：验证中英文 HTTP 界面及业务数据、权限和错误契约的隔离。
实现：真实 Django 中间件、编译后的 gettext 目录和隔离测试库覆盖语言优先级及业务往返。
关联：config.settings.base、common.exceptions、注册/登录接口和销售元数据；不调用外部服务。

目录：
- InterfaceLanguageTests：语言协商与业务隔离测试。
- InterfaceLanguageTests.setUp：创建测试用户与客户端。
- InterfaceLanguageTests.test_cookie_header_and_default_priority：验证 cookie、请求头及默认语言。
- InterfaceLanguageTests.test_login_and_validation_errors_keep_contract：验证翻译和错误契约。
- InterfaceLanguageTests.test_catalog_translates_labels_only：验证目录标签变化不改变字段契约。
- InterfaceLanguageTests.test_business_content_roundtrip_and_account_isolation：验证中文业务内容原样保存且权限不变。
- InterfaceLanguageTests.test_error_translation_preserves_keys_codes_and_unknown_text：验证错误翻译边界。

变量索引：
- 无
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils.translation import override
from rest_framework.exceptions import ErrorDetail
from rest_framework.test import APIClient

from common.exceptions import translate_detail


# 功能：通过真实 HTTP 验证界面语言不会改变业务契约。
# 逻辑：关闭调试自动登录，隔离账户、会话及翻译上下文。
# 约束：仅使用测试库，不连接模型、邮件或日历服务。
@override_settings(DEBUG=False, LOCAL_DEBUG_AUTO_LOGIN=False)
class InterfaceLanguageTests(TestCase):
    # 功能：创建普通测试账户及匿名客户端。
    # 输入：无外部参数，使用隔离测试数据库。
    # 输出：user 和 client 实例状态。
    # 逻辑：账号不设置可登录密码；需要身份的场景显式 force_login。
    # 约束：不会创建管理员或复制真实用户数据。
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="language-owner")
        self.client = APIClient()

    # 功能：验证标准 Django 语言选择优先级。
    # 输入：客户端 cookie 与 Accept-Language 请求头。
    # 输出：Content-Language 和 Vary 断言。
    # 逻辑：分别验证英文、带权重的浏览器偏好、手动中文覆盖及不支持语言的中文默认。
    # 约束：session 仅查询身份，不启用本地自动登录。
    def test_cookie_header_and_default_priority(self):
        for header, expected in [("en-US,en;q=0.9", "en"), ("fr;q=0.9,en;q=0.8", "en"), ("fr", "zh-hans")]:
            with self.subTest(header=header):
                response = self.client.get("/api/v1/session/", HTTP_ACCEPT_LANGUAGE=header)
                self.assertEqual(response["Content-Language"], expected)
                self.assertIn("Accept-Language", response["Vary"])
        self.client.cookies["django_language"] = "zh-hans"
        response = self.client.get("/api/v1/session/", HTTP_ACCEPT_LANGUAGE="en")
        self.assertEqual(response["Content-Language"], "zh-hans")
        self.client.cookies["django_language"] = "en"
        response = self.client.get("/api/v1/session/", HTTP_ACCEPT_LANGUAGE="zh-CN")
        self.assertEqual(response["Content-Language"], "en")

    # 功能：验证英文错误与稳定的机器可读契约。
    # 输入：无效登录及缺失注册字段的 JSON。
    # 输出：状态码、错误代码、请求 ID 和字段提示断言。
    # 逻辑：认证失败经过项目 gettext，字段必填消息经过 DRF 内置翻译。
    # 约束：不接受无效凭据，不创建账户，不削弱密码验证。
    def test_login_and_validation_errors_keep_contract(self):
        response = self.client.post("/api/v1/session/", {"username": "missing-user", "password": "incorrect"}, format="json", HTTP_ACCEPT_LANGUAGE="en")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "authentication_failed")
        self.assertEqual(response.data["error"]["detail"]["detail"], "Incorrect username or password.")
        self.assertEqual(response.data["request_id"], response["X-Request-ID"])
        response = self.client.post("/api/v1/accounts/register/", {}, format="json", HTTP_ACCEPT_LANGUAGE="en")
        self.assertEqual(response.status_code, 400)
        self.assertIn("required", str(response.data["error"]["detail"]["password"]))
        self.client.force_login(self.user)
        response = self.client.post("/api/v1/accounts/register/", {}, format="json", HTTP_ACCEPT_LANGUAGE="en")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "already_authenticated")
        self.assertEqual(response.data["error"]["detail"], "Sign out before creating a new account.")

    # 功能：验证业务目录只翻译人类可读标签。
    # 输入：同一已认证用户的中英文目录请求。
    # 输出：标签翻译与其他资源契约完全相等断言。
    # 逻辑：逐资源比较，字段名、choices、权限提示和状态边均保持原值。
    # 约束：元数据请求只读，不创建业务记录。
    def test_catalog_translates_labels_only(self):
        self.client.force_login(self.user)
        chinese = self.client.get("/api/v1/sales/catalog/", HTTP_ACCEPT_LANGUAGE="zh-hans").data
        english = self.client.get("/api/v1/sales/catalog/", HTTP_ACCEPT_LANGUAGE="en").data
        self.assertEqual(len(chinese["resources"]), len(english["resources"]))
        for before, after in zip(chinese["resources"], english["resources"]):
            self.assertNotEqual(before["label"], after["label"])
            self.assertEqual({k: v for k, v in before.items() if k != "label"}, {k: v for k, v in after.items() if k != "label"})

    # 功能：验证英文请求不会翻译中文业务数据或扩展账户可见范围。
    # 输入：名称恰好等于 UI 翻译键的合成客户及另一账户。
    # 输出：持久化往返与账户隔离断言。
    # 逻辑：经真实接口保存中文公司名，刷新读取原值；另一用户仍看不到该客户。
    # 约束：不使用演示数据或生产账号，不改变 owner 权限模型。
    def test_business_content_roundtrip_and_account_isolation(self):
        self.client.force_login(self.user)
        response = self.client.post("/api/v1/sales/directory/", {"name": "客户"}, format="json", HTTP_ACCEPT_LANGUAGE="en")
        self.assertEqual(response.status_code, 201)
        rows = self.client.get("/api/v1/sales/directory/", HTTP_ACCEPT_LANGUAGE="en").data["results"]
        self.assertEqual([row["name"] for row in rows], ["客户"])
        other = get_user_model().objects.create_user(username="language-other")
        self.client.force_login(other)
        response = self.client.get("/api/v1/sales/directory/", HTTP_ACCEPT_LANGUAGE="en")
        self.assertEqual(response.data["results"], [])

    # 功能：验证错误翻译保持结构、代码与未登记消息。
    # 输入：嵌套错误结构及英文翻译上下文。
    # 输出：精确翻译、不修改输入及代码保留断言。
    # 逻辑：源字段名也为中文翻译键，确保只处理错误值。
    # 约束：未登记诊断保持原文，不产生猜测或机器翻译。
    def test_error_translation_preserves_keys_codes_and_unknown_text(self):
        detail = {"客户": [ErrorDetail("用户名或密码不正确。", code="original_code"), "未登记的服务诊断", 7]}
        with override("en"):
            result = translate_detail(detail)
        self.assertEqual(list(result), ["客户"])
        self.assertEqual(result["客户"][0], "Incorrect username or password.")
        self.assertEqual(result["客户"][0].code, "original_code")
        self.assertEqual(result["客户"][1:], ["未登记的服务诊断", 7])
        self.assertEqual(detail["客户"][0], "用户名或密码不正确。")
