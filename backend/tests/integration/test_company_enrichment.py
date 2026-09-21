"""职责：验证跨账号公司资料补充的实际 HTTP、L2/L3 保存和失效边界。
实现：隔离数据库内生成完整实验夹具，真实 Agent 凭证调用接口；只替换模型输出。
关联：enrichment、results、共享实验写入和真实 DjangoBackendClient；不访问外部模型。
目录：
- initialize：建立不同归属的资料源与分析目标。
- provider：构造引用实验资料的确定性模型输出。
- EnrichmentTests：验证解析、保存和撤销边界。
- EnrichmentTests.setUp：初始化隔离夹具。
- EnrichmentTests.document：通过 HTTP 读取上下文并构造 L2。
- EnrichmentTests.save：通过当前租约提交 L2。
- EnrichmentTests.test_cross_account_context_and_integrity：验证免额外 Tool 凭证及防篡改。
- EnrichmentTests.test_ambiguity_and_exact_matching：验证歧义、完整域名和受限名称匹配。
- EnrichmentTests.test_crm_priority_and_l3_sources：验证 CRM 优先、实验规模和引用白名单。
- EnrichmentTests.test_source_changes_revoke_cached_results：验证修改和删除批准后旧分析失效。
- EnrichmentTests.test_integrity_failure_is_explicit：验证源数据漂移不是无匹配。
- EnrichmentTests.test_no_match_and_legacy_input：验证无匹配和旧协议保持可用。
- EnrichmentLiveTests：真实网络上的 Agent 分析闭环。
- EnrichmentLiveTests.setUp：初始化隔离夹具。
- EnrichmentLiveTests.test_real_worker_client_and_cache：验证无 Tool 令牌的 L2/L3/L4 与缓存。
变量索引：
- 无
"""

import copy
import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from agent.clients.backend_api import DjangoBackendClient
from agent.workflows.customer_analysis import generate_analysis, _size_band
from agent.workflows.orchestration import analyze_company
from apps.crm import results, rules, selectors
from apps.crm.enrichment import resolve
from apps.crm.models import AgentCredential, Analysis, Company, Job
from apps.sales.experiments import APPROVED_BATCHES, load_batch, table_rows
from apps.sales.experiment_writes import mutate
from apps.sales.management.commands.seed_kg_lab import run_seed
from apps.sales.models import AuditEvent
from integrations.company_enrichment import input_version, employee_size


# 功能：建立不同归属的资料源与分析目标。
# 输入：`case` 为 Django 测试实例。
# 输出：初始化账号、目标公司、种子记录、Agent 客户端与租约头。
# 逻辑：生成两组实验记录，另一个普通新账号以相同完整域名创建空 CRM 公司。
# 约束：临时文件自动回收，令牌仅为测试常量，不使用 ToolCredential。
def initialize(case):
    folder = tempfile.TemporaryDirectory(prefix="enrichment-test-")
    case.addCleanup(folder.cleanup)
    config = override_settings(BASE_DIR=Path(folder.name), LOCAL_DEBUG_AUTO_LOGIN=False)
    config.enable()
    case.addCleanup(config.disable)
    case.owner = get_user_model().objects.create_user(username="seed-owner")
    case.reader = get_user_model().objects.create_user(username="new-algorithm-user")
    case.batch = APPROVED_BATCHES[0]
    run_seed(case.owner, case.batch, 2)
    case.row = table_rows(load_batch(case.batch), "crm.Company")[0]
    case.company = Company.objects.create(owner=case.reader, name="实验待补全公司", group_key="enrichment-target", domains=case.row["fields"]["domains"])
    AgentCredential.objects.create(owner=case.reader, name="enrichment", digest=hashlib.sha256(b"enrichment-test").hexdigest())
    case.api = APIClient()
    case.api.credentials(HTTP_AUTHORIZATION="Agent enrichment-test")
    Job.objects.create(company=case.company, revision=case.company.revision, trigger="external_updated")
    job = case.api.post("/api/v1/agent/jobs/claim/", {"limit": 1, "lease_seconds": 120}, format="json").data[0]
    case.headers = {"HTTP_IF_MATCH": str(case.company.revision), "HTTP_X_JOB_ID": job["job_id"], "HTTP_X_LEASE_TOKEN": job["lease_token"]}


