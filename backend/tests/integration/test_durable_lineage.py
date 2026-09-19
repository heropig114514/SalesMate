"""职责：验证原文持久恢复、增量处理和血缘自动纠错。
实现：使用隔离 PostgreSQL、真实业务事务与模拟 Gmail/LLM，检查持久状态和模型调用次数。
关联：durable_sync、lineage、classification、results；不连接真实邮箱或模型。
目录：
- DurableLineageTests：跨连接持久化及来源回归测试。
- DurableLineageTests.setUp：建立员工、邮箱和模拟 HTTP 写入端。
- DurableLineageTests.payload：构造有原文证据的邮件。
- DurableLineageTests.submit：将模拟 HTTP 提交送入真实业务事务。
- DurableLineageTests.start：创建并领取独立批次。
- DurableLineageTests.store：保存一封测试业务邮件。
- DurableLineageTests.fail_after_raw：在模型边界核验原文已提交后模拟失败。
- DurableLineageTests.test_no_purchase_stage_requires_review：覆盖有实质更新但无采购阶段的邮件。
- DurableLineageTests.test_new_purchase_stage_is_saved_as_business：新版采购阶段邮件可保存为业务邮件。
- DurableLineageTests.test_new_mail_without_purchase_stage_waits_for_review：新版无采购阶段的来信进入复核，阶段与证据不一致时拒绝。
- DurableLineageTests.test_old_l1_submission_is_rejected：旧抽取版本和意图值不能进入新邮件接口。
- DurableLineageTests.test_repair_preserves_source_and_unblocks_analysis：人工补抽取保留历史并解除画像阻塞。
- DurableLineageTests.test_stale_repair_is_rejected：模型返回期间人工决定改变时拒绝旧结果。
- DurableLineageTests.test_failed_repair_requires_explicit_retry：失败持久可见且明确重试。
- DurableLineageTests.test_lineage_recomputes_remaining_and_preserves_unrelated：删除来源后重算且不影响其他公司。
- DurableLineageTests.test_restore_same_input_keeps_revision_history：恢复相同输入时保留独立快照。
- DurableLineageTests.test_raw_survives_failure_and_incremental_reuses_results：原文在模型前落库，失败重试和后续同步复用缓存。
- DurableLineageTests.test_backfill_resumes_page_and_processes_more_than_twenty：历史分页失败后恢复而非重读第一页。
- DurableLineageTests.test_checkpoint_rolls_back_discovery_on_database_error：检查点失败不推进游标或丢失 ID。
- DurableLineageTests.test_old_cursor_protocol_fails_loudly：旧协议游标异常不伪装成功。
- DurableLineageTests.test_completed_l1_retries_submission_without_model：提交失败后只重交持久 L1 输出。
- DurableLineageTests.test_worker_dispatches_repair_without_sync_and_once_exits：无同步批次仍消费修复，失败后单轮正常退出。
变量索引：
- 无
"""
from copy import deepcopy
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from agent.workflows.analysis_input import build_analysis_input
from agent.workflows.gmail_sync import _get_sync_state, _save_sync_state
from apps.crm import ingestion, jobs, rules, selectors
from apps.crm.access import Conflict
from apps.crm.classification import review_data, review_email
from apps.crm.durable_models import ExtractionRepair, SnapshotInvalidation, SnapshotSource, StoredMessage, SyncCheckpoint
from apps.crm.durable_sync import checkpoint, sync_persisted
from apps.crm.lineage import claim_repair, complete_repair, run_repair
from apps.crm.models import Email, GmailCredential, Mailbox
from apps.crm.processing import claim_run, finish_run, request_run, retry_run
from apps.crm.results import cached_analysis


