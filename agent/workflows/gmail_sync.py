"""前端可调用的 Gmail 同步服务函数。"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from agent.tools.gmail import create_service, read_sync_emails, resolve_mailbox_address
from agent.workflows.l1_email import bailian_extraction_provider, process_email
from agent.clients.backend_api import BackendClient


def sync_gmail(
    authorization: Mapping[str, Any],
    *,
    backend: BackendClient,
    gmail_factory: Callable[[str], object] = create_service,
    extraction_provider: Callable[[str, str], str] = bailian_extraction_provider,
) -> dict[str, Any]:
    """读取最近邮件、执行 L1 并提交；不在此函数内运行 L2-L4。"""
    mailbox_id = authorization.get("mailbox_id")
    access_token = authorization.get("access_token")
    mailbox_address = authorization.get("mailbox_address")
    max_results = authorization.get("max_results", 20)
    if not isinstance(mailbox_id, str) or not mailbox_id.strip():
        return _failed("", "invalid_authorization", "mailbox_id 不能为空。")
    if not isinstance(access_token, str) or not access_token.strip():
        return _failed(mailbox_id, "gmail_authorization_required", "需要 Gmail 授权。")
    if type(max_results) is not int or not 1 <= max_results <= 20:
        return _failed(mailbox_id, "invalid_authorization", "max_results 必须在 1 到 20 之间。")

    try:
        service = gmail_factory(access_token)
        resolved_address = resolve_mailbox_address(service, mailbox_address)
        emails = read_sync_emails(service, limit=max_results)
    except Exception as error:
        return _failed(
            mailbox_id,
            "gmail_authorization_required",
            f"Gmail 读取失败：{type(error).__name__}: {error}",
        )

    submissions = [
        process_email(email, resolved_address, extraction_provider) for email in emails
    ]
    try:
        submitted = backend.submit_emails(submissions)
    except Exception as error:
        return _failed(
            mailbox_id,
            "backend_submit_failed",
            f"邮件提交失败：{type(error).__name__}: {error}",
            fetched_count=len(emails),
        )

    return {
        "mailbox_id": mailbox_id,
        "status": "completed",
        "fetched_count": len(emails),
        "created_count": int(submitted.get("created_count", 0)),
        "updated_count": int(submitted.get("updated_count", 0)),
        "duplicate_count": int(submitted.get("duplicate_count", 0)),
        "failed_extraction_count": sum(
            item.get("extract_status") == "failed" for item in submissions
        ),
        "affected_company_ids": list(submitted.get("affected_company_ids", [])),
        "job_reports": [],
        "error": None,
    }


def _failed(
    mailbox_id: str,
    code: str,
    message: str,
    *,
    fetched_count: int = 0,
) -> dict[str, Any]:
    return {
        "mailbox_id": mailbox_id,
        "status": "failed",
        "fetched_count": fetched_count,
        "created_count": 0,
        "updated_count": 0,
        "duplicate_count": 0,
        "failed_extraction_count": 0,
        "affected_company_ids": [],
        "job_reports": [],
        "error": {"code": code, "message": message},
    }


__all__ = ["sync_gmail"]
