"""职责：在独立进程中执行邮箱同步和公司分析。
实现：按显式员工执行同步与画像，每个工作单元持有账号共享锁并使用可撤销的独立 HTTP 身份，避免重置期间写回。
关联：crm_worker 共享调度，dispatch 管理临时凭证；Gmail/QQ 与 L1–L4 仍通过 HTTP 协议读写。
目录：
- run_sync：领取并执行一个持久邮箱批次。
- run_analysis：领取并执行一个公司任务。
变量索引：
- logger：工作单元生命周期和安全错误日志。
"""
import logging

from django.db import connections
from apps.accounts.reset_locks import account_work

from agent.tools.gmail import create_service_from_authorization
from agent.tools import qq_mail
from agent.workflows.orchestration import process_jobs_once

from .access import Conflict, InvalidState
from .models import GmailCredential, QQCredential
from .dispatch import scoped_backend
from .qq_connection import authorization_code
from .qq_sync import sync_persisted as sync_qq
from .processing import claim_run, expire_runs, finish_run
from .durable_sync import sync_persisted
from .lineage import run_repair

logger = logging.getLogger("salesmate.crm_worker")


# 功能：执行一个员工邮箱的持久同步批次。
# 输入：`owner` 为共享调度器选出的员工。
# 输出：是否领取了工作；最终状态保存数据库。
# 逻辑：共享锁覆盖整个单元及异常回报；显式结束过期批次后优先人工修复，使用临时身份分发 Gmail/QQ。
# 约束：只读 Gmail/QQ，不执行销售发信或日历，不自动重试失败；QQ 连接在 finally 释放。
@account_work
def run_sync(owner):
    run = None
    qq_client = None
    qq_credential = None
    try:
        expire_runs(owner)
        if run_repair(owner):
            return True
        run = claim_run(owner)
        if run is None:
            return False
        with scoped_backend(owner, str(run.mailbox_id)) as backend:
            qq_credential = QQCredential.objects.filter(mailbox_id=run.mailbox_id).first()
            if qq_credential:
                qq_client = qq_mail.connect(run.mailbox.address, authorization_code(qq_credential))
                result, refreshed = sync_qq(run, qq_client, backend), None
            else:
                credentials = GmailCredential.objects.get(mailbox_id=run.mailbox_id).credentials
                service, refreshed = create_service_from_authorization(credentials)
                result = sync_persisted(run, service, backend)
            finish_run(run.pk, run.lease_token, result, refreshed)
        return True
    except Exception as error:
        if qq_credential is not None and isinstance(error, (qq_mail.QQMailError, Conflict, InvalidState)):
            logger.error("qq_sync_failed run_id=%s reason=%s action=inspect_qq_connection_or_checkpoint", run.pk, str(error))
        logger.error("sync_worker_failed owner_id=%s run_id=%s error_type=%s action=inspect_run_and_retry_explicitly", owner.pk, run.pk if run else None, type(error).__name__)
        if run:
            try:
                finish_run(run.pk, run.lease_token, {"status": "failed", "error": {"code": "worker_sync_failed"}})
            except Exception as report_error:
                logger.error("sync_failure_report_rejected run_id=%s error_type=%s action=inspect_lease", run.pk, type(report_error).__name__)
        return run is not None
    finally:
        if qq_client is not None:
            qq_mail.disconnect(qq_client)
        connections.close_all()


# 功能：执行当前员工的一个公司画像任务。
# 输入：`owner` 为共享调度器选出的员工。
# 输出：是否领取任务。
# 逻辑：共享锁覆盖单元；创建独立临时身份和 HTTP 客户端，按该员工领取一个任务。
# 约束：异常记录后结束本单元，不重试任务；其他公司的单元继续。
@account_work
def run_analysis(owner):
    try:
        with scoped_backend(owner) as backend:
            return bool(process_jobs_once(backend=backend, limit=1))
    except Exception as error:
        logger.error("analysis_worker_failed owner_id=%s error_type=%s action=inspect_job_and_lease", owner.pk, type(error).__name__)
        return False
    finally:
        connections.close_all()
