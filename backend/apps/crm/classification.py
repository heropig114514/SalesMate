"""Responsibility: Maintain valid email business classification and human-review decisions.
Implementation: Private email scope always follows the authenticated employee; inbound messages without a purchasing stage enter review, human results take precedence, and classification changes invalidate and repair lineage automatically.
Relationships: ingestion updates machine classification, processing_views displays sourced original text and review per mailbox, and selectors projects business emails only.
Directory:
- automatic_classification: Map extraction status to business classification.
- apply_classification: Update email classification when no human override exists.
- review_data: Serialize source evidence and review version.
- review_email: Save a human decision and audit it under the company lock.
Variable index:
- logger: Diagnostic logger that records only entity, classification, and operation type.
"""

from common.laboratory import owner_scope
import logging

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from .access import check_version, company_for
from .models import Email

logger = logging.getLogger("salesmate.classification")


# Function: Calculate machine classification according to the requirements document.
# Inputs: `status` is extraction status, `facts` is facts or None, and `payload` is the source email payload.
# Outputs: A classification, source, and reason triple.
# Logic: Hide rule-skipped messages; send inbound messages without a purchasing stage to review, no longer distinguishing them by substantive update.
# Constraints: Does not additionally hide outbound messages without a stage or failed messages, and does not call a model.
def automatic_classification(status, facts, payload):
    if status == "skipped_non_business":
        return "non_business", "rule", payload.get("non_business_reason") or "规则判定为非业务邮件。"
    if status == "completed" and facts:
        if facts.get("intent_hint") is None and payload.get("direction") == "inbound":
            return "needs_review", "llm", "未识别到采购阶段，等待员工判断是否属于业务邮件。"
    return "business", "llm" if status == "completed" else "rule", "保留业务往来；是否重算由实质更新字段决定。"


# Function: Save machine classification while preserving human judgment.
# Inputs: `email` is an email instance and `extraction` is this extraction instance.
# Outputs: None; does not write when a human decision exists.
# Logic: Save classification fields independently without modifying immutable payload or extraction history.
# Constraints: The caller manages transactions and company revision; does not queue independently.
def apply_classification(email, extraction):
    if email.classification_source == "human":
        return
    email.business_classification, email.classification_source, email.classification_reason = automatic_classification(extraction.status, extraction.facts, email.payload)
    email.review_revision += 1
    email.save(update_fields=["business_classification", "classification_source", "classification_reason", "review_revision"])


# Function: Return source email text and evidence required for review.
# Inputs: `email` is an Email authorized through its mailbox owner.
# Outputs: JSON dictionary of source text, origin, date, and classification, excluding OAuth credentials.
# Logic: Take the source from the email itself, use the latest extraction for classification basis and repair status, and use revision for concurrent confirmation; errors are controlled codes.
# Constraints: Hiding entries in business lists does not affect review-source readability.
def review_data(email):
    extraction = email.extractions.order_by("-pk").first()
    facts = extraction.facts or {} if extraction else {}
    repair = email.repairs.order_by("-pk").first()
    return {"email_id": email.pk, "mailbox_id": str(email.mailbox_id), "sender": email.payload.get("from"),
            "source": email.payload.get("source"),
            "subject": email.payload.get("subject"), "body_text": email.payload.get("body_text"),
            "received_at": email.received_at.isoformat(), "classification": email.business_classification,
            "classification_source": email.classification_source, "reason": email.classification_reason,
            "review_status": email.review_status, "revision": email.review_revision,
            "intent_hint": facts.get("intent_hint"), "intent_evidences": facts.get("intent_evidences", []),
            "extraction_status": extraction.status if extraction else None,
            "repair_status": repair.status if repair else None, "repair_error": repair.error if repair else None}


# Function: Save human confirmation and invalidate affected profiles.
# Inputs: `owner` is the signed-in employee, `email_id` is the email key, `decision` is confirmation status, and `expected` is the review version.
# Outputs: Updated review representation.
# Logic: Find the email under the authenticated employee’s private scope; lock company and email, save the human decision, and propagate invalidation while retaining repair and remaining-source recomputation flows.
# Constraints: The same decision does not change version, though reconfirming business can explicitly reschedule failed repairs; profiling stops when no business messages remain.
@transaction.atomic
def review_email(owner, email_id, decision, expected):
    from apps.sales.services import audit
    from .lineage import invalidate_email, request_repair, schedule_analysis
    if decision not in {"confirmed_business", "confirmed_non_business"}:
        raise ValidationError("review_status 必须为 confirmed_business 或 confirmed_non_business。")
    candidate = Email.objects.filter(owner_scope(owner, "mailbox__owner"), pk=email_id).first()
    if candidate is None:
        raise NotFound("邮件不存在。")
    company = company_for(owner, candidate.company_id, lock=True)
    email = Email.objects.select_for_update().get(pk=email_id)
    check_version(expected, email.review_revision)
    if email.review_status == decision:
        if decision == "confirmed_business":
            request_repair(email)
            schedule_analysis(company)
        return review_data(email)
    email.review_status = decision
    email.business_classification = "business" if decision == "confirmed_business" else "non_business"
    email.classification_source = "human"
    email.classification_reason = "员工确认业务邮件。" if decision == "confirmed_business" else "员工确认非业务邮件。"
    email.reviewed_by, email.reviewed_at = owner, timezone.now()
    email.review_revision += 1
    email.save(update_fields=["review_status", "business_classification", "classification_source", "classification_reason", "reviewed_by", "reviewed_at", "review_revision"])
    company.revision += 1
    company.save(update_fields=["revision"])
    # A changed decision cancels old repair extraction; reports from running models are rejected by status and review_revision.
    email.repairs.filter(status__in=["pending", "running", "failed"]).update(status="skipped")
    invalidate_email(email, "classification_changed")
    if decision == "confirmed_business":
        request_repair(email)
    schedule_analysis(company)
    audit(owner, company, "email_reviewed", {"email_id": email.pk, "decision": decision})
    logger.info("email_reviewed company_id=%s classification=%s revision=%s", company.pk, email.business_classification, company.revision)
    return review_data(email)
