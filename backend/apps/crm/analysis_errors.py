"""职责：为分析保存及租约冲突提供可区分的安全原因。
实现：保留 HTTP 409 和失败语义，生成固定错误码与操作提示，记录公司、任务和版本关联。
关联：results 的 L2/L3/L4 保存入口及 jobs 的租约校验；现有 Agent 可原样记录 backend_code。
目录：
- analysis_conflict：构造并记录冲突。
- check_analysis_version：保留版本规则并细化分析错误。
变量索引：
- MESSAGES：允许的原因与固定提示。
- logger：不含正文和令牌的诊断日志。
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


# 功能：构造带固定原因的 409 错误并记录安全关联信息。
# 输入：`reason` 为 MESSAGES 中的代码，`company` 为已授权公司，`job_id` 为可空任务 ID，
# `stage` 为调用方固定阶段，`expected` 为可空输入 revision，`input_version` 为可空输入标识。
# 输出：Conflict 实例，供调用方抛出；无数据库写入。
# 逻辑：任务 ID 规范为 UUID，版本仅记录非负整数，输入标识仅记录 SHA-256；错误码同时供 DRF 与客户端识别。
# 约束：不记录 lease token、业务正文或原始异常，不新增任务状态或自动重试。
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


# 功能：沿用版本约束，仅细化不匹配时的分析错误。
# 输入：`expected` 为客户端版本，`company` 为锁定公司，`job_id` 为任务 ID，`stage` 为保存阶段，`input_version` 为输入标识。
# 输出：无；格式错误仍为原 400，版本不匹配为具有原因的 409。
# 逻辑：调用既有 check_version，保留实验模式及版本格式语义，仅包装 Conflict。
# 约束：不放宽校验、不跳过租约、不重取新版本后重试旧结果。
def check_analysis_version(expected, company, job_id, stage, input_version):
    try:
        check_version(expected, company.revision)
    except Conflict:
        raise analysis_conflict("analysis_revision_changed", company, job_id, stage, expected, input_version) from None
