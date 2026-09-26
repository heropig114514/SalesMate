"""Responsibility: Automatically invalidate and repair results through email, extraction, L2, L3, and L4 lineage.
Implementation: Persist source edges and invalidation records; human correction or explicit upgrade repairs L1 through a repair queue and blocks company analysis claims until repair finishes.
Relationships: classification and ingestion trigger invalidation, results registers sources, and worker consumes repair extraction.
Directory:
- bind_sources: Save email and extraction sources for the current L2.
- invalidate_email: Invalidate all snapshots dependent on an email.
- schedule_analysis: Merge recomputation or cancel pending work for a company without business emails.
- request_repair: Queue repair extraction after human confirmation or explicit legacy-version upgrade.
- repair_allowed: Check whether a human-confirmed or legacy-version business source remains repairable.
- claim_repair: Claim repair extraction and handle expired jobs.
- complete_repair: Save a new extraction after validating source and human version.
- run_repair: Consume one human-authorized repair extraction job.
Variable index:
- logger: Redacted lineage and repair logger.
"""
from datetime import timedelta
import logging

from django.db import connections, transaction
from django.db.models import Q
from django.utils import timezone

from agent.workflows.l1_email import EXTRACT_PROMPT_VERSION, bailian_extraction_provider, validate_facts

from .access import Conflict, company_for
from .durable_models import ExtractionRepair, SnapshotInvalidation, SnapshotSource, StoredMessage
from .jobs import enqueue
from .models import Email, Extraction

logger = logging.getLogger("salesmate.lineage")


# Function: Register versioned source edges when saving L2.
# Inputs: `snapshot` is validated and `company` is locked.
# Outputs: None; creates complete email source edges for the snapshot.
# Logic: Includes messages without facts because they affect counts and completeness; bind the latest extraction for each.
# Constraints: Caller manages the transaction; does not fill old snapshots with current extraction and falsify history.
def bind_sources(snapshot, company):
    for email in company.emails.filter(pk__in=snapshot.payload["member_dedupe_keys"]).prefetch_related("extractions"):
        SnapshotSource.objects.get_or_create(snapshot=snapshot, email=email, defaults={
            "extraction": max(email.extractions.all(), key=lambda item: item.pk), "review_revision": email.review_revision})


# Function: Make all analysis inputs dependent on a changed email and their downstream outputs unavailable.
# Inputs: `email` is the changed message and `reason` is a controlled event name.
# Outputs: Number of invalidated snapshots.
# Logic: Find new snapshots by relation edge and legacy snapshots by stored member keys without inferring legacy extraction versions.
# Constraints: Caller holds the company lock and increments revision; retains source, facts, profiles, and scores.
def invalidate_email(email, reason):
    snapshots = email.company.inputs.filter(Q(sources__email=email) | Q(payload__member_dedupe_keys__contains=[email.pk])).distinct()
    count = 0
    for snapshot in snapshots:
        _, created = SnapshotInvalidation.objects.get_or_create(snapshot=snapshot, defaults={"reason": reason})
        count += created
    logger.info("lineage_invalidated company_id=%s snapshots=%s reason=%s", email.company_id, count, reason)
    return count


# Function: Decide recomputation or stop pending work from remaining business sources.
# Inputs: `company` is locked and has incremented revision.
# Outputs: New or merged Job, or None without business emails.
# Logic: Recompute when business emails exist; otherwise cancel pending work and let revision reject old running results.
# Constraints: Claim endpoints block companies with unfinished repairs; does not alter scoring rules.
def schedule_analysis(company):
    if company.emails.filter(business_classification="business").exists():
        return enqueue(company, "email_ingested")
    company.jobs.filter(status="pending").update(status="skipped", report={"reason": "no_business_sources"})
    return None


# Function: Queue a human-confirmed email without facts or an explicit legacy-version upgrade.
# Inputs: `email` is within a locked company and `upgrade` explicitly allows business emails to upgrade legacy extraction, defaulting to False.
# Outputs: Active repair job; returns None for current completed facts or sources not eligible for upgrade.
# Logic: Retain source version, reuse active jobs, reschedule failures only through explicit request, and do not alter human classification decision.
# Constraints: Does not call Gmail or LLM; caller holds the company lock.
def request_repair(email, *, upgrade=False):
    source = email.extractions.order_by("-pk").first()
    if upgrade and (source.prompt_version == EXTRACT_PROMPT_VERSION or email.business_classification != "business"):
        return None
    if source.status == "completed" and not upgrade:
        return None
    active = email.repairs.filter(status__in=["pending", "running"]).first()
    if active:
        return active
    email.repairs.filter(status="failed").update(status="skipped")
    repair = ExtractionRepair.objects.create(email=email, source=source, review_revision=email.review_revision)
    logger.info("extraction_repair_queued repair_id=%s company_id=%s", repair.pk, email.company_id)
    return repair


