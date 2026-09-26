"""Responsibility: Claim a bounded employee web Gmail batch, process once, continue company analysis, and exit.
Implementation: Forward frozen scope and explicit retry IDs, reject missing scope, and report results and refreshed authorization.
Relationships: BackendClient provides employee queues, gmail_sync enforces scope/deduplication, and orchestration handles company jobs.
Directory:
- sync_authorized_mailboxes_once: Claim bounded mailbox requests and report execution results.
Variable index:
- logger: Synchronization logs excluding authorization data.
- __all__: Public one-shot synchronization entry point.
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

# Function: Process one web-authorized mailbox batch.
# Inputs: `backend`: employee backend client; `limit`: claim limit; `extraction_provider`: L1 provider; `analysis_provider`: company analysis function.
# Outputs: List of mailbox synchronization results; report each result before processing company jobs.
# Logic: Reject legacy requests without scope and forward frozen scope or explicit IDs; convert individual mailbox exceptions to failure reports and claim model jobs one at a time.
# Constraints: No automatic full synchronization or failed-job retries; logs exclude credentials and HTTP report errors propagate.
def sync_authorized_mailboxes_once(
    *,
    backend: BackendClient,
    limit: int = 5,
    extraction_provider: Callable[[str, str], str] = bailian_extraction_provider,
    analysis_provider: Callable[[Mapping[str, Any]], str] = bailian_analysis_provider,
) -> list[dict[str, Any]]:
    """Claim web synchronization requests, report Gmail/L1 first, then continue company jobs in the background."""
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

    # After mailbox reads and individual email saves finish, the frontend can display synchronization results. Company profiles continue
    # independently through backend jobs, claimed one at a time to prevent waiting job leases from expiring.
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
