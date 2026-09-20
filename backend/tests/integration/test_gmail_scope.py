"""职责：验证 Gmail 有界同步、先限量后去重及禁止隐式全量的契约。
实现：模拟 Gmail SDK 验证 50 封默认上限及明确超量批准；持久化测试使用隔离数据库和真实批次服务。
关联：gmail_scope 选择消息，sync_scope 冻结条件，durable_sync/processing 执行持久同步。
目录：
- GmailScopeLogicTests：无需数据库的范围、分页与 CLI 边界测试。
- GmailScopeLogicTests.setUp：建立固定范围和 SDK 模拟。
- GmailScopeLogicTests.test_latest_limit_stops_before_older_page：最新封数截断并限制请求大小。
- GmailScopeLogicTests.test_days_filter_and_combined_limit：天数查询及交集限量。
- GmailScopeLogicTests.test_duplicate_ids_do_not_expand_latest_selection：跨页重复 ID 不重复占位。
- GmailScopeLogicTests.test_missing_or_invalid_scope_never_lists_mail：无范围或非法窗口不读取邮箱。
- GmailScopeLogicTests.test_invalid_response_and_page_cycles_fail：坏响应及分页循环明确失败。
- GmailScopeLogicTests.test_network_failure_does_not_retry：网络异常传播，不隐式重试。
- GmailScopeLogicTests.test_snapshot_is_second_precise_and_validated：范围冻结和非法参数拒绝。
- GmailScopeLogicTests.test_cli_deduplicates_before_reading：一次性 Agent 在正文前去重且不读旧游标。
- GmailScopeLogicTests.test_cli_rejects_unbounded_legacy_claim：旧无范围领取不会触发 Gmail 读取。
- GmailScopeLogicTests.test_days_only_stops_at_fifty：纯天数范围也在 50 封停止分页。
- GmailScopeLogicTests.test_large_sync_requires_specific_approval：超量未经批准不读取，批准后仍受具体封数约束。
- GmailScopeLogicTests.test_snapshot_freezes_cap_and_approval：后端冻结默认上限和批准标记。
- GmailScopeLogicTests.test_explicit_retry_cannot_bypass_approval：CLI 明确重试同样执行超量批准限制。
- GmailScopePersistenceTests：依赖隔离数据库的入口与去重测试。
- GmailScopePersistenceTests.setUp：建立员工、授权邮箱和浏览器。
- GmailScopePersistenceTests.test_http_requires_scope_and_isolates_owner：HTTP 拒绝空范围并验证所有权。
- GmailScopePersistenceTests.test_active_run_rejects_replacement：重复提交不覆盖冻结范围。
- GmailScopePersistenceTests.test_scoped_sync_skips_completed_failed_and_outside_pending：先限量、再跳过终态且不排空范围外积压。
- GmailScopePersistenceTests.test_legacy_unbounded_run_fails_without_listing：旧排队批次无范围时拒绝全量。
- GmailScopePersistenceTests.test_large_sync_requires_approval_and_preserves_it_on_retry：超量排队和明确重试保留批准范围。
变量索引：
- 无
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TransactionTestCase
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from agent.tools.gmail_scope import gmail_message_limit, scoped_message_pages
from agent.workflows.authorized_gmail_sync import sync_authorized_mailboxes_once
from agent.workflows.gmail_sync import sync_gmail
from apps.crm.durable_sync import sync_persisted
from apps.crm.models import GmailCredential, Mailbox, MailboxSyncRun, StoredMessage
from apps.crm.processing import claim_run, finish_run, request_run, retry_run
from apps.crm.sync_scope import SyncRequestSerializer, snapshot


# 功能：验证不依赖持久存储的 Gmail 范围边界。
# 逻辑：模拟 SDK 响应并检查请求参数、读取次数和错误传播。
# 约束：不连接 Google、LLM 或数据库；模拟结果不能证明真实账号联调。
class GmailScopeLogicTests(SimpleTestCase):
    # 功能：准备可重复的冻结窗口及 SDK。
    # 输入：无外部参数。
    # 输出：options、service、listing 和 execute 测试状态。
    # 逻辑：窗口固定为 UTC 整秒，仅模拟消息列表边界。
    # 约束：未指定天数，封数明确为 3，不是产品默认值。
    def setUp(self):
        self.options = {"recent_days": None, "max_messages": 3, "since": None, "until": "2026-09-20T00:00:00+00:00"}
        self.service = Mock()
        self.listing = self.service.users.return_value.messages.return_value.list
        self.execute = self.listing.return_value.execute

    # 功能：验证最新 N 封达到上限后不继续扫描旧页。
    # 输入：无外部参数；首响应包含 4 个 ID 及下一页。
    # 输出：只返回 3 个 ID，一次列表调用。
    # 逻辑：即使 SDK 返回超额也在本地截断，查询大小不超过剩余额度。
    # 约束：不读取正文，不依赖邮件总数估计。
    def test_latest_limit_stops_before_older_page(self):
        self.execute.return_value = {"messages": [{"id": str(i)} for i in range(4)], "nextPageToken": "older"}
        self.assertEqual(list(scoped_message_pages(self.service, self.options)), [["0", "1", "2"]])
        self.assertEqual(self.listing.call_count, 1)
        self.assertEqual(self.listing.call_args.kwargs["maxResults"], 3)
        self.assertIn("{in:inbox in:sent}", self.listing.call_args.kwargs["q"])

    # 功能：验证天数在 Google 端筛选且双限制取交集。
    # 输入：无外部参数；7 天窗口以及两页 SDK 响应。
    # 输出：查询包含秒级上下界，后页限制为剩余封数。
    # 逻辑：纯天数遍历窗口页；双限制在第 3 封停止。
    # 约束：时间使用冻结排队时刻，不读取执行时当前时间。
    def test_days_filter_and_combined_limit(self):
        for limit in (None, 3):
            with self.subTest(limit=limit):
                self.listing.reset_mock()
                self.execute.side_effect = [{"messages": [{"id": "a"}, {"id": "b"}], "nextPageToken": "p2"}, {"messages": [{"id": "c"}]}]
                options = {**self.options, "recent_days": 7, "since": "2026-09-13T00:00:00+00:00", "max_messages": limit}
                self.assertEqual(list(scoped_message_pages(self.service, options)), [["a", "b"], ["c"]])
                since = int(datetime.fromisoformat(options["since"]).timestamp())
                until = int(datetime.fromisoformat(options["until"]).timestamp())
                self.assertEqual(self.listing.call_args.kwargs["q"], f"{{in:inbox in:sent}} before:{until} after:{since}")
                self.assertEqual(self.listing.call_args.kwargs["maxResults"], 1 if limit else 20)

    # 功能：验证跨页相同 ID 只选一次。
    # 输入：无外部参数；两页交叠且第二页达到上限。
    # 输出：共选择三个唯一 ID，不请求第三页。
    # 逻辑：API 重复 ID 去重独立于已同步业务去重。
    # 约束：不会从已同步记录中扣除封数来扩展扫描范围。
    def test_duplicate_ids_do_not_expand_latest_selection(self):
        self.execute.side_effect = [{"messages": [{"id": "a"}, {"id": "b"}], "nextPageToken": "p2"}, {"messages": [{"id": "b"}, {"id": "c"}], "nextPageToken": "p3"}]
        self.assertEqual(list(scoped_message_pages(self.service, self.options)), [["a", "b"], ["c"]])
        self.assertEqual(self.listing.call_count, 2)

    # 功能：验证无界或非法范围不能发起列表请求。
    # 输入：无外部参数；空范围、非正整数、缺失窗口及无时区窗口。
    # 输出：每个输入均抛 ValueError，SDK 未调用。
    # 逻辑：在任何网络调用前完成范围校验。
    # 约束：不把损坏的旧批次解释为默认范围。
    def test_missing_or_invalid_scope_never_lists_mail(self):
        for options in ({}, None, {**self.options, "max_messages": 0}, {**self.options, "max_messages": True}, {**self.options, "max_messages": 1.5}, {**self.options, "until": None}, {**self.options, "recent_days": 7}, {**self.options, "until": "2026-09-20T00:00:00"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                list(scoped_message_pages(self.service, options))
        self.listing.assert_not_called()

    # 功能：验证坏响应与分页循环明确失败。
    # 输入：无外部参数；非法列表结构、空 ID、非法 token 及循环页。
    # 输出：RuntimeError，循环页不产生第二批重复消息。
    # 逻辑：先校验响应再产出；分页 token 必须持续向前。
    # 约束：不静默当作同步完成。
    def test_invalid_response_and_page_cycles_fail(self):
        for response in (None, {"messages": {}}, {"messages": [{"id": ""}]}, {"nextPageToken": 12}):
            self.execute.return_value = response
            with self.subTest(response=response), self.assertRaises(RuntimeError):
                list(scoped_message_pages(self.service, self.options))
        self.execute.side_effect = [{"messages": [{"id": "a"}], "nextPageToken": "p2"}, {"messages": [{"id": "b"}], "nextPageToken": "p2"}]
        with self.assertRaisesRegex(RuntimeError, "分页循环"):
            list(scoped_message_pages(self.service, self.options))

    # 功能：验证 Google 网络异常不会隐式重试或回退全量。
    # 输入：无外部参数；模拟首请求抛出网络错误。
    # 输出：同类错误传播且仅调用一次。
    # 逻辑：范围选择器没有重试分支。
    # 约束：不模拟真实网络恢复。
    def test_network_failure_does_not_retry(self):
        self.execute.side_effect = RuntimeError("network unavailable")
        with self.assertRaisesRegex(RuntimeError, "network unavailable"):
            list(scoped_message_pages(self.service, self.options))
        self.assertEqual(self.execute.call_count, 1)

    # 功能：验证 API 校验及秒级范围冻结。
    # 输入：无外部参数；固定带微秒的排队时间、合法和非法限制。
    # 输出：准确的 7 天窗口，空值、非法整数及客户端时间字段被拒绝。
    # 逻辑：执行实际序列化和 snapshot；QQ 默认精度保持原值。
    # 约束：时钟是模拟值，不修改生产默认值。
    def test_snapshot_is_second_precise_and_validated(self):
        now = datetime(2026, 9, 20, 12, 0, 0, 123456, tzinfo=timezone.utc)
        with patch("apps.crm.sync_scope.timezone.now", return_value=now):
            scope = snapshot({"recent_days": 7}, gmail=True)
            self.assertEqual(scope["until"], now.replace(microsecond=0).isoformat())
            self.assertEqual(scope["since"], (now.replace(microsecond=0) - timedelta(days=7)).isoformat())
            self.assertEqual(snapshot({"max_messages": 2})["until"], now.isoformat())
        for data in ({}, {"sync_options": {}}, {"sync_options": {"max_messages": -1}}, {"sync_options": {"recent_days": 1.5}}, {"sync_options": {"max_messages": 1, "until": "2026-09-20"}}):
            self.assertFalse(SyncRequestSerializer(data=data).is_valid(), data)
        with self.assertRaises(ValidationError):
            snapshot({"recent_days": 10**12})

    # 功能：验证调试 Agent 同样先去重后读取正文。
    # 输入：无外部参数；范围内一封已存、一封未知，旧状态另含范围外 pending 与失败 ID。
    # 输出：只读取未知 ID，不调用旧 History 选择逻辑；旧失败保留但不计入本批失败数。
    # 逻辑：执行实际范围选择及 lookup，模拟原文空结果隔离 L1。
    # 约束：不宣称真实后端写入或模型调用通过。
    def test_cli_deduplicates_before_reading(self):
        backend = Mock()
        backend.get_sync_state.return_value = {"cursor": "old", "scope": {"pending_message_ids": ["outside"], "failed_message_ids": ["old-failure"]}, "version": 0}
        backend.get_stored_email.side_effect = [{"saved": True}, None]
        self.execute.return_value = {"messages": [{"id": "done"}, {"id": "new"}]}
        authorization = {"mailbox_id": "mb1", "access_token": "mock", "mailbox_address": "sales@example.com", "sync_options": self.options}
        with patch("agent.workflows.gmail_sync.resolve_mailbox_address", return_value="sales@example.com"), patch("agent.workflows.gmail_sync.read_messages", return_value=[]) as reader, patch("agent.workflows.gmail_sync._read_email_batch") as legacy:
            result = sync_gmail(authorization, backend=backend, gmail_factory=lambda _: self.service)
        self.assertEqual(reader.call_args.args[1], ["new"])
        self.assertEqual(result["duplicate_count"], 1)
        self.assertEqual(result["failed_email_count"], 0)
        self.assertEqual(backend.save_sync_state.call_args.args[0]["scope"]["failed_message_ids"], ["old-failure"])
        legacy.assert_not_called()

    # 功能：验证升级前排队且没有范围的 CLI 任务不能触发同步。
    # 输入：无外部参数；领取响应含授权但缺少范围与明确 ID。
    # 输出：失败回报，Gmail 客户端未构造。
    # 逻辑：在凭证或网络边界前拒绝旧任务。
    # 约束：独立公司任务模拟为空，不触发真实分析。
    def test_cli_rejects_unbounded_legacy_claim(self):
        backend = Mock()
        backend.claim_mailbox_syncs.return_value = [{"mailbox_id": "mb1", "authorization": {"mock": True}}]
        with patch("agent.workflows.authorized_gmail_sync.create_service_from_authorization") as create, patch("agent.workflows.authorized_gmail_sync.process_jobs_once", return_value=[]):
            reports = sync_authorized_mailboxes_once(backend=backend)
        self.assertEqual(reports[0]["status"], "failed")
        create.assert_not_called()

    # 功能：验证纯天数查询不会持续扫描超过默认 50 封。
    # 输入：无外部参数；窗口内至少 80 封，每页 SDK 返回 20 封及后继页。
    # 输出：只选 50 封，最后一次请求额度为 10，不访问第四页。
    # 逻辑：执行实际选择器，模拟超额响应验证本地截断。
    # 约束：50 是用户指定的产品阈值，单页 20 的既定设置不变。
    def test_days_only_stops_at_fifty(self):
        self.execute.side_effect = [{"messages": [{"id": str(i)} for i in range(start, start + 20)], "nextPageToken": f"p{start}"} for start in (0, 20, 40, 60)]
        options = {**self.options, "max_messages": None, "recent_days": 7, "since": "2026-09-13T00:00:00+00:00"}
        pages = list(scoped_message_pages(self.service, options))
        self.assertEqual([len(page) for page in pages], [20, 20, 10])
        self.assertEqual(self.listing.call_count, 3)
        self.assertEqual(self.listing.call_args.kwargs["maxResults"], 10)

    # 功能：验证批准只允许所选的具体超量封数。
    # 输入：无外部参数；75 封分别未批准、明确拒绝和批准。
    # 输出：未批准时零列表请求；批准后最多选择 75 封。
    # 逻辑：真实校验和分页；批准不能使范围变为无限。
    # 约束：Google 响应模拟，不能证明实际网络耗时有上限。
    def test_large_sync_requires_specific_approval(self):
        for approval in ({}, {"allow_large_sync": False}):
            with self.assertRaisesRegex(ValueError, "明确批准"):
                list(scoped_message_pages(self.service, {**self.options, "max_messages": 75, **approval}))
        self.listing.assert_not_called()
        self.execute.side_effect = [{"messages": [{"id": str(i)} for i in range(start, start + 20)], "nextPageToken": f"p{start}"} for start in (0, 20, 40, 60)]
        pages = list(scoped_message_pages(self.service, {**self.options, "max_messages": 75, "allow_large_sync": True}))
        self.assertEqual(sum(map(len, pages)), 75)
        self.assertEqual(self.listing.call_args.kwargs["maxResults"], 15)
        for options in ({"allow_large_sync": True}, {"max_messages": 75, "allow_large_sync": "true"}):
            with self.assertRaises(ValueError):
                gmail_message_limit(options)

    # 功能：验证服务端默认上限及批准持久载荷。
    # 输入：无外部参数；纯天数、50 封、51 封及只有批准标记的请求。
    # 输出：默认/边界 50，未批准的 51 被拒绝，批准后的 51 与标记同时保留。
    # 逻辑：执行实际 snapshot 和序列化；QQ 原有范围语义不变。
    # 约束：没有用户范围时即使携带批准也不能同步。
    def test_snapshot_freezes_cap_and_approval(self):
        self.assertEqual(snapshot({"recent_days": 7}, gmail=True)["max_messages"], 50)
        self.assertEqual(snapshot({"max_messages": 50}, gmail=True)["max_messages"], 50)
        for approval in ({}, {"allow_large_sync": False}):
            with self.assertRaises(ValidationError):
                snapshot({"max_messages": 51, **approval}, gmail=True)
        approved = snapshot({"max_messages": 51, "allow_large_sync": True}, gmail=True)
        self.assertEqual((approved["max_messages"], approved["allow_large_sync"]), (51, True))
        self.assertFalse(SyncRequestSerializer(data={"sync_options": {"allow_large_sync": True}}).is_valid())
        self.assertIsNone(snapshot({"recent_days": 7})["max_messages"])

    # 功能：验证明确重试不能绕过批准校验。
    # 输入：无外部参数；51 个明确消息 ID，分别不带和带本次批准。
    # 输出：未批准时失败且不读正文，批准后读取明确的 51 个 ID。
    # 逻辑：执行 sync_gmail 的实际入口，模型与正文均用空响应隔离。
    # 约束：保留原同步参数和失败报告格式，不自动拆分或重试。
    def test_explicit_retry_cannot_bypass_approval(self):
        backend = Mock()
        backend.get_sync_state.return_value = {"cursor": None, "scope": {}, "version": 0}
        ids = [str(i) for i in range(51)]
        authorization = {"mailbox_id": "mb1", "access_token": "mock", "mailbox_address": "sales@example.com", "sync_options": {"max_messages": 51}}
        with patch("agent.workflows.gmail_sync.resolve_mailbox_address", return_value="sales@example.com"), patch("agent.workflows.gmail_sync.read_messages", return_value=[]) as reader:
            failed = sync_gmail(authorization, backend=backend, gmail_factory=lambda _: self.service, message_ids=ids)
            self.assertEqual(failed["status"], "failed")
            reader.assert_not_called()
            authorization["sync_options"]["allow_large_sync"] = True
            result = sync_gmail(authorization, backend=backend, gmail_factory=lambda _: self.service, message_ids=ids)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(reader.call_args.args[1], ids)


# 功能：验证实际数据库队列、权限和范围内去重。
# 逻辑：使用 TransactionTestCase 隔离库，实际序列化与事务；不执行 Gmail 正文/模型。
# 约束：需要配置的数据库可用，不能用模拟通过替代持久化验证。
class GmailScopePersistenceTests(TransactionTestCase):
    # 功能：创建独立员工与授权邮箱。
    # 输入：无外部参数。
    # 输出：owner、mailbox 和 browser 测试状态。
    # 逻辑：凭证只用于通过本地授权存在检查。
    # 约束：不会生成真实 Google 授权。
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="gmail-scope")
        self.mailbox = Mailbox.objects.create(owner=self.owner, address="sales@scope.example")
        GmailCredential.objects.create(mailbox=self.mailbox, credentials={"mock": True})
        self.browser = APIClient()
        self.browser.force_authenticate(self.owner)

    # 功能：验证 HTTP 范围必填及员工隔离。
    # 输入：无外部参数；空范围与另一个员工的合法范围请求。
    # 输出：分别 400 和 404，均不建立批次。
    # 逻辑：调用实际 request-sync 入口。
    # 约束：不触发 Worker 或网络。
    def test_http_requires_scope_and_isolates_owner(self):
        path = f"/api/v1/mailboxes/{self.mailbox.pk}/request-sync/"
        for data in ({}, {"sync_options": {}}, {"sync_options": {"recent_days": None, "max_messages": None}}):
            self.assertEqual(self.browser.post(path, data, format="json").status_code, 400)
        self.browser.force_authenticate(get_user_model().objects.create_user(username="scope-other"))
        self.assertEqual(self.browser.post(path, {"sync_options": {"max_messages": 3}}, format="json").status_code, 404)
        self.assertFalse(MailboxSyncRun.objects.exists())

    # 功能：验证重复点击不会改变活动批次的范围。
    # 输入：无外部参数；先选 7 天，再尝试选 500 封。
    # 输出：首次 202，后续 409，数据库保持唯一原批次。
    # 逻辑：真实邮箱行锁保护排队；检查冻结 JSON 未被替换。
    # 约束：并发锁的跨连接测试由 test_shared_worker 覆盖。
    def test_active_run_rejects_replacement(self):
        path = f"/api/v1/mailboxes/{self.mailbox.pk}/request-sync/"
        first = self.browser.post(path, {"sync_options": {"recent_days": 7}}, format="json")
        self.assertEqual(first.status_code, 202)
        self.assertEqual(self.browser.post(path, {"sync_options": {"max_messages": 500}}, format="json").status_code, 409)
        self.assertEqual(MailboxSyncRun.objects.get().sync_options, first.data["sync_options"])

    # 功能：验证最新范围的已完成/失败邮件不导致更早邮件补位。
    # 输入：无外部参数；最新 3 封含完成、失败和新邮件，另有范围外积压。
    # 输出：只处理新邮件，下一页和范围外 pending 均未处理。
    # 逻辑：使用实际 Gmail 选择器、真实数据库去重及登记，模拟原文处理边界。
    # 约束：失败须明确重试，提示词旧版本也不会触发普通同步重新抽取。
    def test_scoped_sync_skips_completed_failed_and_outside_pending(self):
        for message_id, status in [("done", "completed"), ("failed", "failed"), ("outside", "pending")]:
            StoredMessage.objects.create(mailbox=self.mailbox, message_id=message_id, status=status, prompt_version="old")
        request_run(self.owner, self.mailbox.pk, sync_options={"max_messages": 3})
        run = claim_run(self.owner)
        service = Mock()
        listing = service.users.return_value.messages.return_value.list
        listing.return_value.execute.return_value = {"messages": [{"id": value} for value in ["done", "failed", "new"]], "nextPageToken": "older"}
        with patch("apps.crm.durable_sync.resolve_mailbox_address", return_value=self.mailbox.address), patch("apps.crm.durable_sync.process_page") as process:
            result = sync_persisted(run, service, Mock())
        self.assertEqual([record.message_id for record in process.call_args.args[3]], ["new"])
        self.assertEqual(list(run.email_jobs.values_list("gmail_message_id", flat=True)), ["new"])
        self.assertEqual(result["duplicate_count"], 2)
        self.assertEqual(listing.call_count, 1)

    # 功能：验证无范围旧排队任务也不会恢复全量扫描。
    # 输入：无外部参数；直接建立升级前的空范围批次。
    # 输出：ValueError，SDK 消息列表未调用。
    # 逻辑：批次领取后必须通过相同范围选择器。
    # 约束：只模拟 profile 身份确认，不处理历史积压。
    def test_legacy_unbounded_run_fails_without_listing(self):
        MailboxSyncRun.objects.create(mailbox=self.mailbox)
        run = claim_run(self.owner)
        service = Mock()
        with patch("apps.crm.durable_sync.resolve_mailbox_address", return_value=self.mailbox.address), self.assertRaises(ValueError):
            sync_persisted(run, service, Mock())
        service.users.assert_not_called()

    # 功能：验证 HTTP 超量请求必须携带本次批准，重试保留同一已批准范围。
    # 输入：无外部参数；同一员工先提交未批准的 51 封，再提交批准的 51 封。
    # 输出：拒绝请求不排队；批准请求 202，失败重试不扩大批准范围。
    # 逻辑：真实入口、事务和状态转换；模拟批次失败以检查快照继承。
    # 约束：不连接实际 Gmail，不推断重新选择的其他范围已获批准。
    def test_large_sync_requires_approval_and_preserves_it_on_retry(self):
        path = f"/api/v1/mailboxes/{self.mailbox.pk}/request-sync/"
        self.assertEqual(self.browser.post(path, {"sync_options": {"max_messages": 51}}, format="json").status_code, 400)
        self.assertFalse(MailboxSyncRun.objects.exists())
        response = self.browser.post(path, {"sync_options": {"max_messages": 51, "allow_large_sync": True}}, format="json")
        self.assertEqual(response.status_code, 202)
        run = claim_run(self.owner)
        self.assertTrue(run.sync_options["allow_large_sync"])
        finish_run(run.pk, run.lease_token, {"status": "failed"})
        retried = retry_run(self.owner, run.pk)
        self.assertEqual(retried.sync_options, run.sync_options)
