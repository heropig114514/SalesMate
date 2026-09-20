"""职责：验证持久处理、人工复核和公司调度的业务不变量。
实现：Gmail 测试批次显式选择最多 20 封（非运行默认值）；隔离 PostgreSQL 数据库与合成邮件，模拟 Gmail 网络读取，真实校验事务和 HTTP 权限。
关联：processing、classification、jobs 及新增进度 API；不向真实邮箱或模型发起调用。
目录：
- ProcessingTests：验证批次和分类服务。
- ProcessingTests.setUp：创建独立员工和邮箱。
- ProcessingTests.email：保存可分类的合成邮件。
- ProcessingTests.test_nonbusiness_hidden_everywhere：验证隐藏、统计、上下文和分析入口一致。
- ProcessingTests.test_review_is_owned_versioned_and_preserves_source：验证人工优先、原文和权限。
- ProcessingTests.test_queue_is_durable_and_rejects_duplicate_clicks：验证持久排队与 202。
- ProcessingTests.test_progress_partial_retry_and_expired_lease：验证逐封计数、明确重试和旧租约拒绝。
- ProcessingTests.test_same_company_successor_waits_while_other_company_runs：验证公司互斥及跨公司领取。
- ProcessingTests.test_tracked_gmail_read_failure_keeps_other_messages：验证读取失败逐封隔离。
- ProcessingTests.test_backfill_preserves_manual_decisions：验证历史回填不改人工判断。
- ProcessingTests.test_progress_covers_companies_outside_visible_page：验证整体画像进度不依赖分页。
- ProcessingTests.test_expired_history_preserves_pending_ids：验证过期游标仍保留旧待处理清单。
- ProcessingTests.test_review_hides_analysis_that_used_removed_email：验证人工隐藏后停止展示污染画像。
- ProcessingTests.test_saved_mailbox_view_includes_all_classifications：验证按邮箱核对原文、时间排序及权限。
- ProcessingTests.test_company_row_exposes_actual_email_sources：验证真实邮件与演示样例的来源区分。
- WorkerPipelineTests：验证跨线程观察事件和真实业务持久化。
- WorkerPipelineTests.test_worker_persists_stream_and_isolates_read_error：模拟 Gmail 和模型，贯通临时员工身份、Worker 回调及后端落库。
变量索引：
- 无
"""
from datetime import timedelta
from io import StringIO
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from agent.tools.gmail import read_messages
from apps.crm import ingestion, jobs, rules, selectors
from apps.crm.access import Conflict
from apps.crm.classification import review_email
from apps.crm.models import Email, EmailProcessingJob, GmailCredential, Mailbox, MailboxSyncRun
from apps.crm.processing import claim_run, expire_runs, finish_run, record_event, request_run, retry_run, run_data
from apps.crm.worker import run_sync


