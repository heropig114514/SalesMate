"""Responsibility: Manage durable analysis jobs, claim leases, and reports.
Implementation: Return distinguishable 409 reasons for missing, invalid, expired leases and changed input; all Worker submissions require a valid claimed lease. Owner and job row locks ensure company-level exclusion, unfinished L1 repairs block profiling, revision is frozen, credentials are random, and expiration fails explicitly.
Relationships: ingestion queues work, rules or an isolated Agent consumes it, and results validates leases.
Directory:
- enqueue: Merge unclaimed analysis work of the same kind for a company.
- job_data: Map a task to README Job and attach its claim credential.
- claimable_jobs: Query work not blocked by a company running job or L1 repair.
- claim: Atomically claim pending jobs for the current user.
- require_lease: Validate a job claim credential and context version.
- report: Save terminal job state and validate declared output.
Variable index:
- logger: Redacted diagnostic logger for this module.
"""
from datetime import timedelta
import logging
import uuid

from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone
from rest_framework.exceptions import NotFound

from .access import InvalidState, company_for, plain
from .analysis_errors import analysis_conflict
from .models import Analysis, Job

logger = logging.getLogger("salesmate.jobs")


# Function: Merge unclaimed analysis work of the same kind for a company.
# Inputs: `company` is locked and `trigger` is a business-event name.
# Outputs: Newly created or updated Job.
# Logic: An unclaimed job absorbs the latest revision, while a running job at that revision is not duplicated.
# Constraints: Caller must hold the company row lock; failed jobs do not retry automatically.
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


# Function: Map a task to README Job and attach its claim credential.
# Inputs: `job` is authorized.
# Outputs: Job dictionary; lease_token is an explicit transport extension.
# Logic: Pass backend revision so Agent uses If-Match when saving a snapshot.
# Constraints: Contains no Gmail credentials and returns lease_token only to the claimant.
def job_data(job):
    return {"job_id": str(job.pk), "trigger": job.trigger, "company_id": str(job.company_id),
            "enqueued_at": job.enqueued_at.isoformat(), "attempt": job.attempt,
            "lease_until": job.lease_until.isoformat() if job.lease_until else None,
            "lease_token": str(job.lease_token) if job.lease_token else None,
            "expected_version": job.revision}


# Function: Unify executable-work conditions for scheduler and claimant.
# Inputs: `owner` is an authenticated employee.
# Outputs: QuerySet of currently claimable Jobs.
# Logic: Exclude valid running leases and unfinished or failed business-email repair extraction, independently across companies.
# Constraints: Query only without locking; actual claims must reevaluate under the owner lock.
def claimable_jobs(owner):
    from .durable_models import ExtractionRepair
    running = Job.objects.filter(company_id=OuterRef("company_id"), status="running", lease_until__gt=timezone.now())
    repairing = ExtractionRepair.objects.filter(email__company_id=OuterRef("company_id"), email__business_classification="business", status__in=["pending", "running", "failed"])
    return Job.objects.filter(company__owner=owner, status="pending").annotate(company_running=Exists(running), repairing=Exists(repairing)).filter(company_running=False, repairing=False)


