"""职责：验证分析冲突原因和最新版本显式重建流程。
实现：合成邮件、真实 PostgreSQL 事务及 DRF API；规则仅生成测试载荷，模型不调用。
关联：results、jobs、analysis_errors 及现有详情投影；不增加 Agent 状态。
目录：
- AnalysisConflictTests：分析并发契约测试。
- AnalysisConflictTests.setUp：生成已领取任务及输入。
- AnalysisConflictTests.post_analysis：通过实际 Agent 路由提交。
- AnalysisConflictTests.test_revision_conflict_and_explicit_successor：旧结果拒绝、错误可读及新任务去重。
- AnalysisConflictTests.test_lease_reasons_remain_409：区分无效、过期与结束任务。
- AnalysisConflictTests.test_immutable_payload_conflict：相同键不同载荷不能覆盖。
- AnalysisConflictTests.test_missing_snapshot_is_not_model_error：缺少快照单独归因。
- AnalysisConflictTests.test_analysis_v5_aliases_persist_as_full_sources：新 Agent 输出规范化后兼容真实后端。
变量索引：
- 无
"""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import uuid
from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.crm import ingestion, jobs, results, rules, selectors
from apps.crm.models import AgentCredential, Analysis, Company, Job, Mailbox
from agent.tests.test_mvp_pipeline import _payload
from agent.workflows.customer_analysis import _source_aliases, generate_analysis


