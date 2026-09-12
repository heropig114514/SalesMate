"""处理员工在网页中请求的 Gmail 同步，一次执行后立即退出。"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from agent.clients.backend_api import BackendClient
from agent.tools.gmail import create_service_from_authorization
from agent.workflows.customer_analysis import bailian_analysis_provider
from agent.workflows.gmail_sync import sync_gmail
from agent.workflows.l1_email import bailian_extraction_provider
from agent.workflows.orchestration import process_jobs_once


def sync_authorized_mailboxes_once(
    *,
    backend: BackendClient,
    limit: int = 5,
    extraction_provider: Callable[[str, str], str] = bailian_extraction_provider,
    analysis_provider: Callable[[Mapping[str, Any]], str] = bailian_analysis_provider,
) -> list[dict[str, Any]]:
    """领取网页同步请求，完成 Gmail、L1 和 L2-L4 后回报邮箱状态。"""
    claims = backend.claim_mailbox_syncs(limit)
    reports: list[dict[str, Any]] = []
    for claim in claims:
        mailbox_id = str(claim["mailbox_id"])
        refreshed_authorization: dict[str, Any] | None = None
        try:
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
                },
                backend=backend,
                gmail_factory=lambda _token, gmail_service=service: gmail_service,
                extraction_provider=extraction_provider,
            )
            if sync_result["status"] == "completed":
                # 百炼调用串行执行时可能持续一分钟以上。每次只在后端领取
                # 一个任务，完成并回报后再领取下一个，避免尚未开始处理的
                # 任务在本地 MVP 的租约窗口内提前过期。
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
                sync_result["job_reports"] = job_reports
            status = sync_result["status"]
            error = (
                sync_result.get("error", {}).get("message")
                if isinstance(sync_result.get("error"), Mapping)
                else None
            )
        except Exception as exception:
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
        reports.append(sync_result)
    return reports


__all__ = ["sync_authorized_mailboxes_once"]
