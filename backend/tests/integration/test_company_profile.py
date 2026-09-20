"""职责：验证本公司资料的保存、隔离和并发版本契约。
实现：隔离测试库运行真实 Session API 与数据库，测试输入均为合成资料。
关联：accounts.company_profile、CompanyProfile；不连接邮箱、模型或生产服务。
目录：
- CompanyProfileTests：本公司资料集成测试。
- CompanyProfileTests.setUp：准备两个独立账号和登录客户端。
- CompanyProfileTests.test_save_reload_and_owner_isolation：保存与跨账号隔离。
- CompanyProfileTests.test_validation_versions_and_noop：字段及版本错误不产生写入。
- CompanyProfileTests.test_anonymous_and_csrf：认证及会话 CSRF 不可绕过。
变量索引：
- CompanyProfileTests.path：被测 API 地址。
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import CompanyProfile
from apps.crm.models import Company
from apps.sales.models import SellerProfile


# 功能：本公司资料集成测试。
# 逻辑：使用真实数据库和认证客户端校验资料持久化及错误副作用。
# 约束：常规用例 force_login，CSRF 用例单独开启真实保护；不模拟数据保存。
class CompanyProfileTests(TestCase):
    path = "/api/v1/accounts/company-profile/"

    # 功能：准备两个独立账号和登录客户端。
    # 输入：无外部参数，测试框架建立数据库。
    # 输出：设置 owner、other 及 browser 实例状态。
    # 逻辑：创建无业务资料的普通用户；登录 owner。
    # 约束：不创建全局组织或客户，不使用生产凭据。
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="profile-owner")
        self.other = get_user_model().objects.create_user(username="profile-other")
        self.browser = APIClient()
        self.browser.force_login(self.owner)

    # 功能：保存与跨账号隔离。
    # 输入：合成公司资料及两名认证用户。
    # 输出：写后读取一致，另一账号为空；客户及评分画像不受影响。
    # 逻辑：首次 GET 不建档，PATCH 建档，切换账号后验证隔离。
    # 约束：不把不同用户解释为同组织的授权成员。
    def test_save_reload_and_owner_isolation(self):
        empty = self.browser.get(self.path)
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.data["revision"], 0)
        self.assertEqual(CompanyProfile.objects.count(), 0)
        data = {"company_name": "Example Seller", "website": "https://seller.example", "email": "sales@seller.example", "industry": "Manufacturing", "phone": "+65 1234 5678", "address": "Singapore", "description": "Our company"}
        saved = self.browser.patch(self.path, data, format="json", HTTP_IF_MATCH="0")
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(saved.data["revision"], 1)
        self.assertEqual(self.browser.get(self.path).data, saved.data)
        self.assertEqual(saved["ETag"], '"1"')
        self.browser.force_login(self.other)
        self.assertEqual(self.browser.get(self.path).data["company_name"], "")
        other = self.browser.patch(self.path, {"company_name": "Other Seller"}, format="json", HTTP_IF_MATCH="0")
        self.assertEqual(other.status_code, 200)
        self.assertEqual(CompanyProfile.objects.get(owner=self.owner).company_name, "Example Seller")
        self.assertEqual(Company.objects.count(), 0)
        self.assertEqual(SellerProfile.objects.count(), 0)

    # 功能：字段及版本错误不产生写入。
    # 输入：缺失名称、越权字段、错误格式、过期版本及相同资料。
    # 输出：错误返回 400/409，重复内容不递增版本，合法清空可选字段成功。
    # 逻辑：逐次核对数据库数量与最终内容。
    # 约束：版本检查发生在用户行锁内，测试验证 API 契约而非模拟真实并发调度。
    def test_validation_versions_and_noop(self):
        for data in ({}, {"company_name": ""}, {"company_name": "A", "owner": self.other.pk}, {"company_name": "A", "revision": 4}, {"company_name": "A", "email": "invalid"}, {"company_name": "A", "website": "javascript:alert(1)"}, {"company_name": "A", "description": "x" * 5001}):
            self.assertEqual(self.browser.patch(self.path, data, format="json", HTTP_IF_MATCH="0").status_code, 400)
        self.assertEqual(CompanyProfile.objects.count(), 0)
        self.assertEqual(self.browser.patch(self.path, {"company_name": "A"}, format="json").status_code, 400)
        self.browser.patch(self.path, {"company_name": "A", "phone": "123"}, format="json", HTTP_IF_MATCH="0")
        self.assertEqual(self.browser.patch(self.path, {"company_name": "B"}, format="json", HTTP_IF_MATCH="0").status_code, 409)
        same = self.browser.patch(self.path, {"company_name": "A"}, format="json", HTTP_IF_MATCH="1")
        self.assertEqual(same.data["revision"], 1)
        cleared = self.browser.patch(self.path, {"phone": ""}, format="json", HTTP_IF_MATCH="1")
        self.assertEqual((cleared.data["company_name"], cleared.data["phone"], cleared.data["revision"]), ("A", "", 2))

    # 功能：认证及会话 CSRF 不可绕过。
    # 输入：匿名访问和缺少 CSRF 的已登录写请求。
    # 输出：全部拒绝，资料表保持为空。
    # 逻辑：客户端启用 enforce_csrf_checks 并通过真实 session 登录。
    # 约束：不使用 force_authenticate 绕过会话认证。
    def test_anonymous_and_csrf(self):
        browser = APIClient(enforce_csrf_checks=True)
        self.assertEqual(browser.get(self.path).status_code, 403)
        browser.force_login(self.owner)
        self.assertEqual(browser.patch(self.path, {"company_name": "A"}, format="json", HTTP_IF_MATCH="0").status_code, 403)
        self.assertEqual(CompanyProfile.objects.count(), 0)