# 功能：校验持久处理和复核契约。
# 逻辑：数据库为隔离测试库，HTTP 使用已认证合成员工。
# 约束：外部授权只是占位数据，绝不解释为真实授权有效。
@override_settings(ANALYSIS_PROVIDER="agent")
class ProcessingTests(TestCase):
    # 功能：建立两个员工和授权邮箱。
    # 输入：无外部参数；测试框架调用。
    # 输出：初始化 owner、other、mailbox 和 browser。
    # 逻辑：分别保存用户，以测试跨员工拒绝。
    # 约束：不复用本机业务账号和邮箱。
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="processing-owner")
        self.other = get_user_model().objects.create_user(username="processing-other")
        self.mailbox = Mailbox.objects.create(owner=self.owner, address="sales@processing.example")
        GmailCredential.objects.create(mailbox=self.mailbox, credentials={"mock": True})
        self.browser = APIClient()
        self.browser.force_authenticate(self.owner)

    # 功能：保存具有明确分类的合成邮件。
    # 输入：`key` 为消息 ID，`kind` 为 business/non_business/needs_review，`sender` 为合成联系人。
    # 输出：数据库 Email。
    # 逻辑：使用既有规则 fixture 构造合法事实，再按测试前提设置分类信号。
    # 约束：不调用 LLM；该信号只用于验证软件映射，不代表模型分类效果。
    def email(self, key, kind="business", sender="buyer@customer.example"):
        payload = rules.extract_email(self.mailbox, sender, "询价", "需求：设备\n数量：5 台", key)
        if kind == "non_business":
            payload.update(extract_status="skipped_non_business", facts=None, non_business_hint=True, non_business_reason="automated_sender")
        elif kind == "needs_review":
            payload["facts"].update(intent_hint=None, intent_evidences=[], has_substantive_update=False)
        ingestion.submit_emails(self.owner, [payload])
        return Email.objects.get(pk=payload["dedupe_key"])

    # 功能：校验非业务邮件在查询与分析入口一致隐藏。
    # 输入：无外部参数；创建正常公司和仅非业务公司。
    # 输出：列表、统计、成员键和分析入口断言。
    # 逻辑：混合公司只投影业务邮件；仅非业务公司不能分析。
    # 约束：原始邮件保留，不能通过删数据满足断言。
    def test_nonbusiness_hidden_everywhere(self):
        normal = self.email("business")
        hidden = self.email("hidden", "non_business")
        only_hidden = self.email("only-hidden", "non_business", "news@newsletter.example")
        response = self.browser.get("/api/v1/companies/")
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["stats"]["new_emails_today"], 1)
        grouping, context = selectors.context_pair(normal.company)
        self.assertEqual(grouping["member_dedupe_keys"], [normal.pk])
        self.assertEqual(len(context["emails"]), 1)
        self.assertTrue(Email.objects.filter(pk=hidden.pk).exists())
        self.assertEqual(self.browser.post(f"/api/v1/companies/{only_hidden.company_id}/analyze/").status_code, 409)

    # 功能：验证邮箱原文入口不会因业务分类遗漏邮件或混入其他邮箱。
    # 输入：无外部参数；同邮箱三种分类、另一邮箱邮件以及另一员工会话。
    # 输出：saved 返回三种分类，原 all 语义不变，跨邮箱隔离且越权 404。
    # 逻辑：真实 HTTP 查询与数据库排序，核对原文、日期、来源和未改变的分类。
    # 约束：测试使用合成材料，不调用 IMAP 或模型，不自动确认邮件。
    def test_saved_mailbox_view_includes_all_classifications(self):
        items = [self.email("saved-business"), self.email("saved-hidden", "non_business"), self.email("saved-review", "needs_review")]
        original = {item.pk: item.business_classification for item in items}
        latest = timezone.now()
        for index, item in enumerate(items):
            item.received_at = latest - timedelta(days=index)
            item.save(update_fields=["received_at"])
        another = Mailbox.objects.create(owner=self.owner, address="another@processing.example")
        ingestion.submit_emails(self.owner, [rules.extract_email(another, "other@elsewhere.example", "其他邮箱", "需求：设备", "other-mailbox")])
        path = f"/api/v1/mailboxes/{self.mailbox.pk}/email-reviews/"
        response = self.browser.get(path + "?status=saved")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 3)
        self.assertEqual([row["email_id"] for row in response.data["results"]], [item.pk for item in items])
        for item, row in zip(items, response.data["results"]):
            self.assertEqual(row["source"], item.payload["source"])
            self.assertEqual(row["body_text"], item.payload["body_text"])
            self.assertEqual(row["received_at"], item.received_at.isoformat())
        self.assertEqual(self.browser.get(path + "?status=all").data["count"], 2)
        self.assertEqual(dict(Email.objects.filter(pk__in=original).values_list("pk", "business_classification")), original)
        self.browser.force_authenticate(self.other)
        self.assertEqual(self.browser.get(path + "?status=saved").status_code, 404)

    # 功能：验证客户摘要的来源标记来自实际业务邮件。
    # 输入：无外部参数；同公司两封不同来源的合成夹具。
    # 输出：邮件来源集合包含两项，且不依据模型 provider 或客户名称推测。
    # 逻辑：先保存标准夹具，再显式模拟来源混合历史，读取真实 company_row。
    # 约束：只验证投影，不把测试中的 qq_real 标记当作真实 QQ 授权验证。
    def test_company_row_exposes_actual_email_sources(self):
        sample = self.email("sample-source")
        qq = self.email("qq-source")
        qq.payload = {**qq.payload, "source": "qq_real"}
        qq.save(update_fields=["payload"])
        row = selectors.company_row(sample.company)
        self.assertEqual(row["email_sources"], sorted({sample.payload["source"], "qq_real"}))

    # 功能：验证人工复核的权限、版本和来源保留。
    # 输入：无外部参数；创建待复核合成邮件。
    # 输出：越权 404、过期 409、人工判断保留及原文不变。
    # 逻辑：先确认再重复提交同一原文，机器分类不能覆盖人工决定。
    # 约束：不修改抽取事实，不依赖真实外部服务。
    def test_review_is_owned_versioned_and_preserves_source(self):
        email = self.email("review", "needs_review")
        original = dict(email.payload)
        self.assertEqual(self.browser.get("/api/v1/email-reviews/").data["count"], 1)
        other = APIClient()
        other.force_authenticate(self.other)
        path = f"/api/v1/email-reviews/{email.pk}/"
        self.assertEqual(other.patch(path, {"review_status": "confirmed_business"}, format="json", HTTP_IF_MATCH=str(email.review_revision)).status_code, 404)
        response = self.browser.patch(path, {"review_status": "confirmed_business"}, format="json", HTTP_IF_MATCH=str(email.review_revision))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.browser.patch(path, {"review_status": "confirmed_non_business"}, format="json", HTTP_IF_MATCH=str(email.review_revision)).status_code, 409)
        email.refresh_from_db()
        ingestion.submit_emails(self.owner, [selectors.email_data(email)])
        email.refresh_from_db()
        self.assertEqual(email.classification_source, "human")
        self.assertEqual(email.business_classification, "business")
        self.assertEqual(email.payload, original)
        self.assertTrue(email.company.jobs.filter(status="pending").exists())

    # 功能：验证重复同步点击拒绝新建批次。
    # 输入：无外部参数；两次相同邮箱同步请求。
    # 输出：首次 HTTP 202、重复 HTTP 409 和唯一 queued 记录。
    # 逻辑：重新查询数据库确认状态，而非检查进程内变量。
    # 约束：不启动 Worker，也不把排队当成同步完成。
    def test_queue_is_durable_and_rejects_duplicate_clicks(self):
        path = f"/api/v1/mailboxes/{self.mailbox.pk}/request-sync/"
        first, second = [self.browser.post(path, {"sync_options": {"max_messages": 20}}, format="json") for _ in range(2)]
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(MailboxSyncRun.objects.get().status, "queued")
        other = APIClient()
        other.force_authenticate(self.other)
        self.assertEqual(other.get(f"/api/v1/mailbox-sync-runs/{first.data['run_id']}/").status_code, 404)

    # 功能：验证逐封计数、部分完成、明确重试和租约失效。
    # 输入：无外部参数；一个完成事件和一个读取失败事件。
    # 输出：partial、正确失败范围，以及旧执行者被拒绝。
    # 逻辑：重复发现不重复计数，新重试仅包含失败 ID，过期后保留任务。 初始批次显式选择 20 封，后继批次只重试模拟失败 ID。
    # 约束：模拟阶段事件，不声明真实 Gmail 已读取。
    def test_progress_partial_retry_and_expired_lease(self):
        request_run(self.owner, self.mailbox.pk, sync_options={"max_messages": 20})
        run = claim_run(self.owner)
        for _index in range(2):
            record_event(run.pk, run.lease_token, "discovered", {"message_ids": ["good", "bad"]})
        record_event(run.pk, run.lease_token, "completed", {"gmail_message_id": "good"})
        record_event(run.pk, run.lease_token, "failed", {"gmail_message_id": "bad", "stage": "fetching", "code": "gmail_read_failed"})
        result = finish_run(run.pk, run.lease_token, {"status": "completed"})
        self.assertEqual((result["status"], result["total_count"], result["completed_count"], result["failed_count"]), ("partial", 2, 1, 1))
        with self.assertRaises(Conflict):
            record_event(run.pk, run.lease_token, "fetching", {"gmail_message_id": "bad"})
        retry = retry_run(self.owner, run.pk)
        self.assertEqual(retry.message_ids, ["bad"])
        active = claim_run(self.owner)
        MailboxSyncRun.objects.filter(pk=active.pk).update(lease_until=timezone.now() - timedelta(seconds=1))
        self.assertEqual(expire_runs(self.owner), 1)
        self.assertEqual(MailboxSyncRun.objects.get(pk=active.pk).status, "failed")

    # 功能：验证跨公司可领取，同公司后继必须等待。
    # 输入：无外部参数；两家公司的邮件和一个运行中的旧 revision。
    # 输出：第二次领取只有其他公司任务。
    # 逻辑：运行期间同公司新邮件建立后继，后端原子领取排除该公司。
    # 约束：使用真实数据库状态，不以线程池数量代替互斥验证。
    def test_same_company_successor_waits_while_other_company_runs(self):
        first = self.email("first")
        claimed = jobs.claim(self.owner, 1, 120)
        self.assertEqual(claimed[0]["company_id"], str(first.company_id))
        self.email("successor")
        other = self.email("other", sender="buyer@other-customer.example")
        next_jobs = jobs.claim(self.owner, 3, 120)
        self.assertEqual([item["company_id"] for item in next_jobs], [str(other.company_id)])
        self.assertEqual(first.company.jobs.filter(status="pending").count(), 1)

    # 功能：验证观察模式的单封 Gmail 读取失败隔离。
    # 输入：无外部参数；SDK 读取 mock 在第二封抛错。
    # 输出：第一和第三封成功，第二封有失败事件。
    # 逻辑：使用新回调模式，验证真实循环继续执行。
    # 约束：模拟 SDK，未验证真实授权和网络。
    def test_tracked_gmail_read_failure_keeps_other_messages(self):
        events = []
        with patch("agent.tools.gmail.read_email", side_effect=[{"id": "one"}, RuntimeError("mock"), {"id": "three"}]) as reader:
            result = read_messages(object(), ["one", "bad", "three"], progress=lambda stage, data: events.append((stage, data)))
        self.assertEqual(reader.call_count, 3)
        self.assertEqual(result, [{"id": "one"}, {"id": "three"}])
        self.assertEqual([data["gmail_message_id"] for stage, data in events if stage == "failed"], ["bad"])

    # 功能：验证历史回填保持人工决定。
    # 输入：无外部参数；人工确认一封规则隐藏邮件。
    # 输出：回填后仍为人工业务分类，原文存在。
    # 逻辑：明确应用回填，检查人工优先级。
    # 约束：命令只访问隔离测试库。
    def test_backfill_preserves_manual_decisions(self):
        email = self.email("manual", "non_business")
        review_email(self.owner, email.pk, "confirmed_business", email.review_revision)
        call_command("classify_emails", apply=True, stdout=StringIO())
        email.refresh_from_db()
        self.assertEqual((email.business_classification, email.classification_source), ("business", "human"))

    # 功能：验证批次画像统计涵盖所有关联公司。
    # 输入：无外部参数；创建超过一页的 21 家公司和完成邮件任务。
    # 输出：整体进度返回 21 个待处理画像。
    # 逻辑：进度从批次关系查询，不接收前端公司分页参数。 排队显式提供 20 封测试范围；21 家公司进度为手工任务夹具，用于验证分页无关统计。
    # 约束：不调模型，邮件任务完成状态为本测试模拟前提。
    def test_progress_covers_companies_outside_visible_page(self):
        run = request_run(self.owner, self.mailbox.pk, sync_options={"max_messages": 20})
        for index in range(21):
            email = self.email(f"page-{index}", sender=f"buyer@company-{index}.example")
            EmailProcessingJob.objects.create(run=run, gmail_message_id=f"page-{index}", dedupe_key=email.pk, company=email.company, status="completed", stage="completed")
        self.assertEqual(run_data(run)["analysis_pending_count"], 21)


    # 功能：验证游标过期后不遗失已持久化的失败与待处理 ID。
    # 输入：无外部参数；History 404、两个旧 ID 和一个新扫描 ID。
    # 输出：旧 ID 优先读取，超出单轮限制的新 ID 留在 pending。
    # 逻辑：只模拟 Gmail 查询结果，执行实际恢复选择逻辑。
    # 约束：保持单轮上限，不把 Mock 的 historyId 当成真实邮箱游标。
    def test_expired_history_preserves_pending_ids(self):
        from agent.tools.gmail import GmailHistoryExpiredError
        from agent.workflows.gmail_sync import _read_email_batch
        state = {"cursor": "expired", "scope": {"pending_message_ids": ["old-pending"], "failed_message_ids": ["old-failed"]}}
        with (
            patch("agent.workflows.gmail_sync.list_history_message_ids", side_effect=GmailHistoryExpiredError("mock")),
            patch("agent.workflows.gmail_sync.get_profile_history_id", return_value="200"),
            patch("agent.workflows.gmail_sync.list_sync_message_ids", return_value=["recent"]),
            patch("agent.workflows.gmail_sync.read_messages", return_value=[]) as reader,
        ):
            _emails, cursor, pending, mode = _read_email_batch(object(), 2, state)
        self.assertEqual(reader.call_args.args[1], ["old-pending", "old-failed"])
        self.assertEqual((cursor, pending, mode), ("200", ["recent"], "recovered"))

    # 功能：验证非业务决定使依赖该邮件的旧画像停止展示。
    # 输入：无外部参数；先生成一封业务邮件的确定性测试画像。
    # 输出：人工隐藏后 latest_result 返回空，历史分析记录仍存在。
    # 逻辑：不删除画像历史，通过成员键可见性检查阻止继续展示。
    # 约束：测试规则只用于离线 fixture，不调用模型且不触发非业务重算。
    def test_review_hides_analysis_that_used_removed_email(self):
        email = self.email("analysis-source")
        rules.run_company(self.owner, email.company_id)
        self.assertIsNotNone(selectors.latest_result(email.company)[0])
        review_email(self.owner, email.pk, "confirmed_non_business", email.review_revision)
        email.company.refresh_from_db()
        self.assertEqual(selectors.latest_result(email.company), (None, None))
        self.assertTrue(email.company.inputs.filter(analyses__isnull=False).exists())


