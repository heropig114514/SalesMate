"""职责：验证默认测试数据库上的邮件理解业务闭环与隔离边界。
实现：同步请求验证持久批次与 202 契约，不模拟已删除的 Web 调度；Django TestCase 创建隔离测试数据库，以合成邮件覆盖接口、事务、租约、缓存与 CSRF。
关联：使用与独立 Agent 相同的 HTTP 协议；规则仅生成测试载荷，不模拟数据库。
目录：
- CRMTests：验证本地前后端与 Agent 契约所依赖的关键业务不变量。
- CRMTests.setUp：创建两名用户及一个有范围的 Agent 凭证。
- CRMTests.email：构造明确标记的合成邮件。
- CRMTests.submit：提交邮件并返回数据库中的公司。
- CRMTests.prepare：为已提交公司建立一致输入并领取租约。
- CRMTests.test_rule_flow_preserves_budget_history_and_page_projection：验证规则模式真实落库与七维详情查询。
- CRMTests.test_agent_http_contract_end_to_end：验证 README 所有主要 Agent 保存步骤可通过 HTTP 串联。
- CRMTests.test_duplicate_email_is_idempotent_and_immutable：验证重复入库幂等及不可变本体。
- CRMTests.test_public_domain_grouping：验证公共邮箱按联系人分开归组。
- CRMTests.test_batch_ownership_failure_rolls_back：验证整批数据发生越权时回滚所有写入。
- CRMTests.test_user_isolation_and_agent_authentication：验证页面与 Agent 上下文的跨用户访问均被拒绝。
- CRMTests.test_failed_extraction_can_be_completed_once：验证失败事实只能成功补交一次。
- CRMTests.test_failed_extraction_updates_during_next_sync：验证下一次批量同步可直接补齐失败抽取。
- CRMTests.test_non_business_email_is_saved_without_job：验证非业务邮件留档但不创建分析任务。
- CRMTests.test_unlocatable_evidence_and_unknown_fields_rejected：验证证据不在原文及未知字段会被拒绝。
- CRMTests.test_old_revision_cannot_save_after_new_email：验证新邮件到达后旧任务不能保存输入。
- CRMTests.test_lease_token_and_expiration：验证错误或过期租约不能写入。
- CRMTests.test_job_claim_excludes_running_work：验证重复领取不会领取已运行任务。
- CRMTests.test_registration_invalidates_cache_and_checks_version：验证 CRM 建档更新使旧缓存失效并支持未知人数。
- CRMTests.test_null_score_and_list_sorting：验证空分语义及列表无分数排最后。
- CRMTests.test_sync_state_compare_and_swap：验证同步游标乐观锁。
- CRMTests.test_employee_gmail_connection_and_sync_queue：验证员工邮箱隔离、同步领取和回报。
- CRMTests.test_employee_gmail_oauth_browser_routes：验证网页 OAuth 入口和回调跳转。
- CRMTests.test_employee_gmail_oauth_reuses_pkce_verifier：验证授权回调复用 PKCE verifier，并接受包含只读权限的 scope 超集。
- CRMTests.test_agent_mode_has_no_implicit_rule_fallback：验证 Agent 模式不会调用规则或导入样例。
- CRMTests.test_session_login_requires_csrf_and_valid_password：验证匿名登录和已登录写入的真实 CSRF 防护。
- CRMTests.test_demo_seed_is_repeatable_without_duplicate_data：验证演示数据重复导入保持邮件与时间不变。
- CRMTests.test_analysis_rejects_foreign_evidence_and_deal_probability：验证伪造来源或成交概率被拒绝，业务百分比可保存。
- CRMTests.test_analysis_input_requires_complete_fact_multiset：验证归并不得丢掉历史事实或重复计算事实。
- CRMTests.test_invalid_identifiers_return_400：验证非法 UUID 为受控输入错误。
- CRMTests.test_explicit_reanalysis_completes_missing_score：验证部分完成的规则任务可在显式重新分析时补齐评分。
变量索引：
- 无
"""
from copy import deepcopy
from datetime import timedelta
import hashlib
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.crm import jobs, results, rules, selectors
from apps.crm.access import Conflict
from apps.crm.models import AgentCredential, Analysis, AnalysisInput, Company, Email, GmailCredential, Job, Mailbox
from apps.crm.response_schemas import JobResponseSerializer, GroupingResponseSerializer, CompanyContextResponseSerializer