# 功能：构造引用实验资料的确定性模型输出。
# 输入：`document` 为真实后端提供的 L2。
# 输出：L3 list_view/detail_view 的 JSON 文本。
# 逻辑：无邮件时信号保持 unknown，使用补充行业来源和确定性人数档位。
# 约束：只替换模型边界，不模拟鉴权、匹配、数据库或 HTTP。
def provider(document):
    enrichment = document["business_context"]["company_enrichment"]
    refs = [enrichment["source"]["source_id"]] if enrichment["status"] == "matched" else []
    fact = {"text": "虚构实验资料", "source_refs": refs}
    dimension = {"facts": [fact] if refs else [], "inferences": [], "missing_fields": []}
    return json.dumps({"list_view": {
        "signal": "unknown", "signal_evidence": {"text": "没有邮件信号", "source_refs": []}, "ticket_signals": [],
        "industry": enrichment["facts"].get("industry") or "unknown", "industry_evidence": fact,
        "size_band": _size_band(employee_size(document["business_context"])[0]), "size_source": "model-placeholder",
        "headline_summary": "虚构实验公司", "score_features": {name: {"value": None, "basis": "无邮件"} for name in ("demand_clarity", "urgency", "decision_visibility")},
    }, "detail_view": {"conflicts": [], "profile": {name: dimension for name in ("industry_context", "company_ops", "intent")},
        "analysis": {name: dimension for name in ("timeline", "opportunity", "risk", "guidance")},
        "missing_fields": [], "context_completeness": {"unparsed_message_count": 0, "note": None}}}, ensure_ascii=False)