# Function: Determine whether a business source for explicit repair still satisfies authorization-time classification constraints.
# Inputs: `email` is the current message and `source` is the extraction frozen at queue time.
# Outputs: Boolean allowing repair.
# Logic: Human-confirmed business messages can repair extraction; legacy-version business messages without changed human decisions can upgrade.
# Constraints: Caller must still validate review_revision, source identity, job state, and lease; does not queue or write database records.
def repair_allowed(email, source):
    return email.business_classification == "business" and (
        email.review_status == "confirmed_business"
        or (source.prompt_version != EXTRACT_PROMPT_VERSION and email.review_status != "confirmed_non_business")
    )


# Function: Claim one human repair-extraction job.
# Inputs: `owner` is the employee bound to Worker.
# Outputs: Running job or None.
# Logic: Lock company before repair, verify source remains repairable or upgradeable, and explicitly fail expired jobs without retry.
# Constraints: Reuses the synchronization job's 600-second lease and does not hold a transaction across model calls.
@transaction.atomic
def claim_repair(owner):
    expired = ExtractionRepair.objects.filter(email__mailbox__owner=owner, status="running", lease_until__lte=timezone.now()).update(status="failed", error="repair_lease_expired")
    if expired:
        logger.warning("repair_leases_expired owner_id=%s count=%s", owner.pk, expired)
    candidate = ExtractionRepair.objects.filter(email__mailbox__owner=owner, status="pending").select_related("email").order_by("pk").first()
    if candidate is None:
        return None
    company_for(owner, candidate.email.company_id, lock=True)
    repair = ExtractionRepair.objects.select_for_update().select_related("email", "source").get(pk=candidate.pk)
    if repair.status != "pending":
        return None
    if repair.email.review_revision != repair.review_revision or not repair_allowed(repair.email, repair.source):
        repair.status = "skipped"
    else:
        repair.status, repair.lease_until = "running", timezone.now() + timedelta(seconds=600)
    repair.save(update_fields=["status", "lease_until"])
    return repair if repair.status == "running" else None


# Function: Atomically save human repair extraction and propagate invalidation.
# Inputs: `repair` is the executing job and `facts` are L1 facts validated against source text.
# Outputs: None; persists a new extraction and recomputation job.
# Logic: Validate lease, classification, source, and source evidence; save the new version, update machine classification while respecting human decision, and let other repairs continue blocking analysis.
# Constraints: Rejects all stale results and does not modify email payload or existing extraction versions.
@transaction.atomic
def complete_repair(repair, facts):
    company = company_for(repair.email.mailbox.owner, repair.email.company_id, lock=True)
    current = ExtractionRepair.objects.select_for_update().get(pk=repair.pk)
    email = Email.objects.select_for_update().get(pk=repair.email_id)
    if current.status != "running" or current.lease_until <= timezone.now() or email.review_revision != current.review_revision or not repair_allowed(email, current.source) or email.extractions.order_by("-pk").first().pk != current.source_id:
        raise Conflict("补抽取依据已变化或租约过期。")
    from .serializers import validate_extraction
    validate_extraction({"extract_prompt_version": EXTRACT_PROMPT_VERSION, "extract_status": "completed",
                         "extract_error": None, "facts": facts}, email.payload["subject"], email.payload["body_text"],
                        direction=email.direction)
    from .classification import apply_classification
    extraction = Extraction.objects.create(email=email, prompt_version=EXTRACT_PROMPT_VERSION, repair_generation=current.pk, status="completed", facts=facts, error=None)
    apply_classification(email, extraction)
    invalidate_email(email, "extraction_repaired")
    company.revision += 1
    company.save(update_fields=["revision"])
    current.status = "completed"
    current.save(update_fields=["status"])
    schedule_analysis(company)
    logger.info("extraction_repair_completed repair_id=%s revision=%s", current.pk, company.revision)


# Function: Execute one human-authorized L1 repair extraction from database source text.
# Inputs: `owner` is the Worker employee.
# Outputs: Whether work was claimed; converts exceptions to durable failure for explicit retry.
# Logic: Explicitly authorized repair or upgrade extracts with the current L1 prompt and stored source text; failure terminates only jobs still running.
# Constraints: Does not refetch Gmail or change model configuration; upgrades to the current prompt version and uses persisted body_text when legacy records have only that body.
def run_repair(owner):
    repair = claim_repair(owner)
    if repair is None:
        return False
    try:
        email = repair.email
        raw = StoredMessage.objects.filter(mailbox_id=email.mailbox_id, message_id=email.payload["gmail_message_id"]).first()
        body = raw.raw.get("eligible_body_text", raw.raw.get("body_text", "")) if raw and raw.raw else email.payload["body_text"]
        subject = email.payload["subject"]
        facts = validate_facts(
            bailian_extraction_provider(subject, body, direction=email.direction),
            subject,
            body,
        )
        complete_repair(repair, facts)
    except Exception as error:
        ExtractionRepair.objects.filter(pk=repair.pk, status="running").update(status="failed", error="repair_extraction_failed")
        logger.error("extraction_repair_failed repair_id=%s error_type=%s action=explicit_review_or_upgrade_to_retry", repair.pk, type(error).__name__)
    finally:
        connections.close_all()
    return True
