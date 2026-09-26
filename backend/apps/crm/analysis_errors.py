"""Responsibility: Provide distinguishable, safe reasons for analysis saves and lease conflicts.
Implementation: Retain HTTP 409 and failure semantics, generate fixed error codes and action guidance, and log company, job, and version linkage.
Relationships: Used by L2/L3/L4 save entry points in results and by job lease validation; existing Agents can record backend_code unchanged.
Directory:
- analysis_conflict: Construct and log a conflict.
- check_analysis_version: Retain version rules and refine analysis errors.
Variable index:
- MESSAGES: Allowed reasons and fixed guidance.
- logger: Diagnostic logger without bodies or tokens.
"""
import hashlib
import logging
import uuid

from .access import Conflict, check_version

logger = logging.getLogger("salesmate.analysis")
MESSAGES = {
    "analysis_revision_changed": "资料在分析期间更新，旧结果未保存；请刷新后基于最新资料重新分析，已有后继任务时等待其完成。",
    "analysis_lease_required": "分析任务缺少领取凭证，不能保存结果；请通过任务领取流程执行。",
    "analysis_lease_invalid": "分析任务领取凭证无效，不能保存结果；请核对任务与执行者。",
    "analysis_lease_expired": "分析任务租约已到期，旧结果未保存；请显式基于最新资料重新分析。",
    "analysis_job_inactive": "分析任务已结束，不能继续提交旧结果；请刷新任务状态。",
    "analysis_snapshot_changed": "分析输入与当前资料不一致，旧结果未保存；请基于最新资料重新构建输入并分析。",
    "analysis_input_conflict": "同一输入版本对应不同内容，输入未覆盖；请检查归并结果与版本计算。",
    "analysis_result_conflict": "同一输入及提示词版本已有不同分析结果，已保存结果未覆盖；请检查缓存与任务调用顺序。",
    "analysis_score_conflict": "同一评分版本与时间对应不同内容，已保存评分未覆盖；请检查重复提交。",
}


# Function: Construct a 409 error with a fixed reason and log safe linkage information.
# Inputs: `reason` is a MESSAGES code; `company` is an authorized company; `job_id` is an optional job ID;
# `stage` is the caller's fixed stage; `expected` is an optional input revision; and `input_version` is an optional input identifier.
# Outputs: A Conflict instance for the caller to raise; does not write to the database.
# Logic: Normalize job IDs as UUIDs, log only non-negative integer versions, and retain only an input SHA-256; error codes identify failures to both DRF and clients.
# Constraints: Does not log lease tokens, business bodies, or original exceptions, and does not add job states or automatic retries.
def analysis_conflict(reason, company, job_id, stage, expected=None, input_version=None):
    try:
        identifier = str(uuid.UUID(str(job_id)))
    except (ValueError, TypeError, AttributeError):
        identifier = None
    version = str(expected) if expected is not None else ""
    revision = int(version) if version.isascii() and version.isdigit() and len(version) <= 18 else None
    fingerprint = hashlib.sha256(str(input_version).encode()).hexdigest() if input_version is not None else None
    logger.warning("analysis_conflict reason=%s stage=%s company_id=%s job_id=%s expected_revision=%s current_revision=%s input_fingerprint=%s action=inspect_context_and_task", reason, stage, company.pk, identifier, revision, company.revision, fingerprint)
    error = Conflict(MESSAGES[reason], code=reason)
    error.default_code = reason
    return error


# Function: Retain version constraints while refining the analysis error on mismatch.
# Inputs: `expected` is the client version; `company` is the locked company; `job_id` is the job ID; `stage` is the save stage; and `input_version` is the input identifier.
# Outputs: None; format errors remain the original 400, while version mismatches become a reasoned 409.
# Logic: Call existing check_version and preserve experiment-mode and version-format semantics while wrapping only Conflict.
# Constraints: Does not relax validation, bypass leases, or retry old results after fetching a new version.
def check_analysis_version(expected, company, job_id, stage, input_version):
    try:
        check_version(expected, company.revision)
    except Conflict:
        raise analysis_conflict("analysis_revision_changed", company, job_id, stage, expected, input_version) from None
