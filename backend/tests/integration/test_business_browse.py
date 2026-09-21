"""职责：验证现有业务列表跨账号显示共享实验数据且不开放清单外记录。
实现：隔离数据库和临时文件生成完整夹具，经 Session API 检查合并、分页、筛选、计数与拒绝写入。
关联：sales.browse、原业务权限与实验清单；前端真实交互单独验证。
目录：
- BusinessBrowseTests：现有销售页面的合并读取测试。
- BusinessBrowseTests.setUp：建立拥有者、读取者、虚构批次和真实权限反例。
- BusinessBrowseTests.get：调用合并读取并核验状态。
- BusinessBrowseTests.test_all_resources_and_foreign_privacy：业务资源可读、原归属保留、外部连接不开放。
- BusinessBrowseTests.test_mixed_pagination_owner_dedup_and_company_filter：混合分页去重及客户状态筛选。
- BusinessBrowseTests.test_no_unlisted_related_data：共享客户不带出新增普通联系人或设置。
- BusinessBrowseTests.test_stats_permissions_and_revocation：计数、写入拒绝、漂移和撤销。
变量索引：
- 无
"""

import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.crm.models import Company, Contact
from apps.sales.browse import RESOURCES
from apps.sales.experiments import APPROVED_BATCHES
from apps.sales.management.commands.seed_kg_lab import run_seed, verify_manifest
from apps.sales.models import AuditEvent


