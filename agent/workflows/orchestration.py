"""把 L2、L3、L4 串成可供后端或 CLI 调用的一次性流程。"""

from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Callable, Mapping

from agent.workflows.analysis_input import ValidationError, build_analysis_input
from agent.workflows.customer_analysis import (
    ANALYSIS_PROMPT_VERSION,
    bailian_analysis_provider,
    generate_analysis,
)
from agent.workflows.lead_score import compute_score
from agent.clients.backend_api import BackendClient


SUPPORTED_TRIGGERS = frozenset(
    {"email_ingested", "customer_detail_opened", "external_updated", "grouping_changed"}
)


def analyze_company(
    company_id: str,
    *,
    backend: BackendClient,
    analysis_provider: Callable[[Mapping[str, Any]], str] = bailian_analysis_provider,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    merge_version: str = "merge-v2",
) -> dict[str, Any]:
    """一次构建 L2，复用或生成 L3，并计算 L4。"""
    analysis_input = build_analysis_input(
        company_id,
        backend=backend,
        merge_version=merge_version,
        clock=clock,
    )
    if isinstance(analysis_input, ValidationError):
        return {
            "status": "failed",
            "company_id": company_id,
            "analysis_input": None,
            "analysis": None,
            "score": None,
            "cache_hit": False,
            "error": analysis_input.to_dict()["error"],
        }

    input_document = analysis_input.to_dict()
    backend.save_analysis_input(input_document)
    cached = backend.get_cached_analysis(company_id, analysis_input.input_version)
    cache_hit = bool(
        isinstance(cached, Mapping)
        and cached.get("status") == "completed"
        and cached.get("analysis_prompt_version") == ANALYSIS_PROMPT_VERSION
    )
    analysis = dict(cached) if cache_hit else generate_analysis(
        input_document,
        analysis_provider=analysis_provider,
        clock=clock,
    )
    if analysis.get("status") == "completed" and not cache_hit:
        backend.save_analysis(analysis)

    completed = analysis.get("status") == "completed"
    score = compute_score(analysis, input_document, clock=clock) if completed else None
    if score is not None:
        backend.save_score(score)
    return {
        "status": "completed" if completed else "failed",
        "company_id": company_id,
        "analysis_input": input_document,
        "analysis": analysis,
        "score": score,
        "cache_hit": cache_hit,
        "error": None if completed else analysis.get("error"),
    }


def process_jobs_once(
    *,
    backend: BackendClient,
    limit: int = 10,
    analysis_provider: Callable[[Mapping[str, Any]], str] = bailian_analysis_provider,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    merge_version: str = "merge-v2",
) -> list[dict[str, Any]]:
    """领取一批 Demo 任务，每家公司最多分析一次，然后立即返回。"""
    jobs = backend.claim_jobs(limit)
    reports: list[dict[str, Any]] = []
    processed_companies: set[str] = set()

    for job in jobs:
        started = perf_counter()
        job_id = str(job.get("job_id", ""))
        trigger = job.get("trigger")
        company_id = job.get("company_id")
        if trigger not in SUPPORTED_TRIGGERS or not isinstance(company_id, str) or not company_id:
            report = _job_report(
                job_id,
                "failed",
                None,
                {"analysis": False, "score": False, "emails_submitted": 0},
                {"code": "invalid_job", "message": "任务结构或触发类型无效。"},
                started,
            )
        elif company_id in processed_companies:
            report = _job_report(
                job_id,
                "skipped",
                None,
                {"analysis": False, "score": False, "emails_submitted": 0},
                None,
                started,
            )
        else:
            processed_companies.add(company_id)
            try:
                bundle = analyze_company(
                    company_id,
                    backend=backend,
                    analysis_provider=analysis_provider,
                    clock=clock,
                    merge_version=merge_version,
                )
                input_version = (
                    bundle["analysis_input"].get("input_version")
                    if isinstance(bundle.get("analysis_input"), Mapping)
                    else None
                )
                succeeded = bundle["status"] == "completed"
                report = _job_report(
                    job_id,
                    "completed" if succeeded else "failed",
                    input_version,
                    {
                        "analysis": succeeded,
                        "score": succeeded and bundle.get("score") is not None,
                        "emails_submitted": 0,
                    },
                    bundle.get("error"),
                    started,
                )
            except Exception as error:
                report = _job_report(
                    job_id,
                    "failed",
                    None,
                    {"analysis": False, "score": False, "emails_submitted": 0},
                    {
                        "code": "job_failed",
                        "message": f"{type(error).__name__}: {error}",
                    },
                    started,
                )

        backend.report_job(report)
        reports.append(report)
    return reports


def _job_report(
    job_id: str,
    status: str,
    input_version: str | None,
    produced: dict[str, Any],
    error: object,
    started: float,
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "status": status,
        "input_version": input_version,
        "produced": produced,
        "error": error,
        "duration_ms": max(0, round((perf_counter() - started) * 1000)),
    }


__all__ = ["SUPPORTED_TRIGGERS", "analyze_company", "process_jobs_once"]
