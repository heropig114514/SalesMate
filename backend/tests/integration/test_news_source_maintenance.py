"""职责：验证新闻来源维护的显式范围、版本和重复保护。
实现：真实数据库及保存审计；全程禁止模型或网页抓取。
关联：maintain_news_sources 命令及 Agent 的固定 Eurostat URL 规则。
目录：
- NewsSourceMaintenanceTests：维护契约测试。
- NewsSourceMaintenanceTests.setUp：准备两个来源。
- NewsSourceMaintenanceTests.run_command：执行并禁止外部调用。
- NewsSourceMaintenanceTests.test_preview_apply_and_idempotency：验证预览和显式幂等应用。
- NewsSourceMaintenanceTests.test_duplicate_preflight_rolls_back_batch：重复来源整批拒绝。
- NewsSourceMaintenanceTests.test_revision_guard_even_in_lab：实验模式也遵守版本。
- NewsSourceMaintenanceTests.test_unknown_source_is_rejected：拒绝未知来源。
变量索引：
- 无
"""
import io
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.sales.models import AuditEvent, WorldNews


# 功能：确保来源维护不会隐式刷新模型或覆盖其他新闻字段。
# 逻辑：真实事务及唯一性约束，外部调用使用禁止调用断言。
# 约束：不验证真实网站内容是否仍可用。
class NewsSourceMaintenanceTests(TestCase):
    # 功能：创建两条已知迁移来源。
    # 输入：测试框架隔离数据库，无外部参数。
    # 输出：owner 与两条 Agent 新闻 records。
    # 逻辑：两个指标分别对应唯一旧 URL，其他业务字段具有固定值。
    # 约束：合成内容不会写入生产。
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="source-maintenance")
        self.records = [WorldNews.objects.create(owner=self.owner, title="合成新闻", category="industry", industry="制造业", published_at=timezone.now(), source_url=f"https://ec.europa.eu/eurostat/product?code=4-{day}-ap", content="原正文", summary="原摘要", data_source="agent") for day in ["18092026", "16092026"]]

    # 功能：执行有固定版本的命令并确认没有外部请求。
    # 输入：`apply` 为写入开关，`targets` 可替换 ID:revision 序列。
    # 输出：解析后的 JSON。
    # 逻辑：使用命令行参数真实解析，模拟边界只用于禁止调用。
    # 约束：不放宽解析或版本检查。
    def run_command(self, apply=False, targets=None):
        output = io.StringIO()
        with patch("agent.world_insights.fetch_page") as fetch, patch("agent.world_insights.generate_json") as model:
            call_command("maintain_news_sources", *(targets or [f"{row.pk}:{row.revision}" for row in self.records]), apply=apply, stdout=output)
            fetch.assert_not_called()
            model.assert_not_called()
        return json.loads(output.getvalue())

    # 功能：验证预览无写入、明确应用仅改来源且重跑幂等。
    # 输入：两条旧 URL 及明确 apply。
    # 输出：两条 canonical URL，revision 各增加一，内容保留；重复调用不重复审计。
    # 逻辑：实际调用 save_record 和 AuditEvent。
    # 约束：重新应用必须传当前 revision，不能复用过期版本。
    def test_preview_apply_and_idempotency(self):
        preview = self.run_command()
        self.assertTrue(all(item["status"] == "would_update" for item in preview["results"]))
        self.assertEqual(AuditEvent.objects.count(), 0)
        self.run_command(apply=True)
        for row in self.records:
            row.refresh_from_db()
            self.assertIn("/en/web/products-euro-indicators/w/", row.source_url)
            self.assertEqual((row.revision, row.content, row.summary), (1, "原正文", "原摘要"))
        self.assertEqual(AuditEvent.objects.count(), 2)
        result = self.run_command(apply=True)
        self.assertTrue(all(item["status"] == "unchanged" for item in result["results"]))
        self.assertEqual(AuditEvent.objects.count(), 2)

    # 功能：确保后一个目标有重复来源时，前一个目标也不提前写入。
    # 输入：第二条新闻的规范 URL 重复记录。
    # 输出：整批拒绝，第一条 revision 不变。
    # 逻辑：预检所有目标后才应用，跨 owner 重复同样拒绝。
    # 约束：不自动合并、删除或归档重复记录。
    def test_duplicate_preflight_rolls_back_batch(self):
        other = get_user_model().objects.create_user(username="other-source-owner")
        WorldNews.objects.create(owner=other, title="重复", category="industry", industry="制造业", published_at=timezone.now(), source_url="https://ec.europa.eu/eurostat/en/web/products-euro-indicators/w/4-16092026-ap", content="另一条", data_source="agent")
        with self.assertRaises(CommandError):
            self.run_command(apply=True)
        self.records[0].refresh_from_db()
        self.assertEqual(self.records[0].revision, 0)
        self.assertEqual(AuditEvent.objects.count(), 0)

    # 功能：运维维护在实验模式也不得覆盖并发修改。
    # 输入：旧预览 revision 与数据库新版本。
    # 输出：拒绝，来源仍为旧 URL。
    # 逻辑：显式维护版本检查不依赖实验模式跳过 If-Match 的业务行为。
    # 约束：不改变普通业务 API 的实验模式规则。
    @override_settings(LAB_OPEN_ACCESS=True)
    def test_revision_guard_even_in_lab(self):
        WorldNews.objects.filter(pk=self.records[0].pk).update(revision=1)
        with self.assertRaises(CommandError):
            self.run_command(apply=True)
        self.records[0].refresh_from_db()
        self.assertIn("product?code=", self.records[0].source_url)

    # 功能：拒绝尚未核验的迁移及缺少 revision 的输入。
    # 输入：非 Eurostat 来源和非法目标参数。
    # 输出：命令失败且没有审计写入。
    # 逻辑：解析或预检阶段停止，不能绕过为任意重定向维护工具。
    # 约束：不访问目标地址。
    def test_unknown_source_is_rejected(self):
        WorldNews.objects.filter(pk=self.records[0].pk).update(source_url="https://example.test/article")
        with self.assertRaises(CommandError):
            self.run_command(apply=True)
        with self.assertRaises(CommandError):
            self.run_command(targets=[str(self.records[0].pk)])
        self.assertEqual(AuditEvent.objects.count(), 0)
