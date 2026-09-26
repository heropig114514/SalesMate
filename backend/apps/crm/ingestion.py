"""Responsibility: Execute email persistence, grouping, fact resubmission, and synchronization-cursor transactions.
Implementation: Serialize writes by owner and persist business classification independently; source changes invalidate and recompute through lineage while retaining sources and extraction versions.
Relationships: serializers validates protocol, jobs creates tasks, selectors queries complete email, and sales.CompanyAlias supplies confirmed manual grouping.
Directory:
- track_saved_email: Associate a submitted email with an active batch while supporting legacy CLI.
- submit_emails: Atomically save authorized emails and return creation or deduplication state for each.
- resubmit_facts: Resubmit failed facts as completed while retaining email source.
- sync_state: Read a business mailbox synchronization cursor.
- save_sync_state: Save a synchronization cursor under optimistic locking.
Variable index:
- EXTRACTION_KEYS: Versioned extraction fields removed from immutable email source.
- PUBLIC_DOMAINS: MVP public mailbox domains that group independently by contact when matched.
- logger: Redacted diagnostic logger for this module.
"""
import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError
from apps.sales.models import CompanyAlias, CompanySettings

from .access import Conflict, check_version, company_for, mailbox_for, plain
from .jobs import enqueue
from .classification import apply_classification, automatic_classification
from .models import Company, Contact, Email, Extraction
from .serializers import EmailSubmissionSerializer, FactsResubmissionSerializer, validate_extraction

logger = logging.getLogger("salesmate.ingestion")
PUBLIC_DOMAINS = frozenset(["gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com", "yahoo.com.sg", "icloud.com", "qq.com", "163.com", "126.com", "proton.me", "protonmail.com"])
EXTRACTION_KEYS = frozenset(["extract_status", "extract_prompt_version", "extract_error", "facts"])


