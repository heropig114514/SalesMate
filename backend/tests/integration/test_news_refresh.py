"""职责：验证指定旧新闻的线索刷新范围、失败语义和版本保护。
实现：真实数据库及后端保存服务，仅模拟 Agent 的网络和模型边界；不访问外部来源。
关联：refresh_world_news_signals 管理命令；公共线索字段由现有序列化器验证。
目录：
- NewsRefreshTests：显式刷新流程验收。
- NewsRefreshTests.setUp：创建旧 Agent 新闻与限定提取载荷。
- NewsRefreshTests.run_refresh：在模拟来源和模型边界下调用实际命令。
- NewsRefreshTests.test_preview_then_apply_preserves_article：预览不写，应用只更新线索。
- NewsRefreshTests.test_empty_signal_and_idempotent_refresh：缺值不伪造金额，相同值不重复更新。
- NewsRefreshTests.test_failure_preserves_record：错误退出非零，不覆盖旧记录。
- NewsRefreshTests.test_target_preflight：无效或重复目标在外部调用前拒绝。
- NewsRefreshTests.test_concurrent_revision_is_not_overwritten：过期快照不能覆盖并发修改。
- NewsRefreshTests.test_explicit_preview_import：仅导入明确匹配的来源，无额外网络或模型请求。
- NewsRefreshTests.test_preview_mismatch_and_duplicates：预览缺少目标或来源歧义时拒绝。
变量索引：
- 无
"""

import io
import json
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from agent.world_insights import InsightError
from apps.crm.access import Conflict
from apps.sales.management.commands.refresh_world_news_signals import SIGNAL_FIELDS, refresh_one
from apps.sales.models import WorldNews


