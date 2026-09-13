"""前端可调用的 Gmail 同步服务函数。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Mapping

from agent.clients.backend_api import BackendClient
from agent.tools.gmail import (
    GmailHistoryExpiredError,
    create_service,
    get_profile_history_id,
    list_history_message_ids,
    read_messages,
    read_sync_emails,
    resolve_mailbox_address,
)
from agent.workflows.l1_email import (
    EXTRACT_PROMPT_VERSION,
    bailian_extraction_provider,
    process_email,
)


EMAIL_EXTRACTION_WORKERS = 4


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
        sync_state = _get_sync_state(backend, mailbox_id)
        emails, next_cursor, pending_message_ids, sync_mode = _read_email_batch(
            service,
            max_results,
            sync_state,
        )
    except Exception as error:
        return _failed(
            mailbox_id,
            "gmail_authorization_required",
            f"Gmail 读取失败：{type(error).__name__}: {error}",
        )

    try:
        (
            submitted,
            skipped_existing_count,
            retry_message_ids,
            l1_failed_message_ids,
            submission_retry_ids,
            email_errors,
            l1_processed_count,
        ) = _extract_new_or_retryable_emails(
            emails,
            resolved_address,
            mailbox_id,
            backend,
            extraction_provider,
        )
    except Exception as error:
        return _failed(
            mailbox_id,
            "backend_email_lookup_failed",
            f"已保存邮件查询失败：{type(error).__name__}: {error}",
            fetched_count=len(emails),
        )

    retry_message_ids = _unique_message_ids(
        [*retry_message_ids, *l1_failed_message_ids, *submission_retry_ids]
    )
    cursor_saved = _save_sync_state(
        backend,
        mailbox_id,
        sync_state,
        next_cursor,
        pending_message_ids,
        retry_message_ids,
    )

    return {
        "mailbox_id": mailbox_id,
        "status": "completed",
        "sync_mode": sync_mode,
        "cursor_saved": cursor_saved,
        "fetched_count": len(emails),
        "pending_message_count": len(pending_message_ids),
        "retry_message_count": len(retry_message_ids),
        "failed_email_count": len(retry_message_ids),
        "l1_processed_count": l1_processed_count,
        "skipped_existing_count": skipped_existing_count,
        "created_count": int(submitted.get("created_count", 0)),
        "updated_count": int(submitted.get("updated_count", 0)),
        "duplicate_count": int(submitted.get("duplicate_count", 0))
        + skipped_existing_count,
        "failed_extraction_count": len(l1_failed_message_ids),
        "failed_submission_count": len(submission_retry_ids),
        "email_errors": email_errors,
        "affected_company_ids": list(submitted.get("affected_company_ids", [])),
        "job_reports": [],
        "error": None,
    }


def _extract_new_or_retryable_emails(
    emails: list[dict],
    mailbox_address: str,
    mailbox_id: str,
    backend: BackendClient,
    extraction_provider: Callable[[str, str], str],
) -> tuple[
    dict[str, Any],
    int,
    list[str],
    list[str],
    list[str],
    list[dict[str, Any]],
    int,
]:
    """同版本完成记录直接复用；其余邮件并发抽取并在完成后立即逐封提交。"""
    reader = getattr(backend, "get_stored_email", None)
    if not callable(reader):
        candidates = [(email, None) for email in emails]
    else:
        candidates = []

    skipped_existing_count = 0
    retry_message_ids: list[str] = []
    l1_failed_message_ids: list[str] = []
    submission_retry_ids: list[str] = []
    email_errors: list[dict[str, Any]] = []
    submitted: dict[str, Any] = {
        "created_count": 0,
        "updated_count": 0,
        "duplicate_count": 0,
        "affected_company_ids": [],
    }
    if callable(reader):
        for email in emails:
            message_id = _email_message_id(email)
            stored = None
            if message_id is not None:
                dedupe_key = f"{mailbox_address.casefold()}:{message_id}"
                try:
                    stored = reader(mailbox_id, dedupe_key)
                except Exception as error:
                    retry_message_ids.append(message_id)
                    email_errors.append(
                        _email_error(message_id, "lookup", "stored_email_lookup_failed", error)
                    )
                    continue
            if _can_reuse_stored_extraction(stored):
                skipped_existing_count += 1
                continue
            candidates.append((email, stored))

    processed = _process_email_candidates(
        candidates,
        mailbox_address,
        extraction_provider,
    )
    for email, stored, submission, error in processed:
        message_id = _email_message_id(email)
        if error is not None:
            if message_id is not None:
                retry_message_ids.append(message_id)
                l1_failed_message_ids.append(message_id)
            email_errors.append(
                _email_error(message_id, "l1", "email_processing_failed", error)
            )
            continue
        assert submission is not None
        if (
            _is_current_failed_extraction(stored)
            and submission.get("extract_status") != "completed"
        ):
            # 后端只允许同版本 failed → completed。再次失败时保留原记录，
            # 同时把 message ID 留在游标状态中，供下一轮继续重试。
            skipped_existing_count += 1
            if message_id is not None:
                retry_message_ids.append(message_id)
                if submission.get("extract_status") == "failed":
                    l1_failed_message_ids.append(message_id)
            if submission.get("extract_status") == "failed":
                email_errors.append(
                    _email_result_error(
                        message_id,
                        "l1",
                        "extraction_failed",
                        submission.get("extract_error"),
                    )
                )
            continue
        if submission.get("extract_status") == "failed":
            if message_id is not None:
                l1_failed_message_ids.append(message_id)
            email_errors.append(
                _email_result_error(
                    message_id,
                    "l1",
                    "extraction_failed",
                    submission.get("extract_error"),
                )
            )
        current, current_retry_ids, current_errors = _submit_emails_individually(
            backend, [submission]
        )
        _merge_submission_totals(submitted, current)
        submission_retry_ids.extend(current_retry_ids)
        email_errors.extend(current_errors)
    return (
        submitted,
        skipped_existing_count,
        _unique_message_ids(retry_message_ids),
        _unique_message_ids(l1_failed_message_ids),
        _unique_message_ids(submission_retry_ids),
        email_errors,
        len(candidates),
    )


def _process_email_candidates(
    candidates: list[tuple[dict, object]],
    mailbox_address: str,
    extraction_provider: Callable[[str, str], str],
) -> Iterator[tuple[dict, object, dict | None, Exception | None]]:
    """最多四路并发执行 L1，按完成顺序产出结果并隔离单封异常。"""
    if not candidates:
        return

    def run(candidate: tuple[dict, object]):
        email, stored = candidate
        try:
            return email, stored, process_email(
                email, mailbox_address, extraction_provider
            ), None
        except Exception as error:
            return email, stored, None, error

    workers = min(EMAIL_EXTRACTION_WORKERS, len(candidates))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="salesmate-l1") as pool:
        futures = [pool.submit(run, candidate) for candidate in candidates]
        for future in as_completed(futures):
            yield future.result()


def _submit_emails_individually(
    backend: BackendClient,
    submissions: list[dict],
) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    """逐封提交邮件，避免单封冲突或校验错误回滚整批。"""
    totals: dict[str, Any] = {
        "created_count": 0,
        "updated_count": 0,
        "duplicate_count": 0,
        "affected_company_ids": [],
    }
    retry_message_ids: list[str] = []
    errors: list[dict[str, Any]] = []
    for submission in submissions:
        message_id = _email_message_id(submission)
        try:
            result = backend.submit_emails([submission])
        except Exception as error:
            if message_id is not None:
                retry_message_ids.append(message_id)
            errors.append(
                _email_error(message_id, "submission", "backend_submit_failed", error)
            )
            continue
        for field in ("created_count", "updated_count", "duplicate_count"):
            totals[field] += int(result.get(field, 0))
        for company_id in result.get("affected_company_ids", []):
            if company_id not in totals["affected_company_ids"]:
                totals["affected_company_ids"].append(company_id)
    return totals, _unique_message_ids(retry_message_ids), errors


def _merge_submission_totals(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    """把一封邮件的后端结果合并进本轮统计。"""
    for field in ("created_count", "updated_count", "duplicate_count"):
        target[field] += int(source.get(field, 0))
    affected = target["affected_company_ids"]
    for company_id in source.get("affected_company_ids", []):
        if company_id not in affected:
            affected.append(company_id)


def _email_message_id(email: Mapping[str, Any]) -> str | None:
    value = email.get("gmail_message_id")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _email_error(
    message_id: str | None,
    stage: str,
    code: str,
    error: Exception,
) -> dict[str, Any]:
    return _email_result_error(
        message_id,
        stage,
        code,
        f"{type(error).__name__}: {error}",
    )


def _email_result_error(
    message_id: str | None,
    stage: str,
    code: str,
    message: object,
) -> dict[str, Any]:
    return {
        "gmail_message_id": message_id,
        "stage": stage,
        "code": code,
        "message": str(message or "单封邮件处理失败。"),
    }


def _can_reuse_stored_extraction(stored: object) -> bool:
    """只有当前 Prompt 的可信终态可跳过；failed 必须继续抽取。"""
    if stored is None:
        return False
    if not isinstance(stored, Mapping):
        raise TypeError("已保存邮件必须是对象。")
    return (
        stored.get("extract_prompt_version") == EXTRACT_PROMPT_VERSION
        and stored.get("extract_status") in {"completed", "skipped_non_business"}
    )


def _is_current_failed_extraction(stored: object) -> bool:
    """判断后端是否已保存当前 Prompt 的失败结果。"""
    return bool(
        isinstance(stored, Mapping)
        and stored.get("extract_prompt_version") == EXTRACT_PROMPT_VERSION
        and stored.get("extract_status") == "failed"
    )


def _get_sync_state(
    backend: BackendClient, mailbox_id: str
) -> dict[str, Any] | None:
    """后端支持同步游标时读取状态；旧实现自动退回最近邮件扫描。"""
    reader = getattr(backend, "get_sync_state", None)
    writer = getattr(backend, "save_sync_state", None)
    if not callable(reader) or not callable(writer):
        return None
    try:
        state = reader(mailbox_id)
    except Exception:
        return None
    if not isinstance(state, Mapping) or type(state.get("version")) is not int:
        return None
    return dict(state)


def _read_email_batch(
    service,
    limit: int,
    sync_state: Mapping[str, Any] | None,
) -> tuple[list[dict], str | None, list[str], str]:
    """按历史游标读取新增与待重试邮件；无游标时执行一次最近邮件扫描。"""
    if sync_state is None:
        return read_sync_emails(service, limit=limit), None, [], "recent_fallback"

    cursor = sync_state.get("cursor")
    scope = sync_state.get("scope")
    scope = dict(scope) if isinstance(scope, Mapping) else {}
    pending = _message_ids(scope.get("pending_message_ids"))
    failed = _message_ids(scope.get("failed_message_ids"))
    if isinstance(cursor, str) and cursor.strip():
        try:
            added, next_cursor = list_history_message_ids(service, cursor.strip())
            candidates = _unique_message_ids([*pending, *added, *failed])
            selected = candidates[:limit]
            return (
                read_messages(service, selected),
                next_cursor,
                candidates[limit:],
                "incremental",
            )
        except GmailHistoryExpiredError:
            pass

    # 先取得游标再列邮件，避免扫描期间到达的新邮件被跳过；极端情况下
    # 同一封邮件会在下一轮再次出现，但后端 dedupe_key 仍保证保存幂等。
    next_cursor = get_profile_history_id(service)
    return read_sync_emails(service, limit=limit), next_cursor, [], "initial"


def _save_sync_state(
    backend: BackendClient,
    mailbox_id: str,
    previous: Mapping[str, Any] | None,
    cursor: str | None,
    pending_message_ids: list[str],
    failed_message_ids: list[str],
) -> bool:
    """邮件批次提交成功后尽力保存 Gmail 增量游标。"""
    writer = getattr(backend, "save_sync_state", None)
    if previous is None or cursor is None or not callable(writer):
        return False
    scope = previous.get("scope")
    next_scope = dict(scope) if isinstance(scope, Mapping) else {}
    next_scope.update(
        {
            "mode": "gmail_history",
            "query": "{in:inbox in:sent}",
            "pending_message_ids": pending_message_ids,
            "failed_message_ids": failed_message_ids,
        }
    )
    try:
        writer(
            {
                "mailbox_id": mailbox_id,
                "cursor": cursor,
                "scope": next_scope,
                "last_synced_at": datetime.now(timezone.utc).isoformat(),
                "status": "ok",
                "version": previous["version"],
            }
        )
    except Exception:
        # 游标只是性能优化。邮件已经由 dedupe_key 安全提交时，保存冲突
        # 不应把整次同步改成失败；下一轮会重新扫描最近邮件。
        return False
    return True


def _message_ids(value: object) -> list[str]:
    """从同步 scope 中只保留合法且不重复的 Gmail message ID。"""
    if not isinstance(value, list):
        return []
    return _unique_message_ids(value)


def _unique_message_ids(values: list[object]) -> list[str]:
    """按出现顺序规范化 Gmail message ID。"""
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            continue
        normalized = value.strip()
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


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
