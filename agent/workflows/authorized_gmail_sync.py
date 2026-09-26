"""职责：领取员工网页 Gmail 有界批次，一次处理后继续公司分析并退出。
实现：传递冻结范围和明确重试 ID，缺少范围时拒绝执行；回报结果与刷新授权。
关联：BackendClient 提供员工队列，gmail_sync 执行范围和去重，orchestration 处理公司任务。
目录：
- sync_authorized_mailboxes_once：领取有界邮箱请求并回报执行结果。
变量索引：
- logger：不含授权的同步日志。
- __all__：公开的单次同步入口。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Mapping

from agent.clients.backend_api import BackendClient
from agent.tools.gmail import create_service_from_authorization
from agent.workflows.customer_analysis import bailian_analysis_provider
from agent.workflows.gmail_sync import sync_gmail
from agent.workflows.l1_email import bailian_extraction_provider
from agent.workflows.orchestration import process_jobs_once

logger = logging.getLogger("salesmate.agent.authorized_gmail_sync")

# 功能：处理一次网页授权邮箱批次。
# 输入：`backend` 为员工后端客户端；`limit` 为领取上限；`extraction_provider` 为 L1；`analysis_provider` 为公司分析函数。
# 输出：邮箱同步结果列表；每次结果先回报后执行公司任务。
# 逻辑：拒绝无范围的旧请求，冻结范围或显式 ID 透传；单邮箱异常转失败报告，模型任务逐个领取。
# 约束：不自动全量同步或重试失败；日志不输出凭证，HTTP 回报错误传播。
def sync_authorized_mailboxes_once(
    *,
    backend: BackendClient,
    limit: int = 5,
    extraction_provider: Callable[[str, str], str] = bailian_extraction_provider,
    analysis_provider: Callable[[Mapping[str, Any]], str] = bailian_analysis_provider,
) -> list[dict[str, Any]]:
    """领取网页同步请求，先回报 Gmail/L1，再在后台继续处理公司任务。"""
    claims = backend.claim_mailbox_syncs(limit)
    logger.info("authorized_gmail_claimed count=%s limit=%s", len(claims), limit)
    reports: list[dict[str, Any]] = []
    for claim in claims:
        mailbox_id = str(claim["mailbox_id"])
        logger.info("authorized_gmail_started mailbox_id=%s", mailbox_id)
        refreshed_authorization: dict[str, Any] | None = None
        try:
            if not claim.get("sync_options") and not claim.get("message_ids"):
                raise ValueError("This Gmail batch has no scope. Select a scope and sync again.")
            service, refreshed_authorization = create_service_from_authorization(
                claim["authorization"]
            )
            if hasattr(backend, "mailbox_id"):
                backend.mailbox_id = mailbox_id
            sync_result = sync_gmail(
                {
                    "mailbox_id": mailbox_id,
                    "access_token": "backend-oauth-service",
                    "mailbox_address": claim["mailbox_address"],
                    "max_results": claim.get("max_results", 20),
                    "sync_options": claim.get("sync_options", {}),
                },
                backend=backend,
                message_ids=claim.get("message_ids") or None,
                gmail_factory=lambda _token, gmail_service=service: gmail_service,
                extraction_provider=extraction_provider,
            )
            status = sync_result["status"]
            error = (
                sync_result.get("error", {}).get("message")
                if isinstance(sync_result.get("error"), Mapping)
                else None
            )
        except Exception as exception:
            logger.warning(
                "authorized_gmail_failed mailbox_id=%s error_type=%s",
                mailbox_id, type(exception).__name__,
            )
            status = "failed"
            error = f"{type(exception).__name__}: {exception}"
            sync_result = {
                "mailbox_id": mailbox_id,
                "status": "failed",
                "error": {"code": "gmail_sync_failed", "message": error},
            }

        backend.report_mailbox_sync(
            {
                "mailbox_id": mailbox_id,
                "status": status,
                "sync_result": sync_result,
                "error": error,
                "authorization": refreshed_authorization,
            }
        )
        logger.info(
            "authorized_gmail_reported mailbox_id=%s status=%s fetched=%s failed=%s",
            mailbox_id, status, sync_result.get("fetched_count"),
            sync_result.get("failed_email_count"),
        )
        reports.append(sync_result)

    # 邮箱读取和逐封保存完成后，前端已经可以看到同步结果。公司画像继续
    # 通过后端 Job 独立处理；一次只领取一个，避免等待中的任务租约过期。
    job_reports: list[dict[str, Any]] = []
    while True:
        batch = process_jobs_once(
            backend=backend,
            limit=1,
            analysis_provider=analysis_provider,
        )
        if not batch:
            break
        job_reports.extend(batch)
    for result in reports:
        if result.get("status") == "completed":
            result["job_reports"] = job_reports
    logger.info("authorized_gmail_completed mailbox_count=%s analysis_jobs=%s", len(reports), len(job_reports))
    return reports


__all__ = ["sync_authorized_mailboxes_once"]