# 功能：验证显式旧新闻刷新不会变成全库重采或私人商机关联。
# 逻辑：关闭实验模式，采用真实保存服务与审计路径；来源和模型是模拟边界。
# 约束：测试通过不代表模型提取或外部来源真实可用。
@override_settings(LAB_OPEN_ACCESS=False, WORKSPACE_OWNER_ONLY=False, LOCAL_DEBUG_AUTO_LOGIN=False)
class NewsRefreshTests(TestCase):
    # 功能：准备待刷新的历史记录。
    # 输入：无外部参数。
    # 输出：record 与符合协作契约的 payload。
    # 逻辑：旧记录线索为空，新闻正文和版本有明确初始值。
    # 约束：金额和证据均为测试样例，不写运行中的数据库。
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="news-refresh")
        self.record = WorldNews.objects.create(owner=self.owner, title="历史新闻", category="industry", industry="制造业", published_at=timezone.now(), source_url="https://example.org/refresh", content="旧正文", summary="旧摘要不是原文证据", data_source="agent")
        self.payload = {field: None if field == "amount" else "" for field in SIGNAL_FIELDS}
        self.payload.update(company_name="示例公司", signal_type="new_factory", evidence="示例公司投资 CNY 100 元建厂。", amount="100", currency="CNY", amount_type="total_investment", amount_scope="whole_project", amount_evidence="投资 CNY 100 元")

    # 功能：在明确模拟边界下调用命令。
    # 输入：`apply` 是否传入 --apply，`payload` 可替换模型结果。
    # 输出：命令 JSON 与模型 mock，供验证证据来源。
    # 逻辑：实际执行参数、目标校验和数据库事务，模拟 fetch_page 与 summarize_news。
    # 约束：load_environment 不读取本机运行配置，外部请求不发送。
    def run_refresh(self, apply=False, payload=None):
        output = io.StringIO()
        with patch("agent.world_insights.load_environment"), patch("agent.world_insights.fetch_page", return_value=("来源原文", None, [])) as fetch, patch("agent.world_insights.summarize_news", return_value=self.payload if payload is None else payload) as model:
            call_command("refresh_world_news_signals", str(self.record.pk), apply=apply, stdout=output)
            fetch.assert_called_once_with(self.record.source_url)
        return json.loads(output.getvalue()), model

    # 功能：验证预览无副作用、应用只回写公共线索。
    # 输入：同一条旧新闻的预览与应用两次显式调用。
    # 输出：预览 revision 不变，应用 revision 加一且正文/摘要/时间/来源不变。
    # 逻辑：核对 Agent 的 candidate 没有历史生成摘要作为证据。
    # 约束：两次调用是测试独立路径，不是生产隐式重试策略。
    def test_preview_then_apply_preserves_article(self):
        report, model = self.run_refresh()
        self.assertEqual(report["results"][0]["status"], "would_update")
        self.assertEqual(model.call_args.args[0].excerpt, "")
        self.record.refresh_from_db()
        self.assertEqual((self.record.revision, self.record.company_name), (0, ""))
        original = (self.record.content, self.record.summary, self.record.source_url, self.record.published_at)
        report, _ = self.run_refresh(apply=True)
        self.record.refresh_from_db()
        self.assertEqual(report["results"][0]["status"], "updated")
        self.assertEqual((self.record.revision, self.record.company_name, str(self.record.amount)), (1, "示例公司", "100.000000"))
        self.assertEqual((self.record.content, self.record.summary, self.record.source_url, self.record.published_at), original)

    # 功能：验证未知金额与相同载荷不会制造数据变化。
    # 输入：空信号及后续相同有效信号。
    # 输出：空信号金额保持 null，相同数据第二次不增加 revision。
    # 逻辑：以数据库校验后的 Decimal 和字段值判断变化。
    # 约束：不从新闻缺失信息推算金额。
    def test_empty_signal_and_idempotent_refresh(self):
        empty = {field: None if field == "amount" else "" for field in SIGNAL_FIELDS}
        report, _ = self.run_refresh(apply=True, payload=empty)
        self.assertEqual(report["results"][0]["status"], "unchanged")
        self.record.refresh_from_db()
        self.assertIsNone(self.record.amount)
        self.assertEqual(self.record.revision, 0)
        self.run_refresh(apply=True)
        report, _ = self.run_refresh(apply=True)
        self.assertEqual(report["results"][0]["status"], "unchanged")
        self.record.refresh_from_db()
        self.assertEqual(self.record.revision, 1)

    # 功能：验证提取错误不会擦除历史内容。
    # 输入：来源已读取但 Agent 抛出明确契约错误。
    # 输出：命令非零错误与逐条失败结果，记录不变。
    # 逻辑：模型仅调用一次，失败没有自动重试或摘要回退。
    # 约束：异常为测试模拟，不能推断真实模型的失败原因。
    def test_failure_preserves_record(self):
        output = io.StringIO()
        with patch("agent.world_insights.load_environment"), patch("agent.world_insights.fetch_page", return_value=("原文", None, [])), patch("agent.world_insights.summarize_news", side_effect=InsightError("资讯国家缺少可核对的文本依据。")) as model:
            with self.assertRaises(CommandError):
                call_command("refresh_world_news_signals", str(self.record.pk), apply=True, stdout=output)
            model.assert_called_once()
        self.assertEqual(json.loads(output.getvalue())["failed"], 1)
        self.record.refresh_from_db()
        self.assertEqual((self.record.revision, self.record.company_name), (0, ""))

    # 功能：验证目标范围在任何外部调用前固定。
    # 输入：缺失 UUID、重复 ID、非 Agent 和归档记录。
    # 输出：均拒绝，fetch_page 未调用。
    # 逻辑：整体预检，不对部分合法目标提前执行。
    # 约束：不扩大为全表或按名称模糊匹配。
    def test_target_preflight(self):
        with patch("agent.world_insights.fetch_page") as fetch:
            for identifiers in ((str(uuid.uuid4()),), (str(self.record.pk), str(self.record.pk))):
                with self.assertRaises(CommandError):
                    call_command("refresh_world_news_signals", *identifiers)
            for fields in ({"data_source": "manual"}, {"data_source": "agent", "archived": True}):
                WorldNews.objects.filter(pk=self.record.pk).update(**fields)
                with self.assertRaises(CommandError):
                    call_command("refresh_world_news_signals", str(self.record.pk))
            fetch.assert_not_called()

    # 功能：验证外部调用期间发生的修改不会被旧快照覆盖。
    # 输入：旧 revision 快照与数据库已更新版本。
    # 输出：Conflict，保留并发编辑内容。
    # 逻辑：调用真实 save_record 的版本与事务检查。
    # 约束：不在冲突后自动重取版本并重试。
    def test_concurrent_revision_is_not_overwritten(self):
        WorldNews.objects.filter(pk=self.record.pk).update(revision=1, company_name="其他编辑")
        with patch("agent.world_insights.fetch_page", return_value=("原文", None, [])), patch("agent.world_insights.summarize_news", return_value=self.payload):
            with self.assertRaises(Conflict):
                refresh_one(self.record, True)
        self.record.refresh_from_db()
        self.assertEqual((self.record.revision, self.record.company_name), (1, "其他编辑"))

    # 功能：验证显式预览文件可复用 Agent 原采集流程的公开结果。
    # 输入：一个精确来源匹配的 payload 及一条未授权目标的额外预览。
    # 输出：仅指定旧新闻更新，不调用网络、环境加载或模型。
    # 逻辑：预览文件为模拟边界，导入经过实际字段和版本校验。
    # 约束：不将预览里的未知记录创建为新新闻，不修改原标题或正文。
    def test_explicit_preview_import(self):
        payload = {**self.payload, "source_url": self.record.source_url, "data_source": "agent", "content": "不能覆盖原正文"}
        document = {"preview": [{"tool": "world_news.create", "data": payload}, {"tool": "world_news.create", "data": {**payload, "source_url": "https://example.org/unselected"}}]}
        output = io.StringIO()
        with patch("pathlib.Path.read_text", return_value=json.dumps(document)), patch("agent.world_insights.fetch_page") as fetch, patch("agent.world_insights.summarize_news") as model, patch("agent.world_insights.load_environment") as environment:
            call_command("refresh_world_news_signals", str(self.record.pk), agent_preview="preview.json", apply=True, stdout=output)
            fetch.assert_not_called()
            model.assert_not_called()
            environment.assert_not_called()
        self.record.refresh_from_db()
        self.assertEqual((self.record.company_name, self.record.content, self.record.revision), ("示例公司", "旧正文", 1))
        self.assertEqual(WorldNews.objects.count(), 1)
        self.assertEqual(json.loads(output.getvalue())["results"][0]["status"], "updated")

    # 功能：拒绝缺失、歧义或伪装来源的预览。
    # 输入：无匹配、重复匹配及非 Agent 来源标记的 JSON。
    # 输出：命令失败，原记录和版本不变，无网络请求。
    # 逻辑：必须明确命中单个 source_url，不能按标题或列表位置猜测。
    # 约束：不会因导入失败切回网络重采。
    def test_preview_mismatch_and_duplicates(self):
        item = {"tool": "world_news.create", "data": {**self.payload, "source_url": self.record.source_url, "data_source": "agent"}}
        invalid_source = {"tool": "world_news.create", "data": {**item["data"], "data_source": "manual"}}
        for rows in ([], [item, item], [invalid_source]):
            with self.subTest(rows=rows), patch("pathlib.Path.read_text", return_value=json.dumps({"preview": rows})), patch("agent.world_insights.fetch_page") as fetch:
                with self.assertRaises(CommandError):
                    call_command("refresh_world_news_signals", str(self.record.pk), agent_preview="preview.json", apply=True, stdout=io.StringIO())
                fetch.assert_not_called()
        self.record.refresh_from_db()
        self.assertEqual((self.record.revision, self.record.company_name), (0, ""))
