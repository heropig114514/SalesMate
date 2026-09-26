"""Responsibility: Provide authorized-company preview and explicit queuing of historical L1 upgrades.
Implementation: Distinguish fact schema and synthesized-source versions according to the shared contract and reuse the durable repair queue; neither alter old facts nor fetch mailboxes.
Relationships: CompanyViewSet exposes the session interface, lineage performs re-extraction, and jobs block analyses with unfinished repairs.
Directory:
- upgrade_summary: Read the current company's version distribution and repair status.
- queue_upgrades: Queue legacy-fact upgrades under the company version lock.
Variable index:
- logger: Diagnostic logger that outputs only owner and company identity and upgrade count.
"""
import logging

from django.db import transaction
from agent.workflows.l1_email import EXTRACT_PROMPT_VERSION

from integrations.extraction_contract import compatible_extraction

from .access import check_version, company_for
from .lineage import request_repair, schedule_analysis
from .selectors import latest_extraction

logger = logging.getLogger("salesmate.extraction_upgrades")


# Function: Read version compatibility and repair progress for a company's current business emails.
# Inputs: `company` is an authorized company; the caller holds its lock for a consistent read.
# Outputs: Current target version, counts by version, incompatible-email count, and repair-status counts.
# Logic: Inspect only the latest extraction; synthesized sources that satisfy the current schema need no model call.
# Constraints: GET neither queues nor calls models, and returns no email bodies or cross-account data.
def upgrade_summary(company):
    versions = {}
    incompatible = 0
    for email in company.emails.filter(business_classification="business").prefetch_related("extractions"):
        version = latest_extraction(email).prompt_version
        versions[version] = versions.get(version, 0) + 1
        incompatible += not compatible_extraction({**email.payload, "extract_prompt_version": version}, EXTRACT_PROMPT_VERSION)
    repairs = {status: 0 for status in ("pending", "running", "failed")}
    for email in company.emails.filter(business_classification="business").prefetch_related("repairs"):
        latest = max(email.repairs.all(), key=lambda item: item.pk, default=None)
        if latest and latest.status in repairs:
            repairs[latest.status] += 1
    return {"target_version": EXTRACT_PROMPT_VERSION, "versions": dict(sorted(versions.items())),
            "incompatible_emails": incompatible, "repairs": repairs}


# Function: Explicitly queue legacy-fact upgrades for one company under an owner.
# Inputs: `owner` is the authenticated employee, `company_id` is the company UUID, and `expected` is the If-Match revision.
# Outputs: Updated preview, counts of jobs created and reused this time, and the company revision.
# Logic: Validate company ownership and version first; queue only structurally incompatible business emails; failed jobs are rescheduled only through this explicit request.
# Constraints: Does not alter human decisions, delete old extractions, or call a model; queuing increments revision and prevents old analyses from writing back.
@transaction.atomic
def queue_upgrades(owner, company_id, expected):
    company = company_for(owner, company_id, lock=True)
    check_version(expected, company.revision)
    created, reused = 0, 0
    for email in company.emails.filter(business_classification="business").prefetch_related("extractions").order_by("pk"):
        if compatible_extraction({**email.payload, "extract_prompt_version": latest_extraction(email).prompt_version}, EXTRACT_PROMPT_VERSION):
            continue
        active = email.repairs.filter(status__in=["pending", "running"]).exists()
        repair = request_repair(email, upgrade=True)
        if repair is not None:
            reused += int(active)
            created += int(not active)
    if created:
        company.revision += 1
        company.save(update_fields=["revision"])
        schedule_analysis(company)
    logger.info("extraction_upgrade_queued owner_id=%s company_id=%s created=%s reused=%s target=%s",
                owner.pk, company.pk, created, reused, EXTRACT_PROMPT_VERSION)
    return {**upgrade_summary(company), "created": created, "reused": reused, "revision": company.revision}
