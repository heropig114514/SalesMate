"""职责：编排 Gmail 同步、并发 L1、逐封提交和可选进度观察。
实现：保留既有默认参数及逐封处理辅助函数；Worker 使用持久检查点编排，网页领取的 CLI 批次严格执行冻结范围，底层旧游标接口异常强失败。
关联：软件 Worker 使用本模块，Gmail 工具提供原文，后端 HTTP 客户端保存业务数据。
目录：
- sync_gmail：同步一个邮箱并逐封保存 L1 结果。
- sync_gmail.observe：转发进度并收集读取失败 ID。
- _extract_new_or_retryable_emails：复用完成抽取或执行逐封重做。
- _process_email_candidates：按完成顺序产出并发 L1。
- _process_email_candidates.run：执行一封候选邮件的抽取。
- _submit_emails_individually：隔离逐封后端提交失败。
- _merge_submission_totals：合并提交汇总。
- _email_message_id：读取标准邮件 Gmail ID。
- _email_error：将异常转换为现有 Agent 错误对象。
- _email_result_error：组装逐封错误表示。
- _can_reuse_stored_extraction：判断同版本可信终态是否可复用。
- _is_current_failed_extraction：判断当前版本失败事实。
- _get_sync_state：读取兼容后端的同步游标。
- _read_email_batch：选择增量或首次同步范围。
- _save_sync_state：保存游标和待处理消息清单。
- _message_ids：读取合法消息数组。
- _unique_message_ids：稳定去重 Gmail 消息标识。
- _failed：构造批次失败汇总。
变量索引：
- logger：同步阶段安全日志。
- EMAIL_EXTRACTION_WORKERS：既定最多四路 L1 并发。
- __all__：公开 sync_gmail 接口。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import logging
from time import perf_counter
from typing import Any, Callable, Iterator, Mapping

from agent.clients.backend_api import BackendClient
from agent.tools.gmail_scope import gmail_message_limit, scoped_message_pages
from agent.tools.gmail import (
    GmailHistoryExpiredError,
    create_service,
    get_profile_history_id,
    list_history_message_ids,
    list_sync_message_ids,
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
logger = logging.getLogger("salesmate.agent.gmail_sync")


# 功能：同步一个邮箱并逐封保存 L1 结果。
# 输入：`authorization` 为授权或邮箱同步请求对象；`backend` 为业务后端协议客户端；`gmail_factory` 为令牌到 SDK 的构造函数；`extraction_provider` 为单封邮件事实抽取函数；`progress` 为可选阶段回调；`message_ids` 为指定 Gmail ID 数组，None 表示既有扫描范围。
# 输出：Gmail 同步汇总字典。
# 逻辑：有冻结范围时执行默认 50 封或已批准上限，明确重试也校验封数；读取前按已存天然键去重，保留范围外失败状态但不计入本批失败数；否则沿底层游标协议，四路抽取逐封提交。
# 约束：不执行 L2–L4；旧汇总状态兼容，软件 Worker 另行计算 partial；配置和评分不变。
def sync_gmail(
    authorization: Mapping[str, Any],
    *,
    backend: BackendClient,
    gmail_factory: Callable[[str], object] = create_service,
    extraction_provider: Callable[[str, str], str] = bailian_extraction_provider,
    progress=None,
    message_ids: list[str] | None = None,
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

    started = perf_counter()
    logger.info(
        "gmail_sync_started mailbox_id=%s max_results=%s explicit_ids=%s",
        mailbox_id, max_results, len(message_ids) if message_ids is not None else None,
    )
    read_failures = []
    skipped_before_read = 0

    # 功能：转发进度并收集读取失败 ID。
    # 输入：`stage` 为当前处理阶段；`data` 为阶段关联数据。
    # 输出：无。
    # 逻辑：记录 fetching 失败供游标重试，再调用外部观察者。
    # 约束：回调错误不吞掉，不携带授权字段。
    def observe(stage, data):
        if stage == "failed" and data.get("stage") == "fetching":
            read_failures.append(data["gmail_message_id"])
        if progress:
            progress(stage, data)

    observer = observe if progress else None
    try:
        service = gmail_factory(access_token)
        resolved_address = resolve_mailbox_address(service, mailbox_address)
        sync_state = _get_sync_state(backend, mailbox_id)
        if message_ids is not None:
            if len(message_ids) > gmail_message_limit(authorization.get("sync_options") or {}):
                raise ValueError("重试邮件数超过本批允许的封数，请先批准相应的同步范围。")
            emails = read_messages(service, message_ids, progress=observer)
            next_cursor = (sync_state or {}).get("cursor")
            pending_message_ids = (sync_state or {}).get("scope", {}).get("pending_message_ids", [])
            sync_mode = "explicit_retry"
        elif "sync_options" in authorization:
            emails = []
            failed_ids = set((sync_state or {}).get("scope", {}).get("failed_message_ids", []))
            for page in scoped_message_pages(service, authorization["sync_options"], max_results):
                unread = [value for value in page if value not in failed_ids and backend.get_stored_email(mailbox_id, f"{resolved_address.casefold()}:{value}") is None]
                skipped_before_read += len(page) - len(unread)
                emails.extend(read_messages(service, unread, progress=observer))
            next_cursor = (sync_state or {}).get("cursor")
            pending_message_ids = (sync_state or {}).get("scope", {}).get("pending_message_ids", [])
            sync_mode = "bounded"
        else:
            emails, next_cursor, pending_message_ids, sync_mode = _read_email_batch(
                service, max_results, sync_state, progress=observer,
            )
    except Exception as error:
        logger.warning(
            "gmail_sync_failed mailbox_id=%s stage=read error_type=%s duration_ms=%s",
            mailbox_id, type(error).__name__, round((perf_counter() - started) * 1000),
        )
        return _failed(
            mailbox_id,
            "gmail_authorization_required",
            f"Gmail 读取失败：{type(error).__name__}: {error}",
        )

    logger.info(
        "gmail_sync_fetched mailbox_id=%s mode=%s fetched=%s pending=%s read_failures=%s",
        mailbox_id, sync_mode, len(emails), len(pending_message_ids), len(read_failures),
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
            progress=observer,
        )
    except Exception as error:
        logger.warning(
            "gmail_sync_failed mailbox_id=%s stage=lookup_or_extract error_type=%s duration_ms=%s",
            mailbox_id, type(error).__name__, round((perf_counter() - started) * 1000),
        )
        return _failed(
            mailbox_id,
            "backend_email_lookup_failed",
            f"已保存邮件查询失败：{type(error).__name__}: {error}",
            fetched_count=len(emails),
        )

    skipped_existing_count += skipped_before_read
    retry_message_ids = _unique_message_ids(
        [*read_failures, *retry_message_ids, *l1_failed_message_ids, *submission_retry_ids]
    )
    if message_ids is not None:
        previous_failed = (sync_state or {}).get("scope", {}).get("failed_message_ids", [])
        retry_message_ids = _unique_message_ids([*[item for item in previous_failed if item not in (message_ids or [])], *retry_message_ids])
    state_retry_message_ids = retry_message_ids
    if sync_mode == "bounded":
        previous_failed = (sync_state or {}).get("scope", {}).get("failed_message_ids", [])
        state_retry_message_ids = _unique_message_ids([*previous_failed, *retry_message_ids])
    cursor_saved = _save_sync_state(
        backend,
        mailbox_id,
        sync_state,
        next_cursor,
        pending_message_ids,
        state_retry_message_ids,
    )
    logger.info(
        "gmail_sync_completed mailbox_id=%s fetched=%s processed=%s skipped=%s l1_failed=%s submit_failed=%s retry=%s cursor_saved=%s duration_ms=%s",
        mailbox_id, len(emails), l1_processed_count, skipped_existing_count,
        len(l1_failed_message_ids), len(submission_retry_ids), len(retry_message_ids),
        cursor_saved, round((perf_counter() - started) * 1000),
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


# 功能：复用完成抽取或执行逐封重做。
# 输入：`emails` 为已读取的标准邮件数组；`mailbox_address` 为授权账号完整地址；`mailbox_id` 为后端邮箱标识；`backend` 为业务后端协议客户端；`extraction_provider` 为单封邮件事实抽取函数；`progress` 为可选阶段回调。
# 输出：统计、跳过数、三类失败 ID、错误数组及处理数。
# 逻辑：先查同版本终态，再并发抽取和逐封提交，分别回报处理阶段。
# 约束：失败事实只允许补为成功，不覆盖既有完成记录。
def _extract_new_or_retryable_emails(
    emails: list[dict],
    mailbox_address: str,
    mailbox_id: str,
    backend: BackendClient,
    extraction_provider: Callable[[str, str], str],
    progress=None,
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
                    logger.warning(
                        "gmail_email_failed mailbox_id=%s message_id=%s stage=lookup error_type=%s",
                        mailbox_id, message_id, type(error).__name__,
                    )
                    retry_message_ids.append(message_id)
                    if progress:
                        progress("failed", {"gmail_message_id": message_id, "stage": "lookup", "code": "stored_email_lookup_failed"})
                    email_errors.append(
                        _email_error(message_id, "lookup", "stored_email_lookup_failed", error)
                    )
                    continue
            if _can_reuse_stored_extraction(stored):
                logger.info("gmail_email_reused mailbox_id=%s message_id=%s", mailbox_id, message_id)
                skipped_existing_count += 1
                if progress:
                    progress("completed", {"gmail_message_id": message_id})
                continue
            candidates.append((email, stored))

    processed = _process_email_candidates(
        candidates,
        mailbox_address,
        extraction_provider,
        progress=progress,
    )
    for email, stored, submission, error in processed:
        message_id = _email_message_id(email)
        if error is not None:
            logger.warning(
                "gmail_email_failed mailbox_id=%s message_id=%s stage=l1 error_type=%s",
                mailbox_id, message_id, type(error).__name__,
            )
            if progress:
                progress("failed", {"gmail_message_id": message_id, "stage": "extracting", "code": "email_processing_failed"})
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
            if progress:
                progress("failed", {"gmail_message_id": message_id, "stage": "extracting", "code": "extraction_failed"})
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
        if progress:
            progress("persisting", {"gmail_message_id": message_id})
        current, current_retry_ids, current_errors = _submit_emails_individually(
            backend, [submission]
        )
        logger.info(
            "gmail_email_processed mailbox_id=%s message_id=%s extract_status=%s submit_failed=%s created=%s updated=%s duplicate=%s",
            mailbox_id, message_id, submission.get("extract_status"), bool(current_retry_ids),
            current.get("created_count", 0), current.get("updated_count", 0),
            current.get("duplicate_count", 0),
        )
        if progress:
            failed = bool(current_retry_ids) or submission.get("extract_status") == "failed"
            progress("failed" if failed else "completed", {"gmail_message_id": message_id, "stage": "persisting" if current_retry_ids else "extracting", "code": "backend_submit_failed" if current_retry_ids else "extraction_failed"})
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


# 功能：按完成顺序产出并发 L1。
# 输入：`candidates` 为邮件与已有记录的候选数组；`mailbox_address` 为授权账号完整地址；`extraction_provider` 为单封邮件事实抽取函数；`progress` 为可选阶段回调。
# 输出：包含邮件、原记录、提交对象或异常的迭代器。
# 逻辑：主线程顺序回报抽取开始，线程池最多四路执行模型，单封完成即产出。
# 约束：不并发操作 Gmail SDK 或进度观察器；只有模型调用在线程池中并发。
def _process_email_candidates(
    candidates: list[tuple[dict, object]],
    mailbox_address: str,
    extraction_provider: Callable[[str, str], str],
    progress=None,
) -> Iterator[tuple[dict, object, dict | None, Exception | None]]:
    """最多四路并发执行 L1，按完成顺序产出结果并隔离单封异常。"""
    if not candidates:
        return

    # 功能：执行一封候选邮件的抽取。
    # 输入：`candidate` 为一封邮件与原记录元组。
    # 输出：邮件与抽取结果或异常四元组。
    # 逻辑：调用 process_email，捕获本封异常；进度由提交线程统一回报。
    # 约束：不从工作线程调用可能写数据库的观察器。
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
        futures = []
        for candidate in candidates:
            if progress:
                progress(
                    "extracting",
                    {"gmail_message_id": _email_message_id(candidate[0])},
                )
            futures.append(pool.submit(run, candidate))
        for future in as_completed(futures):
            yield future.result()


# 功能：隔离逐封后端提交失败。
# 输入：`backend` 为业务后端协议客户端；`submissions` 为标准邮件提交数组。
# 输出：汇总、待重试 ID 与错误数组。
# 逻辑：每次提交单元素数组，错误记录后继续其他邮件。
# 约束：保持后端批量事务契约，不隐式修改已保存事实。
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
            logger.warning(
                "gmail_email_failed message_id=%s stage=submission error_type=%s",
                message_id, type(error).__name__,
            )
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


# 功能：合并提交汇总。
# 输入：`target` 为可修改的目标汇总；`source` 为来源汇总。
# 输出：无，修改目标字典。
# 逻辑：累计数量并去重受影响公司。
# 约束：仅内存更新，不改业务记录。
def _merge_submission_totals(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    """把一封邮件的后端结果合并进本轮统计。"""
    for field in ("created_count", "updated_count", "duplicate_count"):
        target[field] += int(source.get(field, 0))
    affected = target["affected_company_ids"]
    for company_id in source.get("affected_company_ids", []):
        if company_id not in affected:
            affected.append(company_id)


# 功能：读取标准邮件 Gmail ID。
# 输入：`email` 为标准邮件映射。
# 输出：有效字符串或 None。
# 逻辑：验证类型并去掉两端空白。
# 约束：不创造缺失 ID。
def _email_message_id(email: Mapping[str, Any]) -> str | None:
    value = email.get("gmail_message_id")
    return value.strip() if isinstance(value, str) and value.strip() else None


# 功能：将异常转换为现有 Agent 错误对象。
# 输入：`message_id` 为指定 Gmail 消息 ID；`stage` 为当前处理阶段；`code` 为错误代码；`error` 为异常对象。
# 输出：错误字典。
# 逻辑：保留异常类型和文本后委托结构组装。
# 约束：软件进度接口只保存受控代码和安全说明，不转发该原异常文本。
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


# 功能：组装逐封错误表示。
# 输入：`message_id` 为指定 Gmail 消息 ID；`stage` 为当前处理阶段；`code` 为错误代码；`message` 为错误描述原值。
# 输出：带消息 ID、阶段、代码和说明的字典。
# 逻辑：统一字符串消息与缺省错误说明。
# 约束：此函数不记录日志或写数据库。
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


# 功能：判断同版本可信终态是否可复用。
# 输入：`stored` 为已保存抽取表示。
# 输出：布尔值。
# 逻辑：检查提取版本和 completed/skipped_non_business 状态。
# 约束：None 不可复用，非法非对象抛 TypeError。
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


# 功能：判断当前版本失败事实。
# 输入：`stored` 为已保存抽取表示。
# 输出：布尔值。
# 逻辑：匹配版本和 failed 状态。
# 约束：无网络和存储副作用。
def _is_current_failed_extraction(stored: object) -> bool:
    """判断后端是否已保存当前 Prompt 的失败结果。"""
    return bool(
        isinstance(stored, Mapping)
        and stored.get("extract_prompt_version") == EXTRACT_PROMPT_VERSION
        and stored.get("extract_status") == "failed"
    )


# 功能：读取兼容后端的同步游标。
# 输入：`backend` 为业务后端协议客户端；`mailbox_id` 为后端邮箱标识。
# 输出：状态字典或 None。
# 逻辑：确认读写方法存在并验证版本类型。
# 约束：只有旧后端未实现游标协议才返回 None；读写协议存在时的错误与非法状态必须失败。
def _get_sync_state(
    backend: BackendClient, mailbox_id: str
) -> dict[str, Any] | None:
    """读取支持游标的后端状态；读取失败不能伪装为首次同步。"""
    reader = getattr(backend, "get_sync_state", None)
    writer = getattr(backend, "save_sync_state", None)
    if not callable(reader) or not callable(writer):
        return None
    state = reader(mailbox_id)
    if not isinstance(state, Mapping) or type(state.get("version")) is not int:
        raise ValueError("同步游标响应缺少有效版本。")
    return dict(state)


# 功能：选择增量或首次同步范围。
# 输入：`service` 为已授权 Gmail SDK 客户端；`limit` 为调用方明确的数量上限；`sync_state` 为后端同步状态或 None；`progress` 为可选阶段回调。
# 输出：邮件、下一游标、剩余 ID、模式四元组。
# 逻辑：先选 pending/新增/failed，按上限读取；游标失效合并已持久化清单与最近扫描。
# 约束：保持历史过期处理的现有边界；进度回调只扩展观察和单封读取隔离。
def _read_email_batch(
    service,
    limit: int,
    sync_state: Mapping[str, Any] | None,
    progress=None,
) -> tuple[list[dict], str | None, list[str], str]:
    """按历史游标读取新增与待重试邮件；无游标时执行一次最近邮件扫描。"""
    if sync_state is None:
        return (read_sync_emails(service, limit=limit, progress=progress) if progress else read_sync_emails(service, limit=limit)), None, [], "recent_fallback"

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
                (read_messages(service, selected, progress=progress) if progress else read_messages(service, selected)),
                next_cursor,
                candidates[limit:],
                "incremental",
            )
        except GmailHistoryExpiredError:
            if pending or failed:
                next_cursor = get_profile_history_id(service)
                recent = list_sync_message_ids(service, limit=limit)
                candidates = _unique_message_ids([*pending, *failed, *recent])
                selected = candidates[:limit]
                return (
                    read_messages(service, selected, progress=progress) if progress else read_messages(service, selected),
                    next_cursor, candidates[limit:], "recovered",
                )

    # 先取得游标再列邮件，避免扫描期间到达的新邮件被跳过；极端情况下
    # 同一封邮件会在下一轮再次出现，但后端 dedupe_key 仍保证保存幂等。
    next_cursor = get_profile_history_id(service)
    return (read_sync_emails(service, limit=limit, progress=progress) if progress else read_sync_emails(service, limit=limit)), next_cursor, [], "initial"


# 功能：保存游标和待处理消息清单。
# 输入：`backend` 为业务后端协议客户端；`mailbox_id` 为后端邮箱标识；`previous` 为读取时的同步状态及版本；`cursor` 为待保存的 Gmail 游标；`pending_message_ids` 为尚未处理的消息 ID；`failed_message_ids` 为待重试消息 ID。
# 输出：是否保存成功的布尔值。
# 逻辑：采用先读版本执行乐观锁保存。
# 约束：未配置游标协议返回 False；已配置协议的写入异常向上传播，不声明同步成功。
def _save_sync_state(
    backend: BackendClient,
    mailbox_id: str,
    previous: Mapping[str, Any] | None,
    cursor: str | None,
    pending_message_ids: list[str],
    failed_message_ids: list[str],
) -> bool:
    """提交成功后保存 Gmail 增量游标，写入异常必须向调用方报告。"""
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
    writer({"mailbox_id": mailbox_id, "cursor": cursor, "scope": next_scope,
            "last_synced_at": datetime.now(timezone.utc).isoformat(), "status": "ok", "version": previous["version"]})
    return True


# 功能：读取合法消息数组。
# 输入：`value` 为待验证原值。
# 输出：去重字符串列表。
# 逻辑：非数组为空，其余交统一规范化。
# 约束：不修改输入对象。
def _message_ids(value: object) -> list[str]:
    """从同步 scope 中只保留合法且不重复的 Gmail message ID。"""
    if not isinstance(value, list):
        return []
    return _unique_message_ids(value)


# 功能：稳定去重 Gmail 消息标识。
# 输入：`values` 为待规范化原值数组。
# 输出：按首次出现排序的字符串列表。
# 逻辑：去空白并排除非字符串和重复 ID。
# 约束：纯内存转换，无网络副作用。
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


# 功能：构造批次失败汇总。
# 输入：`mailbox_id` 为后端邮箱标识；`code` 为错误代码；`message` 为错误描述原值；`fetched_count` 为已读取邮件数量。
# 输出：带 code/message 的失败对象。
# 逻辑：保持原 HTTP/CLI 汇总字段与零值。
# 约束：不把失败报告当作邮件保存证明。
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
