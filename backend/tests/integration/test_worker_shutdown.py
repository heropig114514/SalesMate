"""职责：验证部署停止不会打断当前外部动作或领取后续动作。
实现：模拟动作和信号调用，真实执行 Worker 循环及处理器恢复，不发送 OS 信号或邮件。
关联：common.shutdown 和 sales_worker；CRM 线程池排空依赖相同停止状态。
目录：
- ShutdownTests：停止边界测试。
- ShutdownTests.test_handler_restored_after_exception：异常后恢复信号处理器。
- ShutdownTests.test_sales_finishes_current_action_before_exit：当前动作完成且后续动作不启动。
- ShutdownTests.test_crm_waits_for_pending_result：停止时等待在途单元并保留异常。
变量索引：
- 无
"""
import signal
from unittest import TestCase
from unittest.mock import Mock, patch
from django.test import override_settings
from common.shutdown import graceful_shutdown
from apps.sales.management.commands import sales_worker
from apps.crm.management.commands import crm_worker


# 功能：验证信号到业务边界的转换。
# 逻辑：直接调用临时处理器，不向真实进程发送 SIGTERM。
# 约束：所有业务网络与查询均被替换。
class ShutdownTests(TestCase):
    # 功能：确保异常退出仍恢复原处理器。
    # 输入：原 SIGTERM 状态和模拟业务异常。
    # 输出：停止标志置真，原处理器恢复。
    # 逻辑：在上下文内调用当前处理器后抛异常。
    # 约束：不改变测试进程的永久信号行为。
    def test_handler_restored_after_exception(self):
        previous = signal.getsignal(signal.SIGTERM)
        with self.assertRaises(RuntimeError):
            with graceful_shutdown() as stop:
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                self.assertTrue(stop["requested"])
                raise RuntimeError("synthetic")
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    # 功能：部署停止时必须读取所有在途 CRM 单元的结果。
    # 输入：模拟已调度同步 Future，随后收到停止请求；分别返回成功或抛异常。
    # 输出：Future.result 被等待一次，异常继续向上报告。
    # 逻辑：模拟调度边界，仅在轮询等待处设置停止状态，不执行数据库或网络工作。
    # 约束：不把线程池退出当作业务成功，不吞掉未完成单元的错误。
    @override_settings(ANALYSIS_PROVIDER="agent")
    def test_crm_waits_for_pending_result(self):
        for failure in (False, True):
            with self.subTest(failure=failure), patch.object(crm_worker, "worker_owner", return_value=Mock(pk=1)), patch.object(crm_worker, "close_old_connections"), patch.object(crm_worker, "expire_runs"), patch.object(crm_worker, "ExtractionRepair") as repair, patch.object(crm_worker, "MailboxSyncRun") as sync, patch.object(crm_worker, "claimable_jobs") as jobs, patch.object(crm_worker, "ThreadPoolExecutor") as executor, patch.object(crm_worker.time, "sleep", side_effect=lambda seconds: signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)):
                repair.objects.filter.return_value.filter.return_value.exists.return_value = False
                sync.objects.filter.return_value.exists.return_value = True
                jobs.return_value.exists.return_value = False
                future = executor.return_value.__enter__.return_value.submit.return_value
                if failure:
                    future.result.side_effect = RuntimeError("synthetic")
                    with self.assertRaises(RuntimeError):
                        crm_worker.Command().handle(once=False, poll=1, analysis_workers=2)
                else:
                    crm_worker.Command().handle(once=False, poll=1, analysis_workers=2)
                future.result.assert_called_once_with()

    # 功能：当前邮件动作返回后才退出，并不领取队列中第二项。
    # 输入：合成两动作队列；首项执行中触发停止处理器。
    # 输出：run_action 一次且正常返回，Worker 不等待下一轮。
    # 逻辑：以 Mock 的 side_effect 调用处理器后返回成功，检查调用边界和处理器恢复。
    # 约束：不连接数据库或外部服务。
    def test_sales_finishes_current_action_before_exit(self):
        previous = signal.getsignal(signal.SIGTERM)
        with patch.object(sales_worker, "ToolAction") as actions, patch.object(sales_worker, "notify_due"), patch.object(sales_worker, "close_old_connections"), patch.object(sales_worker.time, "sleep") as sleep, patch.object(sales_worker, "run_action", side_effect=lambda key: (signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None), "succeeded")) as execute:
            actions.objects.filter.return_value.order_by.return_value.values_list.return_value = ["first", "second"]
            sales_worker.Command().handle(once=False, poll=5)
        execute.assert_called_once_with("first")
        sleep.assert_not_called()
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)
