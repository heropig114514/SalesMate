"""职责：在独立进程中执行邮箱同步和公司分析。
实现：同步单元先消费人工补抽取，再按独立凭证分发 Gmail/QQ 检查点；公司分析使用独立 HTTP 客户端。
关联：crm_worker 调度本模块；Agent 的 Gmail/L1–L4 仍通过 HTTP 业务协议读写。
目录：
- worker_owner：核验本机 Agent 服务令牌绑定的员工。
- run_sync：领取并执行一个持久邮箱批次。
- run_analysis：领取并执行一个公司任务。
变量索引：
- logger：工作单元生命周期和安全错误日志。
"""
import hashlib
import logging
import os

from django.db import connections
from django.core.management.base import CommandError

from agent.clients.backend_api import django_backend_from_environment
from agent.config import load_environment
from agent.tools.gmail import create_service_from_authorization
from agent.tools import qq_mail
from agent.workflows.orchestration import process_jobs_once

from .access import Conflict, InvalidState
from .models import AgentCredential, GmailCredential, QQCredential
from .qq_connection import authorization_code
from .qq_sync import sync_persisted as sync_qq
from .processing import claim_run, finish_run
from .durable_sync import sync_persisted
from .lineage import run_repair

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


# 功能：执行一个员工邮箱的持久同步批次。
# 输入：`owner` 为已认证 Worker 员工。
# 输出：是否领取了工作；最终状态保存数据库。
# 逻辑：优先人工修复；QQ 解密后建立只读 IMAP，其他批次沿 Gmail 原路径；QQ 受控错误另记可操作说明。
# 约束：只读 Gmail/QQ，不执行销售发信或日历，不自动重试失败；QQ 连接在 finally 释放。
def run_sync(owner):
    run = None
    qq_client = None
    qq_credential = None
    try:
        if run_repair(owner):
            return True
        run = claim_run(owner)
        if run is None:
            return False
        backend = django_backend_from_environment(mailbox_id=str(run.mailbox_id))
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
        logger.error("sync_worker_failed run_id=%s error_type=%s action=inspect_run_and_retry_explicitly", run.pk if run else None, type(error).__name__)
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
