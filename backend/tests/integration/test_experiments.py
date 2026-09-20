"""职责：验证实验共享的跨账号读取、精确批次边界、归属、导出和只读行为。
实现：隔离 PostgreSQL 内创建两组真实关联夹具和普通非夹具数据，使用实际 HTTP 视图。
关联：sales.experiments、seed_kg_lab 与既有业务权限；不访问外部服务，不写真实数据库。
目录：
- ExperimentTests：实验共享集成测试。
- ExperimentTests.setUp：建立临时文件、完整夹具及非归属读取者。
- ExperimentTests.url：生成当前批次的表路径。
- ExperimentTests.test_cross_account_all_tables_and_ownership：所有表可读且保留归属。
- ExperimentTests.test_only_exact_batch_rows_are_shared：其他批次及伪造前缀不授予权限。
- ExperimentTests.test_mutations_and_original_write_paths_denied：拒绝写入及动作执行。
- ExperimentTests.test_anonymous_denied_and_page_available：匿名不能获取数据且站点有实验入口。
- ExperimentTests.test_changed_or_missing_records_fail_closed：内容漂移及缺失明确失败。
- ExperimentTests.test_filters_and_pagination：搜索、归属与页码边界。
- ExperimentTests.test_export_keeps_lineage_without_credentials：导出完整关系并排除凭据。
- ExperimentTests.test_documents_and_attachment_integrity：文档可下载且破坏附件被拒绝。
- ExperimentTests.test_removed_manifest_revokes_access：清单撤销立即收回读取能力。
变量索引：
- 无
"""

import json
import tempfile
from pathlib import Path

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.crm.models import Company
from apps.sales.experiments import APPROVED_BATCHES, TABLES
from apps.sales.management.commands.seed_kg_lab import run_seed, verify_manifest
from apps.sales.models import AuditEvent, Product


