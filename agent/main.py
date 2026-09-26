"""Responsibility: One-shot command-line entry point for the SalesMate Agent MVP.
Implementation: Parse one-shot commands, load environment configuration, invoke backend-bound workflows, and emit JSON results.
Relationships: Connects DjangoBackendClient to analysis, job, chat, and Gmail workflows.

Directory:
- main: Run one company analysis, job processing pass, chat answer, or employee-authorized mailbox synchronization.
- _run_chat_once: Claim and process at most one chat answer request from the real Django backend.
- _process_chat_once: Load chat modules lazily so chat skill settings do not affect existing L1-L4 commands.
- _run_l2: Read company context from Django and build one L2 snapshot.
- _run_jobs_once: Claim jobs from the real Django backend and execute one L2-L4 pass.
- _run_authorized_sync: Process employee Gmail synchronization requested through the web UI.
- _print_json: Write one UTF-8-capable JSON document to standard output.
- _print_safe_error: Write a CLI error as the sole safe JSON document on standard output.

Variable index:
- None
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Support both direct execution of ``agent/main.py`` and ``python -m agent.main``.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.clients.backend_api import django_backend_from_environment
from agent.config import load_environment
from agent.workflows.analysis_input import ValidationError, build_analysis_input
from agent.workflows.authorized_gmail_sync import sync_authorized_mailboxes_once
from agent.workflows.customer_analysis import bailian_analysis_provider
from agent.workflows.orchestration import process_jobs_once


def main(argv: list[str] | None = None) -> int:
    """Run one company analysis, job processing pass, chat answer, or employee-authorized mailbox synchronization."""
    raw_argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description="SalesMate one-shot Agent commands")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--analysis-company-id",
        metavar="COMPANY_ID",
        help="Build L2 AnalysisInput for a company",
    )
    selection.add_argument(
        "--process-jobs-once",
        action="store_true",
        help="Claim a batch from Django and run L2-L4 once",
    )
    selection.add_argument(
        "--process-chat-once",
        action="store_true",
        help="Claim and answer one chat request from Django, then exit",
    )
    selection.add_argument(
        "--sync-authorized-mailboxes-once",
        action="store_true",
        help="Process one employee-requested Gmail sync, then exit",
    )
    parser.add_argument(
        "--merge-version",
        default="merge-v2",
        help="L2 merge rule version (default: merge-v2)",
    )
    parser.add_argument("--job-limit", type=int, default=10, help="Number of jobs to claim (default: 10)")
    args = parser.parse_args(raw_argv)

    is_l2 = args.analysis_company_id is not None
    if not is_l2 and "--merge-version" in raw_argv:
        parser.error("--merge-version requires --analysis-company-id")
    if args.job_limit <= 0:
        parser.error("--job-limit must be greater than 0")

    if is_l2:
        return _run_l2(
            args.analysis_company_id,
            merge_version=args.merge_version,
        )
    if args.process_jobs_once:
        return _run_jobs_once(args.job_limit)
    if args.process_chat_once:
        return _run_chat_once()
    return _run_authorized_sync(args.job_limit)


def _run_chat_once() -> int:
    """Claim and process at most one chat answer request from the real Django backend."""
    try:
        load_environment()
        backend = django_backend_from_environment()
        result = _process_chat_once(backend=backend)
    except Exception:
        return _print_safe_error(
            "chat_processing_failed",
            "Chat request processing failed. Check the Agent configuration or retry later.",
            1,
        )
    _print_json(result)
    return 1 if result is not None and result.get("status") == "failed" else 0


def _process_chat_once(*, backend):
    """Load chat modules lazily so chat skill settings do not affect existing L1-L4 commands."""
    from agent.workflows.chat import process_chat_once

    return process_chat_once(backend=backend)


def _run_l2(
    company_id: str,
    *,
    merge_version: str,
) -> int:
    """Read company context from Django and build one L2 snapshot."""
    try:
        load_environment()
        backend = django_backend_from_environment()
    except Exception as error:
        return _print_safe_error(
            "configuration_failed",
            f"Live backend configuration is invalid: {type(error).__name__}: {error}",
            1,
        )

    result = build_analysis_input(
        company_id,
        backend=backend,
        merge_version=merge_version,
        clock=lambda: datetime.now(timezone.utc),
    )
    _print_json(result.to_dict())
    return 1 if isinstance(result, ValidationError) else 0


def _run_jobs_once(limit: int) -> int:
    """Claim jobs from the real Django backend and execute one L2-L4 pass."""
    try:
        load_environment()
        backend = django_backend_from_environment()
        reports = process_jobs_once(
            backend=backend,
            limit=limit,
            analysis_provider=bailian_analysis_provider,
        )
    except Exception as error:
        return _print_safe_error(
            "job_processing_failed",
            f"Job processing failed: {type(error).__name__}: {error}",
            1,
        )
    _print_json(reports)
    return 1 if any(report.get("status") == "failed" for report in reports) else 0


def _run_authorized_sync(limit: int) -> int:
    """Process employee Gmail synchronization requested through the web UI."""
    try:
        load_environment()
        backend = django_backend_from_environment()
        reports = sync_authorized_mailboxes_once(
            backend=backend,
            limit=min(limit, 10),
        )
    except Exception as error:
        return _print_safe_error(
            "authorized_gmail_sync_failed",
            f"Employee Gmail sync failed: {type(error).__name__}: {error}",
            1,
        )
    _print_json(reports)
    return 1 if any(item.get("status") == "failed" for item in reports) else 0


def _print_json(document: object) -> None:
    """Write one UTF-8-capable JSON document to standard output."""
    print(json.dumps(document, ensure_ascii=False, indent=2))


def _print_safe_error(code: str, message: str, exit_code: int) -> int:
    """Write a CLI error as the sole safe JSON document on standard output."""
    _print_json({"error": {"code": code, "message": message}})
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
