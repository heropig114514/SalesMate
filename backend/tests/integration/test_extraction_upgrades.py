"""职责：验证后端历史 L1 显式升级及 v7 方向契约。
实现：真实隔离 PostgreSQL 和并发连接验证行锁；在事务内部注入故障，模型由固定事实替身提供。
关联：extraction_upgrades、lineage、serializers；不读取真实邮箱或改写本机业务数据。
目录：
- ExtractionUpgradeTests：升级授权、版本、失败及来源回归测试。
- ExtractionUpgradeTests.setUp：准备旧版业务来源和两个员工。
- ExtractionUpgradeTests.test_preview_and_queue_are_owner_scoped：验证只读、权限、乐观锁与幂等排队。
- ExtractionUpgradeTests.test_upgrade_keeps_history_and_uses_stored_body：验证持久原文、历史保留和分析解锁。
- ExtractionUpgradeTests.test_upgraded_unknown_intent_requires_review：验证新抽取更新机器分类而非保留过时业务判断。
- ExtractionUpgradeTests.test_failure_requires_explicit_retry：验证失败不自动重试。
- ExtractionUpgradeTests.test_changed_review_rejects_inflight_result：验证人工改变决定时拒绝旧结果。
- ExtractionUpgradeTests.test_invalid_direction_rejected_at_submission_and_completion：验证写入及修复的方向边界。
- ExtractionUpgradeTests.test_old_analysis_is_explicitly_blocked：验证旧事实分析入口给出可操作错误。
- ExtractionUpgradeTests.add_email：构造同客户的新旧版本或非业务邮件对照。
- ExtractionUpgradeTests.test_concurrent_queue_serializes_revision：验证真实并发 POST 仅排队一次并拒绝旧版本。
- ExtractionUpgradeTests.test_concurrent_queue_serializes_revision.submit：通过独立连接提交相同版本升级请求。
- ExtractionUpgradeTests.test_queue_rolls_back_partial_batch：验证第二封排队故障回滚整个批次及失败状态变更。
- ExtractionUpgradeTests.test_queue_rolls_back_partial_batch.fail_second：在真实修复写入后注入批次中途异常。
- ExtractionUpgradeTests.test_queue_rolls_back_revision_and_analysis：验证后置调度失败回滚任务、公司版本与分析队列。
- ExtractionUpgradeTests.test_queue_rolls_back_revision_and_analysis.fail_after_schedule：在真实分析调度写入后注入异常。
- ExtractionUpgradeTests.test_expired_lease_rejects_result_without_retry：验证租期等于当前时刻时拒绝、过期清理不重试。
- ExtractionUpgradeTests.test_replaced_source_rejects_result：独立覆盖原抽取身份被替换的完成校验。
- ExtractionUpgradeTests.test_duplicate_completion_has_no_side_effects：验证重复完成不会再次写抽取或推进版本。
- ExtractionUpgradeTests.test_mixed_batch_blocks_until_all_repairs_complete：验证混合版本批次只升级目标，部分失败阻塞分析。
- ExtractionUpgradeTests.test_latest_version_and_empty_batch_are_noops：验证空批次及仅剩当前版本时不产生写入。
- ExtractionUpgradeTests.test_upgrade_http_rejects_missing_version_and_payload：验证缺失版本和夹带参数的 HTTP 边界。
变量索引：
- 无
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient
from rest_framework.exceptions import ValidationError

from apps.crm import ingestion, jobs, rules
from apps.crm.access import Conflict
from apps.crm.classification import review_email
from apps.crm.extraction_upgrades import queue_upgrades
from apps.crm.durable_models import ExtractionRepair
from apps.crm.lineage import claim_repair, complete_repair, request_repair, run_repair, schedule_analysis
from apps.crm.models import Email, Extraction, Mailbox


# 功能：检验版本升级的持久边界及跨账户隔离。
# 逻辑：每个测试构造一个旧版业务来源，所有模型调用用可核验固定事实替代。
# 约束：真实事务测试仅使用隔离测试库，测试通过不代表模型采购阶段语义正确。
@override_settings(ANALYSIS_PROVIDER="agent")
class ExtractionUpgradeTests(TransactionTestCase):
    # 功能：准备旧事实、已存正文、会话身份与新事实候选。
    # 输入：无外部参数，由测试运行器调用。
    # 输出：实例保存 owner、other、mailbox、email、source、facts、url 和 client。
    # 逻辑：先按真实 v7 接口入库，再只在测试库把抽取改为历史 v6，模拟升级前存量。
    # 约束：不通过放宽生产入口来制造旧数据。
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="upgrade-owner")
        self.other = get_user_model().objects.create_user(username="upgrade-other")
        self.mailbox = Mailbox.objects.create(owner=self.owner, address="sales@upgrade.example")
        self.payload = rules.extract_email(self.mailbox, "buyer@client.example", "询价", "需求：设备\n数量：2 台", "old-one")
        ingestion.submit_emails(self.owner, [self.payload])
        self.email = Email.objects.get(pk=self.payload["dedupe_key"])
        self.source = self.email.extractions.get()
        self.facts = deepcopy(self.source.facts)
        self.source.prompt_version = "extract-v6"
        self.source.facts["intent_hint"] = "purchase_inquiry"
        self.source.save(update_fields=["prompt_version", "facts"])
        self.url = f"/api/v1/companies/{self.email.company_id}/extraction-upgrade/"
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    # 功能：核验升级预览只读及排队的账户、版本边界。
    # 输入：两个会话身份及当前/旧 If-Match。
    # 输出：越权 404、旧版本 409、正常排队 202，重复显式请求复用活动任务。
    # 逻辑：比较修复行数、公司 revision 和摘要计数。
    # 约束：不启动 Worker，不调用模型。
    def test_preview_and_queue_are_owner_scoped(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["incompatible_emails"], 1)
        self.assertFalse(self.email.repairs.exists())
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url, {}, format="json", HTTP_IF_MATCH=response["ETag"]).status_code, 404)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.post(self.url, {}, format="json", HTTP_IF_MATCH="0").status_code, 409)
        queued = self.client.post(self.url, {}, format="json", HTTP_IF_MATCH=response["ETag"])
        self.assertEqual(queued.status_code, 202, queued.data)
        self.assertEqual(queued.data["created"], 1)
        repeated = self.client.post(self.url, {}, format="json", HTTP_IF_MATCH=queued["ETag"])
        self.assertEqual(repeated.data["reused"], 1)
        self.assertEqual(self.email.repairs.count(), 1)
        self.assertEqual(jobs.claim(self.owner, 1, 120), [])

    # 功能：验证升级使用持久正文且保留旧来源。
    # 输入：显式队列及固定 v7 模型候选。
    # 输出：新旧两份抽取，旧事实不变，原文/人工决定不变，分析任务可领取。
    # 逻辑：真实 Worker 单元读取邮件正文，模拟提供者仅代替外部模型。
    # 约束：不访问 Gmail，模型替身不证明真实抽取质量。
    def test_upgrade_keeps_history_and_uses_stored_body(self):
        old_facts = deepcopy(self.source.facts)
        queue_upgrades(self.owner, self.email.company_id, self.email.company.revision)
        with patch("apps.crm.lineage.bailian_extraction_provider", return_value=self.facts) as model:
            self.assertTrue(run_repair(self.owner))
        model.assert_called_once_with(self.payload["subject"], self.payload["body_text"], direction="inbound")
        self.source.refresh_from_db()
        self.email.refresh_from_db()
        self.assertEqual(self.source.facts, old_facts)
        self.assertEqual(self.email.extractions.count(), 2)
        self.assertEqual(self.email.extractions.order_by("-pk").first().prompt_version, "extract-v7")
        self.assertEqual(self.email.review_status, "")
        self.assertEqual(self.email.payload["body_text"], self.payload["body_text"])
        self.assertEqual(self.client.get(self.url).data["incompatible_emails"], 0)
        self.assertEqual(len(jobs.claim(self.owner, 1, 120)), 1)

    # 功能：验证升级后没有采购阶段的机器来源重新进入复核。
    # 输入：旧版自动业务来源及意图为 null 的有效 v7 事实。
    # 输出：新抽取保留，分类为 needs_review；没有可领取分析任务。
    # 逻辑：通过实际完成服务更新分类；未伪造人工确认业务记录。
    # 约束：不会丢弃旧来源，也不把未知阶段猜成最低阶段。
    def test_upgraded_unknown_intent_requires_review(self):
        queue_upgrades(self.owner, self.email.company_id, self.email.company.revision)
        facts = deepcopy(self.facts)
        facts["intent_hint"], facts["intent_evidences"] = None, []
        complete_repair(claim_repair(self.owner), facts)
        self.email.refresh_from_db()
        self.assertEqual(self.email.business_classification, "needs_review")
        self.assertEqual(self.email.extractions.count(), 2)
        self.assertEqual(jobs.claim(self.owner, 1, 120), [])

    # 功能：验证失败结果持久化且只由明确 POST 重排。
    # 输入：模型异常以及后续显式升级请求。
    # 输出：单次模型调用、失败摘要和阻塞分析；显式重排后产生新任务。
    # 逻辑：先执行两次 Worker 单元，第二次不能再次调用模型。
    # 约束：不注入重试或降级算法。
    def test_failure_requires_explicit_retry(self):
        queue_upgrades(self.owner, self.email.company_id, self.email.company.revision)
        with patch("apps.crm.lineage.bailian_extraction_provider", side_effect=RuntimeError("test failure")) as model:
            self.assertTrue(run_repair(self.owner))
            self.assertFalse(run_repair(self.owner))
        self.assertEqual(model.call_count, 1)
        preview = self.client.get(self.url)
        self.assertEqual(preview.data["repairs"]["failed"], 1)
        self.assertEqual(jobs.claim(self.owner, 1, 120), [])
        result = self.client.post(self.url, {}, format="json", HTTP_IF_MATCH=preview["ETag"])
        self.assertEqual(result.data["created"], 1)

    # 功能：验证在途升级不能覆盖新的人工复核决定。
    # 输入：已领取任务及后续人工确认非业务操作。
    # 输出：旧结果抛 Conflict，历史抽取仍为唯一一份。
    # 逻辑：用真实分类事务使 review_revision 和修复状态改变。
    # 约束：没有真实并发线程，验证的是同样的交错执行顺序。
    def test_changed_review_rejects_inflight_result(self):
        queue_upgrades(self.owner, self.email.company_id, self.email.company.revision)
        repair = claim_repair(self.owner)
        review_email(self.owner, self.email.pk, "confirmed_non_business", self.email.review_revision)
        with self.assertRaises(Conflict):
            complete_repair(repair, self.facts)
        self.assertEqual(self.email.extractions.count(), 1)

    # 功能：验证外发或未知方向不能提供客户采购阶段。
    # 输入：带阶段的外发/未知邮件及冻结为外发方向的旧业务来源。
    # 输出：普通入库与修复保存均拒绝错误阶段，不产生新抽取。
    # 逻辑：覆盖 serializer 与绕过 HTTP 的 Worker 结果保存边界。
    # 约束：不将无效阶段静默改为 null。
    def test_invalid_direction_rejected_at_submission_and_completion(self):
        for direction in ("outbound", "unknown"):
            payload = deepcopy(self.payload)
            payload["direction"] = direction
            with self.assertRaises(ValidationError):
                ingestion.submit_emails(self.owner, [payload])
        self.email.direction = "outbound"
        self.email.save(update_fields=["direction"])
        queue_upgrades(self.owner, self.email.company_id, self.email.company.revision)
        repair = claim_repair(self.owner)
        with self.assertRaises(ValidationError):
            complete_repair(repair, self.facts)
        self.assertEqual(self.email.extractions.count(), 1)

    # 功能：验证旧版事实不会直接入队一个注定不兼容的 Agent 分析。
    # 输入：含 v6 来源的客户分析 POST。
    # 输出：409 和明确升级入口提示。
    # 逻辑：经真实会话接口调用，保留原始数据。
    # 约束：不验证前端是否已经展示该入口。
    def test_old_analysis_is_explicitly_blocked(self):
        response = self.client.post(f"/api/v1/companies/{self.email.company_id}/analyze/")
        self.assertEqual(response.status_code, 409)
        self.assertIn("extraction-upgrade", str(response.data))


    # 功能：构造同客户的版本及分类对照邮件。
    # 输入：`key` 为消息标识；`legacy` 控制测试库旧版本，`business` 控制是否保留采购阶段。
    # 输出：已持久化 Email 实例。
    # 逻辑：真实入库后仅在测试库修改历史版本或机器分类，正文与已有样本保持相同。
    # 约束：不改变生产接口接受的版本；返回对象与原样本属于同一 owner 和客户。
    def add_email(self, key, *, legacy=True, business=True):
        payload = rules.extract_email(self.mailbox, "buyer@client.example", "询价", "需求：设备\n数量：2 台", key)
        if not business:
            payload["facts"]["intent_hint"], payload["facts"]["intent_evidences"] = None, []
        ingestion.submit_emails(self.owner, [payload])
        email = Email.objects.get(pk=payload["dedupe_key"])
        if legacy:
            extraction = email.extractions.get()
            extraction.prompt_version = "extract-v6"
            extraction.save(update_fields=["prompt_version"])
        return email

    # 功能：验证同时提交相同 ETag 时由数据库行锁保证单次排队。
    # 输入：两个独立线程、连接和会话客户端；屏障同步发送同一客户版本。
    # 输出：一条 202、一条 409、单个修复及一次公司版本推进。
    # 逻辑：执行真实 HTTP 和 PostgreSQL 事务，不替换行锁或权限检查。
    # 约束：不声明穷尽所有调度；线程退出关闭连接，屏障设超时避免测试挂起。
    def test_concurrent_queue_serializes_revision(self):
        company = self.email.company
        revision, barrier = company.revision, Barrier(2)

        # 功能：通过独立连接提交相同版本的客户升级。
        # 输入：`index` 为并发任务编号；读取闭包中的 owner、revision 和屏障。
        # 输出：HTTP 状态码。
        # 逻辑：先创建线程独立 APIClient，再同步调用真实 POST。
        # 约束：每个线程自行关闭连接，不共享客户端状态。
        def submit(index):
            try:
                client = APIClient()
                client.force_authenticate(get_user_model().objects.get(pk=self.owner.pk))
                barrier.wait(timeout=10)
                response = client.post(self.url, {}, format="json", HTTP_IF_MATCH=str(revision))
                return response.status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(submit, range(2)))
        self.assertEqual(sorted(statuses), [202, 409])
        company.refresh_from_db()
        self.assertEqual(company.revision, revision + 1)
        self.assertEqual(self.email.repairs.count(), 1)
        self.assertEqual(self.email.repairs.get().status, "pending")

    # 功能：验证多封排队发生部分写入后整个事务回滚。
    # 输入：两个旧来源，其中首封已有 failed 修复；第二次真实排队后抛故障。
    # 输出：修复行、失败状态、公司版本及分析队列都恢复原值。
    # 逻辑：模拟故障发生点而非模拟事务，第一封创建和旧失败状态变更均实际执行。
    # 约束：不修改事务隔离级别；仅注入可重复的测试异常。
    def test_queue_rolls_back_partial_batch(self):
        second = self.add_email("old-two")
        failed = ExtractionRepair.objects.create(email=self.email, source=self.source,
            review_revision=self.email.review_revision, status="failed", error="original_failure")
        company = self.email.company
        company.refresh_from_db()
        revision = company.revision
        before_jobs = list(company.jobs.order_by("pk").values())
        calls = []

        # 功能：在真实排队产生写入后触发第二项故障。
        # 输入：`email` 为当前邮件，`upgrade` 为调用方显式传入的模式。
        # 输出：第一项返回原服务结果，第二项抛 RuntimeError。
        # 逻辑：先调用原 request_repair，确保测试覆盖已发生写入的回滚。
        # 约束：不捕获异常，外层真实 atomic 必须处理回滚。
        def fail_second(email, *, upgrade):
            result = request_repair(email, upgrade=upgrade)
            calls.append(email.pk)
            if len(calls) == 2:
                raise RuntimeError("injected after second repair write")
            return result

        with patch("apps.crm.extraction_upgrades.request_repair", side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, "second repair"):
                queue_upgrades(self.owner, company.pk, revision)
        self.assertEqual(set(calls), {self.email.pk, second.pk})
        failed.refresh_from_db()
        company.refresh_from_db()
        self.assertEqual((failed.status, failed.error), ("failed", "original_failure"))
        self.assertEqual(ExtractionRepair.objects.count(), 1)
        self.assertEqual(company.revision, revision)
        self.assertEqual(list(company.jobs.order_by("pk").values()), before_jobs)

    # 功能：验证调度尾部故障也能回滚整个升级事务。
    # 输入：一个旧来源；在真正更新分析队列后抛异常。
    # 输出：无新修复，公司 revision 及原分析记录完全不变。
    # 逻辑：覆盖公司版本已保存且 schedule_analysis 已执行的后置异常路径。
    # 约束：不是仅测试前置参数失败，不修改业务实现。
    def test_queue_rolls_back_revision_and_analysis(self):
        company = self.email.company
        revision = company.revision
        before_jobs = list(company.jobs.order_by("pk").values())

        # 功能：完成真实分析调度后注入故障。
        # 输入：`company` 为当前事务持锁的客户。
        # 输出：总是抛出 RuntimeError。
        # 逻辑：先调用原调度以产生数据库变更，再交给外层 atomic 回滚。
        # 约束：不吞异常，不模拟数据库写入结果。
        def fail_after_schedule(company):
            schedule_analysis(company)
            raise RuntimeError("injected after analysis scheduling")

        with patch("apps.crm.extraction_upgrades.schedule_analysis", side_effect=fail_after_schedule):
            with self.assertRaisesRegex(RuntimeError, "analysis scheduling"):
                queue_upgrades(self.owner, company.pk, revision)
        company.refresh_from_db()
        self.assertEqual(company.revision, revision)
        self.assertFalse(self.email.repairs.exists())
        self.assertEqual(list(company.jobs.order_by("pk").values()), before_jobs)

    # 功能：验证精确租期边界拒绝迟到完成，并将超时显式记为失败。
    # 输入：已领取任务，固定当前时刻等于 lease_until。
    # 输出：完成抛 Conflict，无新抽取；后续领取将其置 failed，且不自动建重试任务。
    # 逻辑：仅固定时钟，不修改 600 秒租期；分别检查完成与过期清理分支。
    # 约束：不真实等待租期，不把超时当作成功或自动续期。
    def test_expired_lease_rejects_result_without_retry(self):
        queue_upgrades(self.owner, self.email.company_id, self.email.company.revision)
        repair = claim_repair(self.owner)
        company = self.email.company
        company.refresh_from_db()
        revision = company.revision
        with patch("apps.crm.lineage.timezone.now", return_value=repair.lease_until):
            with self.assertRaises(Conflict):
                complete_repair(repair, self.facts)
            self.assertIsNone(claim_repair(self.owner))
            self.assertIsNone(claim_repair(self.owner))
        repair.refresh_from_db()
        company.refresh_from_db()
        self.assertEqual((repair.status, repair.error), ("failed", "repair_lease_expired"))
        self.assertEqual(company.revision, revision)
        self.assertEqual(self.email.extractions.count(), 1)
        self.assertEqual(self.email.repairs.count(), 1)
        self.assertEqual(jobs.claim(self.owner, 1, 120), [])

    # 功能：独立验证来源身份被替换时不能提交旧模型结果。
    # 输入：状态与租期有效的任务，随后在测试库插入新的当前抽取。
    # 输出：旧完成请求抛 Conflict，不写第三份抽取，不改变版本或任务状态。
    # 逻辑：仅改变来源身份，排除其他拒绝条件对该分支的掩盖。
    # 约束：直接建模只用于故障注入，不代表生产允许绕过入库事务。
    def test_replaced_source_rejects_result(self):
        queue_upgrades(self.owner, self.email.company_id, self.email.company.revision)
        repair = claim_repair(self.owner)
        current = Extraction.objects.create(email=self.email, prompt_version="extract-v7", status="completed",
                                             facts=self.facts, error=None)
        company = self.email.company
        company.refresh_from_db()
        revision = company.revision
        with self.assertRaises(Conflict):
            complete_repair(repair, self.facts)
        repair.refresh_from_db()
        company.refresh_from_db()
        self.assertEqual(repair.status, "running")
        self.assertEqual(company.revision, revision)
        self.assertEqual(self.email.extractions.count(), 2)
        self.assertEqual(self.email.extractions.order_by("-pk").first().pk, current.pk)

    # 功能：验证成功任务再次完成时不能重复产生副作用。
    # 输入：同一个已领取任务及相同 v7 事实，连续提交两次完成。
    # 输出：第二次抛 Conflict；新旧事实共两份，版本及分析队列不再变化。
    # 逻辑：第一次真实提交成功后捕获数据库状态，再比较拒绝后的全部相关写入。
    # 约束：保持既定重复完成失败语义，不将拒绝改为隐式成功。
    def test_duplicate_completion_has_no_side_effects(self):
        queue_upgrades(self.owner, self.email.company_id, self.email.company.revision)
        repair = claim_repair(self.owner)
        complete_repair(repair, self.facts)
        company = self.email.company
        company.refresh_from_db()
        revision = company.revision
        before_jobs = list(company.jobs.order_by("pk").values())
        with self.assertRaises(Conflict):
            complete_repair(repair, self.facts)
        company.refresh_from_db()
        repair.refresh_from_db()
        self.assertEqual(repair.status, "completed")
        self.assertEqual(company.revision, revision)
        self.assertEqual(self.email.extractions.count(), 2)
        self.assertEqual(list(company.jobs.order_by("pk").values()), before_jobs)

    # 功能：验证新旧版本混合批次只升级业务旧来源，全部修复完成前阻塞分析。
    # 输入：两封旧业务、一封 v7、一封待复核邮件；第二封旧来源模型失败一次。
    # 输出：仅两封旧业务入队；部分成功/失败时仍阻塞，显式重试完成后才能领取分析。
    # 逻辑：运行真实 Worker 单元并在模型边界提供固定成功或异常，最后验证完成批次是无写入操作。
    # 约束：不自动重试、不处理未确认的复核邮件，不修改既有新版本数据。
    def test_mixed_batch_blocks_until_all_repairs_complete(self):
        second = self.add_email("old-two")
        current = self.add_email("current", legacy=False)
        review = self.add_email("review", business=False)
        company = self.email.company
        company.refresh_from_db()
        queued = queue_upgrades(self.owner, company.pk, company.revision)
        self.assertEqual(queued["created"], 2)
        self.assertEqual(queued["incompatible_emails"], 2)
        self.assertEqual(set(ExtractionRepair.objects.values_list("email_id", flat=True)), {self.email.pk, second.pk})
        with patch("apps.crm.lineage.bailian_extraction_provider", return_value=self.facts):
            self.assertTrue(run_repair(self.owner))
        self.assertEqual(jobs.claim(self.owner, 1, 120), [])
        with patch("apps.crm.lineage.bailian_extraction_provider", side_effect=RuntimeError("one failed")):
            self.assertTrue(run_repair(self.owner))
        self.assertEqual(jobs.claim(self.owner, 1, 120), [])
        preview = self.client.get(self.url)
        self.assertEqual(preview.data["incompatible_emails"], 1)
        self.assertEqual(preview.data["repairs"], {"pending": 0, "running": 0, "failed": 1})
        retry = self.client.post(self.url, {}, format="json", HTTP_IF_MATCH=preview["ETag"])
        self.assertEqual((retry.data["created"], retry.data["reused"]), (1, 0))
        with patch("apps.crm.lineage.bailian_extraction_provider", return_value=self.facts):
            self.assertTrue(run_repair(self.owner))
        self.assertFalse(current.repairs.exists())
        self.assertFalse(review.repairs.exists())
        self.assertEqual(current.extractions.count(), 1)
        self.assertEqual(review.extractions.count(), 1)
        company.refresh_from_db()
        revision = company.revision
        before_jobs = list(company.jobs.order_by("pk").values())
        repair_count = ExtractionRepair.objects.count()
        noop = queue_upgrades(self.owner, company.pk, revision)
        self.assertEqual((noop["created"], noop["reused"], noop["revision"]), (0, 0, revision))
        self.assertEqual(noop["incompatible_emails"], 0)
        self.assertEqual(ExtractionRepair.objects.count(), repair_count)
        self.assertEqual(list(company.jobs.order_by("pk").values()), before_jobs)
        self.assertEqual(len(jobs.claim(self.owner, 1, 120)), 1)

    # 功能：验证无待升级来源时不会推进版本或创建队列。
    # 输入：空业务集合，再恢复为仅含当前 v7 的业务集合。
    # 输出：两次均 created/reused 为零，版本、原分析记录保持不变。
    # 逻辑：分别走空循环和跳过当前版本分支，并核对最新版本计数。
    # 约束：直接修改样本分类只用于边界构造，不模拟生产人工操作。
    def test_latest_version_and_empty_batch_are_noops(self):
        company = self.email.company
        revision = company.revision
        before_jobs = list(company.jobs.order_by("pk").values())
        self.email.business_classification = "needs_review"
        self.email.save(update_fields=["business_classification"])
        empty = queue_upgrades(self.owner, company.pk, revision)
        self.assertEqual(empty["versions"], {})
        self.email.business_classification = "business"
        self.email.save(update_fields=["business_classification"])
        Extraction.objects.create(email=self.email, prompt_version="extract-v7", status="completed", facts=self.facts)
        current = queue_upgrades(self.owner, company.pk, revision)
        self.assertEqual(current["versions"], {"extract-v7": 1})
        for result in (empty, current):
            self.assertEqual((result["created"], result["reused"], result["revision"]), (0, 0, revision))
        company.refresh_from_db()
        self.assertEqual(company.revision, revision)
        self.assertFalse(self.email.repairs.exists())
        self.assertEqual(list(company.jobs.order_by("pk").values()), before_jobs)

    # 功能：验证升级 API 不接受缺失版本或额外模型/事实参数。
    # 输入：缺少 If-Match 的空请求，或携带未经支持参数的请求。
    # 输出：缺少版本或含额外参数均为 400，修复与公司 revision 保持原状。
    # 逻辑：通过真实 HTTP 入参边界，核验拒绝不产生写入。
    # 约束：不更改 HTTP 状态码约定或放宽权限。
    def test_upgrade_http_rejects_missing_version_and_payload(self):
        revision = self.email.company.revision
        self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 400)
        response = self.client.post(self.url, {"model": "override"}, format="json", HTTP_IF_MATCH=str(revision))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.email.repairs.exists())
        self.email.company.refresh_from_db()
        self.assertEqual(self.email.company.revision, revision)
