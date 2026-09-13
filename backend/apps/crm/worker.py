"""职责：在独立进程中执行邮箱同步和公司分析。
实现：每个工作单元使用独立 HTTP 客户端及数据库连接；邮箱观察事件保存到批次任务。
关联：crm_worker 调度本模块；Agent 的 Gmail/L1–L4 仍通过 HTTP 业务协议读写。
目录：
- worker_owner：核验本机 Agent 服务令牌绑定的员工。
- observe_email：持久化观察事件并释放线程连接。
- run_sync：领取并执行一个持久邮箱批次。
- run_analysis：领取并执行一个公司任务。
变量索引：
- logger：工作单元生命周期和安全错误日志。
"""
from functools import partial
import hashlib
import logging
import os

from django.db import connections
from django.core.management.base import CommandError

from agent.clients.backend_api import django_backend_from_environment
from agent.config import load_environment
from agent.tools.gmail import create_service_from_authorization
from agent.workflows.gmail_sync import sync_gmail
from agent.workflows.orchestration import process_jobs_once

from .models import AgentCredential, GmailCredential
from .processing import claim_run, finish_run, record_event

logger = logging.getLogger("salesmate.crm_worker")


# 功能：解析当前独立 Worker 的员工身份。
# 输入：无外部参数；读取现有 Agent 环境配置和数据库凭证摘要。
# 输出：启用的员工对象；配置不匹配抛 CommandError。
# 逻辑：复用服务令牌，按摘要查询 owner，不创建或输出令牌。
# 约束：一个 Worker 绑定一个员工，多员工分别部署；保持原 HTTP 认证边界。
def worker_owner():
    load_environment()
    django_backend_from_environment()
    digest = hashlib.sha256(os.environ.get("SALESMATE_AGENT_SERVICE_TOKEN", "").encode()).hexdigest()
    credential = AgentCredential.objects.select_related("owner").filter(digest=digest, owner__is_active=True).first()
    if credential is None:
        raise CommandError("Worker 服务令牌未绑定有效员工，请检查 Agent 配置。")
    return credential.owner


# 功能：跨 L1 线程保存一个阶段事件。
# 输入：`run_id`、`token` 为批次凭证，`stage`、`data` 为 Agent 进度事件。
# 输出：无；持久更新进度，异常向执行者传播。
# 逻辑：复用原子事件服务，每次回调后关闭该线程数据库连接。
# 约束：不吞掉租约或数据库错误；不在日志暴露邮件正文。
def observe_email(run_id, token, stage, data):
    try:
        record_event(run_id, token, stage, data)
    finally:
        connections.close_all()


# 功能：执行一个员工邮箱的持久同步批次。
# 输入：`owner` 为已认证 Worker 员工。
# 输出：是否领取了工作；最终状态保存数据库。
# 逻辑：领取后调用 Agent，同步阶段事件保存；单元异常安全回报，其他工作单元继续。
# 约束：只读 Gmail；不执行销售发信和日历动作，不自动重试失败批次。
def run_sync(owner):
    run = None
    try:
        run = claim_run(owner)
        if run is None:
            return False
        backend = django_backend_from_environment(mailbox_id=str(run.mailbox_id))
        credentials = GmailCredential.objects.get(mailbox_id=run.mailbox_id).credentials
        service, refreshed = create_service_from_authorization(credentials)
        result = sync_gmail({"mailbox_id": str(run.mailbox_id), "mailbox_address": run.mailbox.address,
                             "access_token": "worker-authorized-service", "max_results": 20},
                            backend=backend, gmail_factory=lambda _token: service,
                            progress=partial(observe_email, run.pk, run.lease_token),
                            message_ids=run.message_ids or None)
        finish_run(run.pk, run.lease_token, result, refreshed)
        return True
    except Exception as error:
        logger.error("sync_worker_failed run_id=%s error_type=%s action=inspect_run_and_retry_explicitly", run.pk if run else None, type(error).__name__)
        if run:
            try:
                finish_run(run.pk, run.lease_token, {"status": "failed", "error": {"code": "worker_sync_failed"}})
            except Exception as report_error:
                logger.error("sync_failure_report_rejected run_id=%s error_type=%s action=inspect_lease", run.pk, type(report_error).__name__)
        return run is not None
    finally:
        connections.close_all()


# 功能：执行当前员工的一个公司画像任务。
# 输入：无外部参数；读取当前 Worker 的独立 Agent 配置。
# 输出：是否领取任务。
# 逻辑：每个工作单元独立创建 HTTP 客户端，领取一个任务以保留既定租约。
# 约束：异常记录后结束本单元，不重试任务；其他公司的单元继续。
def run_analysis():
    try:
        return bool(process_jobs_once(backend=django_backend_from_environment(), limit=1))
    except Exception as error:
        logger.error("analysis_worker_failed error_type=%s action=inspect_job_and_lease", type(error).__name__)
        return False
    finally:
        connections.close_all()
