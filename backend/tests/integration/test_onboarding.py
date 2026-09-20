"""职责：验证首次引导、简化密码和私有资料文件边界。
实现：真实隔离测试数据库与 APIClient；不模拟授权判断，不访问外部服务。
关联：accounts.onboarding、registration、CRM session 接口。
目录：
- OnboardingTests：引导资料集成场景。
- OnboardingTests.setUp：创建两个合成账号。
- OnboardingTests.test_save_reload_conflict_and_isolation：验证持久化、版本和账号隔离。
- OnboardingTests.test_documents_and_product_validation：验证文件读取与产品引用权限。
- OnboardingTests.test_file_validation_and_anonymous：验证格式、大小、匿名及 CSRF。
- OnboardingTests.test_simple_password_and_onboarding_lifecycle：验证纯数字密码及引导完成状态。
变量索引：
- OnboardingTests.path：引导 API 地址。
- OnboardingTests.files：私有附件 API 地址。
"""
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import SalesSetup
from apps.accounts.onboarding import MAX_BYTES


# 功能：验证引导与授权边界。
# 逻辑：关闭调试自动登录，使用隔离数据库和合成文件。
# 约束：测试不证明真实外部服务或浏览器 PDF 插件可用。
@override_settings(DEBUG=False, LOCAL_DEBUG_AUTO_LOGIN=False)
class OnboardingTests(TestCase):
    path = "/api/v1/accounts/onboarding/"
    files = "/api/v1/accounts/onboarding/documents/"

    # 功能：准备独立账号。
    # 输入：无外部参数；读取测试数据库。
    # 输出：两个用户和认证客户端状态。
    # 逻辑：使用 force_authenticate 隔离资料测试；CSRF 单独覆盖。
    # 约束：无生产数据或外部请求。
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="setup-owner")
        self.other = get_user_model().objects.create_user(username="setup-other")
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    # 功能：验证保存与并发冲突。
    # 输入：合成身份、当前版本及过期版本。
    # 输出：持久化、409 及他人空资料断言。
    # 逻辑：真实 PATCH 后再次读取并切换用户。
    # 约束：不是并发调度压力测试。
    def test_save_reload_conflict_and_isolation(self):
        self.assertEqual(self.client.get(self.path).data["revision"], 0)
        self.assertEqual(SalesSetup.objects.count(), 0)
        personal = {"name": "Alex", "title": "Sales", "email": "alex@example.com", "phone": "", "regions": ["亚太"], "industries": ["光学检测"]}
        response = self.client.patch(self.path, {"personal": personal}, format="json", HTTP_IF_MATCH="0")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["personal"], personal)
        self.assertEqual(self.client.patch(self.path, {"completed": True}, format="json", HTTP_IF_MATCH="0").status_code, 409)
        self.assertFalse(self.client.get(self.path).data["completed"])
        self.assertEqual(self.client.patch(self.path, {"owner": self.other.pk}, format="json", HTTP_IF_MATCH="1").status_code, 400)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.path).data["personal"], {})

    # 功能：验证私有附件和价格区间。
    # 输入：UTF-8 文本、引用该文件的产品及其他账号。
    # 输出：200 私有读取、400 越权引用和倒置价格、404 越权读取。
    # 逻辑：真实 multipart 上传后关联产品并重新读取 JSON。
    # 约束：不执行文件，不接触真实客户。
    def test_documents_and_product_validation(self):
        response = self.client.post(self.files, {"file": SimpleUploadedFile("spec.txt", b"Specification")}, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)
        document_id = str(response.data["id"])
        download = self.client.get(self.files + document_id + "/")
        self.assertEqual(download.content, b"Specification")
        self.assertIn("no-store", download["Cache-Control"])
        product = {"name": "Scanner", "category": "Optics", "specifications": ["10 mm"], "price_min": "10.00", "price_max": "20.00", "currency": "SGD", "scenarios": ["inspection"], "document_id": document_id}
        response = self.client.patch(self.path, {"products": [product]}, format="json", HTTP_IF_MATCH="0")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["products"][0]["price_min"], "10.00")
        product["price_min"] = "30.00"
        self.assertEqual(self.client.patch(self.path, {"products": [product]}, format="json", HTTP_IF_MATCH="1").status_code, 400)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.files + document_id + "/").status_code, 404)
        self.assertEqual(self.client.get(self.path).data["documents"], [])
        self.assertEqual(self.client.patch(self.path, {"solutions": [{"name": "Proposal", "document_id": document_id}]}, format="json", HTTP_IF_MATCH="0").status_code, 400)

    # 功能：验证附件输入和会话边界。
    # 输入：伪装 PDF、无效编码、超限文件以及未认证请求。
    # 输出：格式拒绝与 403 断言。
    # 逻辑：逐个测试失败输入，另用启用 CSRF 的客户端覆盖会话写入。
    # 约束：上传大小是测试构造值，不使用真实文件。
    def test_file_validation_and_anonymous(self):
        for name, content in [("bad.pdf", b"not pdf"), ("bad.txt", b"\xff"), ("big.txt", b"a" * (MAX_BYTES + 1))]:
            self.assertEqual(self.client.post(self.files, {"file": SimpleUploadedFile(name, content)}, format="multipart").status_code, 400)
        anonymous = APIClient(enforce_csrf_checks=True)
        self.assertEqual(anonymous.get(self.path).status_code, 403)
        anonymous.force_login(self.owner)
        self.assertEqual(anonymous.patch(self.path, {"completed": True}, format="json", HTTP_IF_MATCH="0").status_code, 403)

    # 功能：验证简化注册与首次引导。
    # 输入：新用户名和八位纯数字密码，真实 CSRF Cookie。
    # 输出：201 注册、引导标志及完成后标志清除的断言。
    # 逻辑：注册创建未完成资料；显式完成 PATCH 后再次读取会话。
    # 约束：不降低密码哈希、CSRF 或账号权限要求。
    def test_simple_password_and_onboarding_lifecycle(self):
        client = APIClient(enforce_csrf_checks=True)
        token = client.get("/api/v1/session/").data["csrf_token"]
        response = client.post("/api/v1/accounts/register/", {"username": "simple-password", "password": "12345678"}, format="json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 201, response.data)
        session = client.get("/api/v1/session/").data
        self.assertTrue(session["onboarding_required"])
        response = client.patch(self.path, {"completed": True}, format="json", HTTP_IF_MATCH="0", HTTP_X_CSRFTOKEN=session["csrf_token"])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(client.get("/api/v1/session/").data["onboarding_required"])
