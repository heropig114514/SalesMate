"""职责：管理持久化分析任务、领取租约和回报。
实现：行锁领取待办、固定输入 revision、随机领取凭证；过期显式失败。
关联：ingestion 入队，rules 或独立 Agent 消费，results 验证租约。
目录：
- enqueue：合并公司尚未领取的同类分析工作。
- job_data：映射任务为 README Job 并附领取凭证。
- claim：原子领取当前用户的待处理任务。
- require_lease：核验任务领取凭证和上下文版本。
- report：保存任务最终状态并核验产出声明。
变量索引：
- logger：模块脱敏诊断日志记录器
"""
from datetime import timedelta
import logging
import uuid

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound

from .access import Conflict, InvalidState, company_for, plain
from .models import Analysis, Job

logger = logging.getLogger("salesmate.jobs")


# 功能：合并公司尚未领取的同类分析工作。
# 输入：`company` 为已锁定公司；`trigger` 为业务事件名称。
# 输出：新建或更新的 Job。
# 逻辑：未领取任务吸收最新 revision，运行中同 revision 不重复建立。
# 约束：调用方须持有 company 行锁；失败任务不自动重试。
def enqueue(company, trigger):
    pending = company.jobs.select_for_update().filter(status="pending").order_by("enqueued_at").first()
    if pending:
        pending.revision, pending.trigger = company.revision, trigger
        pending.save(update_fields=["revision", "trigger"])
        return pending
    running = company.jobs.filter(status="running", revision=company.revision, lease_until__gt=timezone.now()).first()
    if running:
        return running
    job = Job.objects.create(company=company, trigger=trigger, revision=company.revision)
    logger.info("job_enqueued job_id=%s company_id=%s revision=%s trigger=%s", job.pk, company.pk, job.revision, trigger)
    return job


# 功能：映射任务为 README Job 并附领取凭证。
# 输入：`job` 为已授权任务。
# 输出：Job 字典；lease_token 是显式的传输扩展。
# 逻辑：传递后端 revision，让 Agent 保存快照时使用 If-Match。
# 约束：不包含 Gmail 凭证；只向领取者返回 lease_token。
def job_data(job):
    return {"job_id": str(job.pk), "trigger": job.trigger, "payload": {"company_id": str(job.company_id)},
            "enqueued_at": job.enqueued_at.isoformat(), "attempt": job.attempt,
            "lease_until": job.lease_until.isoformat() if job.lease_until else None,
            "lease_token": str(job.lease_token) if job.lease_token else None,
            "expected_version": job.revision}


# 功能：原子领取当前用户的待处理任务。
# 输入：`owner` 为服务凭证用户；`limit` 为数量；`lease_seconds` 为显式租期；`company_id` 可限制公司。
# 输出：领取后的 Job 数组。
# 逻辑：先将已过期运行任务标记失败，再用 skip_locked 领取待办并生成新凭证。
# 约束：不重派已过期任务，不自动重试；需用户显式重新分析。
@transaction.atomic
def claim(owner, limit, lease_seconds, company_id=None):
    now = timezone.now()
    scope = Job.objects.filter(company__owner=owner)
    if company_id:
        scope = scope.filter(company_id=company_id)
    expired = scope.filter(status="running", lease_until__lte=now).update(status="failed", report={"error": {"code": "invalid_state", "message": "任务租约已过期，请重新分析。"}})
    if expired:
        logger.warning("job_leases_expired count=%s owner_id=%s action=request_new_analysis", expired, owner.pk)
    # 只锁任务表，避免先锁任务再锁公司的反向锁序；enqueue 在公司锁之后锁待办。
    jobs = list(scope.select_for_update(of=("self",), skip_locked=True).filter(status="pending").order_by("enqueued_at")[:limit])
    for job in jobs:
        job.status, job.attempt = "running", job.attempt + 1
        job.lease_until, job.lease_token = now + timedelta(seconds=lease_seconds), uuid.uuid4()
        job.save(update_fields=["status", "attempt", "lease_until", "lease_token"])
        logger.info("job_claimed job_id=%s revision=%s attempt=%s", job.pk, job.revision, job.attempt)
    return [job_data(job) for job in jobs]


# 功能：核验任务领取凭证和上下文版本。
# 输入：`company` 为锁定公司；`job_id`、`token` 为请求头；`require_revision` 控制是否检查当前 revision。
# 输出：锁定 Job；无效或过期抛 Conflict。
# 逻辑：按公司限制范围，核查运行状态、令牌、截止时间和 revision。
# 约束：调用方处于事务中；租约凭证不得进入日志。
def require_lease(company, job_id, token, require_revision=True):
    if not job_id or not token:
        raise Conflict("必须提供 X-Job-ID 与 X-Lease-Token。")
    try:
        job = Job.objects.select_for_update().filter(pk=uuid.UUID(str(job_id)), company=company).first()
    except (ValueError, TypeError):
        raise Conflict("任务凭证格式无效。") from None
    if job is None or job.status != "running" or str(job.lease_token) != str(token) or job.lease_until <= timezone.now():
        raise Conflict("任务已过期或领取凭证无效。")
    if require_revision and job.revision != company.revision:
        raise Conflict("公司上下文已变化，旧任务不得覆盖新结果。")
    return job


# 功能：保存任务最终状态并核验产出声明。
# 输入：`owner` 为认证用户；`data` 为 JobReport；`token` 为领取凭证。
# 输出：job_id 与最终状态。
# 逻辑：成功回报需要当前版本实际存在分析及声明的评分；失败允许旧 revision 回报。
# 约束：过期或重复回报返回冲突；错误不自动触发重试。
@transaction.atomic
def report(owner, data, token):
    candidate = Job.objects.filter(pk=data["job_id"], company__owner=owner).first()
    if candidate is None:
        raise NotFound("任务不存在。")
    company = company_for(owner, candidate.company_id, lock=True)
    job = require_lease(company, candidate.pk, token, require_revision=data["status"] != "failed")
    if data["status"] != "failed":
        result = Analysis.objects.filter(snapshot__company=company, snapshot__revision=company.revision,
                                         snapshot__input_version=data["input_version"], payload__status="completed").order_by("-id").first()
        if result is None or (data["produced"].get("score") and not result.scores.exists()) or data["error"] is not None:
            raise InvalidState("成功或缓存回报需要已保存且有效的结果。")
    elif not data["error"]:
        raise InvalidState("失败回报必须说明错误。")
    job.status, job.report = data["status"], plain(data)
    job.save(update_fields=["status", "report"])
    logger.info("job_reported job_id=%s status=%s duration_ms=%s", job.pk, job.status, data["duration_ms"])
    return {"job_id": str(job.pk), "status": job.status}