# 功能：验证跨阶段数据库恢复与来源变更不变量。
# 逻辑：TransactionTestCase 允许真实线程连接观察已提交原文；Gmail/模型边界全部模拟。
# 约束：模拟通过不证明真实服务质量；不修改本机业务数据。
class DurableLineageTests(TransactionTestCase):
    # 功能：创建隔离身份及写入适配器。
    # 输入：无外部参数，由测试框架调用。
    # 输出：owner、mailbox、backend 实例状态。
    # 逻辑：HTTP mock 委托真实 ingestion，不绕过业务验证。
    # 约束：凭证为合成占位，不可用于网络授权。
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="durable-owner")
        self.mailbox = Mailbox.objects.create(owner=self.owner, address="sales@durable.example")
        GmailCredential.objects.create(mailbox=self.mailbox, credentials={"mock": True})
        self.backend = Mock()
        self.backend.submit_emails.side_effect = self.submit

    # 功能：生成可核验事实及邮件原文。
    # 输入：`key` 为消息 ID，`sender` 为合成发件地址。
    # 输出：标准提交字典。
    # 逻辑：测试固定标签正文，事实证据来自该正文。
    # 约束：不使用规则作为运行时模型失败回退。
    def payload(self, key, sender="buyer@customer.example"):
        return rules.extract_email(self.mailbox, sender, "询价", "需求：设备\n数量：2 台", key)

    # 功能：模拟 Agent HTTP 写入传输。
    # 输入：`submissions` 为真实 L1 生成的数组。
    # 输出：真实保存结果的统计。
    # 逻辑：补上适配器负责的邮箱身份与 gmail_real 来源，再调用原事务服务。
    # 约束：只绑定测试员工，原文和事实仍经过 serializer 校验。
    def submit(self, submissions):
        result = ingestion.submit_emails(self.owner, [{**item, "mailbox_id": str(self.mailbox.pk), "source": "gmail_real"} for item in submissions])
        return {"created_count": sum(item["status"] == "created" for item in result), "updated_count": sum(item["status"] == "updated" for item in result), "duplicate_count": sum(item["status"] == "duplicate" for item in result), "affected_company_ids": [item["company_id"] for item in result]}

    # 功能：创建已领取批次。
    # 输入：无外部参数，读取当前测试员工和邮箱。
    # 输出：带租约的运行批次。
    # 逻辑：通过真实排队和领取服务。
    # 约束：前一批次必须已结束。
    def start(self):
        request_run(self.owner, self.mailbox.pk)
        return claim_run(self.owner)

    # 功能：保存一封有明确业务事实的测试邮件。
    # 输入：`key` 为消息标识，`sender` 为公司分组依据。
    # 输出：Email 实例。
    # 逻辑：调用真实入库和分类逻辑。
    # 约束：不生成模型画像。
    def store(self, key, sender="buyer@customer.example"):
        data = self.payload(key, sender)
        ingestion.submit_emails(self.owner, [data])
        return Email.objects.get(pk=data["dedupe_key"])

    # 功能：验证进入模型前原文可由独立线程连接读取。
    # 输入：`subject`、`body` 为模型实际输入。
    # 输出：抛出模拟模型异常。
    # 逻辑：先断言持久正文与输入一致且业务邮件尚未落库，关闭模拟提供者在线程内打开的数据库连接再失败。
    # 约束：仅供 mock provider；真实提供者不访问 Django，不调用真实模型。
    def fail_after_raw(self, subject, body):
        try:
            raw = StoredMessage.objects.get().raw
            self.assertEqual((raw["subject"], raw["eligible_body_text"]), (subject, body))
            self.assertFalse(Email.objects.exists())
        finally:
            connections.close_all()
        raise RuntimeError("mock model failure after durable raw")

    # 功能：验证无采购阶段的来信进入复核。
    # 输入：无外部参数，构造有事实更新但无采购阶段的来信。
    # 输出：needs_review 且无画像任务。
    # 逻辑：有无实质更新不影响无阶段来信的人工复核。
    # 约束：不调用模型或外部邮箱。
    def test_no_purchase_stage_requires_review(self):
        data = self.payload("no-purchase-stage")
        data["facts"]["intent_hint"] = None
        data["facts"]["intent_evidences"] = []
        ingestion.submit_emails(self.owner, [data])
        email = Email.objects.get(pk=data["dedupe_key"])
        self.assertEqual(email.business_classification, "needs_review")
        self.assertFalse(email.company.jobs.exists())

    # 功能：验证新版采购阶段可通过真实入库校验。
    # 输入：带可定位数量证据的 extract-v7 合成邮件。
    # 输出：邮件归为业务且抽取版本保持 v7。
    # 逻辑：经 submit_emails 保存后读取分类与 Extraction。
    # 约束：不调用模型或外部邮箱。
    def test_new_purchase_stage_is_saved_as_business(self):
        data = self.payload("purchase-stage")
        data["extract_prompt_version"] = "extract-v7"
        data["facts"]["intent_hint"] = "L3 Qualified"
        data["facts"]["intent_evidences"] = ["数量：2 台"]
        ingestion.submit_emails(self.owner, [data])
        email = Email.objects.get(pk=data["dedupe_key"])
        self.assertEqual(email.business_classification, "business")
        self.assertEqual(email.extractions.get().prompt_version, "extract-v7")

    # 功能：验证无阶段来信的复核与阶段证据约束。
    # 输入：intent_hint 为 null 的 v7 邮件及缺少证据的 v7 阶段邮件。
    # 输出：前者进入待复核且不入分析队列，后者被拒绝。
    # 逻辑：调用真实邮件写入服务并读取分类和任务状态。
    # 约束：不把 null 直接认定为非业务，也不调用模型。
    def test_new_mail_without_purchase_stage_waits_for_review(self):
        data = self.payload("no-stage")
        data["extract_prompt_version"] = "extract-v7"
        data["facts"]["intent_hint"] = None
        data["facts"]["intent_evidences"] = []
        ingestion.submit_emails(self.owner, [data])
        email = Email.objects.get(pk=data["dedupe_key"])
        self.assertEqual(email.business_classification, "needs_review")
        self.assertFalse(email.company.jobs.exists())

        invalid = self.payload("missing-stage-evidence")
        invalid["extract_prompt_version"] = "extract-v7"
        invalid["facts"]["intent_hint"] = "L3 Qualified"
        invalid["facts"]["intent_evidences"] = []
        with self.assertRaises(ValidationError):
            ingestion.submit_emails(self.owner, [invalid])

    # 功能：验证新邮件接口拒绝旧版 L1 格式。
    # 输入：旧抽取版本及旧版意图枚举的合成邮件。
    # 输出：两种输入都被拒绝且不入库。
    # 逻辑：调用真实入库校验，不触发模型或外部邮箱。
    # 约束：历史迁移文件保持不变。
    def test_old_l1_submission_is_rejected(self):
        old_version = self.payload("old-version")
        old_version["extract_prompt_version"] = "extract-v6"
        with self.assertRaises(ValidationError):
            ingestion.submit_emails(self.owner, [old_version])

        old_intent = self.payload("old-intent")
        old_intent["facts"]["intent_hint"] = "purchase_inquiry"
        with self.assertRaises(ValidationError):
            ingestion.submit_emails(self.owner, [old_intent])
        self.assertFalse(Email.objects.exists())

    # 功能：验证误判修复先补 L1 再执行画像。
    # 输入：无外部参数；规则跳过的合成邮件与模拟 L1 事实。
    # 输出：旧抽取保留、新事实可被真实 Agent L2 消费、公司任务解除阻塞。
    # 逻辑：原文和提示词版本不修改，修复使用独立代次，并通过 Agent 的真实 L2 入口校验。
    # 约束：不把模拟 LLM 返回解释为真实模型已验证。
    def test_repair_preserves_source_and_unblocks_analysis(self):
        data = self.payload("skipped")
        facts = deepcopy(data["facts"])
        facts["intent_hint"] = "L1 Exploring"
        data.update(extract_status="skipped_non_business", facts=None, non_business_hint=True, non_business_reason="命中 no-reply 发件地址规则。")
        ingestion.submit_emails(self.owner, [data])
        email = Email.objects.get(pk=data["dedupe_key"])
        original = deepcopy(email.payload)
        source = email.extractions.get()
        review_email(self.owner, email.pk, "confirmed_business", email.review_revision)
        self.assertEqual(jobs.claim(self.owner, 1, 120), [])
        with patch("apps.crm.lineage.bailian_extraction_provider", return_value=facts) as model:
            self.assertTrue(run_repair(self.owner))
        email.refresh_from_db()
        self.assertEqual(email.payload, original)
        source.refresh_from_db()
        self.assertEqual(source.status, "skipped_non_business")
        self.assertEqual(email.extractions.count(), 2)
        self.assertEqual(selectors.latest_extraction(email).facts, facts)
        grouping, context = selectors.context_pair(email.company)
        backend = Mock()
        backend.get_company_grouping.return_value = grouping
        backend.get_company_context.return_value = context
        result = build_analysis_input(str(email.company_id), backend=backend, clock=timezone.now).to_dict()
        self.assertEqual(result.get("unparsed_message_count"), 0, result)
        model.assert_called_once_with(data["subject"], data["body_text"], direction="inbound")
        self.assertEqual(len(jobs.claim(self.owner, 1, 120)), 1)

    # 功能：拒绝人工决定改变后的旧补抽取结果。
    # 输入：无外部参数；已领取的任务在返回前被确认非业务。
    # 输出：旧结果冲突且不增加抽取记录。
    # 逻辑：检查 repair 状态、人工版本及来源。
    # 约束：模拟时间交错，不依赖线程调度速度。
    def test_stale_repair_is_rejected(self):
        data = self.payload("stale")
        facts = data["facts"]
        data.update(extract_status="failed", facts=None, extract_error="事实抽取失败。")
        ingestion.submit_emails(self.owner, [data])
        email = Email.objects.get(pk=data["dedupe_key"])
        review_email(self.owner, email.pk, "confirmed_business", email.review_revision)
        repair = claim_repair(self.owner)
        email.refresh_from_db()
        review_email(self.owner, email.pk, "confirmed_non_business", email.review_revision)
        with self.assertRaises(Conflict):
            complete_repair(repair, facts)
        self.assertEqual(email.extractions.count(), 1)

    # 功能：验证补抽取失败不会无声重试。
    # 输入：无外部参数；模拟一次模型异常。
    # 输出：失败可见，重复 Worker 不调用模型，再次确认业务后才新建任务。
    # 逻辑：失败任务保留，明确重试创建后继。
    # 约束：不改变原人工版本或事实。
    def test_failed_repair_requires_explicit_retry(self):
        data = self.payload("retry-repair")
        data.update(extract_status="failed", facts=None, extract_error="事实抽取失败。")
        ingestion.submit_emails(self.owner, [data])
        email = Email.objects.get(pk=data["dedupe_key"])
        review_email(self.owner, email.pk, "confirmed_business", email.review_revision)
        with patch("apps.crm.lineage.bailian_extraction_provider", side_effect=RuntimeError("mock")) as model:
            self.assertTrue(run_repair(self.owner))
            self.assertFalse(run_repair(self.owner))
        self.assertEqual(model.call_count, 1)
        email.refresh_from_db()
        self.assertEqual(review_data(email)["repair_status"], "failed")
        review_email(self.owner, email.pk, "confirmed_business", email.review_revision)
        self.assertEqual(email.repairs.filter(status="pending").count(), 1)

    # 功能：验证血缘失效只影响依赖公司并自动重算。
    # 输入：无外部参数；两封同公司邮件和另一公司结果。
    # 输出：受影响画像与分数不可用，重算只含剩余来源，其他公司结果仍有效。
    # 逻辑：用确定性规则执行真实结果持久化，不模拟血缘关系。
    # 约束：历史快照与来源边必须保留。
    def test_lineage_recomputes_remaining_and_preserves_unrelated(self):
        removed = self.store("remove")
        remaining = self.store("remain")
        unrelated = self.store("other", "buyer@other.example")
        rules.run_company(self.owner, removed.company_id)
        rules.run_company(self.owner, unrelated.company_id)
        before, _ = selectors.latest_result(removed.company)
        self.assertEqual(SnapshotSource.objects.filter(snapshot=before.snapshot).count(), 2)
        review_email(self.owner, removed.pk, "confirmed_non_business", removed.review_revision)
        removed.company.refresh_from_db()
        self.assertEqual(selectors.latest_result(removed.company), (None, None))
        self.assertFalse(cached_analysis(removed.company, before.snapshot.input_version)["hit"])
        self.assertIsNotNone(selectors.latest_result(unrelated.company)[0])
        self.assertTrue(SnapshotInvalidation.objects.filter(snapshot=before.snapshot).exists())
        rules.run_company(self.owner, removed.company_id)
        current, _ = selectors.latest_result(removed.company)
        self.assertEqual(current.snapshot.payload["member_dedupe_keys"], [remaining.pk])

    # 功能：验证撤销后恢复同一事实可重新分析。
    # 输入：无外部参数；业务→非业务→业务的完整循环。
    # 输出：相同 input_version 的两个 revision 均保留，新快照有效。
    # 逻辑：回归旧 company/input 唯一约束导致的永久冲突。
    # 约束：原抽取已完成，不调用 LLM 补抽取。
    def test_restore_same_input_keeps_revision_history(self):
        email = self.store("restore")
        rules.run_company(self.owner, email.company_id)
        original, _ = selectors.latest_result(email.company)
        review_email(self.owner, email.pk, "confirmed_non_business", email.review_revision)
        email.refresh_from_db()
        review_email(self.owner, email.pk, "confirmed_business", email.review_revision)
        rules.run_company(self.owner, email.company_id)
        current, _ = selectors.latest_result(email.company)
        self.assertEqual(original.snapshot.input_version, current.snapshot.input_version)
        self.assertNotEqual(original.snapshot.pk, current.snapshot.pk)
        self.assertFalse(ExtractionRepair.objects.exists())

    # 功能：验证原文先于 L1 保存，重试无需重读 Gmail。
    # 输入：无外部参数；一次模型失败、一次明确重试及一次空增量。
    # 输出：原文保留、首次失败、重试只调用模型、后续模型和正文调用均为零。
    # 逻辑：使用真实 L1 校验及提交，检查失败后数据库内容与边界调用次数。
    # 约束：Gmail 和百炼全部 mock，游标值为合成标识。
    def test_raw_survives_failure_and_incremental_reuses_results(self):
        raw = self.payload("raw-first")
        raw["facts"]["intent_hint"] = "L1 Exploring"
        raw["eligible_body_text"] = raw["body_text"]
        run = self.start()
        with patch("apps.crm.durable_sync.resolve_mailbox_address", return_value=self.mailbox.address), patch("apps.crm.durable_sync.get_profile_history_id", return_value="100"), patch("apps.crm.durable_sync.history_page", return_value=(["raw-first"], "")), patch("apps.crm.durable_sync.list_history_message_ids", return_value=([], "101")), patch("apps.crm.durable_sync.read_email", return_value=raw), patch("apps.crm.durable_sync.bailian_extraction_provider", side_effect=self.fail_after_raw):
            result = sync_persisted(run, object(), self.backend)
        finish_run(run.pk, run.lease_token, result)
        self.assertEqual(StoredMessage.objects.get().raw, raw)
        self.assertEqual(StoredMessage.objects.get().status, "failed")
        retry_run(self.owner, run.pk)
        retry = claim_run(self.owner)
        with patch("apps.crm.durable_sync.resolve_mailbox_address", return_value=self.mailbox.address), patch("apps.crm.durable_sync.read_email") as reader, patch("apps.crm.durable_sync.bailian_extraction_provider", return_value=raw["facts"]) as model:
            result = sync_persisted(retry, object(), self.backend)
        finish_run(retry.pk, retry.lease_token, result)
        reader.assert_not_called()
        model.assert_called_once()
        self.assertEqual(StoredMessage.objects.get().status, "completed")
        next_run = self.start()
        with patch("apps.crm.durable_sync.resolve_mailbox_address", return_value=self.mailbox.address), patch("apps.crm.durable_sync.list_history_message_ids", return_value=(["raw-first"], "102")), patch("apps.crm.durable_sync.read_email") as reader, patch("apps.crm.durable_sync.bailian_extraction_provider") as model:
            result = sync_persisted(next_run, object(), self.backend)
        finish_run(next_run.pk, next_run.lease_token, result)
        reader.assert_not_called()
        model.assert_not_called()
        self.assertEqual(SyncCheckpoint.objects.get().cursor, "102")

    # 功能：验证超过二十封历史邮件的可恢复分页。
    # 输入：无外部参数；已有二十封结果，第二页首次失败后成功。
    # 输出：第一页不重读，第二批从持久 page_token 恢复并进入增量。
    # 逻辑：使用完成事实复用隔离网络成本，仍执行真实扫描与数据库检查点。
    # 约束：不放宽单页二十封参数。
    def test_backfill_resumes_page_and_processes_more_than_twenty(self):
        for index in range(21):
            self.store(f"history-{index}")
        run = self.start()
        with patch("apps.crm.durable_sync.resolve_mailbox_address", return_value=self.mailbox.address), patch("apps.crm.durable_sync.get_profile_history_id", return_value="100"), patch("apps.crm.durable_sync.history_page", side_effect=[([f"history-{i}" for i in range(20)], "page-two"), RuntimeError("mock page failure")]), patch("apps.crm.durable_sync.read_email") as reader:
            with self.assertRaises(RuntimeError):
                sync_persisted(run, object(), self.backend)
        finish_run(run.pk, run.lease_token, {"status": "failed", "error": {"code": "page_failed"}})
        reader.assert_not_called()
        self.assertEqual(SyncCheckpoint.objects.get().page_token, "page-two")
        resumed = self.start()
        with patch("apps.crm.durable_sync.resolve_mailbox_address", return_value=self.mailbox.address), patch("apps.crm.durable_sync.history_page", return_value=(["history-20"], "")) as page, patch("apps.crm.durable_sync.list_history_message_ids", return_value=([], "101")), patch("apps.crm.durable_sync.read_email") as reader:
            result = sync_persisted(resumed, object(), self.backend)
        finish_run(resumed.pk, resumed.lease_token, result)
        self.assertEqual(page.call_args.args[1], "page-two")
        reader.assert_not_called()
        self.assertEqual(StoredMessage.objects.count(), 21)
        self.assertTrue(SyncCheckpoint.objects.get().backfill_complete)

    # 功能：验证数据库故障时游标与消息登记原子回滚。
    # 输入：无外部参数；模拟检查点 save 抛出异常。
    # 输出：旧游标保留，新消息未被虚假登记。
    # 逻辑：在登记 ID 后、保存游标前注入失败。
    # 约束：只模拟数据库操作边界，不改变事务实现。
    def test_checkpoint_rolls_back_discovery_on_database_error(self):
        run = self.start()
        checkpoint(run, cursor="100")
        with patch("apps.crm.durable_sync.SyncCheckpoint.save", side_effect=RuntimeError("mock database failure")):
            with self.assertRaises(RuntimeError):
                checkpoint(run, ["not-committed"], cursor="200")
        self.assertEqual(SyncCheckpoint.objects.get().cursor, "100")
        self.assertFalse(StoredMessage.objects.exists())

    # 功能：验证旧 CLI 的游标读写异常向上报告。
    # 输入：无外部参数；模拟已支持协议的读写错误。
    # 输出：明确异常，不回退为首次同步或返回写入成功。
    # 逻辑：直接验证 Agent 边界，不访问真实服务。
    # 约束：未支持协议的历史兼容情况不在此测试中。
    def test_old_cursor_protocol_fails_loudly(self):
        backend = Mock()
        backend.get_sync_state.side_effect = RuntimeError("mock")
        with self.assertRaises(RuntimeError):
            _get_sync_state(backend, str(self.mailbox.pk))
        backend.save_sync_state.side_effect = RuntimeError("mock")
        with self.assertRaises(RuntimeError):
            _save_sync_state(backend, str(self.mailbox.pk), {"version": 1}, "100", [], [])

    # 功能：验证模型已完成而 HTTP 写入失败时不重复调用模型。
    # 输入：无外部参数；一次成功 L1、一次 HTTP 异常和明确重试。
    # 输出：缓存保留完整事实，重试无 Gmail 读取或 LLM 调用且业务邮件保存成功。
    # 逻辑：第一次失败发生在持久 L1 之后、业务入库之前。
    # 约束：模拟 HTTP 中断，不宣称真实外部服务恢复已验证。
    def test_completed_l1_retries_submission_without_model(self):
        raw = self.payload("submission-retry")
        raw["facts"]["intent_hint"] = "L1 Exploring"
        run = self.start()
        self.backend.submit_emails.side_effect = RuntimeError("mock HTTP down")
        with patch("apps.crm.durable_sync.resolve_mailbox_address", return_value=self.mailbox.address), patch("apps.crm.durable_sync.get_profile_history_id", return_value="100"), patch("apps.crm.durable_sync.history_page", return_value=(["submission-retry"], "")), patch("apps.crm.durable_sync.list_history_message_ids", return_value=([], "101")), patch("apps.crm.durable_sync.read_email", return_value=raw), patch("apps.crm.durable_sync.bailian_extraction_provider", return_value=raw["facts"]):
            result = sync_persisted(run, object(), self.backend)
        finish_run(run.pk, run.lease_token, result)
        self.assertFalse(Email.objects.exists())
        self.assertEqual(StoredMessage.objects.get().submission["facts"], raw["facts"])
        self.backend.submit_emails.side_effect = self.submit
        retry_run(self.owner, run.pk)
        retry = claim_run(self.owner)
        with patch("apps.crm.durable_sync.resolve_mailbox_address", return_value=self.mailbox.address), patch("apps.crm.durable_sync.read_email") as reader, patch("apps.crm.durable_sync.bailian_extraction_provider") as model:
            result = sync_persisted(retry, object(), self.backend)
        finish_run(retry.pk, retry.lease_token, result)
        reader.assert_not_called()
        model.assert_not_called()
        self.assertEqual(StoredMessage.objects.get().status, "completed")
        self.assertEqual(Email.objects.count(), 1)

    # 功能：验证独立 Worker 调度人工修复而不要求点击同步。
    # 输入：无外部参数；仅有补抽取与被其阻塞的画像任务。
    # 输出：模型被调用一次、失败持久化，--once 不被阻塞画像无限挂起。
    # 逻辑：调用真实共享 management command，从数据库发现员工，仅替换模型边界。
    # 约束：测试明确开启 agent 模式但不允许真实 Gmail/LLM 调用。
    @override_settings(ANALYSIS_PROVIDER="agent")
    def test_worker_dispatches_repair_without_sync_and_once_exits(self):
        data = self.payload("worker-repair")
        data.update(extract_status="failed", facts=None, extract_error="事实抽取失败。")
        ingestion.submit_emails(self.owner, [data])
        email = Email.objects.get(pk=data["dedupe_key"])
        review_email(self.owner, email.pk, "confirmed_business", email.review_revision)
        with patch("apps.crm.lineage.bailian_extraction_provider", side_effect=RuntimeError("mock model failure")) as model:
            call_command("crm_worker", once=True)
        model.assert_called_once()
        self.assertEqual(email.repairs.get().status, "failed")
