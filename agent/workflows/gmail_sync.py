"""Responsibility: Orchestrate Gmail synchronization, concurrent L1, individual submissions, and optional progress observation.
Implementation: Preserve defaults and per-email helpers; workers orchestrate durable checkpoints, web-claimed CLI batches strictly enforce frozen scope, and legacy cursor failures remain explicit failures.
Relationships: Software workers use this module; Gmail tools provide raw content and backend HTTP clients persist business data.
Directory:
- sync_gmail: Synchronize one mailbox and persist L1 results per email.
- sync_gmail.observe: Forward progress and collect IDs of read failures.
- _extract_new_or_retryable_emails: Reuse completed extractions or reprocess individual emails.
- _process_email_candidates: Yield concurrent L1 results in completion order.
- _process_email_candidates.run: Extract facts from one candidate email.
- _submit_emails_individually: Isolate individual backend submission failures.
- _merge_submission_totals: Merge submission summaries.
- _email_message_id: Read the Gmail ID from a standard email.
- _email_error: Convert an exception to the existing agent error object.
- _email_result_error: Assemble an individual email error representation.
- _can_reuse_stored_extraction: Determine whether a trusted terminal state at the same version is reusable.
- _is_current_failed_extraction: Identify failed facts at the current version.
- _get_sync_state: Read the synchronization cursor from a compatible backend.
- _read_email_batch: Select incremental or initial synchronization scope.
- _save_sync_state: Save the cursor and pending message list.
- _message_ids: Read a valid message array.
- _unique_message_ids: Stably deduplicate Gmail message identifiers.
- _failed: Build a batch failure summary.
Variable index:
- logger: Safe synchronization-stage logs.
- EMAIL_EXTRACTION_WORKERS: Existing maximum L1 concurrency of four.
- __all__: Public sync_gmail interface.
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


# Function: Synchronize one mailbox and persist L1 results per email.
# Inputs: `authorization`: authorization or mailbox synchronization request object; `backend`: business backend protocol client; `gmail_factory`: token-to-SDK factory; `extraction_provider`: single-email fact extraction function; `progress`: optional stage callback; `message_ids`: selected Gmail IDs, or None for the existing scan scope.
# Outputs: Gmail synchronization summary dictionary.
# Logic: For frozen scopes, enforce the default 50 or approved limit, including explicit retries; deduplicate by stored natural keys before reads and preserve out-of-scope failures without counting them in this batch; otherwise use the legacy cursor protocol with four-way extraction and individual submissions.
# Constraints: Do not run L2-L4; preserve legacy summary states while software workers compute partial separately; configuration and scoring remain unchanged.
def sync_gmail(
    authorization: Mapping[str, Any],
    *,
    backend: BackendClient,
    gmail_factory: Callable[[str], object] = create_service,
    extraction_provider: Callable[[str, str], str] = bailian_extraction_provider,
    progress=None,
    message_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Read recent emails, run L1, and submit results; do not run L2-L4 inside this function."""
    mailbox_id = authorization.get("mailbox_id")
    access_token = authorization.get("access_token")
    mailbox_address = authorization.get("mailbox_address")
    max_results = authorization.get("max_results", 20)
    if not isinstance(mailbox_id, str) or not mailbox_id.strip():
        return _failed("", "invalid_authorization", "mailbox_id must not be empty.")
    if not isinstance(access_token, str) or not access_token.strip():
        return _failed(mailbox_id, "gmail_authorization_required", "Gmail authorization is required.")
    if type(max_results) is not int or not 1 <= max_results <= 20:
        return _failed(mailbox_id, "invalid_authorization", "max_results must be between 1 and 20.")

    started = perf_counter()
    logger.info(
        "gmail_sync_started mailbox_id=%s max_results=%s explicit_ids=%s",
        mailbox_id, max_results, len(message_ids) if message_ids is not None else None,
    )
    read_failures = []
    skipped_before_read = 0

    # Function: Forward progress and collect IDs of read failures.
    # Inputs: `stage`: current processing stage; `data`: associated stage data.
    # Outputs: Outputs: None.
    # Logic: Record fetching failures for cursor retries, then invoke the external observer.
    # Constraints: Do not swallow callback errors or include authorization fields.
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
                raise ValueError("The retry count exceeds this batch's approved message limit. Approve the corresponding sync scope first.")
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
            f"Gmail read failed: {type(error).__name__}: {error}",
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
            f"Saved email query failed: {type(error).__name__}: {error}",
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


