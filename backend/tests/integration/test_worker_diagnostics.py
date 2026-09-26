"""职责：验证同步 Worker 失败可定位且不泄漏敏感异常正文。
实现：隔离数据库保存真实批次；模拟外部授权及执行失败，检验原有终态与不重试语义。
关联：crm.worker、processing、dispatch；不调用真实邮箱、Agent 或模型。
目录：
- WorkerDiagnosticsTests：Worker 诊断回归。
- WorkerDiagnosticsTests.setUp：创建合成员工和邮箱。
- WorkerDiagnosticsTests.test_authorization_failure_records_stage_without_secrets：授权失败保留终态并安全记录。
- WorkerDiagnosticsTests.test_sync_failure_records_execution_stage：同步执行失败可区分授权阶段。
- WorkerDiagnosticsTests.test_report_failure_preserves_both_locations：失败回报被拒时保留两次错误位置。
- WorkerDiagnosticsTests.test_error_chain_omits_messages：异常链不输出秘密或业务正文。
- WorkerDiagnosticsTests.test_idle_connection_loss_does_not_fail_completed_sync：空闲数据库连接被关闭不误判已完成同步。
- WorkerDiagnosticsTests.test_broken_connection_failure_revokes_identity：故障时重新建立清理连接，撤销临时身份并回报失败。
- close_idle_connection：模拟长外部调用期间数据库关闭空闲连接。
- fail_with_closed_connection：模拟空闲连接关闭后外部调用失败。
变量索引：
- 无
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, OperationalError
from django.test import TransactionTestCase

from apps.crm import worker
from apps.crm.models import AgentCredential, GmailCredential, Mailbox
from apps.crm.processing import request_run


# 功能：在成功的外部同步边界模拟空闲数据库连接关闭。
# 输入：`run`、`service`、`backend` 是 Worker 的原调用参数，仅用于匹配替代函数签名。
# 输出：完成结果；底层 PostgreSQL 连接关闭。
# 逻辑：实际关闭本测试线程的底层连接，而不是模拟 ORM 成功。
# 约束：仅作用于隔离测试库，不修改连接参数、不发送邮箱请求。
def close_idle_connection(run, service, backend):
    connection.connection.close()
    return {"status": "completed"}


# 功能：模拟连接空闲失效与外部授权异常同时发生。
# 输入：`credentials` 为虚构授权占位值。
# 输出：无，关闭测试数据库底层连接后抛 OperationalError。
# 逻辑：暴露身份回收和失败持久化是否复用已关闭连接。
# 约束：不打印凭证、不终止服务器或其他测试连接。
def fail_with_closed_connection(credentials):
    connection.connection.close()
    raise OperationalError("synthetic closed connection")


# 功能：检查真实批次状态与安全日志。
# 逻辑：执行原 Worker 与持久化，仅替换外部调用；失败不会触发真实网络。
# 约束：测试不能证明线上凭证可用，也不重放历史业务任务。
class WorkerDiagnosticsTests(TransactionTestCase):
    # 功能：建立测试邮箱和明确的同步范围。
    # 输入：无外部参数，读取隔离测试库。
    # 输出：owner、mailbox 与 run 实例。
    # 逻辑：按现有 API 创建 queued 批次；20 封为既有测试夹具条件。
    # 约束：不更改产品默认参数或使用真实凭证。
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="diagnostics")
        self.mailbox = Mailbox.objects.create(owner=self.owner, address="diagnostics@example.test")
        GmailCredential.objects.create(mailbox=self.mailbox, credentials={"mock": True})
        self.run = request_run(self.owner, self.mailbox.pk, sync_options={"max_messages": 20})

    # 功能：验证授权失败仍显式失败，并标记失败阶段。
    # 输入：无外部参数；注入含假令牌的异常。
    # 输出：failed、单次授权调用、无残留身份、日志无令牌。
    # 逻辑：保留真实领取与 finish_run，检查失败语义没有被诊断改动改变。
    # 约束：禁止实际邮箱或模型调用。
    def test_authorization_failure_records_stage_without_secrets(self):
        with patch.object(worker, "create_service_from_authorization", side_effect=RuntimeError("secret-access-token")) as authorize, patch.object(worker, "sync_persisted") as sync:
            with self.assertLogs(worker.logger, level="ERROR") as logged:
                self.assertTrue(worker.run_sync(self.owner))
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, "failed")
        self.assertEqual(self.run.error["code"], "worker_sync_failed")
        authorize.assert_called_once()
        sync.assert_not_called()
        self.assertFalse(AgentCredential.objects.exists())
        output = "\n".join(logged.output)
        self.assertIn("stage=gmail_authorize", output)
        self.assertIn("worker.py:run_sync:", output)
        self.assertNotIn("secret-access-token", output)

    # 功能：区分已授权后的执行错误。
    # 输入：无外部参数；模拟授权成功及同步失败。
    # 输出：gmail_sync 阶段、failed 终态及一次执行。
    # 逻辑：执行完整身份生命周期，只替换网络边界。
    # 约束：不增加失败自动重试。
    def test_sync_failure_records_execution_stage(self):
        with patch.object(worker, "create_service_from_authorization", return_value=(object(), None)), patch.object(worker, "sync_persisted", side_effect=ValueError("private-email-body")) as sync:
            with self.assertLogs(worker.logger, level="ERROR") as logged:
                self.assertTrue(worker.run_sync(self.owner))
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, "failed")
        sync.assert_called_once()
        self.assertIn("stage=gmail_sync", "\n".join(logged.output))
        self.assertNotIn("private-email-body", "\n".join(logged.output))

    # 功能：保留原始失败与回报失败两处定位信息。
    # 输入：无外部参数；模拟授权异常和失败回报异常。
    # 输出：两个事件、原阶段及 report_failure 阶段，无异常正文。
    # 逻辑：模拟 finish_run 不可写；不伪造已落库的失败终态。
    # 约束：此边界依靠既有租约到期处理，不新增重试。
    def test_report_failure_preserves_both_locations(self):
        with patch.object(worker, "create_service_from_authorization", side_effect=RuntimeError("secret-a")), patch.object(worker, "finish_run", side_effect=RuntimeError("secret-b")) as finish:
            with self.assertLogs(worker.logger, level="ERROR") as logged:
                self.assertTrue(worker.run_sync(self.owner))
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, "running")
        finish.assert_called_once()
        output = "\n".join(logged.output)
        self.assertIn("stage=gmail_authorize", output)
        self.assertIn("stage=report_failure", output)
        self.assertNotIn("secret-a", output)
        self.assertNotIn("secret-b", output)

    # 功能：确保嵌套异常具有可定位信息而不携带敏感内容。
    # 输入：无外部参数；构造显式 cause 链。
    # 输出：两类异常及代码位置，不含两层正文。
    # 逻辑：直接测试诊断提取函数而非日志格式化 mock。
    # 约束：不读取 traceback 的局部变量或源码文本。
    def test_error_chain_omits_messages(self):
        try:
            try:
                raise ValueError("secret-inner")
            except ValueError as cause:
                raise RuntimeError("secret-outer") from cause
        except RuntimeError as error:
            location = worker.error_location(error)
        self.assertIn("RuntimeError[", location)
        self.assertIn("ValueError[", location)
        self.assertIn("test_worker_diagnostics.py:test_error_chain_omits_messages:", location)
        self.assertNotIn("secret-", location)

    # 功能：验证完成外部工作后不因旧数据库连接失效而错误标记失败。
    # 输入：成功结果和已关闭的空闲 PostgreSQL 连接。
    # 输出：批次 completed，临时凭证全部撤销，同步执行一次。
    # 逻辑：真实执行结束事务与身份回收。
    # 约束：不是重试同步或修改连接寿命的测试。
    def test_idle_connection_loss_does_not_fail_completed_sync(self):
        with patch.object(worker, "create_service_from_authorization", return_value=(object(), None)), patch.object(worker, "sync_persisted", side_effect=close_idle_connection) as sync:
            self.assertTrue(worker.run_sync(self.owner))
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, "completed")
        self.assertFalse(AgentCredential.objects.exists())
        sync.assert_called_once()

    # 功能：验证失败后能在新连接上完成回收与失败回报。
    # 输入：授权异常前关闭的底层连接。
    # 输出：批次 failed、无有效临时凭证、授权只调用一次。
    # 逻辑：保留真实 ORM，仅模拟外部调用错误。
    # 约束：不将数据库仍不可达的情况伪装为可恢复。
    def test_broken_connection_failure_revokes_identity(self):
        with patch.object(worker, "create_service_from_authorization", side_effect=fail_with_closed_connection) as authorize:
            self.assertTrue(worker.run_sync(self.owner))
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, "failed")
        self.assertFalse(AgentCredential.objects.exists())
        authorize.assert_called_once()