# 功能：验证本地前后端与 Agent 契约所依赖的关键业务不变量。
# 逻辑：每项测试使用独立用户、邮箱和隔离数据库；rules 用于确定性断言，同步请求只验证持久排队。
# 约束：不连接 Gmail 或真实模型，不将测试载荷生成解释为 Agent 效果评测。
@override_settings(ANALYSIS_PROVIDER="rules")
class CRMTests(TestCase):
    # 功能：创建两名用户及一个有范围的 Agent 凭证。
    # 输入：测试框架调用，无外部参数。
    # 输出：初始化 user、other、mailbox、agent 与 browser。
    # 逻辑：浏览器强制认证用于业务测试；CSRF 专项单独真实登录。
    # 约束：所有密码和令牌均为虚构测试数据。
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="sales", password="test-password-123")
        self.other = get_user_model().objects.create_user(username="other", password="test-password-456")
        self.mailbox = Mailbox.objects.create(owner=self.user, address="sales@internal.example")
        AgentCredential.objects.create(owner=self.user, name="test", digest=hashlib.sha256(b"test-agent-token").hexdigest())
        self.agent = APIClient()
        self.agent.credentials(HTTP_AUTHORIZATION="Agent test-agent-token")
        self.browser = APIClient()
        self.browser.force_authenticate(self.user)

    # 功能：构造明确标记的合成邮件。
    # 输入：`message_id` 为去重身份；`sender` 为可选发件人；`body` 为可选测试正文。
    # 输出：完整 EmailSubmission。
    # 逻辑：使用规则提取固定标签，保留原文证据。
    # 约束：不读取外部数据，不暗改实验样本。
    def email(self, message_id="one", sender="buyer@optics.example", body=None):
        return rules.extract_email(self.mailbox, sender, "设备询价", body or "公司：测试光学\n行业：光学检测\n需求：采购设备\n数量：12 台\n预算：30 万\n交期：下月\n决策流程：经理审批", message_id)

    # 功能：提交邮件并返回数据库中的公司。
    # 输入：`payload` 可覆盖默认合成邮件。
    # 输出：Company。
    # 逻辑：调用 Agent HTTP 提交，要求成功后查询公司。
    # 约束：未通过协议不能继续测试后续步骤。
    def submit(self, payload=None):
        response = self.agent.post("/api/v1/agent/emails/", [payload or self.email()], format="json")
        self.assertEqual(response.status_code, 200, response.data)
        return Company.objects.get(pk=response.data[0]["company_id"])

    # 功能：为已提交公司建立一致输入并领取租约。
    # 输入：`company` 为数据库公司。
    # 输出：job、snapshot、grouping、context。
    # 逻辑：使用真实领取服务与确定性归并。
    # 约束：仅测试内直接调用服务；独立完整协议测试覆盖 HTTP 传输。
    def prepare(self, company):
        job = jobs.claim(self.user, 1, 120)[0]
        grouping, context = selectors.context_pair(company)
        snapshot = rules.build_input(grouping, context)
        return job, snapshot, grouping, context

    # 功能：验证规则模式真实落库与七维详情查询。
    # 输入：测试用户与两封预算变更邮件。
    # 输出：断言画像、评分、事实历史与来源。
    # 逻辑：HTTP 触发规则消费者，检查 L2 保留两条预算而非覆盖。
    # 约束：规则输出不等同于真实模型效果。
    def test_rule_flow_preserves_budget_history_and_page_projection(self):
        company = self.submit()
        self.submit(self.email("two", body="预算：26 万\n数量：12 台"))
        response = self.browser.post(f"/api/v1/companies/{company.pk}/analyze/")
        self.assertEqual(response.status_code, 200, response.data)
        detail = self.browser.get(f"/api/v1/companies/{company.pk}/").data
        self.assertEqual(detail["provider"], "rules")
        self.assertEqual(detail["email_count"], 2)
        self.assertFalse(detail["stale"])
        self.assertEqual(detail["score"], 33)
        self.assertEqual(len(detail["analysis"]["detail_view"]["profile"]), 3)
        self.assertEqual(len(detail["analysis"]["detail_view"]["analysis"]), 4)
        self.assertEqual([item["value"] for item in AnalysisInput.objects.get().payload["facts"]["budget"]], ["30 万", "26 万"])
        self.assertEqual(Job.objects.get().status, "completed")

    # 功能：验证 README 所有主要 Agent 保存步骤可通过 HTTP 串联。
    # 输入：缺少可选时间和线程字段的合成 L1 载荷及已领取任务。
    # 输出：成功回报和 provider=agent 结果。
    # 逻辑：读取 ETag，核对实际查询响应符合 Schema，再逐次发送 Input、Analysis、Score 与 Report。
    # 约束：只用规则生成契约样例，不调用真实 Agent。
    def test_agent_http_contract_end_to_end(self):
        payload = self.email()
        payload.update(sent_at=None, received_at=None, thread_id=None)
        company = self.submit(payload)
        claimed = self.agent.post("/api/v1/agent/jobs/claim/", {"limit": 1, "lease_seconds": 120}, format="json")
        job = claimed.data[0]
        grouping_response = self.agent.get("/api/v1/agent/grouping/", {"company_id": str(company.pk)})
        context_response = self.agent.get("/api/v1/agent/context/", {"company_id": str(company.pk)}, HTTP_IF_MATCH=grouping_response["ETag"])
        self.assertEqual(context_response.status_code, 200, context_response.data)
        grouping, context = grouping_response.data, context_response.data
        for schema, payload in [(JobResponseSerializer, job), (GroupingResponseSerializer, grouping), (CompanyContextResponseSerializer, context)]:
            checked = schema(data=payload)
            self.assertTrue(checked.is_valid(), checked.errors)
        snapshot = rules.build_input(grouping, context)
        headers = {"HTTP_IF_MATCH": grouping_response["ETag"], "HTTP_X_JOB_ID": job["job_id"], "HTTP_X_LEASE_TOKEN": job["lease_token"]}
        response = self.agent.post("/api/v1/agent/analysis-inputs/", snapshot, format="json", **headers)
        self.assertEqual(response.status_code, 200, response.data)
        analysis = rules.generate_analysis(snapshot, grouping, context)
        analysis["analysis_prompt_version"] = "agent-contract-test-v1"
        response = self.agent.post("/api/v1/agent/analyses/", analysis, format="json", **headers)
        self.assertEqual(response.status_code, 200, response.data)
        response = self.agent.post("/api/v1/agent/scores/", rules.compute_score(analysis), format="json", **headers)
        self.assertEqual(response.status_code, 200, response.data)
        response = self.agent.post("/api/v1/agent/jobs/report/", {"job_id": job["job_id"], "status": "completed", "input_version": snapshot["input_version"], "produced": {"analysis": True, "score": True, "emails_submitted": 0}, "error": None, "duration_ms": 1}, format="json", HTTP_X_LEASE_TOKEN=job["lease_token"])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Analysis.objects.get().provider, "agent")

    # 功能：验证重复入库幂等及不可变本体。
    # 输入：同一载荷重复提交，再更改正文。
    # 输出：duplicate、单条邮件以及 409。
    # 逻辑：比较数据库行数和后端状态。
    # 约束：不以独立新 ID 规避去重要求。
    def test_duplicate_email_is_idempotent_and_immutable(self):
        payload = self.email()
        self.submit(payload)
        duplicate = self.agent.post("/api/v1/agent/emails/", [payload], format="json")
        self.assertEqual(duplicate.data[0]["status"], "duplicate")
        self.assertEqual(Email.objects.count(), 1)
        payload["body_text"] += "\n新内容"
        rejected = self.agent.post("/api/v1/agent/emails/", [payload], format="json")
        self.assertEqual(rejected.status_code, 409)

    # 功能：验证公共邮箱按联系人分开归组。
    # 输入：两个 gmail.com 联系人与两个同企业域名联系人。
    # 输出：三个公司组。
    # 逻辑：企业同域归一，公共邮箱互不合并。
    # 约束：不测试未实现的子域人工合并。
    def test_public_domain_grouping(self):
        for index, address in enumerate(["a@gmail.com", "b@gmail.com", "a@optics.example", "b@optics.example"]):
            self.submit(self.email(str(index), address))
        self.assertEqual(Company.objects.count(), 3)

    # 功能：验证整批数据发生越权时回滚所有写入。
    # 输入：第一封合法邮件和第二封属于其他用户的邮箱。
    # 输出：404 且数据库无新增邮件。
    # 逻辑：构造有效协议但归属不合法的第二项。
    # 约束：不依赖前端隐藏入口提供隔离。
    def test_batch_ownership_failure_rolls_back(self):
        foreign = Mailbox.objects.create(owner=self.other, address="other@example.com")
        first = self.email()
        second = rules.extract_email(foreign, "b@example.com", "询价", "需求：设备", "foreign")
        response = self.agent.post("/api/v1/agent/emails/", [first, second], format="json")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Email.objects.count(), 0)
        self.assertEqual(Company.objects.count(), 0)

    # 功能：验证页面与 Agent 上下文的跨用户访问均被拒绝。
    # 输入：其他用户会话访问已创建公司。
    # 输出：404、空列表和无令牌的 Agent 请求 401。
    # 逻辑：浏览器会话不能替代独立 Agent 凭证。
    # 约束：不打印其他用户数据。
    def test_user_isolation_and_agent_authentication(self):
        company = self.submit()
        self.browser.force_authenticate(self.other)
        self.assertEqual(self.browser.get(f"/api/v1/companies/{company.pk}/").status_code, 404)
        self.assertEqual(self.browser.get("/api/v1/companies/").data["count"], 0)
        anonymous = APIClient()
        self.assertEqual(anonymous.get("/api/v1/agent/grouping/", {"company_id": str(company.pk)}).status_code, 401)
        anonymous.force_login(self.other)
        self.assertEqual(anonymous.post("/api/v1/agent/jobs/claim/", {"limit": 1, "lease_seconds": 120}, format="json").status_code, 401)

    # 功能：验证失败事实只能成功补交一次。
    # 输入：无事实的 failed 邮件和原有可定位事实。
    # 输出：第一次成功、第二次冲突以及 revision 递增。
    # 逻辑：通过真实 Agent facts 路由重做。
    # 约束：邮件本体保持一条且不丢失。
    def test_failed_extraction_can_be_completed_once(self):
        payload = self.email()
        facts = deepcopy(payload["facts"])
        payload.update(extract_status="failed", extract_error="simulated model failure", facts=None)
        company = self.submit(payload)
        resubmit = {"dedupe_key": payload["dedupe_key"], "extract_prompt_version": payload["extract_prompt_version"], "extract_status": "completed", "extract_error": None, "facts": facts}
        first = self.agent.post("/api/v1/agent/facts/", resubmit, format="json")
        self.assertEqual(first.status_code, 200, first.data)
        second = self.agent.post("/api/v1/agent/facts/", resubmit, format="json")
        self.assertEqual(second.status_code, 409)
        company.refresh_from_db()
        self.assertEqual(company.revision, 2)
        self.assertEqual(Email.objects.count(), 1)

    # 功能：验证 Agent 重扫同一邮件时可直接把 failed 更新为 completed。
    # 输入：同一邮件本体的失败载荷、成功载荷和再次成功载荷。
    # 输出：created、updated、duplicate 三种状态，且只创建一项分析任务。
    # 逻辑：沿用 Gmail 同步实际调用的 emails 批量端点完成恢复。
    # 约束：邮件正文和 extract_prompt_version 必须保持一致。
    def test_failed_extraction_updates_during_next_sync(self):
        completed = self.email("retry")
        failed = deepcopy(completed)
        failed.update(extract_status="failed", extract_error="temporary model error", facts=None)
        first = self.agent.post("/api/v1/agent/emails/", [failed], format="json")
        second = self.agent.post("/api/v1/agent/emails/", [completed], format="json")
        third = self.agent.post("/api/v1/agent/emails/", [completed], format="json")
        self.assertEqual(first.data[0]["status"], "created")
        self.assertEqual(second.data[0]["status"], "updated")
        self.assertEqual(third.data[0]["status"], "duplicate")
        self.assertEqual(Job.objects.count(), 1)

    # 功能：验证非业务邮件保存后不会进入公司分析任务队列。
    # 输入：一封由 L1 标记 skipped_non_business 的完整邮件本体。
    # 输出：邮件正常 created，Extraction 保留跳过状态，Job 数量为零。
    # 逻辑：使用正式批量入库端点检查任务触发条件。
    # 约束：非业务邮件仍参与邮件历史和去重。
    def test_non_business_email_is_saved_without_job(self):
        payload = self.email("non-business")
        payload.update(non_business_hint=True, non_business_reason="automated",
                       extract_status="skipped_non_business", facts=None, extract_error=None)
        response = self.agent.post("/api/v1/agent/emails/", [payload], format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data[0]["status"], "created")
        self.assertEqual(Email.objects.count(), 1)
        self.assertEqual(Job.objects.count(), 0)

    # 功能：验证证据不在原文及未知字段会被拒绝。
    # 输入：只改变空白/不可见格式的正文、篡改预算 evidence，以及未声明 access_token。
    # 输出：排版差异正常入库；伪造证据和未知字段返回 400。
    # 逻辑：先核对与 Agent 一致的证据定位，再覆盖事实与协议边界。
    # 约束：token 值为虚构字符串。
    def test_unlocatable_evidence_and_unknown_fields_rejected(self):
        compatible = self.email("formatting")
        compatible["body_text"] = compatible["body_text"].replace(
            "需求：采购设备", "需求：采\u200b购\n设备"
        )
        accepted = self.agent.post(
            "/api/v1/agent/emails/", [compatible], format="json"
        )
        self.assertEqual(accepted.status_code, 200, accepted.data)

        payload = self.email()
        payload["facts"]["budget"][0]["evidences"][0] = "原文不存在的证据"
        self.assertEqual(self.agent.post("/api/v1/agent/emails/", [payload], format="json").status_code, 400)
        payload = self.email()
        payload["access_token"] = "synthetic-not-a-token"
        self.assertEqual(self.agent.post("/api/v1/agent/emails/", [payload], format="json").status_code, 400)
        self.assertEqual(Email.objects.count(), 1)

    # 功能：验证新邮件到达后旧任务不能保存输入。
    # 输入：已领取旧 revision，随后提交另一封邮件。
    # 输出：Conflict 和独立待办后继任务。
    # 逻辑：比较真实 company revision，而非仅检查 input_version。
    # 约束：不靠终止旧消费者掩盖并发覆盖。
    def test_old_revision_cannot_save_after_new_email(self):
        company = self.submit()
        job, snapshot, _, _ = self.prepare(company)
        self.submit(self.email("two"))
        with self.assertRaises(Conflict):
            results.save_input(self.user, snapshot, job["expected_version"], job["job_id"], job["lease_token"])
        self.assertEqual(Job.objects.filter(status="pending").count(), 1)

    # 功能：验证错误或过期租约不能写入。
    # 输入：正确任务配错误 token，再人为推进截止时间。
    # 输出：两次 Conflict；重新领取只标失败而不自动重试。
    # 逻辑：直接更新测试记录模拟时间边界。
    # 约束：仅测试数据库时间，不改变生产时钟。
    def test_lease_token_and_expiration(self):
        company = self.submit()
        job, snapshot, _, _ = self.prepare(company)
        with self.assertRaises(Conflict):
            results.save_input(self.user, snapshot, company.revision, job["job_id"], "wrong")
        Job.objects.filter(pk=job["job_id"]).update(lease_until=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(Conflict):
            results.save_input(self.user, snapshot, company.revision, job["job_id"], job["lease_token"])
        self.assertEqual(jobs.claim(self.user, 1, 120), [])
        self.assertEqual(Job.objects.get().status, "failed")

    # 功能：验证重复领取不会领取已运行任务。
    # 输入：同一待办连续领取两次。
    # 输出：第一次一条，第二次空列表。
    # 逻辑：使用当前测试数据库的事务路径领取。
    # 约束：此项为顺序测试，不声明已覆盖多进程调度压力。
    def test_job_claim_excludes_running_work(self):
        self.submit()
        self.assertEqual(len(jobs.claim(self.user, 1, 120)), 1)
        self.assertEqual(jobs.claim(self.user, 1, 120), [])

    # 功能：验证 CRM 建档更新使旧缓存失效并支持未知人数。
    # 输入：已有规则结果，切换 agent 模式后建档。
    # 输出：旧分析 stale、external_version 更新，旧 revision 写入被拒绝。
    # 逻辑：不自动处理新任务，从页面检查旧结果标记。
    # 约束：不以新规则结果掩盖缓存失效过程。
    def test_registration_invalidates_cache_and_checks_version(self):
        company = self.submit()
        rules.run_company(self.user, company.pk)
        version = AnalysisInput.objects.get().input_version
        self.assertTrue(results.cached_analysis(company, version, rules.ANALYSIS_VERSION)["hit"])
        self.assertFalse(results.cached_analysis(company, version, "different-prompt")["hit"])
        body = {"company_name": "测试光学", "industry_from_crm": "光学检测", "employee_count": None, "employee_count_source": None}
        with override_settings(ANALYSIS_PROVIDER="agent"):
            response = self.browser.post(f"/api/v1/companies/{company.pk}/register/", body, format="json", HTTP_IF_MATCH=str(company.revision))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["stale"])
        company.refresh_from_db()
        self.assertFalse(results.cached_analysis(company, version)["hit"])
        rejected = self.browser.post(f"/api/v1/companies/{company.pk}/register/", body, format="json", HTTP_IF_MATCH="0")
        self.assertEqual(rejected.status_code, 409)

    # 功能：验证空分语义及列表无分数排最后。
    # 输入：一家公司有完整规则字段，另一家缺失交期和决策。
    # 输出：已评分公司先于未评分公司，缺失项不伪造零分。
    # 逻辑：两个公司都完成规则流程再查列表。
    # 约束：不测试正式 Agent 排序质量。
    def test_null_score_and_list_sorting(self):
        complete = self.submit()
        incomplete = self.submit(self.email("other", "b@other.example", "需求：采购设备"))
        rules.run_company(self.user, complete.pk)
        rules.run_company(self.user, incomplete.pk)
        response = self.browser.get("/api/v1/companies/")
        self.assertEqual(response.data["results"][0]["company_id"], str(complete.pk))
        self.assertIsNone(response.data["results"][1]["score"])
        self.assertEqual(response.data["results"][1]["score_reasons"][0]["feature"], "insufficient_data")

    # 功能：验证同步游标乐观锁。
    # 输入：初始状态及同一版本的重复推进。
    # 输出：首次 version=1，第二次 409。
    # 逻辑：由 Agent 路由读写，不调用 Gmail。
    # 约束：只验证后端游标并发，不证明 Gmail 邮件已经同步。
    def test_sync_state_compare_and_swap(self):
        state = self.agent.get("/api/v1/agent/sync-state/", {"mailbox_id": str(self.mailbox.pk)}).data
        state.update(cursor="historyId:123", status="ok", last_synced_at=timezone.now().isoformat(), scope={"labels": ["INBOX", "SENT"], "since": timezone.now().isoformat()})
        first = self.agent.post("/api/v1/agent/sync-state-save/", state, format="json", HTTP_IF_MATCH="0")
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data["version"], 1)
        self.assertEqual(self.agent.post("/api/v1/agent/sync-state-save/", state, format="json", HTTP_IF_MATCH="0").status_code, 409)

    # 功能：验证员工 Gmail 连接只在本人页面可见，并能被对应 Agent 领取和回报。
    # 输入：测试 Gmail 凭证、浏览器同步请求和 Agent 服务调用，执行由独立 Worker 消费。
    # 输出：浏览器无令牌、Agent 单次领取、最终同步完成状态。
    # 逻辑：同一 Mailbox 贯穿页面状态与 Agent 队列，第二名员工不能操作。 浏览器显式提交最多 20 封的测试范围，Agent 领取保留相同冻结条件。
    # 约束：不连接真实 Google，凭证内容完全为测试数据。
    def test_employee_gmail_connection_and_sync_queue(self):
        credentials = {
            "token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "fake-client",
            "client_secret": "fake-secret",
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
        }
        GmailCredential.objects.create(mailbox=self.mailbox, credentials=credentials)

        listed = self.browser.get("/api/v1/mailboxes/")
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertTrue(listed.data[0]["gmail_authorized"])
        self.assertNotIn("authorization", listed.data[0])
        self.assertNotIn("fake-access-token", str(listed.data))

        queued = self.browser.post(
            f"/api/v1/mailboxes/{self.mailbox.pk}/request-sync/",
            {"sync_options": {"max_messages": 20}}, format="json"
        )
        self.assertEqual(queued.status_code, 202, queued.data)
        self.assertEqual(queued.data["sync_state"]["status"], "sync_requested")

        claimed = self.agent.post(
            "/api/v1/agent/mailbox-syncs/claim/", {"limit": 5}, format="json"
        )
        self.assertEqual(claimed.status_code, 200, claimed.data)
        self.assertEqual(len(claimed.data), 1)
        self.assertEqual(claimed.data[0]["authorization"]["token"], "fake-access-token")
        self.assertEqual(
            self.agent.post(
                "/api/v1/agent/mailbox-syncs/claim/", {"limit": 5}, format="json"
            ).data,
            [],
        )

        report = self.agent.post(
            "/api/v1/agent/mailbox-syncs/report/",
            {
                "mailbox_id": str(self.mailbox.pk),
                "status": "completed",
                "sync_result": {"fetched_count": 3, "created_count": 2},
                "error": None,
                "authorization": credentials,
            },
            format="json",
        )
        self.assertEqual(report.status_code, 200, report.data)
        self.assertEqual(report.data["sync_state"]["status"], "completed")
        self.assertIn("last_synced_at", report.data["sync_state"])

        other_browser = APIClient()
        other_browser.force_authenticate(self.other)
        self.assertEqual(
            other_browser.post(
                f"/api/v1/mailboxes/{self.mailbox.pk}/request-sync/",
                {"sync_options": {"max_messages": 20}}, format="json"
            ).status_code,
            404,
        )

    # 功能：验证网页授权入口和 Google 回调返回当前员工工作台。
    # 输入：`finish` 为模拟 OAuth 完成邮箱的 mock，执行由独立 Worker 消费。
    # 输出：授权地址 JSON 与带授权状态的 302 跳转。
    # 逻辑：视图负责会话边界，Google 网络细节由 gmail_oauth 服务封装。
    # 约束：不请求 Google，不验证第三方 OAuth SDK 行为。
    @override_settings(
        GOOGLE_OAUTH_CLIENT_ID="fake-web-client.apps.googleusercontent.com",
        GOOGLE_OAUTH_CLIENT_SECRET="fake-web-secret",
        GOOGLE_OAUTH_REDIRECT_URI="http://testserver/api/v1/mailboxes/gmail-callback/",
    )
    @patch("apps.crm.views.gmail_oauth.finish_authorization")
    def test_employee_gmail_oauth_browser_routes(self, finish):
        started = self.browser.post("/api/v1/mailboxes/gmail-authorize/")
        self.assertEqual(started.status_code, 200, started.data)
        self.assertTrue(
            started.data["authorization_url"].startswith(
                "https://accounts.google.com/o/oauth2/auth?"
            )
        )
        self.assertIn("gmail.readonly", started.data["authorization_url"])

        finish.return_value = self.mailbox
        callback = self.browser.get(
            "/api/v1/mailboxes/gmail-callback/?code=fake&state=fake"
        )
        self.assertEqual(callback.status_code, 302)
        self.assertIn("gmail=authorized", callback["Location"])
        self.assertIn("sales%40internal.example", callback["Location"])

    # 功能：验证 PKCE code_verifier 保持一致，并兼容 Google 返回已授权 scope 超集。
    # 输入：`flow_factory` 构造两次 Flow；`build` 模拟 Gmail profile；令牌交换抛出携带可用 token 的 scope Warning。
    # 输出：回调成功、凭据落库但不排队，第二个 Flow 收到第一个 Flow 的 verifier。
    # 逻辑：回调恢复 verifier，并在返回权限仍包含 gmail.readonly 时接受 token。
    # 约束：测试凭据均为虚构内容，不发起任何外部请求。
    @override_settings(
        GOOGLE_OAUTH_CLIENT_ID="fake-web-client.apps.googleusercontent.com",
        GOOGLE_OAUTH_CLIENT_SECRET="fake-web-secret",
        GOOGLE_OAUTH_REDIRECT_URI="http://testserver/api/v1/mailboxes/gmail-callback/",
    )
    @patch("apps.crm.gmail_oauth.build")
    @patch("apps.crm.gmail_oauth.Flow.from_client_config")
    def test_employee_gmail_oauth_reuses_pkce_verifier(self, flow_factory, build):
        begin_flow = Mock()
        begin_flow.code_verifier = "test-pkce-verifier"
        begin_flow.authorization_url.return_value = (
            "https://accounts.google.com/o/oauth2/auth?state=test-state",
            "test-state",
        )
        finish_flow = Mock()
        finish_flow.credentials.to_json.return_value = '{"token":"fake-token"}'
        scope_warning = Warning(
            'Scope has changed from "gmail.readonly" to "gmail.readonly gmail.insert".'
        )
        scope_warning.token = {
            "access_token": "fake-token",
            "expires_at": 1_900_000_000,
            "scope": [
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.insert",
            ],
        }
        scope_warning.new_scope = scope_warning.token["scope"]
        finish_flow.fetch_token.side_effect = scope_warning
        flow_factory.side_effect = [begin_flow, finish_flow]
        build.return_value.users.return_value.getProfile.return_value.execute.return_value = {
            "emailAddress": self.mailbox.address
        }

        started = self.browser.post("/api/v1/mailboxes/gmail-authorize/")
        self.assertEqual(started.status_code, 200, started.data)
        self.assertEqual(
            self.browser.session["salesmate_gmail_oauth_code_verifier"],
            "test-pkce-verifier",
        )
        authorization_options = begin_flow.authorization_url.call_args.kwargs
        self.assertNotIn("include_granted_scopes", authorization_options)

        callback = self.browser.get(
            "/api/v1/mailboxes/gmail-callback/?code=test-code&state=test-state"
        )

        self.assertEqual(callback.status_code, 302)
        self.assertIn("gmail=authorized", callback["Location"])
        self.assertTrue(GmailCredential.objects.filter(mailbox=self.mailbox).exists())
        self.assertFalse(self.mailbox.sync_runs.exists())
        finish_flow.fetch_token.assert_called_once_with(code="test-code")
        self.assertEqual(finish_flow.oauth2session.token, scope_warning.token)
        self.assertEqual(
            flow_factory.call_args_list[1].kwargs["code_verifier"],
            "test-pkce-verifier",
        )
        self.assertFalse(
            flow_factory.call_args_list[1].kwargs["autogenerate_code_verifier"]
        )

    # 功能：验证 Agent 模式不会调用规则或导入样例。
    # 输入：切换显式配置后请求分析。
    # 输出：任务 pending、结果为空、模拟入口拒绝。
    # 逻辑：检查持久化状态而非仅检查配置值。
    # 约束：此测试不连接外部 Agent。
    @override_settings(ANALYSIS_PROVIDER="agent")
    def test_agent_mode_has_no_implicit_rule_fallback(self):
        company = self.submit()
        response = self.browser.post(f"/api/v1/companies/{company.pk}/analyze/")
        self.assertEqual(response.data["status"], "pending")
        self.assertEqual(Analysis.objects.count(), 0)
        self.assertEqual(self.browser.post("/api/v1/demo/seed/").status_code, 409)

    # 功能：验证匿名登录和已登录写入的真实 CSRF 防护。
    # 输入：启用 enforce_csrf_checks 的独立浏览器客户端。
    # 输出：缺失 token 被拒绝；合法 token 可登录并创建业务邮箱。
    # 逻辑：关闭本地自动会话，登录通过真实 Django 密码验证，再读取轮换后的 cookie。
    # 约束：测试密码为虚构数据，不使用真实账户。
    @override_settings(LOCAL_DEBUG_AUTO_LOGIN=False)
    def test_session_login_requires_csrf_and_valid_password(self):
        client = APIClient(enforce_csrf_checks=True)
        client.get("/api/v1/session/")
        credentials = {"username": "sales", "password": "test-password-123"}
        self.assertEqual(client.post("/api/v1/session/", credentials, format="json").status_code, 403)
        response = client.post("/api/v1/session/", credentials, format="json", HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["authenticated"])
        self.assertEqual(client.post("/api/v1/mailboxes/", {"address": "new@example.com"}, format="json").status_code, 403)
        self.assertEqual(client.post("/api/v1/mailboxes/", {"address": "new@example.com"}, format="json", HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value).status_code, 201)

    # 功能：验证演示数据重复导入保持邮件与时间不变。
    # 输入：连续两次显式 seed 操作。
    # 输出：首次四封、第二次零封，既有记录内容不变。
    # 逻辑：固定 ID 去重，比较实际数据库 payload。
    # 约束：不把样例当研究数据集。
    def test_demo_seed_is_repeatable_without_duplicate_data(self):
        first = self.browser.post("/api/v1/demo/seed/")
        self.assertEqual(first.status_code, 200, first.data)
        before = list(Email.objects.order_by("dedupe_key").values_list("payload", flat=True))
        second = self.browser.post("/api/v1/demo/seed/")
        self.assertEqual(second.data["created_emails"], 0)
        self.assertEqual(list(Email.objects.order_by("dedupe_key").values_list("payload", flat=True)), before)

    # 功能：验证伪造来源或成交概率被拒绝，原文业务百分比可保存。
    # 输入：合法 L2 后分别修改来源、插入成交概率和付款比例。
    # 输出：前两个请求返回 400，付款比例分析成功保存。
    # 逻辑：通过正式 Agent HTTP 写入检查。
    # 约束：不调用外部模型。
    def test_analysis_rejects_foreign_evidence_and_deal_probability(self):
        company = self.submit()
        job, snapshot, grouping, context = self.prepare(company)
        results.save_input(self.user, snapshot, company.revision, job["job_id"], job["lease_token"])
        headers = {"HTTP_IF_MATCH": str(company.revision), "HTTP_X_JOB_ID": job["job_id"], "HTTP_X_LEASE_TOKEN": job["lease_token"]}
        analysis = rules.generate_analysis(snapshot, grouping, context)
        analysis["detail_view"]["profile"]["intent"]["facts"][0]["source_refs"] = ["foreign-email"]
        self.assertEqual(self.agent.post("/api/v1/agent/analyses/", analysis, format="json", **headers).status_code, 400)
        analysis = rules.generate_analysis(snapshot, grouping, context)
        analysis["detail_view"]["profile"]["intent"]["facts"][0]["text"] = "成交可能 80%"
        self.assertEqual(self.agent.post("/api/v1/agent/analyses/", analysis, format="json", **headers).status_code, 400)
        analysis = rules.generate_analysis(snapshot, grouping, context)
        analysis["detail_view"]["profile"]["intent"]["facts"][0]["text"] = "首付款 30%，验收后支付 60%，剩余 10% 为质保金"
        response = self.agent.post("/api/v1/agent/analyses/", analysis, format="json", **headers)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Analysis.objects.count(), 1)

    # 功能：验证归并不得丢掉历史事实或重复计算事实。
    # 输入：已保存邮件的有效快照，分别删除预算和重复预算。
    # 输出：两次 400，快照未入库。
    # 逻辑：从服务返回的全集对账，防止部分归并伪装完整。
    # 约束：测试只更改提交副本，不修改原邮件或抽取事实。
    def test_analysis_input_requires_complete_fact_multiset(self):
        company = self.submit()
        job, snapshot, _, _ = self.prepare(company)
        headers = {"HTTP_IF_MATCH": str(company.revision), "HTTP_X_JOB_ID": job["job_id"], "HTTP_X_LEASE_TOKEN": job["lease_token"]}
        omitted = deepcopy(snapshot)
        del omitted["facts"]["budget"]
        duplicated = deepcopy(snapshot)
        duplicated["facts"]["budget"] *= 2
        for payload in [omitted, duplicated]:
            response = self.agent.post("/api/v1/agent/analysis-inputs/", payload, format="json", **headers)
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(AnalysisInput.objects.count(), 0)

    # 功能：验证非法 UUID 为受控输入错误。
    # 输入：非法公司与邮箱标识查询。
    # 输出：两个 400 响应。
    # 逻辑：确保 ORM UUID 转换异常不会泄露为调试 500。
    # 约束：不查询其他用户实体。
    def test_invalid_identifiers_return_400(self):
        self.assertEqual(self.agent.get("/api/v1/agent/grouping/", {"company_id": "invalid"}).status_code, 400)
        self.assertEqual(self.agent.get("/api/v1/agent/sync-state/", {"mailbox_id": "invalid"}).status_code, 400)

    # 功能：验证部分完成的规则任务可在显式重新分析时补齐评分。
    # 输入：已有成功分析，测试中移除其评分并重新入队。
    # 输出：复用原分析并新增评分，任务正常完成。
    # 逻辑：区分分析缓存命中和完整产出，避免永久缺分。
    # 约束：删除评分仅用于构造测试边界；生产代码不自动删除或重试。
    def test_explicit_reanalysis_completes_missing_score(self):
        company = self.submit()
        rules.run_company(self.user, company.pk)
        analysis = Analysis.objects.get()
        analysis.scores.all().delete()
        response = self.browser.post(f"/api/v1/companies/{company.pk}/analyze/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Analysis.objects.count(), 1)
        self.assertTrue(analysis.scores.exists())