# Function: Atomically claim pending jobs for the current user.
# Inputs: `owner` is the service-credential user, `limit` is count, `lease_seconds` is explicit lease duration, and `company_id` can limit the company.
# Outputs: Array of claimed Jobs.
# Logic: Explicitly fail expired jobs; owner lock serializes claims and excludes running companies and pending or failed business L1 repairs.
# Constraints: Does not redispatch expired work or retry automatically; users must explicitly analyze again.
@transaction.atomic
def claim(owner, limit, lease_seconds, company_id=None):
    from django.contrib.auth import get_user_model
    get_user_model().objects.select_for_update().get(pk=owner.pk)
    now = timezone.now()
    scope = Job.objects.filter(company__owner=owner)
    if company_id:
        scope = scope.filter(company_id=company_id)
    expired = scope.filter(status="running", lease_until__lte=now).update(status="failed", report={"error": {"code": "invalid_state", "message": "任务租约已过期，请重新分析。"}})
    if expired:
        logger.warning("job_leases_expired count=%s owner_id=%s action=request_new_analysis", expired, owner.pk)
    # Lock only jobs to avoid reverse ordering of job then company locks; enqueue locks pending work after the company lock.
    available = claimable_jobs(owner)
    if company_id:
        available = available.filter(company_id=company_id)
    # Claim after locking the owner so company mutual-exclusion checks and claims are atomic across Worker processes.
    jobs = list(available.select_for_update(of=("self",), skip_locked=True).order_by("enqueued_at")[:limit])
    selected = []
    companies = set()
    for job in jobs:
        if job.company_id in companies:
            continue
        companies.add(job.company_id)
        selected.append(job)
        job.status, job.attempt = "running", job.attempt + 1
        job.lease_until, job.lease_token = now + timedelta(seconds=lease_seconds), uuid.uuid4()
        job.save(update_fields=["status", "attempt", "lease_until", "lease_token"])
        logger.info("job_claimed job_id=%s revision=%s attempt=%s", job.pk, job.revision, job.attempt)
    return [job_data(job) for job in selected]


# Function: Validate a job claim credential and context version.
# Inputs: `company` is locked; `job_id` and `token` come from headers; `require_revision` controls current-revision validation.
# Outputs: Locked Job; invalid, ended, expired, and changed-version states each raise a reasoned Conflict.
# Logic: Require lease headers and validate claimed state, expiration, and version in every environment.
# Constraints: Caller is in a transaction and lease credentials never enter logs.
def require_lease(company, job_id, token, require_revision=True):
    if not job_id or not token:
        raise analysis_conflict("analysis_lease_required", company, job_id, "require_lease")
    try:
        job = Job.objects.select_for_update().filter(pk=uuid.UUID(str(job_id)), company=company).first()
    except (ValueError, TypeError):
        raise analysis_conflict("analysis_lease_invalid", company, job_id, "require_lease") from None
    if job is None or str(job.lease_token) != str(token):
        raise analysis_conflict("analysis_lease_invalid", company, job_id, "require_lease")
    if job.status != "running":
        raise analysis_conflict("analysis_job_inactive", company, job_id, "require_lease", job.revision)
    if job.lease_until is None or job.lease_until <= timezone.now():
        raise analysis_conflict("analysis_lease_expired", company, job_id, "require_lease", job.revision)
    if require_revision and job.revision != company.revision:
        raise analysis_conflict("analysis_revision_changed", company, job_id, "require_lease", job.revision)
    return job


# Function: Save terminal job state and validate declared output.
# Inputs: `owner` is an authenticated user, `data` is JobReport, and `token` is a claim credential.
# Outputs: job_id and terminal state.
# Logic: Successful reports require analysis and declared scoring to exist at the current revision; failed reports may use an old revision.
# Constraints: Expired or duplicate reports return conflict and errors do not automatically trigger retry.
@transaction.atomic
def report(owner, data, token):
    candidate = Job.objects.filter(pk=data["job_id"], company__owner=owner).first()
    if candidate is None:
        raise NotFound("任务不存在。")
    company = company_for(owner, candidate.company_id, lock=True)
    job = require_lease(company, candidate.pk, token, require_revision=data["status"] != "failed")
    if data["status"] == "completed":
        result = Analysis.objects.filter(snapshot__company=company, snapshot__revision=company.revision,
                                         snapshot__input_version=data["input_version"], payload__status="completed").order_by("-id").first()
        if result is None or (data["produced"].get("score") and not result.scores.exists()) or data["error"] is not None:
            raise InvalidState("成功回报需要已保存且有效的结果。")
    elif data["status"] == "skipped":
        if data["error"] is not None or any(data["produced"].values()):
            raise InvalidState("跳过回报不得声明产出或错误。")
    elif not data["error"]:
        raise InvalidState("失败回报必须说明错误。")
    job.status, job.report = data["status"], plain(data)
    job.save(update_fields=["status", "report"])
    logger.info("job_reported job_id=%s status=%s duration_ms=%s", job.pk, job.status, data["duration_ms"])
    return {"job_id": str(job.pk), "status": job.status}
