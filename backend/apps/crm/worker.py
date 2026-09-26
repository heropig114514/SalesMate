"""职责：在独立进程中执行邮箱同步和公司分析。
实现：按显式员工执行同步与画像，每个工作单元持有账号共享锁并使用可撤销的独立 HTTP 身份；外部调用后按 Django 生命周期清理失效连接，异常记录执行阶段和无敏感正文的调用位置。
关联：crm_worker 共享调度，dispatch 管理临时凭证；Gmail/QQ 与 L1–L4 仍通过 HTTP 协议读写。
目录：
- error_location：提取异常链的安全代码位置。
- run_sync：领取并执行一个持久邮箱批次。
- run_analysis：领取并执行一个公司任务。
变量索引：
- logger：工作单元生命周期和安全错误日志。
"""
import logging
from pathlib import Path

from django.db import close_old_connections, connections
from apps.accounts.reset_locks import account_work

from agent.tools.gmail import create_service_from_authorization
from agent.tools import qq_mail
from agent.workflows.orchestration import process_jobs_once

from .models import GmailCredential, QQCredential
from .dispatch import scoped_backend
from .qq_connection import authorization_code
from .qq_sync import sync_persisted as sync_qq
from .processing import claim_run, expire_runs, finish_run
from .durable_sync import sync_persisted
from .lineage import run_repair

logger = logging.getLogger("salesmate.crm_worker")


# 功能：提取诊断所需异常类型和调用帧，不输出异常正文或局部变量。
# 输入：`error` 为捕获的异常实例。
# 输出：异常链各层类型、文件名、函数名和行号组成的字符串。
# 逻辑：沿显式 cause 或未隐藏 context 遍历，使用已见集合防止异常链循环。
# 约束：不读取源码行、不序列化异常参数、凭证或 HTTP 正文；完整路径不入日志。
def error_location(error):
    parts, seen = [], set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        frames, frame = [], error.__traceback__
        while frame is not None:
            code = frame.tb_frame.f_code
            frames.append(f"{Path(code.co_filename).name}:{code.co_name}:{frame.tb_lineno}")
            frame = frame.tb_next
        parts.append(f"{type(error).__name__}[{' > '.join(frames)}]")
        error = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
    return " <- ".join(parts)


# 功能：执行一个员工邮箱的持久同步批次。
# 输入：`owner` 为共享调度器选出的员工。
# 输出：是否领取了工作；最终状态保存数据库。
# 逻辑：共享锁覆盖整个单元及异常回报；显式结束过期批次后优先人工修复，使用临时身份分发 Gmail/QQ；外部等待后清理失效连接再写终态，stage 标识失败边界。
# 约束：只读 Gmail/QQ，不执行销售发信或日历，不自动重试失败；错误正文不入日志，QQ 连接在 finally 释放。
@account_work
def run_sync(owner):
    run = None
    qq_client = None
    qq_credential = None
    stage = "expire_runs"
    try:
        expire_runs(owner)
        stage = "repair"
        if run_repair(owner):
            return True
        stage = "claim_run"
        run = claim_run(owner)
        if run is None:
            return False
        stage = "backend_identity"
        with scoped_backend(owner, str(run.mailbox_id)) as backend:
            stage = "load_credentials"
            qq_credential = QQCredential.objects.filter(mailbox_id=run.mailbox_id).first()
            if qq_credential:
                stage = "qq_connect"
                qq_client = qq_mail.connect(run.mailbox.address, authorization_code(qq_credential))
                stage = "qq_sync"
                result, refreshed = sync_qq(run, qq_client, backend), None
            else:
                credentials = GmailCredential.objects.get(mailbox_id=run.mailbox_id).credentials
                stage = "gmail_authorize"
                service, refreshed = create_service_from_authorization(credentials)
                stage = "gmail_sync"
                result = sync_persisted(run, service, backend)
            stage = "finish_run"
            close_old_connections()
            finish_run(run.pk, run.lease_token, result, refreshed)
            stage = "revoke_identity"
        return True
    except Exception as error:
        logger.error("sync_worker_failed owner_id=%s run_id=%s stage=%s error_type=%s location=%s action=inspect_run_and_retry_explicitly", owner.pk, run.pk if run else None, stage, type(error).__name__, error_location(error))
        if run:
            try:
                close_old_connections()
                finish_run(run.pk, run.lease_token, {"status": "failed", "error": {"code": "worker_sync_failed"}})
            except Exception as report_error:
                logger.error("sync_failure_report_rejected run_id=%s stage=report_failure error_type=%s location=%s action=inspect_lease", run.pk, type(report_error).__name__, error_location(report_error))
        return run is not None
    finally:
        if qq_client is not None:
            qq_mail.disconnect(qq_client)
        connections.close_all()


# 功能：执行当前员工的一个公司画像任务。
# 输入：`owner` 为共享调度器选出的员工。
# 输出：是否领取任务。
# 逻辑：共享锁覆盖单元；创建独立临时身份和 HTTP 客户端，按该员工领取一个任务。
# 约束：异常仅记录类型与代码位置后结束本单元，不记录正文或重试任务；其他公司的单元继续。
@account_work
def run_analysis(owner):
    try:
        with scoped_backend(owner) as backend:
            return bool(process_jobs_once(backend=backend, limit=1))
    except Exception as error:
        logger.error("analysis_worker_failed owner_id=%s error_type=%s location=%s action=inspect_job_and_lease", owner.pk, type(error).__name__, error_location(error))
        return False
    finally:
        connections.close_all()
