"""职责：验证共享调度的公平性、HTTP 员工隔离和凭证撤销。
实现：Gmail 测试批次显式选择最多 20 封（非运行默认值）；隔离 PostgreSQL 与本地测试 HTTP 服务；邮箱/模型被模拟，权限和租约执行真实代码。
关联：dispatch、worker、crm_worker 及 AgentAuthentication；不连接真实邮箱或 LLM。
目录：
- SharedWorkerTests：共享 Worker 集成测试。
- SharedWorkerTests.setUp：创建两位无服务凭证员工和邮箱。
- SharedWorkerTests.test_round_robin_and_inactive_owner：公平轮转并排除停用员工。
- SharedWorkerTests.test_parallel_clients_are_scoped_and_revoked：并发客户端拒绝跨员工访问并撤销身份。
- SharedWorkerTests.test_exception_revokes_identity：异常退出撤销身份。
- SharedWorkerTests.test_shared_command_drains_two_owners：真实调度和 HTTP 同步处理两位新员工。
- SharedWorkerTests.test_analysis_uses_selected_owner：公司任务领取使用对应员工身份。
- SharedWorkerTests.test_expired_sync_fails_without_retry：共享调度显式结束租约过期批次。
- SharedWorkerTests.test_concurrent_claim_is_unique：两个执行者不能重复领取批次。
- exercise_sync：通过真实本地 HTTP 查询批次邮箱并返回合成结果。
- claim_in_thread：在独立数据库连接中领取一次批次。
变量索引：
- 无
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connections
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone

from agent.clients.backend_api import BackendRequestError
from apps.crm import dispatch, worker
from apps.crm.models import AgentCredential, Company, GmailCredential, Job, Mailbox, MailboxSyncRun
from apps.crm.processing import claim_run, request_run


# 功能：模拟邮箱读取，仍以真实 HTTP 验证当前批次访问权限。
# 输入：`run` 为领取批次，`service` 为占位服务，`backend` 为独立客户端。
# 输出：合成成功汇总。
# 逻辑：请求当前邮箱状态，若员工身份错误则 HTTP 直接拒绝。
# 约束：不调用 Google 或 LLM，不写合成邮件。
def exercise_sync(run, service, backend):
    backend.get_sync_state(str(run.mailbox_id))
    return {"status": "completed"}


# 功能：在独立线程内尝试领取并关闭数据库连接。
# 输入：`owner` 为目标员工。
# 输出：批次主键或 None。
# 逻辑：调用真实事务领取，finally 释放线程连接。
# 约束：只操作隔离测试库，不模拟事务锁。
def claim_in_thread(owner):
    try:
        run = claim_run(owner)
        return run.pk if run else None
    finally:
        connections.close_all()


# 功能：跨员工端到端验证共享调度和认证边界。
# 逻辑：使用本机 LiveServer，保留真实认证而仅替换外部邮箱/模型。
# 约束：测试通过不表示真实 Gmail/QQ 授权有效；所有数据均为合成隔离数据。
@override_settings(ANALYSIS_PROVIDER="agent")
class SharedWorkerTests(LiveServerTestCase):
    # 功能：准备两个无预配置服务令牌的员工。
    # 输入：测试框架创建的隔离库与 localhost HTTP 服务。
    # 输出：owners、mailboxes；临时覆盖连接配置并自动恢复。
    # 逻辑：环境中放入无效旧员工令牌/邮箱，验证显式任务身份优先且环境不被修改。
    # 约束：只连接 localhost，未使用任何真实凭证。
    def setUp(self):
        self.owners = [get_user_model().objects.create_user(username=f"shared-{i}") for i in range(2)]
        self.mailboxes = [Mailbox.objects.create(owner=owner, address=f"shared-{owner.pk}@example.test") for owner in self.owners]
        for mailbox in self.mailboxes:
            GmailCredential.objects.create(mailbox=mailbox, credentials={"mock": True})
        environment = patch.dict(os.environ, {
            "SALESMATE_BACKEND_AGENT_URL": self.live_server_url + "/api/v1/agent/",
            "SALESMATE_AGENT_SERVICE_TOKEN": "unused-legacy-token",
            "SALESMATE_MAILBOX_ID": "unused-legacy-mailbox",
            "NO_PROXY": "localhost,127.0.0.1",
        })
        environment.start()
        self.addCleanup(environment.stop)

    # 功能：确保持续排队的第一位员工不会阻止其他员工被选择。
    # 输入：两位员工分别拥有 queued 批次。
    # 输出：轮转顺序 first/second/first；停用后只选有效员工。
    # 逻辑：批次保持 queued，直接验证游标选择而非依赖任务完成顺序。 各 Gmail 夹具显式选择 20 封，原员工顺序与活跃状态断言保持不变。
    # 约束：不领取任务，不修改既定并发限制。
    def test_round_robin_and_inactive_owner(self):
        for owner, mailbox in zip(self.owners, self.mailboxes):
            request_run(owner, mailbox.pk, sync_options={"max_messages": 20})
        first = dispatch.next_owner("sync")
        second = dispatch.next_owner("sync", first.pk)
        self.assertEqual([first.pk, second.pk], [owner.pk for owner in self.owners])
        self.assertEqual(dispatch.next_owner("sync", second.pk).pk, first.pk)
        first.is_active = False
        first.save(update_fields=["is_active"])
        self.assertEqual(dispatch.next_owner("sync", second.pk).pk, second.pk)

    # 功能：同时存在的任务客户端必须保持各自身份且结束后失效。
    # 输入：两位员工的临时凭证与真实本地 HTTP 请求。
    # 输出：各自邮箱可读，交叉邮箱 404；退出上下文后旧令牌 401，环境仍为旧值。
    # 逻辑：并行请求验证实例隔离；API 通过真实 AgentAuthentication 认证。
    # 约束：从不打印凭证；不把 mock 认证作为权限通过证据。
    def test_parallel_clients_are_scoped_and_revoked(self):
        with dispatch.scoped_backend(self.owners[0], str(self.mailboxes[0].pk)) as left, dispatch.scoped_backend(self.owners[1], str(self.mailboxes[1].pk)) as right:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(client.get_sync_state, str(mailbox.pk)) for client, mailbox in zip([left, right], self.mailboxes)]
                for future in futures:
                    self.assertIsInstance(future.result(), dict)
            for client, foreign in [(left, self.mailboxes[1]), (right, self.mailboxes[0])]:
                with self.assertRaises(BackendRequestError) as error:
                    client.get_sync_state(str(foreign.pk))
                self.assertEqual(error.exception.status_code, 404)
            self.assertEqual(AgentCredential.objects.count(), 2)
        self.assertFalse(AgentCredential.objects.exists())
        with self.assertRaises(BackendRequestError) as error:
            left.get_sync_state(str(self.mailboxes[0].pk))
        self.assertEqual(error.exception.status_code, 401)
        left.close()
        self.assertEqual(os.environ["SALESMATE_AGENT_SERVICE_TOKEN"], "unused-legacy-token")
        self.assertEqual(os.environ["SALESMATE_MAILBOX_ID"], "unused-legacy-mailbox")

    # 功能：执行失败时不遗留有效临时身份。
    # 输入：临时身份上下文内的合成异常。
    # 输出：异常传播且凭证被删除。
    # 逻辑：不替换生命周期实现，实际查询数据库确认撤销。
    # 约束：不触发邮箱调用或重试。
    def test_exception_revokes_identity(self):
        with self.assertRaises(RuntimeError):
            with dispatch.scoped_backend(self.owners[0]) as backend:
                self.assertIsNone(backend.mailbox_id)
                raise RuntimeError("synthetic failure")
        self.assertFalse(AgentCredential.objects.exists())

    # 功能：复现新员工队列并验证共享命令自动处理两位员工。
    # 输入：两位均未绑定旧环境令牌的员工及各自 queued 批次。
    # 输出：两批次 completed，均有开始时间；临时凭证全部撤销。
    # 逻辑：真实线程池调度和领取，仅模拟 Gmail 网络/模型；HTTP 员工认证不模拟。 两名员工各显式选择 20 封范围，模拟 Worker 验证共享调度与命令退出。
    # 约束：没有发送邮件，不更改命令默认并发和轮询参数。
    def test_shared_command_drains_two_owners(self):
        runs = [request_run(owner, mailbox.pk, sync_options={"max_messages": 20}) for owner, mailbox in zip(self.owners, self.mailboxes)]
        with patch("apps.crm.worker.create_service_from_authorization", return_value=(object(), None)), patch("apps.crm.worker.sync_persisted", side_effect=exercise_sync):
            call_command("crm_worker", once=True)
        for run in runs:
            run.refresh_from_db()
            self.assertEqual(run.status, "completed")
            self.assertIsNotNone(run.started_at)
        self.assertFalse(AgentCredential.objects.exists())

    # 功能：确保画像发现和 HTTP 领取也按选择的员工隔离。
    # 输入：两个员工各有一家公司和 pending Job。
    # 输出：第二员工客户端只领取第二员工任务，第一员工任务保持 pending。
    # 逻辑：以客户端真实 claim_jobs 替代 LLM 编排，执行 Worker 的完整身份生命周期。
    # 约束：不调用分析模型；运行租约留作断言，不模拟权限过滤。
    def test_analysis_uses_selected_owner(self):
        work = []
        for owner in self.owners:
            company = Company.objects.create(owner=owner, name=f"company-{owner.pk}", group_key=f"owner-{owner.pk}.test")
            work.append(Job.objects.create(company=company, trigger="test", revision=company.revision))
        self.assertEqual(dispatch.next_owner("analysis", self.owners[0].pk).pk, self.owners[1].pk)
        with patch("apps.crm.worker.process_jobs_once", side_effect=lambda backend, limit: backend.claim_jobs(limit)):
            self.assertTrue(worker.run_analysis(self.owners[1]))
        for job in work:
            job.refresh_from_db()
        self.assertEqual([job.status for job in work], ["pending", "running"])
        self.assertFalse(AgentCredential.objects.exists())

    # 功能：验证共享调度能发现过期批次并明确失败，不自动重新读取邮箱。
    # 输入：只有一条租约过期 running 批次。
    # 输出：状态 failed，错误 worker_interrupted，没有可调度同步工作。
    # 逻辑：调用实际调度与工作单元，断言不进入 Gmail 分支。 创建显式 20 封范围的批次后模拟租约到期，不改变原失败语义。
    # 约束：保留现有显式重试语义。
    def test_expired_sync_fails_without_retry(self):
        run = request_run(self.owners[0], self.mailboxes[0].pk, sync_options={"max_messages": 20})
        MailboxSyncRun.objects.filter(pk=run.pk).update(status="running", lease_until=timezone.now() - timedelta(seconds=1))
        owner = dispatch.next_owner("sync")
        with patch("apps.crm.worker.create_service_from_authorization") as gmail:
            self.assertFalse(worker.run_sync(owner))
        gmail.assert_not_called()
        run.refresh_from_db()
        self.assertEqual(run.error["code"], "worker_interrupted")
        self.assertEqual(run.status, "failed")
        self.assertIsNone(dispatch.next_owner("sync"))

    # 功能：验证共享部署多个进程时沿用真实数据库互斥。
    # 输入：同一员工一条 queued 批次，两个独立线程竞争领取。
    # 输出：只有一个线程取得批次 ID。
    # 逻辑：真实 PostgreSQL 行锁与状态复查阻止重复领取。 竞争对象为显式选择 20 封的唯一批次；不以重复排队替代并发领取测试。
    # 约束：不模拟数据库锁，不代表无限并发负载测试。
    def test_concurrent_claim_is_unique(self):
        run = request_run(self.owners[0], self.mailboxes[0].pk, sync_options={"max_messages": 20})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim_in_thread, [self.owners[0], self.owners[0]]))
        self.assertCountEqual(results, [run.pk, None])
