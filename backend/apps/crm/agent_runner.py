"""职责：在本地 MVP 中由后台触发一次 Agent Gmail 同步与分析链路。
实现：后台线程串行领取邮箱同步与公司分析任务，并合并运行期间收到的新请求。
关联：views 在授权或刷新时调度；Agent 通过 BackendAPIClient 领取并回报任务。

目录：
- schedule_agent_sync：启动后台 Agent，或将并发触发合并到下一轮。
- _run_agent_sync_loop：持续处理当前进程收到的邮箱同步与分析任务。

变量索引：
- logger：自动运行流程的日志记录器。
- _runner_lock：保护运行状态的进程内互斥锁。
- _runner_active：标记当前进程是否已有 Agent 线程运行。
- _rerun_requested：记录运行期间是否又收到同步请求。
- __all__：限制模块公开导出的调度入口。
"""

from __future__ import annotations

import logging
import sys
import threading

from django.conf import settings

logger = logging.getLogger("salesmate.agent_runner")

_runner_lock = threading.Lock()
_runner_active = False
_rerun_requested = False


# 功能：启动一个后台 Agent，已有任务运行时合并为下一轮处理。
# 输入：无参数；从 Django settings 读取自动运行开关。
# 输出：已启动或已合并请求时返回 True；功能关闭时返回 False。
# 逻辑：在锁内更新进程状态，仅为首个请求创建守护线程。
# 约束：状态只在当前后台进程内共享，适用于本地单进程 MVP。
def schedule_agent_sync() -> bool:
    if not settings.SALESMATE_AUTO_RUN_AGENT:
        logger.info("automatic_agent_sync_disabled")
        return False

    global _runner_active, _rerun_requested
    with _runner_lock:
        if _runner_active:
            _rerun_requested = True
            logger.info("automatic_agent_sync_coalesced")
            return True
        _runner_active = True

    threading.Thread(
        target=_run_agent_sync_loop,
        name="salesmate-agent-sync",
        daemon=True,
    ).start()
    logger.info("automatic_agent_sync_started")
    return True


# 功能：持续领取并处理当前进程收到的邮箱同步与分析任务。
# 输入：无参数；运行配置和后端连接均从当前环境加载。
# 输出：无返回值；通过后端 API 回报任务结果并写入日志。
# 逻辑：每轮批量同步邮箱，再逐个处理分析任务，队列与合并请求均为空时退出。
# 约束：异常只终止当前后台线程；finally 必须恢复运行状态并处理遗漏请求。
def _run_agent_sync_loop() -> None:
    global _runner_active, _rerun_requested
    restart = False
    try:
        project_dir = str(settings.PROJECT_DIR)
        if project_dir not in sys.path:
            sys.path.insert(0, project_dir)

        from agent.clients.backend_api import django_backend_from_environment
        from agent.config import load_environment
        from agent.workflows.authorized_gmail_sync import (
            sync_authorized_mailboxes_once,
        )
        from agent.workflows.customer_analysis import bailian_analysis_provider
        from agent.workflows.orchestration import process_jobs_once

        load_environment()
        backend = django_backend_from_environment()
        processed_mailboxes = 0
        processed_jobs = 0
        while True:
            reports = sync_authorized_mailboxes_once(backend=backend, limit=10)
            job_reports = process_jobs_once(
                backend=backend,
                limit=1,
                analysis_provider=bailian_analysis_provider,
            )
            processed_mailboxes += len(reports)
            processed_jobs += len(job_reports)
            with _runner_lock:
                rerun = _rerun_requested
                _rerun_requested = False
            if reports:
                logger.info(
                    "automatic_agent_sync_batch_completed count=%s statuses=%s",
                    len(reports),
                    [item.get("status") for item in reports],
                )
            if job_reports:
                logger.info(
                    "automatic_agent_jobs_batch_completed count=%s statuses=%s",
                    len(job_reports),
                    [item.get("status") for item in job_reports],
                )
            if not reports and not job_reports and not rerun:
                break
        logger.info(
            "automatic_agent_sync_finished mailboxes=%s jobs=%s",
            processed_mailboxes,
            processed_jobs,
        )
    except Exception:
        logger.exception("automatic_agent_sync_failed")
    finally:
        with _runner_lock:
            _runner_active = False
            restart = _rerun_requested
            _rerun_requested = False
        if restart:
            schedule_agent_sync()


__all__ = ["schedule_agent_sync"]