# 功能：验证任务运行期间的版本冲突及安全重建。
# 逻辑：正式权限与实际持久化，外部模型不参与。
# 约束：成功样例为规则构造，不表示真实 L3 效果。
@override_settings(ANALYSIS_PROVIDER="agent", LAB_OPEN_ACCESS=False)
class AnalysisConflictTests(TestCase):
    # 功能：创建独立业务输入和有效租约。
    # 输入：测试数据库及固定合成内容，无外部参数。
    # 输出：用户、客户端、公司、任务、L2 与 L3 夹具。
    # 逻辑：走真实入库、领取与快照服务。
    # 约束：规则方法仅用于生成载荷，不启用生产规则降级。
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="conflict-owner")
        self.mailbox = Mailbox.objects.create(owner=self.owner, address="sales@conflict.example")
        AgentCredential.objects.create(owner=self.owner, digest=hashlib.sha256(b"conflict-test").hexdigest())
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Agent conflict-test")
        self.browser = APIClient()
        self.browser.force_authenticate(self.owner)
        payload = rules.extract_email(self.mailbox, "buyer@customer.example", "询价", "需求：设备\n数量：3 台", "one")
        ingestion.submit_emails(self.owner, [payload])
        self.company = Company.objects.get(owner=self.owner)
        self.job = jobs.claim(self.owner, 1, 120)[0]
        self.snapshot = rules.build_input(*selectors.context_pair(self.company))
        results.save_input(self.owner, self.snapshot, self.company.revision, self.job["job_id"], self.job["lease_token"])
        self.analysis = rules.generate_analysis(self.snapshot, *selectors.context_pair(self.company))

    # 功能：提交实际 L3 HTTP 请求。
    # 输入：`payload` 为可选分析，`token` 为可选租约，默认读取当前夹具。
    # 输出：DRF 响应。
    # 逻辑：保留 If-Match 与任务头，不模拟异常处理器。
    # 约束：只访问测试客户端，不发网络请求。
    def post_analysis(self, payload=None, token=None):
        return self.client.post("/api/v1/agent/analyses/", payload or self.analysis, format="json", HTTP_IF_MATCH=str(self.company.revision), HTTP_X_JOB_ID=self.job["job_id"], HTTP_X_LEASE_TOKEN=token or self.job["lease_token"])

    # 功能：验证旧分析失败后只由显式请求建立最新任务。
    # 输入：L3 开始后更新公司的合成并发事件。
    # 输出：409 原因、新版本未覆盖、错误提示可读、新任务连续点击去重。
    # 逻辑：保留旧任务报告和后继任务，重新领取后构建当前输入。
    # 约束：不自动重试旧分析或改变任务状态枚举。
    def test_revision_conflict_and_explicit_successor(self):
        Company.objects.filter(pk=self.company.pk).update(revision=self.company.revision + 1)
        response = self.post_analysis()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "analysis_revision_changed")
        self.assertFalse(Analysis.objects.exists())
        message = str(response.data["error"]["detail"]["detail"])
        jobs.report(self.owner, {"job_id": self.job["job_id"], "status": "failed", "input_version": None, "produced": {"analysis": False, "score": False}, "error": {"code": "job_failed", "message": message}, "duration_ms": 1}, self.job["lease_token"])
        self.company.refresh_from_db()
        self.assertIn("资料在分析期间更新", selectors.company_row(self.company)["job_error"]["message"])
        first = self.browser.post(f"/api/v1/companies/{self.company.pk}/analyze/")
        second = self.browser.post(f"/api/v1/companies/{self.company.pk}/analyze/")
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data["job_id"], second.data["job_id"])
        next_job = jobs.claim(self.owner, 1, 120)[0]
        self.assertEqual(next_job["expected_version"], self.company.revision)
        snapshot = rules.build_input(*selectors.context_pair(self.company))
        results.save_input(self.owner, snapshot, self.company.revision, next_job["job_id"], next_job["lease_token"])
        results.save_analysis(self.owner, rules.generate_analysis(snapshot, *selectors.context_pair(self.company)), self.company.revision, next_job["job_id"], next_job["lease_token"])
        self.assertEqual(Analysis.objects.get().snapshot.revision, self.company.revision)

    # 功能：区分错误凭证、过期凭证与结束任务。
    # 输入：原租约及明确修改后的测试状态。
    # 输出：各自固定错误码，HTTP 均为 409。
    # 逻辑：逐项发实际 HTTP 请求，错误凭证不暴露任务状态。
    # 约束：不续期、不重新领取、不保存分析。
    def test_lease_reasons_remain_409(self):
        response = self.post_analysis(token=str(uuid.uuid4()))
        self.assertEqual((response.status_code, response.data["error"]["code"]), (409, "analysis_lease_invalid"))
        Job.objects.filter(pk=self.job["job_id"]).update(lease_until=timezone.now() - timedelta(seconds=1))
        response = self.post_analysis()
        self.assertEqual((response.status_code, response.data["error"]["code"]), (409, "analysis_lease_expired"))
        Job.objects.filter(pk=self.job["job_id"]).update(status="failed")
        response = self.post_analysis()
        self.assertEqual((response.status_code, response.data["error"]["code"]), (409, "analysis_job_inactive"))
        self.assertFalse(Analysis.objects.exists())

    # 功能：防止不同载荷覆盖已保存分析。
    # 输入：同版本、同提示词的两个不同有效载荷。
    # 输出：analysis_result_conflict，原载荷保留。
    # 逻辑：改变生成时间以保持其他事实和契约不变。
    # 约束：幂等相同载荷仍成功。
    def test_immutable_payload_conflict(self):
        self.assertEqual(self.post_analysis().status_code, 200)
        self.assertEqual(self.post_analysis().status_code, 200)
        changed = deepcopy(self.analysis)
        changed["generated_at"] = (timezone.now() + timedelta(seconds=1)).isoformat()
        response = self.post_analysis(changed)
        self.assertEqual((response.status_code, response.data["error"]["code"]), (409, "analysis_result_conflict"))
        self.assertEqual(Analysis.objects.count(), 1)

    # 功能：将缺失输入快照与模型字段校验区分。
    # 输入：有效分析载荷及尚未保存的输入键。
    # 输出：analysis_snapshot_changed，不保存结果。
    # 逻辑：只改变输入键，保留其他字段有效。
    # 约束：不替客户端创建快照或绕过校验。
    def test_missing_snapshot_is_not_model_error(self):
        changed = deepcopy(self.analysis)
        changed["input_version"] = "missing-input"
        response = self.post_analysis(changed)
        self.assertEqual((response.status_code, response.data["error"]["code"]), (409, "analysis_snapshot_changed"))

    # 功能：验证拉取后的 analysis-v5 与后端保存契约实际兼容。
    # 输入：真实 L2、含短来源编号及错误完整性字段的合成模型输出。
    # 输出：Agent 规范化为完整来源和实际计数后，后端成功保存 v5 分析。
    # 逻辑：只模拟模型生成文本；Agent 规范化、校验、DRF 验证与数据库写入均执行实际代码。
    # 约束：不调用真实模型，不据此宣称历史客户分析已恢复。
    def test_analysis_v5_aliases_persist_as_full_sources(self):
        payload = _payload(self.snapshot)
        source = self.snapshot["member_dedupe_keys"][0]
        alias = next(key for key, value in _source_aliases(self.snapshot).items() if value == source)
        payload["detail_view"]["profile"]["intent"]["facts"][0]["source_refs"] = [alias]
        payload["detail_view"]["context_completeness"] = {"note": [], "unparsed_message_count": 999}
        provider = Mock(return_value=json.dumps(payload))
        analysis = generate_analysis(self.snapshot, analysis_provider=provider, clock=timezone.now)
        self.assertEqual(analysis["status"], "completed", analysis.get("error"))
        response = self.post_analysis(analysis)
        self.assertEqual(response.status_code, 200, response.data)
        saved = Analysis.objects.get().payload
        self.assertEqual(saved["analysis_prompt_version"], "analysis-v5")
        self.assertEqual(saved["detail_view"]["profile"]["intent"]["facts"][0]["source_refs"], [source])
        self.assertEqual(saved["detail_view"]["context_completeness"], {"unparsed_message_count": 0, "note": None})
        provider.assert_called_once()