# 功能：验证解析、保存和撤销边界。
# 逻辑：用真实 HTTP 视图和事务数据库，构造准确匹配与攻击反例。
# 约束：所有写入限测试数据库。
class EnrichmentTests(TestCase):
    # 功能：初始化隔离夹具。
    # 输入：无外部参数，测试实例。
    # 输出：initialize 建立的实例状态。
    # 逻辑：每个测试独立建数据。
    # 约束：不复用生产凭证。
    def setUp(self):
        initialize(self)

    # 功能：通过 HTTP 读取上下文并构造 L2。
    # 输入：实例的 api、company。
    # 输出：(L2 字典, 公司上下文)。
    # 逻辑：使用原规则归并构造邮件部分，再按正式共享版本函数绑定补充资料。
    # 约束：真实 Agent builder 在 LiveServer 测试独立覆盖。
    def document(self):
        grouping = self.api.get("/api/v1/agent/grouping/", {"company_id": str(self.company.pk)}).data
        response = self.api.get("/api/v1/agent/context/", {"company_id": str(self.company.pk)}, HTTP_IF_MATCH=str(self.company.revision))
        self.assertEqual(response.status_code, 200, response.data)
        context = response.data
        document = rules.build_input(grouping, context)
        document["business_context"]["company_enrichment"] = context["company_enrichment"]
        document["input_version"] = input_version(context["emails"], document["merge_version"], context["external_snapshot_version"], context["company_enrichment"])
        return document, context

    # 功能：通过当前租约提交 L2。
    # 输入：`document` 为待保存的快照。
    # 输出：实际 HTTP 响应。
    # 逻辑：带 Agent 凭证及领取凭据执行 POST。
    # 约束：不绕过 results.save_input。
    def save(self, document):
        return self.api.post("/api/v1/agent/analysis-inputs/", document, format="json", **self.headers)

    # 功能：验证免额外 Tool 凭证及防篡改。
    # 输入：跨账号实验资料与空 CRM 目标。
    # 输出：成功保存，伪造字段、版本和私有公司访问失败。
    # 逻辑：逐项篡改且重算版本以排除只检查摘要的假安全。
    # 约束：普通其他员工私有数据不开放。
    def test_cross_account_context_and_integrity(self):
        document, context = self.document()
        enrichment = context["company_enrichment"]
        self.assertEqual(enrichment["status"], "matched")
        self.assertEqual(enrichment["source"]["owner"]["id"], self.owner.pk)
        self.assertEqual(self.save(document).status_code, 200)
        for key, value in [("facts", {"employee_count": 999}), ("source", {"source_id": "private"}), ("status", "not_found")]:
            bad = copy.deepcopy(document)
            bad["business_context"]["company_enrichment"][key] = value
            bad["input_version"] = input_version(context["emails"], bad["merge_version"], context["external_snapshot_version"], bad["business_context"]["company_enrichment"])
            self.assertEqual(self.save(bad).status_code, 409)
        bad = copy.deepcopy(document)
        bad["input_version"] = "invented-version"
        self.assertEqual(self.save(bad).status_code, 409)
        response = self.api.get("/api/v1/agent/grouping/", {"company_id": self.row["pk"]})
        self.assertEqual(response.status_code, 404)
        anonymous = APIClient()
        self.assertEqual(anonymous.get("/api/v1/agent/context/", {"company_id": str(self.company.pk)}).status_code, 401)

    # 功能：验证歧义、完整域名和受限名称匹配。
    # 输入：同域名第二候选及子域、普通同名目标。
    # 输出：ambiguous、not_found、matched 的准确状态。
    # 逻辑：通过公开维护服务增加候选，避免直接破坏清单。
    # 约束：不将 q 子串或普通公司同名当成精确对应。
    def test_ambiguity_and_exact_matching(self):
        self.company.domains = ["sub." + self.row["fields"]["domains"][0]]
        self.assertEqual(resolve(self.company)["status"], "not_found")
        self.company.name = self.row["fields"]["name"]
        self.assertEqual(resolve(self.company)["status"], "not_found")
        self.company.domains = []
        self.assertEqual(resolve(self.company)["match_basis"], "exact_name")
        self.company.domains = self.row["fields"]["domains"]
        mutate(self.reader, "create", self.batch, "crm.Company", data={"group_key": "duplicate-domain", "name": "另一个虚构", "domains": self.company.domains})
        self.assertEqual(resolve(self.company)["status"], "ambiguous")

    # 功能：验证 CRM 优先、实验规模和引用白名单。
    # 输入：匹配的 L2、未知 CRM 人数与伪造引用。
    # 输出：L3 实验规模保存成功，越界引用失败，已有 CRM 数值优先。
    # 逻辑：执行实际 Agent 校验和后端保存；资料仍仅存于快照。
    # 约束：不评估真实模型生成质量。
    def test_crm_priority_and_l3_sources(self):
        document, _ = self.document()
        self.assertEqual(self.save(document).status_code, 200)
        analysis = generate_analysis(document, analysis_provider=provider, clock=timezone.now)
        self.assertEqual(analysis["status"], "completed", analysis)
        self.assertEqual(analysis["list_view"]["size_source"], "synthetic_sample")
        forged = copy.deepcopy(analysis)
        forged["list_view"]["industry_evidence"]["source_refs"] = ["experiment:unapproved"]
        self.assertEqual(self.api.post("/api/v1/agent/analyses/", forged, format="json", **self.headers).status_code, 400)
        response = self.api.post("/api/v1/agent/analyses/", analysis, format="json", **self.headers)
        self.assertEqual(response.status_code, 200, response.data)
        self.company.refresh_from_db()
        self.assertEqual(self.company.customer, {})
        document["business_context"]["customer"].update(employee_count=800, employee_count_source="crm")
        analysis = generate_analysis(document, analysis_provider=provider, clock=timezone.now)
        self.assertEqual(analysis["list_view"]["size_band"], "gte_500")
        self.assertEqual(analysis["list_view"]["size_source"], "crm")

    # 功能：验证修改和删除批准后旧分析失效。
    # 输入：已保存 L2/L3 和合法实验修改。
    # 输出：缓存 miss、当前结果为空、旧快照不可继续保存；新版本变化。
    # 逻辑：正常维护清单后测试所有当前结果入口，并移除批次清单模拟撤销。
    # 约束：历史记录保留，不隐式重新调用模型。
    def test_source_changes_revoke_cached_results(self):
        document, _ = self.document()
        self.save(document)
        analysis = generate_analysis(document, analysis_provider=provider, clock=timezone.now)
        self.assertEqual(self.api.post("/api/v1/agent/analyses/", analysis, format="json", **self.headers).status_code, 200)
        self.assertTrue(results.cached_analysis(self.company, document["input_version"])["hit"])
        mutate(self.reader, "update", self.batch, "crm.Company", pk=self.row["pk"], expected=self.row["fingerprint"], data={"customer": {"employee_count": 301, "industry_from_crm": "工业检测"}})
        self.assertEqual(self.save(document).status_code, 409)
        self.assertFalse(results.cached_analysis(self.company, document["input_version"])["hit"])
        self.assertEqual(selectors.latest_result(self.company), (None, None))
        self.assertEqual(self.api.get("/api/v1/agent/latest-analysis-input/", {"company_id": str(self.company.pk)}).status_code, 404)
        self.assertEqual(self.api.post("/api/v1/agent/analyses/", analysis, format="json", **self.headers).status_code, 409)
        updated, _ = self.document()
        self.assertNotEqual(updated["input_version"], document["input_version"])
        AuditEvent.objects.filter(event="kg_synthetic_batch_v1", object_id=self.batch).delete()
        self.assertEqual(resolve(self.company)["status"], "unavailable")
        self.assertEqual(self.save(updated).status_code, 409)
        with patch("apps.sales.experiments.APPROVED_BATCHES", ()):
            self.assertEqual(selectors.latest_result(self.company), (None, None))

    # 功能：验证源数据漂移不是无匹配。
    # 输入：绕过正式维护入口的实验记录修改。
    # 输出：明确 integrity_error 状态和无补充事实；L2 仍可保存状态。
    # 逻辑：实际指纹检查拒绝损坏数据，不阻断已完成的 L1。
    # 约束：不吞掉未知异常或伪造来源。
    def test_integrity_failure_is_explicit(self):
        Company.objects.filter(pk=self.row["pk"]).update(name="untracked edit")
        document, context = self.document()
        self.assertEqual(context["company_enrichment"]["reason"], "integrity_error")
        self.assertEqual(context["company_enrichment"]["facts"], {})
        self.assertEqual(self.save(document).status_code, 200)

    # 功能：验证无匹配和旧协议保持可用。
    # 输入：普通公司域名和不提交补充字段的旧客户端。
    # 输出：无错误补全，两种输入均可保存。
    # 逻辑：旧协议仍遵循原版本规则，新协议记录 not_found 状态。
    # 约束：不把无匹配解释为没有其他私有公司。
    def test_no_match_and_legacy_input(self):
        self.company.domains = ["ordinary.example"]
        self.company.save(update_fields=["domains"])
        document, context = self.document()
        self.assertEqual(context["company_enrichment"]["status"], "not_found")
        self.assertEqual(self.save(document).status_code, 200)
        del document["business_context"]["company_enrichment"]
        document["input_version"] = "legacy-input"
        self.assertEqual(self.save(document).status_code, 200)