# 功能：验证真实用户 Session 下的合并列表及原权限边界。
# 逻辑：普通账号不加入导入者团队，原业务列表应为空，新的浏览列表应显示获准夹具。
# 约束：仅使用测试库和临时附件，不调用外部服务。
class BusinessBrowseTests(TestCase):
    # 功能：建立两账号和两个场景的共享夹具。
    # 输入：测试数据库及临时目录。
    # 输出：owner、reader、manifest、private、client 实例属性。
    # 逻辑：将同前缀私有客户作为精确清单反例，不授予团队权限。
    # 约束：测试结束回滚数据库并清理临时文件。
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="business-browse-")
        self.addCleanup(folder.cleanup)
        settings = override_settings(BASE_DIR=Path(folder.name), LOCAL_DEBUG_AUTO_LOGIN=False)
        settings.enable()
        self.addCleanup(settings.disable)
        self.owner = get_user_model().objects.create_user(username="browse-owner")
        self.reader = get_user_model().objects.create_user(username="browse-reader")
        self.manifest = run_seed(self.owner, APPROVED_BATCHES[0], 2)
        self.private = Company.objects.create(owner=self.owner, name=APPROVED_BATCHES[0] + " private", group_key="private")
        self.client = APIClient()
        self.client.force_login(self.reader)

    # 功能：读取一个合并页面。
    # 输入：`resource` 业务资源、`params` 可选查询参数。
    # 输出：成功响应数据。
    # 逻辑：通过 Session 中间件调用实际路由，失败保留原响应供断言诊断。
    # 约束：不绕过视图认证或 Schema。
    def get(self, resource, params=None):
        response = self.client.get(f"/api/v1/sales/browse/{resource}/", params or {})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    # 功能：验证所有已映射业务表对普通其他账号可见。
    # 输入：完整夹具、未共享客户、登录读取者。
    # 输出：每个资源计数等于清单；原业务仍隔离，外部连接及匿名请求拒绝。
    # 逻辑：逐资源读取并核对 experiment 来源，额外查询私有主键验证不泄露。
    # 约束：不把 Session 测试解释为浏览器视觉验证。
    def test_all_resources_and_foreign_privacy(self):
        self.assertEqual(self.client.get("/api/v1/sales/directory/").data["count"], 0)
        for resource, model in RESOURCES.items():
            data = self.get(resource, {"archived": "all"})
            self.assertEqual(data["count"], self.manifest["table_counts"][model], resource)
            self.assertTrue(all(row["experiment"]["read_only"] and row["experiment"]["synthetic"] for row in data["results"]))
            self.assertEqual({row["experiment"]["owner"]["id"] for row in data["results"]}, {self.owner.pk})
        self.assertEqual(self.get("directory", {"company": str(self.private.pk)})["count"], 0)
        self.assertEqual(self.client.get("/api/v1/sales/browse/connections/").status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get("/api/v1/sales/browse/directory/").status_code, 403)

    # 功能：验证普通记录与共享记录混合时分页、去重和筛选一致。
    # 输入：读取者自有客户和导入者账号，同批已核验客户与工单。
    # 输出：普通记录排在共享之前，无重复主键；导入者看到每条夹具一次。
    # 逻辑：跨分页边界逐页检查，并通过客户 ID 与状态筛选工单。
    # 约束：不让共享客户成为普通写权限的可引用对象。
    def test_mixed_pagination_owner_dedup_and_company_filter(self):
        own = Company.objects.create(owner=self.reader, name="my real customer", group_key="own")
        first = self.get("directory", {"page_size": 2})
        second = self.get("directory", {"page_size": 2, "page": 2})
        self.assertEqual((first["count"], first["shared_count"]), (3, 2))
        self.assertEqual(first["results"][0]["id"], str(own.pk))
        self.assertNotIn("experiment", first["results"][0])
        self.assertEqual(len({row["id"] for row in first["results"] + second["results"]}), 3)
        company = self.manifest["truth"][0]["company_id"]
        tickets = self.get("tickets", {"company": company})
        self.assertEqual(tickets["count"], 1)
        status = tickets["results"][0]["status"]
        self.assertEqual(self.get("tickets", {"company": company, "status": status})["count"], 1)
        self.client.force_login(self.owner)
        owner_data = self.get("directory")
        self.assertEqual((owner_data["count"], owner_data["shared_count"]), (3, 2))

    # 功能：验证共享客户的反向关系不能带出清单外记录。
    # 输入：指向已共享客户但未登记到清单的普通联系人。
    # 输出：共享客户中只有原清单联系人，不含新增敏感邮箱。
    # 逻辑：实际创建外键关联，再通过合并目录读取。
    # 约束：普通非共享数据只在测试库构造；不修改原夹具行。
    def test_no_unlisted_related_data(self):
        company = self.manifest["truth"][0]["company_id"]
        Contact.objects.create(company_id=company, name="private new contact", email="private@example.test")
        row = self.get("directory", {"company": company})["results"][0]
        self.assertNotIn("private@example.test", str(row))
        self.assertTrue(row["contacts"])
        self.assertEqual(verify_manifest(self.manifest), self.manifest["table_counts"])

    # 功能：验证合并概览、只读方法和完整性失败。
    # 输入：原权限为空的普通账号，完整批次后再模拟记录漂移与清单撤销。
    # 输出：客户计数为 2 且标明实验数，写入口拒绝，漂移为 409，撤销后为零。
    # 逻辑：检查列表和概览，再通过原写路径验证没有扩权。
    # 约束：仅在测试事务内改变夹具；错误不得降级为部分成功。
    def test_stats_permissions_and_revocation(self):
        data = self.get("overview")
        self.assertEqual((data["customers"], data["shared_counts"]["customers"]), (2, 2))
        self.assertEqual(data["confirmed_order_net"], {})
        for method in ("post", "patch", "delete"):
            self.assertEqual(getattr(self.client, method)("/api/v1/sales/browse/directory/", {}, format="json").status_code, 405)
        company = self.manifest["truth"][0]["company_id"]
        self.assertEqual(self.client.get(f"/api/v1/companies/{company}/").status_code, 404)
        Company.objects.filter(pk=company).update(name="modified")
        self.assertEqual(self.client.get("/api/v1/sales/browse/directory/").status_code, 409)
        AuditEvent.objects.filter(event="kg_synthetic_batch_v1", object_id=APPROVED_BATCHES[0]).delete()
        self.assertEqual(self.get("directory")["count"], 0)