# 功能：验证 Worker 和多线程 L1 的真实持久化关系。
# 逻辑：使用可提交的隔离事务库，使抽取线程可看到批次；网络和模型均 Mock。
# 约束：不访问真实 Gmail 或 HTTP；只验证协议回调与数据库实现之间的集成。
class WorkerPipelineTests(TransactionTestCase):
    # 功能：贯通批次领取、逐封读取、并发抽取和持久进度。
    # 输入：无外部参数；两封合法合成邮件、一封读取失败的消息。
    # 输出：两个业务邮件保存，批次 partial，失败 ID 保留且公司分析入队。
    # 逻辑：替换 Gmail、LLM 和临时身份的 HTTP 客户端，执行检查点及真实 ingestion 事务。 在显式 20 封范围内模拟选择三封邮件，实际读取错误隔离与入库路径保持真实。
    # 约束：模型输出为合成 fixture；测试通过不代表真实邮箱授权或模型质量已验证。
    def test_worker_persists_stream_and_isolates_read_error(self):
        owner = get_user_model().objects.create_user(username="worker-integration")
        mailbox = Mailbox.objects.create(owner=owner, address="worker@processing.example")
        GmailCredential.objects.create(mailbox=mailbox, credentials={"mock": True})
        run = request_run(owner, mailbox.pk, sync_options={"max_messages": 20})
        payloads = {key: rules.extract_email(mailbox, "buyer@pipeline.example", "询价", "需求：设备\n数量：2 台", key) for key in ["one", "three"]}
        client = Mock()
        client.get_sync_state.return_value = {"mailbox_id": str(mailbox.pk), "cursor": None, "scope": {}, "version": 0}
        client.get_stored_email.return_value = None
        client.submit_emails.side_effect = lambda submissions: {"created_count": len(ingestion.submit_emails(owner, submissions)), "updated_count": 0, "duplicate_count": 0, "affected_company_ids": []}
        with (
            patch("apps.crm.dispatch.django_backend_from_environment", return_value=client),
            patch("apps.crm.worker.create_service_from_authorization", return_value=(object(), None)),
            patch("apps.crm.durable_sync.resolve_mailbox_address", return_value=mailbox.address),
            patch("apps.crm.durable_sync.scoped_message_pages", return_value=[["one", "bad", "three"]]),
            patch("apps.crm.durable_sync.read_email", side_effect=[{"gmail_message_id": "one"}, RuntimeError("simulated"), {"gmail_message_id": "three"}]),
            patch("agent.workflows.gmail_sync.process_email", side_effect=lambda email, *_args: payloads[email["gmail_message_id"]]),
        ):
            self.assertTrue(run_sync(owner))
        run.refresh_from_db()
        self.assertEqual(run.status, "partial")
        self.assertEqual(Email.objects.filter(mailbox=mailbox).count(), 2)
        data = run_data(run)
        self.assertEqual((data["completed_count"], data["failed_count"], data["total_count"]), (2, 1, 3))
        self.assertEqual(data["email_errors"][0]["gmail_message_id"], "bad")
        self.assertEqual(data["analysis_pending_count"], 1)
