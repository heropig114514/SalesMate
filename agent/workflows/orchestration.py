"""把 L2、L3、L4 串成可供后端或 CLI 调用的一次性流程。"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from time import perf_counter
from typing import Any, Callable, Mapping

from agent.workflows.analysis_input import ValidationError, build_analysis_input
from agent.workflows.customer_analysis import (
    ANALYSIS_PROMPT_VERSION,
    bailian_analysis_provider,
    generate_analysis,
)
from agent.workflows.lead_score import compute_priority_result
from agent.clients.backend_api import BackendClient


SUPPORTED_TRIGGERS = frozenset(
    {"email_ingested", "customer_detail_opened", "external_updated", "grouping_changed"}
)
logger = logging.getLogger("salesmate.agent.orchestration")


def analyze_company(
    company_id: str,
    *,
    backend: BackendClient,
    analysis_provider: Callable[[Mapping[str, Any]], str] = bailian_analysis_provider,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    merge_version: str = "merge-v2",
) -> dict[str, Any]:
    """一次构建 L2，复用或生成 L3，并计算 L4。"""
    total_started = perf_counter()
    logger.info("company_analysis_started company_id=%s merge_version=%s", company_id, merge_version)
    l2_started = perf_counter()
    analysis_input = build_analysis_input(
        company_id,
        backend=backend,
        merge_version=merge_version,
        clock=clock,
    )
    l2_ms = round((perf_counter() - l2_started) * 1000)
    if isinstance(analysis_input, ValidationError):
        logger.warning(
            "company_analysis_failed company_id=%s stage=l2 error=%s duration_ms=%s",
            company_id,
            analysis_input.to_dict()["error"],
            l2_ms,
        )
        return {
            "status": "failed",
            "company_id": company_id,
            "analysis_input": None,
            "analysis": None,
            "score": None,
            "score_details": None,
            "cache_hit": False,
            "error": analysis_input.to_dict()["error"],
        }

    input_document = analysis_input.to_dict()
    logger.info(
        "company_analysis_l2_ready company_id=%s input_version=%s emails=%s unparsed=%s duration_ms=%s",
        company_id, analysis_input.input_version,
        len(input_document.get("member_dedupe_keys", [])),
        input_document.get("unparsed_message_count"), l2_ms,
    )
    backend_started = perf_counter()
    backend.save_analysis_input(input_document)
    cached = backend.get_cached_analysis(company_id, analysis_input.input_version)
    backend_ms = round((perf_counter() - backend_started) * 1000)
    cache_hit = bool(
        isinstance(cached, Mapping)
        and cached.get("status") == "completed"
        and cached.get("analysis_prompt_version") == ANALYSIS_PROMPT_VERSION
    )
    logger.info(
        "company_analysis_l3_cache company_id=%s input_version=%s hit=%s lookup_ms=%s",
        company_id, analysis_input.input_version, cache_hit, backend_ms,
    )
    l3_started = perf_counter()
    analysis = (
        dict(cached)
        if cache_hit
        else generate_analysis(
            input_document,
            analysis_provider=analysis_provider,
            clock=clock,
        )
    )
    l3_ms = round((perf_counter() - l3_started) * 1000)
    if analysis.get("status") == "completed" and not cache_hit:
        backend_started = perf_counter()
        backend.save_analysis(analysis)
        backend_ms += round((perf_counter() - backend_started) * 1000)

    completed = analysis.get("status") == "completed"
    if not completed:
        logger.warning(
            "company_analysis_failed company_id=%s stage=l3 error=%s",
            company_id, analysis.get("error"),
        )
    l4_started = perf_counter()
    priority_result = (
        compute_priority_result(
            analysis,
            input_document,
            clock=clock,
            priority_context=analysis_input.priority_context,
        )
        if completed else None
    )
    score = priority_result["score"] if priority_result else None
    score_details = priority_result["details"] if priority_result else None
    if priority_result:
        logger.info(
            "company_analysis_l4_ready company_id=%s score=%s reason_features=%s",
            company_id, score.get("score") if isinstance(score, Mapping) else None,
            [reason.get("feature") for reason in score.get("score_reasons", [])]
            if isinstance(score, Mapping) else [],
        )
    l4_ms = round((perf_counter() - l4_started) * 1000)
    if score is not None:
        backend_started = perf_counter()
        backend.save_score(score)
        backend_ms += round((perf_counter() - backend_started) * 1000)
    logger.info(
        "company_analysis_completed company_id=%s status=%s cache_hit=%s total_ms=%s l2_ms=%s l3_ms=%s l4_ms=%s backend_ms=%s",
        company_id,
        "completed" if completed else "failed",
        cache_hit,
        round((perf_counter() - total_started) * 1000),
        l2_ms,
        l3_ms,
        l4_ms,
        backend_ms,
    )
    return {
        "status": "completed" if completed else "failed",
        "company_id": company_id,
        "analysis_input": input_document,
        "analysis": analysis,
        "score": score,
        "score_details": score_details,
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
    logger.info("analysis_jobs_claimed count=%s limit=%s", len(jobs), limit)
    reports: list[dict[str, Any]] = []
    processed_companies: set[str] = set()

    for job in jobs:
        started = perf_counter()
        job_id = str(job.get("job_id", ""))
        trigger = job.get("trigger")
        company_id = job.get("company_id")
        if trigger not in SUPPORTED_TRIGGERS or not isinstance(company_id, str) or not company_id:
            logger.warning("analysis_job_invalid job_id=%s trigger=%s company_id=%s", job_id, trigger, company_id)
            report = _job_report(
                job_id,
                "failed",
                None,
                {"analysis": False, "score": False, "emails_submitted": 0},
                {"code": "invalid_job", "message": "任务结构或触发类型无效。"},
                started,
            )
        elif company_id in processed_companies:
            logger.info("analysis_job_skipped job_id=%s company_id=%s reason=same_batch", job_id, company_id)
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
                logger.warning(
                    "analysis_job_failed job_id=%s company_id=%s error_type=%s",
                    job_id, company_id, type(error).__name__,
                )
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
        logger.info(
            "analysis_job_reported job_id=%s company_id=%s status=%s duration_ms=%s",
            job_id, company_id, report["status"], report["duration_ms"],
        )
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
