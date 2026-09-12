"""SalesMate Agent MVP 的一次性命令行入口。"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# 同时支持直接运行 ``agent/main.py`` 与 ``python -m agent.main``。
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.clients.backend_api import django_backend_from_environment
from agent.config import load_environment
from agent.tools.gmail import (
    connect_gmail,
    read_email,
    read_recent_emails,
    resolve_mailbox_address,
)
from agent.workflows.analysis_input import ValidationError, build_analysis_input
from agent.workflows.authorized_gmail_sync import sync_authorized_mailboxes_once
from agent.workflows.customer_analysis import bailian_analysis_provider
from agent.workflows.gmail_sync import sync_gmail
from agent.workflows.l1_email import bailian_extraction_provider, process_email
from agent.workflows.orchestration import process_jobs_once


def main(argv: list[str] | None = None) -> int:
    """执行一个 L1 邮件读取或 L2 分析输入构建并输出一个 JSON 文档。"""
    raw_argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description="SalesMate 一次性 Agent 命令")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--message-id", metavar="ID", help="读取指定 Gmail 邮件")
    selection.add_argument(
        "--recent",
        action="store_true",
        help="读取最近至多五封 Gmail 邮件",
    )
    selection.add_argument(
        "--analysis-company-id",
        metavar="COMPANY_ID",
        help="为指定公司构建 L2 AnalysisInput",
    )
    selection.add_argument(
        "--process-jobs-once",
        action="store_true",
        help="从 Django 后端领取一批任务并运行一次 L2-L4",
    )
    selection.add_argument(
        "--sync-gmail",
        action="store_true",
        help="同步最近 Gmail 邮件到 Django 后端，并处理一次分析任务",
    )
    selection.add_argument(
        "--sync-authorized-mailboxes-once",
        action="store_true",
        help="处理员工在网页中请求的 Gmail 同步，然后立即退出",
    )
    parser.add_argument(
        "--mailbox-address",
        help="当前授权邮箱地址（用于单封读取、最近邮件或同步）",
    )
    parser.add_argument(
        "--merge-version",
        default="merge-v2",
        help="L2 归并规则版本（默认：merge-v2）",
    )
    parser.add_argument("--job-limit", type=int, default=10, help="一次领取任务数（默认：10）")
    args = parser.parse_args(raw_argv)

    is_l2 = args.analysis_company_id is not None
    if not is_l2 and "--merge-version" in raw_argv:
        parser.error("--merge-version 必须与 --analysis-company-id 一起使用")
    if is_l2 and args.mailbox_address is not None:
        parser.error("--mailbox-address 不能与公司分析命令一起使用")
    if args.job_limit <= 0:
        parser.error("--job-limit 必须大于 0")

    if is_l2:
        return _run_l2(
            args.analysis_company_id,
            merge_version=args.merge_version,
        )
    if args.process_jobs_once:
        return _run_jobs_once(args.job_limit)
    if args.sync_authorized_mailboxes_once:
        return _run_authorized_sync(args.job_limit)
    if args.sync_gmail:
        return _run_sync(args, args.job_limit)

    return _run_l1(args)


def _run_l1(args: argparse.Namespace) -> int:
    """Preserve the existing one-shot Gmail L1 route."""
    load_environment()

    try:
        service = connect_gmail()
        mailbox_address = resolve_mailbox_address(service, args.mailbox_address)
        if args.recent:
            emails = read_recent_emails(service, limit=5)
        else:
            emails = [read_email(service, args.message_id)]
    except Exception:
        # 输入层只暴露固定安全错误，不包含 OAuth 或服务端详情。
        return _print_safe_error("input_read_failed", "邮件读取失败。", 1)

    results = [
        process_email(email, mailbox_address, bailian_extraction_provider)
        for email in emails
    ]
    document = results if args.recent else results[0]
    _print_json(document)
    return 0


def _run_l2(
    company_id: str,
    *,
    merge_version: str,
) -> int:
    """从 Django 后端读取公司上下文并构建一份 L2 快照。"""
    try:
        load_environment()
        backend = django_backend_from_environment()
    except Exception as error:
        return _print_safe_error(
            "configuration_failed",
            f"真实后端配置无效：{type(error).__name__}: {error}",
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
    """从真实 Django 后端领取任务并执行一轮 L2-L4。"""
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
            f"任务处理失败：{type(error).__name__}: {error}",
            1,
        )
    _print_json(reports)
    return 1 if any(report.get("status") == "failed" for report in reports) else 0


def _run_sync(args: argparse.Namespace, limit: int) -> int:
    """使用本地 Gmail OAuth，将邮件提交到 Django 后端并处理一轮任务。"""
    try:
        load_environment()
        service = connect_gmail()
        backend = django_backend_from_environment()
        if not isinstance(backend.mailbox_id, str) or not backend.mailbox_id:
            raise ValueError("SALESMATE_MAILBOX_ID 不能为空。")
        sync_result = sync_gmail(
            {
                "mailbox_id": backend.mailbox_id,
                "access_token": "local-oauth-service",
                "mailbox_address": args.mailbox_address,
                "max_results": 20,
            },
            backend=backend,
            gmail_factory=lambda _token: service,
            extraction_provider=bailian_extraction_provider,
        )
        reports = []
        if sync_result["status"] == "completed":
            reports = process_jobs_once(
                backend=backend,
                limit=limit,
                analysis_provider=bailian_analysis_provider,
            )
        sync_result["job_reports"] = reports
    except Exception as error:
        return _print_safe_error(
            "gmail_sync_failed",
            f"Gmail 同步失败：{type(error).__name__}: {error}",
            1,
        )
    _print_json(sync_result)
    return 0 if sync_result["status"] == "completed" else 1


def _run_authorized_sync(limit: int) -> int:
    """处理网页发起的员工 Gmail 同步请求。"""
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
            f"员工 Gmail 同步失败：{type(error).__name__}: {error}",
            1,
        )
    _print_json(reports)
    return 1 if any(item.get("status") == "failed" for item in reports) else 0


def _print_json(document: object) -> None:
    """将一个支持 UTF-8 的 JSON 文档写到标准输出。"""
    print(json.dumps(document, ensure_ascii=False, indent=2))


def _print_safe_error(code: str, message: str, exit_code: int) -> int:
    """将 CLI 错误作为唯一、安全的 JSON 文档写到标准输出。"""
    _print_json({"error": {"code": code, "message": message}})
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