# Function: Atomically save authorized emails and return creation or deduplication state for each.
# Inputs: `owner` is the authenticated user and `payloads` is an EmailSubmission array.
# Outputs: Each email's dedupe_key, company_id, and created, updated, or duplicate state.
# Logic: Validate before locking owner; machine classification respects humans and existing-source changes invalidate snapshots. Successful resubmission cancels stale repairs, while new emails group through established mapping.
# Constraints: Any item failure rolls back the whole batch; does not accept Gmail credentials, call models, or silently overwrite facts.
@transaction.atomic
def submit_emails(owner, payloads):
    from .lineage import invalidate_email, schedule_analysis
    serializer = EmailSubmissionSerializer(data=payloads, many=True)
    serializer.is_valid(raise_exception=True)
    if not 1 <= len(serializer.validated_data) <= 100:
        raise ValidationError("每批提交 1–100 封邮件。")
    get_user_model().objects.select_for_update().get(pk=owner.pk)
    results = []
    for value in serializer.validated_data:
        data = plain(value)
        mailbox = mailbox_for(owner, data["mailbox_id"])
        if data["mailbox_address"].casefold() != mailbox.address.casefold():
            raise ValidationError("mailbox_address 必须与后端业务邮箱一致。")
        body = {key: item for key, item in data.items() if key not in EXTRACTION_KEYS}
        email = Email.objects.filter(pk=data["dedupe_key"], mailbox__owner=owner).first()
        if email and email.payload != body:
            raise Conflict("同一 dedupe_key 的邮件本体不可修改。")
        if email:
            company = company_for(owner, email.company_id, lock=True)
            existing = email.extractions.filter(prompt_version=data["extract_prompt_version"]).order_by("-pk").first()
            if existing:
                if existing.status == "failed" and data["extract_status"] == "completed":
                    existing.status, existing.facts, existing.error = "completed", data["facts"], None
                    existing.save(update_fields=["status", "facts", "error"])
                    apply_classification(email, existing)
                    email.repairs.filter(status__in=["pending", "running", "failed"]).update(status="skipped")
                    company.revision += 1
                    company.save(update_fields=["revision"])
                    invalidate_email(email, "extraction_completed")
                    schedule_analysis(company)
                    track_saved_email(email)
                    results.append({"dedupe_key": email.pk, "company_id": str(company.pk), "status": "updated"})
                    continue
                if (existing.status, existing.facts, existing.error) != (data["extract_status"], data["facts"], data["extract_error"]):
                    raise Conflict("同一抽取版本只能由 failed 更新为 completed。")
                track_saved_email(email)
                results.append({"dedupe_key": email.pk, "company_id": str(company.pk), "status": "duplicate"})
                continue
        else:
            address = data["contact_email"].lower() if data["contact_email"] else None
            domain = address.rsplit("@", 1)[1] if address else None
            key = (
                f"contact:{address}"
                if domain in PUBLIC_DOMAINS
                else f"domain:{domain}"
                if domain
                else f"unknown:{mailbox.pk}"
            )
            alias = CompanyAlias.objects.filter(owner=owner, archived=False, group_key=f"contact:{address}").first() if address else None
            if alias is None and domain:
                alias = CompanyAlias.objects.filter(owner=owner, archived=False, group_key=f"domain:{domain}").first()
            if alias:
                company = alias.company
                if CompanySettings.objects.filter(company=company, archived=True).exists():
                    raise Conflict("人工归组的目标公司已归档，请先恢复客户或调整归组。")
            else:
                company, _ = Company.objects.get_or_create(
                    owner=owner,
                    group_key=key,
                    defaults={"domains": [] if not domain or domain in PUBLIC_DOMAINS else [domain]},
                )
            company = company_for(owner, company.pk, lock=True)
            facts = data["facts"] or {}
            contact_names = facts.get("contact_name") or []
            company_names = facts.get("company_self_reported") or []
            contact = None
            if address:
                contact, _ = Contact.objects.get_or_create(
                    company=company,
                    email=address,
                    defaults={"name": contact_names[0]["value"] if contact_names else None},
                )
            if company.name is None and company_names and automatic_classification(data["extract_status"], data["facts"], data)[0] == "business":
                company.name = company_names[0]["value"]
            storage_time = value["sent_at"] or value["received_at"] or timezone.now()
            email = Email.objects.create(dedupe_key=data["dedupe_key"], mailbox=mailbox, company=company, contact=contact,
                                         payload=body, sent_at=storage_time, received_at=value["received_at"] or storage_time,
                                         direction=data["direction"])
        replacing = email.extractions.exists()
        extraction = Extraction.objects.create(email=email, prompt_version=data["extract_prompt_version"], status=data["extract_status"], facts=data["facts"], error=data["extract_error"])
        apply_classification(email, extraction)
        if replacing or email.business_classification == "business":
            company.revision += 1
        company.save(update_fields=["revision", "name"])
        if replacing:
            if extraction.status == "completed":
                email.repairs.filter(status__in=["pending", "running", "failed"]).update(status="skipped")
            invalidate_email(email, "extraction_version_changed")
            schedule_analysis(company)
        elif (
            data["extract_status"] == "completed"
            and email.business_classification == "business"
            and data["facts"]["has_substantive_update"]
        ):
            enqueue(company, "email_ingested")
        track_saved_email(email)
        results.append({"dedupe_key": email.pk, "company_id": str(company.pk), "status": "created"})
    logger.info("emails_submitted owner_id=%s batch_size=%s created=%s", owner.pk, len(results), sum(item["status"] == "created" for item in results))
    return results