# Function: Reuse completed extractions or reprocess individual emails.
# Inputs: `emails`: already-read standard email array; `mailbox_address`: authorized account's complete address; `mailbox_id`: backend mailbox identifier; `backend`: business backend protocol client; `extraction_provider`: single-email fact extraction function; `progress`: optional stage callback.
# Outputs: Statistics, skipped count, three categories of failed IDs, error array, and processed count.
# Logic: Check terminal states at the same version, then extract concurrently and submit individually, reporting each stage.
# Constraints: Failed facts may only be upgraded to success; do not overwrite completed records.
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
    """Reuse completed records at the same version; extract remaining emails concurrently and submit each immediately on completion."""
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
            # The backend permits only failed -> completed at the same version. Repeated failure preserves the original record
            # and retains the message ID in cursor state for retry in the next pass.
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


# Function: Yield concurrent L1 results in completion order.
# Inputs: `candidates`: array of emails and existing records; `mailbox_address`: authorized account's complete address; `extraction_provider`: single-email fact extraction function; `progress`: optional stage callback.
# Outputs: Iterator containing email, original record, and submission object or exception.
# Logic: The main thread reports extraction starts sequentially; the pool runs at most four model calls and yields each completed email.
# Constraints: Do not concurrently access the Gmail SDK or progress observer; only model calls run concurrently in the pool.
def _process_email_candidates(
    candidates: list[tuple[dict, object]],
    mailbox_address: str,
    extraction_provider: Callable[[str, str], str],
    progress=None,
) -> Iterator[tuple[dict, object, dict | None, Exception | None]]:
    """Run L1 with at most four concurrent calls, yielding completion-order results and isolating individual exceptions."""
    if not candidates:
        return

    # Function: Extract facts from one candidate email.
    # Inputs: `candidate`: tuple of one email and its original record.
    # Outputs: Four-tuple containing email, extraction result, or exception.
    # Logic: Call process_email and capture that email's exception; the submission thread reports progress centrally.
    # Constraints: Do not call observers that may write to the database from worker threads.
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


# Function: Isolate individual backend submission failures.
# Inputs: `backend`: business backend protocol client; `submissions`: standard email submission array.
# Outputs: Summary, retry IDs, and error array.
# Logic: Submit one-element arrays, recording errors and continuing other emails.
# Constraints: Preserve the backend batch transaction contract; do not implicitly modify stored facts.
def _submit_emails_individually(
    backend: BackendClient,
    submissions: list[dict],
) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    """Submit emails individually so one conflict or validation failure does not roll back the whole batch."""
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


# Function: Merge submission summaries.
# Inputs: `target`: mutable target summary; `source`: source summary.
# Outputs: None; mutate the target dictionary.
# Logic: Accumulate counts and deduplicate affected companies.
# Constraints: Update memory only, not business records.
def _merge_submission_totals(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    """Merge one email's backend result into this pass's statistics."""
    for field in ("created_count", "updated_count", "duplicate_count"):
        target[field] += int(source.get(field, 0))
    affected = target["affected_company_ids"]
    for company_id in source.get("affected_company_ids", []):
        if company_id not in affected:
            affected.append(company_id)


# Function: Read the Gmail ID from a standard email.
# Inputs: `email`: standard email mapping.
# Outputs: Valid string or None.
# Logic: Validate type and strip surrounding whitespace.
# Constraints: Do not invent missing IDs.
def _email_message_id(email: Mapping[str, Any]) -> str | None:
    value = email.get("gmail_message_id")
    return value.strip() if isinstance(value, str) and value.strip() else None


# Function: Convert an exception to the existing agent error object.
# Inputs: `message_id`: selected Gmail message ID; `stage`: current processing stage; `code`: error code; `error`: exception object.
# Outputs: Error dictionary.
# Logic: Preserve exception type and text, then delegate structure assembly.
# Constraints: Software progress endpoints persist only controlled codes and safe details, never this raw exception text.
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


# Function: Assemble an individual email error representation.
# Inputs: `message_id`: selected Gmail message ID; `stage`: current processing stage; `code`: error code; `message`: original error description.
# Outputs: Dictionary containing message ID, stage, code, and details.
# Logic: Normalize string messages and default error details.
# Constraints: This function neither logs nor writes to the database.
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
        "message": str(message or "Single-message processing failed."),
    }


# Function: Determine whether a trusted terminal state at the same version is reusable.
# Inputs: `stored`: persisted extraction representation.
# Outputs: Boolean.
# Logic: Check extraction version and completed/skipped_non_business status.
# Constraints: None is not reusable; invalid non-objects raise TypeError.
def _can_reuse_stored_extraction(stored: object) -> bool:
    """Skip only trusted terminal states for the current prompt; failed records must be extracted again."""
    if stored is None:
        return False
    if not isinstance(stored, Mapping):
        raise TypeError("Saved email must be an object.")
    return (
        stored.get("extract_prompt_version") == EXTRACT_PROMPT_VERSION
        and stored.get("extract_status") in {"completed", "skipped_non_business"}
    )