# 功能：真实网络上的 Agent 分析闭环。
# 逻辑：LiveServer 与真实客户端共享隔离 PostgreSQL，只替换模型输出。
# 约束：不使用外部模型或生产数据库。
class EnrichmentLiveTests(LiveServerTestCase):
    # 功能：初始化隔离夹具。
    # 输入：无外部参数，测试实例。
    # 输出：initialize 建立的实例状态。
    # 逻辑：提交真实数据供服务器线程读取。
    # 约束：测试数据库由框架清理。
    def setUp(self):
        initialize(self)

    # 功能：验证无 Tool 令牌的 L2/L3/L4 与缓存。
    # 输入：真实 Agent 令牌、HTTP 服务地址和确定性 provider。
    # 输出：画像完成、再次运行命中缓存、L1 事实空且不污染 CRM。
    # 逻辑：重新排队后由 DjangoBackendClient 领取租约并运行实际 analyze_company。
    # 约束：不把模型替身当成真实 LLM 评测。
    def test_real_worker_client_and_cache(self):
        Job.objects.filter(company=self.company).update(status="pending")
        backend = DjangoBackendClient(self.live_server_url + "/api/v1/agent/", "enrichment-test")
        self.addCleanup(backend.close)
        backend.claim_jobs(1)
        first = analyze_company(str(self.company.pk), backend=backend, analysis_provider=provider, clock=timezone.now)
        self.assertEqual(first["status"], "completed", first)
        self.assertFalse(first["cache_hit"])
        second = analyze_company(str(self.company.pk), backend=backend, analysis_provider=provider, clock=timezone.now)
        self.assertTrue(second["cache_hit"], second)
        self.assertEqual(Analysis.objects.filter(snapshot__company=self.company).count(), 1)
        self.assertFalse(any(first["analysis_input"]["facts"].values()))
        self.assertEqual(first["analysis"]["list_view"]["size_source"], "synthetic_sample")