# Function: Resubmit failed facts as completed while retaining email source.
# Inputs: `owner` is the authenticated user and `payload` is FactsResubmission.
# Outputs: Company ID and new revision.
# Logic: Lock company and extraction record, verify source text and persisted direction, transition failed to completed, cancel stale repairs, and recompute through lineage.
# Constraints: Repeated successful resubmission returns conflict and does not automatically reread Gmail.
@transaction.atomic
def resubmit_facts(owner, payload):
    from .lineage import invalidate_email, schedule_analysis
    serializer = FactsResubmissionSerializer(data=payload)
    serializer.is_valid(raise_exception=True)
    data = plain(serializer.validated_data)
    email = Email.objects.filter(pk=data["dedupe_key"], mailbox__owner=owner).first()
    if email is None:
        raise NotFound("邮件不存在。")
    company = company_for(owner, email.company_id, lock=True)
    record = Extraction.objects.select_for_update().filter(email=email, prompt_version=data["extract_prompt_version"]).order_by("-pk").first()
    if record is None:
        raise NotFound("抽取版本不存在。")
    if record.status != "failed":
        raise Conflict("仅允许失败事实补交一次。")
    validate_extraction(data, email.payload["subject"], email.payload["body_text"], direction=email.direction)
    record.status, record.facts, record.error = "completed", data["facts"], None
    record.save(update_fields=["status", "facts", "error"])
    apply_classification(email, record)
    email.repairs.filter(status__in=["pending", "running", "failed"]).update(status="skipped")
    company.revision += 1
    company.save(update_fields=["revision"])
    invalidate_email(email, "extraction_completed")
    schedule_analysis(company)
    logger.info("facts_resubmitted company_id=%s revision=%s", company.pk, company.revision)
    return {"company_id": str(company.pk), "revision": company.revision}


# Function: Read the synchronization cursor for a business mailbox.
# Inputs: `mailbox` is an authorized mailbox instance.
# Outputs: README SyncState; explicitly returns an empty cursor before synchronization.
# Logic: Merge only initial representation and current state without claiming synchronization completed.
# Constraints: Initial scope is empty and the Agent must explicitly provide first Gmail scan scope.
def sync_state(mailbox):
    return {"mailbox_id": str(mailbox.pk), "cursor": None, "scope": {}, "last_synced_at": None,
            "status": "authorization_required", **mailbox.sync_state, "version": mailbox.version}


# Function: Save a synchronization cursor under optimistic locking.
# Inputs: `owner` is the authenticated user, `data` is validated SyncState, and `expected` is If-Match.
# Outputs: Written SyncState.
# Logic: Lock mailbox, validate and increment version, and retain active batch identity and running state without claiming batch completion through a cursor write.
# Constraints: A mailbox with durable checkpoints is managed by independent Worker and rejects legacy CLI writes; no token custody occurs.
@transaction.atomic
def save_sync_state(owner, data, expected):
    mailbox = mailbox_for(owner, data["mailbox_id"], lock=True)
    from .durable_models import SyncCheckpoint
    if SyncCheckpoint.objects.filter(mailbox=mailbox).exists():
        raise Conflict("此邮箱已使用持久检查点，请通过 crm_worker 同步，不能混用旧 CLI。")
    check_version(expected, mailbox.version)
    if data["version"] != mailbox.version:
        raise Conflict("载荷 version 与 If-Match 不一致。")
    active = mailbox.sync_runs.filter(status__in=["queued", "running"]).first()
    mailbox.sync_state = plain(data)
    if active:
        mailbox.sync_state.update(run_id=str(active.pk), status="sync_running" if active.status == "running" else "sync_requested")
    mailbox.version += 1
    mailbox.save(update_fields=["sync_state", "version"])
    logger.info("sync_state_saved mailbox_id=%s version=%s status=%s", mailbox.pk, mailbox.version, data["status"])
    return sync_state(mailbox)


# Function: Associate an active synchronization batch within the email persistence transaction.
# Inputs: `email` is a saved email.
# Outputs: None; updates active-batch per-message terminal state and company.
# Logic: Both ordinary submissions and deduplication register actual persisted outcomes, supporting legacy CLI that has not emitted stage events.
# Constraints: Does not create progress without an active batch; extraction failure remains a failed job while source email is retained.
def track_saved_email(email):
    from .models import EmailProcessingJob
    run = email.mailbox.sync_runs.filter(status="running").first()
    if run is None:
        return
    extraction = email.extractions.order_by("-pk").first()
    failed = extraction.status == "failed"
    EmailProcessingJob.objects.update_or_create(run=run, gmail_message_id=email.payload["gmail_message_id"], defaults={
        "dedupe_key": email.pk, "company": email.company, "status": "failed" if failed else "completed",
        "stage": "failed" if failed else "completed", "finished_at": timezone.now(),
        "error": {"code": "extraction_failed", "stage": "extracting", "message": "邮件已保存，事实抽取失败，请明确重试。"} if failed else None,
    })