# Function: Identify failed facts at the current version.
# Inputs: `stored`: persisted extraction representation.
# Outputs: Boolean.
# Logic: Match the version and failed status.
# Constraints: No network or storage side effects.
def _is_current_failed_extraction(stored: object) -> bool:
    """Check whether the backend has saved a failed result for the current prompt."""
    return bool(
        isinstance(stored, Mapping)
        and stored.get("extract_prompt_version") == EXTRACT_PROMPT_VERSION
        and stored.get("extract_status") == "failed"
    )


# Function: Read the synchronization cursor from a compatible backend.
# Inputs: `backend`: business backend protocol client; `mailbox_id`: backend mailbox identifier.
# Outputs: State dictionary or None.
# Logic: Verify read/write methods exist and validate the version type.
# Constraints: Return None only for legacy backends without the cursor protocol; errors and invalid state must fail when the protocol exists.
def _get_sync_state(
    backend: BackendClient, mailbox_id: str
) -> dict[str, Any] | None:
    """Read state from a cursor-capable backend; never disguise a read failure as initial synchronization."""
    reader = getattr(backend, "get_sync_state", None)
    writer = getattr(backend, "save_sync_state", None)
    if not callable(reader) or not callable(writer):
        return None
    state = reader(mailbox_id)
    if not isinstance(state, Mapping) or type(state.get("version")) is not int:
        raise ValueError("Sync cursor response is missing a valid version.")
    return dict(state)


# Function: Select incremental or initial synchronization scope.
# Inputs: `service`: authorized Gmail SDK client; `limit`: explicit caller-specified count limit; `sync_state`: backend synchronization state or None; `progress`: optional stage callback.
# Outputs: Four-tuple of emails, next cursor, remaining IDs, and mode.
# Logic: Select pending/new/failed IDs first and read within the limit; on cursor expiration, merge persisted lists with a recent scan.
# Constraints: Preserve existing history-expiration boundaries; progress callbacks extend only observation and individual read-failure isolation.
def _read_email_batch(
    service,
    limit: int,
    sync_state: Mapping[str, Any] | None,
    progress=None,
) -> tuple[list[dict], str | None, list[str], str]:
    """Read new and retry emails from the history cursor; without a cursor, perform one recent-email scan."""
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

    # Obtain the cursor before listing emails so arrivals during the scan are not skipped. In edge cases,
    # the same email may appear in the next pass, but backend dedupe_key still guarantees idempotent persistence.
    next_cursor = get_profile_history_id(service)
    return (read_sync_emails(service, limit=limit, progress=progress) if progress else read_sync_emails(service, limit=limit)), next_cursor, [], "initial"


# Function: Save the cursor and pending message list.
# Inputs: `backend`: business backend protocol client; `mailbox_id`: backend mailbox identifier; `previous`: previously read state/version; `cursor`: Gmail cursor to save; `pending_message_ids`: unprocessed IDs; `failed_message_ids`: retry IDs.
# Outputs: Boolean indicating successful persistence.
# Logic: Save with optimistic locking using the previously read version.
# Constraints: Return False if the cursor protocol is absent; propagate write failures when configured and do not claim successful synchronization.
def _save_sync_state(
    backend: BackendClient,
    mailbox_id: str,
    previous: Mapping[str, Any] | None,
    cursor: str | None,
    pending_message_ids: list[str],
    failed_message_ids: list[str],
) -> bool:
    """Save the Gmail incremental cursor after successful submission; report write failures to the caller."""
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


# Function: Read a valid message array.
# Inputs: `value`: original value to validate.
# Outputs: Deduplicated string list.
# Logic: Treat non-arrays as empty; otherwise delegate to shared normalization.
# Constraints: Do not mutate the input object.
def _message_ids(value: object) -> list[str]:
    """Retain only valid, distinct Gmail message IDs from synchronization scope."""
    if not isinstance(value, list):
        return []
    return _unique_message_ids(value)


# Function: Stably deduplicate Gmail message identifiers.
# Inputs: `values`: original value array to normalize.
# Outputs: String list in first-appearance order.
# Logic: Strip whitespace and exclude non-strings and duplicate IDs.
# Constraints: Pure in-memory transformation without network side effects.
def _unique_message_ids(values: list[object]) -> list[str]:
    """Normalize Gmail message IDs in appearance order."""
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


# Function: Build a batch failure summary.
# Inputs: `mailbox_id`: backend mailbox identifier; `code`: error code; `message`: original error description; `fetched_count`: number of emails read.
# Outputs: Failure object containing code/message.
# Logic: Preserve original HTTP/CLI summary fields and zero values.
# Constraints: Do not treat a failure report as proof of email persistence.
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