# 功能：验证实验数据授权的正反例。
# 逻辑：每项测试使用隔离事务及临时附件目录，普通读取者与导入者不同。
# 约束：未模拟数据库授权或投影；所有样例为虚构，不执行 Worker。
class ExperimentTests(TestCase):
    # 功能：创建独立的实验上下文。
    # 输入：无外部参数，读取测试数据库与临时目录。
    # 输出：owner、reader、manifest、base、client 等实例状态。
    # 逻辑：真实生成两组 44 表数据，并创建不在清单中的客户作为越权反例。
    # 约束：测试结束由框架回滚数据并删除临时文件。
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="kg-read-test-")
        self.addCleanup(self.folder.cleanup)
        self.settings_context = override_settings(BASE_DIR=Path(self.folder.name), LOCAL_DEBUG_AUTO_LOGIN=False)
        self.settings_context.enable()
        self.addCleanup(self.settings_context.disable)
        self.owner = get_user_model().objects.create_user(username="experiment-owner")
        self.reader = get_user_model().objects.create_user(username="experiment-reader")
        self.manifest = run_seed(self.owner, APPROVED_BATCHES[0], 2)
        self.private = Company.objects.create(owner=self.owner, name="KGSEED_20260921_01 private lookalike", group_key="private")
        self.base = f"/api/v1/experiments/{APPROVED_BATCHES[0]}/"
        self.client = APIClient()
        self.client.force_authenticate(self.reader)

    # 功能：生成当前批次的表路径。
    # 输入：`label` 模型名。
    # 输出：相对 URL 字符串。
    # 逻辑：只用于测试构造已知路径。
    # 约束：不触发 HTTP 调用。
    def url(self, label):
        return self.base + label + "/"

    # 功能：验证全部 44 表跨账号可读且归属保持原值。
    # 输入：setUp 创建的夹具与第二个普通账号。
    # 输出：断言表数量、每表行数、归属及原记录指纹。
    # 逻辑：逐表通过真实 API 读取，确认每条记录标记只读和虚构。
    # 约束：不把普通权限测试等同于真实浏览器视觉验证。
    def test_cross_account_all_tables_and_ownership(self):
        summary = self.client.get("/api/v1/experiments/").data["batches"][0]
        self.assertEqual(len(summary["tables"]), 44)
        self.assertEqual(summary["total"], 120)
        for label in TABLES:
            response = self.client.get(self.url(label))
            self.assertEqual(response.status_code, 200, (label, response.data))
            self.assertEqual(response.data["count"], self.manifest["table_counts"][label])
            self.assertTrue(all(row["synthetic"] and row["read_only"] for row in response.data["results"]))
        companies = self.client.get(self.url("crm.Company")).data["results"]
        self.assertEqual({row["owner"]["username"] for row in companies}, {self.owner.username})
        self.assertEqual(verify_manifest(self.manifest), self.manifest["table_counts"])

    # 功能：验证清单外数据、其他批次及凭据表不被开放。
    # 输入：名称伪装成批次的普通客户及未批准路径。
    # 输出：精确清单外客户不出现，未知批次和模型返回 404。
    # 逻辑：按主键查询伪装数据，覆盖字段名和名称前缀无法替代授权。
    # 约束：不通过正则扩大读取范围。
    def test_only_exact_batch_rows_are_shared(self):
        response = self.client.get(self.url("crm.Company"), {"pk": str(self.private.pk)})
        self.assertEqual(response.data["count"], 0)
        for label in ("crm.GmailCredential", "crm.AgentCredential", "agent_tools.ToolCredential", "sales.Connection", "auth.Permission"):
            self.assertEqual(self.client.get(self.url(label)).status_code, 404)
        self.assertEqual(self.client.get("/api/v1/experiments/KGSEED_20260921_02/crm.Company/").status_code, 404)
        self.assertEqual(self.client.get(f"/api/v1/companies/{self.private.pk}/").status_code, 404)

    # 功能：验证实验接口与原有跨账号写入口均拒绝变更。
    # 输入：其他账号读取到的夹具主键。
    # 输出：写方法返回 405，原业务编辑和分析返回 404，夹具指纹不变。
    # 逻辑：同时覆盖一般记录、订单动作和模型分析入口。
    # 约束：不使用真实外部动作提供者。
    def test_mutations_and_original_write_paths_denied(self):
        for method in ("post", "patch", "put", "delete"):
            response = getattr(self.client, method)(self.url("sales.Product"), {}, format="json")
            self.assertEqual(response.status_code, 405)
        product = next(row["pk"] for row in self.manifest["rows"] if row["model"] == "sales.Product")
        company = self.manifest["truth"][0]["company_id"]
        self.assertEqual(self.client.patch(f"/api/v1/sales/records/products/{product}/", {"name": "changed"}, format="json").status_code, 404)
        self.assertEqual(self.client.post(f"/api/v1/companies/{company}/analyze/").status_code, 404)
        self.assertEqual(verify_manifest(self.manifest), self.manifest["table_counts"])

    # 功能：验证匿名被拒绝且站点可提供实验页面。
    # 输入：未认证 APIClient 与普通已认证账号。
    # 输出：API 返回 403，HTML 页面包含只读列表及脚本入口。
    # 逻辑：页面骨架可公开，但没有通过接口认证就无法读取正文。
    # 约束：不将页面响应视为已完成浏览器脚本测试。
    def test_anonymous_denied_and_page_available(self):
        anonymous = APIClient()
        self.assertEqual(anonymous.get("/api/v1/experiments/").status_code, 403)
        self.assertEqual(anonymous.get(self.base + "export/").status_code, 403)
        # 测试临时 BASE_DIR 只影响夹具文件，模板加载器仍指向配置初始化时的真实模板目录。
        page = self.client.get("/experiments/")
        self.assertContains(page, "experiment-rows")
        self.assertContains(page, "experiments.js")

    # 功能：验证清单行修改或缺失时不再返回内容。
    # 输入：在测试事务中修改一条产品，再删除无外部依赖的通知。
    # 输出：两个表读取均返回 409，响应不含被替换正文。
    # 逻辑：分别覆盖摘要变化与数量变化，不自动刷新指纹。
    # 约束：测试修改由事务回滚，不更改原生成参数。
    def test_changed_or_missing_records_fail_closed(self):
        product = Product.objects.filter(owner=self.owner).first()
        Product.objects.filter(pk=product.pk).update(name="new-private-content")
        response = self.client.get(self.url("sales.Product"))
        self.assertEqual(response.status_code, 409)
        self.assertNotIn(b"new-private-content", response.content)
        apps.get_model("sales.Notification").objects.filter(owner=self.owner).first().delete()
        self.assertEqual(self.client.get(self.url("sales.Notification")).status_code, 409)

    # 功能：验证筛选和页码契约。
    # 输入：归属用户名、文本及非法分页参数。
    # 输出：正确筛选、稳定分页与受控 400。
    # 逻辑：先在已授权行集上筛选再分页，不从全库搜索。
    # 约束：仅校验请求参数，不修改模型参数或数据划分。
    def test_filters_and_pagination(self):
        result = self.client.get(self.url("crm.Company"), {"owner": self.owner.username, "page_size": 1}).data
        self.assertEqual(result["count"], 2)
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(self.client.get(self.url("crm.Company"), {"owner": self.reader.username}).data["count"], 0)
        self.assertEqual(self.client.get(self.url("crm.Company"), {"q": "private lookalike"}).data["count"], 0)
        for params in ({"page": 0}, {"page_size": 201}, {"page": "bad"}):
            self.assertEqual(self.client.get(self.url("crm.Company"), params).status_code, 400)

    # 功能：验证完整导出保留关系与模拟声明且排除登录凭据。
    # 输入：当前批次完整导出请求。
    # 输出：44 表、120 行、精确血缘目标以及下载头。
    # 逻辑：检查来源边所指邮件与抽取主键均出现在导出中。
    # 约束：向量和评分仍为夹具值，不声称是真实模型效果。
    def test_export_keeps_lineage_without_credentials(self):
        response = self.client.get(self.base + "export/")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(sum(len(rows) for rows in data["records"].values()), 120)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("no-store", response["Cache-Control"])
        for row in data["records"]["accounts.User"]:
            self.assertNotIn("password", row["fields"])
        emails = {row["pk"] for row in data["records"]["crm.Email"]}
        extractions = {row["pk"] for row in data["records"]["crm.Extraction"]}
        for source in data["records"]["crm.SnapshotSource"]:
            self.assertIn(str(source["fields"]["email_id"]), emails)
            self.assertIn(str(source["fields"]["extraction_id"]), extractions)
        self.assertTrue(data["synthetic"])
        self.assertEqual(data["scenario_links"], self.manifest["truth"])

    # 功能：验证两类文件可读及附件内容核验。
    # 输入：已批准的文档和附件主键；临时损坏一个夹具附件。
    # 输出：正常下载含原内容，损坏文件 409，非清单主键 404。
    # 逻辑：文件读取必须同时通过模型行指纹和文件摘要核验。
    # 约束：只操作测试临时目录，绝不访问共享服务器文件。
    def test_documents_and_attachment_integrity(self):
        for label in ("accounts.SetupDocument", "sales.Attachment"):
            pk = next(row["pk"] for row in self.manifest["rows"] if row["model"] == label)
            response = self.client.get(self.url(label) + pk + "/download/")
            self.assertEqual(response.status_code, 200)
            self.assertTrue(b"".join(response.streaming_content))
            self.assertIn("attachment", response["Content-Disposition"])
        attachment = apps.get_model("sales.Attachment").objects.get(pk=pk)
        (Path(self.folder.name) / "private_uploads" / attachment.storage_key).write_bytes(b"tampered")
        self.assertEqual(self.client.get(self.url("sales.Attachment") + pk + "/download/").status_code, 409)
        self.assertEqual(self.client.get(self.url("sales.Attachment") + "not-a-uuid/download/").status_code, 404)

    # 功能：验证清单撤销后立即停止跨账号读取。
    # 输入：先将清单标记为文件清理中，再删除当前批次的发布清单事件。
    # 输出：清理中返回 409，清单删除后目录为空、原表路径返回 404。
    # 逻辑：每次请求重新读取清单及清理状态，不使用可能过时的权限缓存。
    # 约束：只在测试事务中删除清单，实际清理由原命令负责。
    def test_removed_manifest_revokes_access(self):
        entry = AuditEvent.objects.get(event="kg_synthetic_batch_v1", object_id=APPROVED_BATCHES[0])
        entry.changes["cleanup_state"] = "files_pending"
        entry.save(update_fields=["changes"])
        self.assertEqual(self.client.get(self.url("crm.Company")).status_code, 409)
        AuditEvent.objects.filter(event="kg_synthetic_batch_v1", object_id=APPROVED_BATCHES[0]).delete()
        self.assertEqual(self.client.get("/api/v1/experiments/").data["batches"], [])
        self.assertEqual(self.client.get(self.url("crm.Company")).status_code, 404)
