"""Responsibility: Generate company, context, and page query projections.
Implementation: Lists bulk-read necessary email fields and latest summaries, verify shared experiment data once, and retain global filtering and score ordering. Detail and analysis context still read complete emails and invalid results are hidden.
Relationships: API calls after authorization; ingestion and results use the same snapshot representation; sales configures human primary contacts and company archiving.
Directory:
- latest_extraction: Select most recently created extraction version for an email.
- email_data: Return protocol representation merging normalized email with current extraction.
- contact_rows: Build contact statistics and primary contact from visible business emails.
- context_pair: Build consistent Grouping and CompanyContext.
- latest_result: Select most recently stored successful analysis and its latest score.
- company_row: Generate a frontend company-list row.
- list_projection: Bulk read emails, contacts, settings, latest analysis, score, and jobs needed for lists.
- list_companies: Generate Page A filtering, sorting, pagination, and global statistics.
Variable index:
- logger: Logs list-projection scale and duration without email bodies or customer data.
"""
from datetime import datetime, time, timedelta
import logging
from time import perf_counter

from django.utils import timezone
from django.conf import settings
from django.db.models import F, Max, OuterRef, Q, Subquery
from apps.sales.models import CompanySettings

from .models import Analysis, Company, Contact, Email, Extraction, Job, Score
from .enrichment import resolve, resolve_many, snapshot_current

logger = logging.getLogger("salesmate.crm.selectors")

# Function: Select the most recently created extraction version for an email.
# Inputs: `email` is an Email instance and may have prefetched extractions.
# Outputs: Extraction instance.
# Logic: Select greatest monotonic database ID rather than sorting prompt names.
# Constraints: Email persistence transaction guarantees at least one extraction.
def latest_extraction(email):
    return max(email.extractions.all(), key=lambda item: item.pk)


# Function: Return protocol representation merging normalized email with current extraction.
# Inputs: `email` is a persisted email.
# Outputs: EmailSubmission dictionary containing body and facts.
# Logic: Keep source unchanged and let current extraction override extraction fields; mailbox_address completes a legacy transport field from the mailbox relation.
# Constraints: Company or mailbox authorization must be validated first.
def email_data(email):
    extraction = latest_extraction(email)
    return {**email.payload, "mailbox_address": email.mailbox.address, "extract_status": extraction.status,
            "extract_prompt_version": extraction.prompt_version,
            "extract_error": extraction.error, "facts": extraction.facts}


# Function: Build contact display and interaction statistics.
# Inputs: `company` is authorized, `emails` are business messages filtered by established visibility, and `projection` is list pre-read data or None.
# Outputs: Dictionary list ordered by interactions and email with a primary-contact flag.
# Logic: A human-selected primary contact must remain visible; otherwise use the existing first-contact rule.
# Constraints: Lists and complete context share this implementation; it does not access email bodies or modify contacts.
def contact_rows(company, emails, projection=None):
    source_contacts = (projection["contacts"] if projection is not None else
                       company.contacts.filter(Q(messages__business_classification="business") | Q(messages__isnull=True)).distinct())
    interactions = {}
    for email in emails:
        interactions[email.contact_id] = interactions.get(email.contact_id, 0) + 1
    contacts = []
    for contact in source_contacts:
        contacts.append({"contact_email": contact.email, "contact_name": contact.name,
                         "interaction_count": interactions.get(contact.pk, 0),
                         "is_primary": False})
    contacts.sort(key=lambda item: (-item["interaction_count"], item["contact_email"]))
    selected = (projection["settings"] if projection is not None else
                CompanySettings.objects.filter(company=company, primary_contact__company=company).select_related("primary_contact").first())
    if contacts:
        primary = selected.primary_contact.email if selected and any(item["contact_email"] == selected.primary_contact.email for item in contacts) else contacts[0]["contact_email"]
        for contact in contacts:
            contact["is_primary"] = contact["contact_email"] == primary
    return contacts


# Function: Build consistent Grouping and CompanyContext.
# Inputs: `company` is authorized; `include_priority` defaults to True and controls scoring context.
# Outputs: Grouping and CompanyContext tuple.
# Logic: Fully read visible business emails and extractions; contact statistics reuse contact_rows, while analysis context queries on demand.
# Constraints: This function serves complete context while lists use necessary-field projections; it does not truncate Agent input or change email order.
def context_pair(company, include_priority=True):
    emails = list(company.emails.filter(business_classification="business").select_related("mailbox").prefetch_related("extractions").order_by("sent_at", "dedupe_key"))
    contacts = contact_rows(company, emails)
    grouping = {"company_id": str(company.pk), "company_name": company.name, "crm_status": company.crm_status,
                "domains": company.domains, "contacts": contacts,
                "member_dedupe_keys": [email.dedupe_key for email in emails]}
    customer = {"customer_id": None, "industry_from_crm": None, "employee_count": None,
                "employee_count_source": None, "first_deal_at": None, **company.customer}
    context = {"company_id": str(company.pk), "external_snapshot_version": f"ext-{company.external_version}",
               "emails": [email_data(email) for email in emails], "customer": customer,
               "tickets": company.tickets, "quotes": company.quotes, "orders": company.orders}
    if include_priority:
        from apps.sales.priority import priority_context
        context["priority_context"] = priority_context(company)
        context["company_enrichment"] = resolve(company)
    return grouping, context


# Function: Select most recently stored successful analysis and its latest score.
# Inputs: `company` is authorized; `projection` can supply related records from this list and defaults to None for independent query.
# Outputs: Analysis or None and Score or None.
# Logic: Bulk path reuses experiment data verified this time and independent path verifies immediately; both validate lineage and visible emails, while agent uses score-v2 only.
# Constraints: Does not cache across requests; returns empty if the latest successful analysis is not visible and does not fall back to an older analysis.
def latest_result(company, projection=None):
    analysis = (projection["analysis"] if projection is not None else
                Analysis.objects.filter(snapshot__company=company, snapshot__invalidation__isnull=True, payload__status="completed").select_related("snapshot").order_by("-id").first())
    current = projection["enrichment"] if projection is not None else None
    if analysis and not snapshot_current(analysis.snapshot, company, current=current):
        return None, None
    if analysis:
        visible = (set(email.dedupe_key for email in projection["emails"]) if projection is not None else
                   set(company.emails.filter(business_classification="business").values_list("dedupe_key", flat=True)))
        if set(analysis.snapshot.payload.get("member_dedupe_keys", [])) - visible:
            return None, None
    if projection is not None:
        return analysis, projection["score"] if analysis else None
    scores = analysis.scores.all() if analysis else None
    if scores is not None and settings.ANALYSIS_PROVIDER == "agent":
        scores = scores.filter(score_version="score-v2")
    return analysis, scores.order_by("-id").first() if scores is not None else None


# Function: Generate a frontend company-list row.
# Inputs: `company` is authorized; `projection` is optional one-time related data and defaults to None for independent read.
# Outputs: Dictionary of identity, summary, email-source set, signal, score, and processing state.
# Logic: Build only contacts and email metadata required for display, take summary from latest extraction of latest email, and source profile, score, and score version from the same Analysis.
# Constraints: Does not build or reduce complete Agent context; rules output displays provider, old analysis displays stale, and unknown score remains null.
def company_row(company, projection=None):
    email_models = (projection["emails"] if projection is not None else list(company.emails.filter(
        business_classification="business").order_by("sent_at", "dedupe_key")))
    contacts = contact_rows(company, email_models, projection)
    analysis, score = latest_result(company, projection=projection)
    view = analysis.payload["list_view"] if analysis else {}
    emails = ([{"source": email.list_source, "direction": email.list_direction,
                "sent_at": email.list_sent_at, "subject": email.list_subject} for email in email_models]
              if projection is not None else [email.payload for email in email_models])
    inbound = [item for item in emails if item["direction"] == "inbound"]
    latest = emails[-1] if emails else None
    summary = (projection["summary"] if projection is not None else
               (latest_extraction(email_models[-1]).facts or {}).get("message_summary") if email_models else None)
    job = projection["job"] if projection is not None else company.jobs.order_by("-enqueued_at").first()
    return {"company_id": str(company.pk), "company_name": company.name,
            "domains": company.domains, "contacts": contacts, "crm_status": company.crm_status,
            "revision": company.revision, "email_count": len(emails),
            "email_sources": sorted({item.get("source") for item in emails if item.get("source")}),
            "last_message_at": latest["sent_at"] if latest else None,
            "last_inbound_at": inbound[-1]["sent_at"] if inbound else None,
            "headline_summary": (summary or latest["subject"]) if latest else "暂无邮件",
            "signal": view.get("signal", "unknown"), "industry": view.get("industry", "unknown"),
            "size_band": view.get("size_band", "unknown"), "score": score.value if score else None,
            "score_reasons": score.payload["score_reasons"] if score else [],
            "score_version": score.score_version if score else None,
            "scored_at": score.payload["scored_at"] if score else None,
            "provider": analysis.provider if analysis else None,
            "generated_at": analysis.payload["generated_at"] if analysis else None,
            "stale": analysis is not None and analysis.snapshot.revision != company.revision,
            "job_status": job.status if job else None,
            "job_error": job.report.get("error") if job and job.report else None}


# Function: Bulk read related records needed by lists to eliminate per-company relation queries.
# Inputs: `companies` is an evaluated sequence of authorized companies excluding archived and no-business-email companies.
# Outputs: Dictionary keyed by company.pk containing emails, summary, contacts, settings, analysis, score, job, and enrichment.
# Logic: Emails read only list fields, summaries query only latest extraction of each company's latest email, analysis and score select by maximum ID, and experiment data verifies in one batch.
# Constraints: Query remains limited to this company set, does not cache across requests or alter pagination or scoring; missing source summary stays empty and a missing extraction fails explicitly.
def list_projection(companies):
    projections = {company.pk: {"emails": [], "contacts": [], "settings": None,
                               "analysis": None, "score": None, "job": None,
                               "summary": None, "enrichment": None} for company in companies}
    if not projections:
        return projections
    ids = list(projections)
    emails = Email.objects.filter(company_id__in=ids, business_classification="business").only(
        "dedupe_key", "company_id", "contact_id", "sent_at", "received_at").annotate(
        list_source=F("payload__source"), list_direction=F("payload__direction"),
        list_sent_at=F("payload__sent_at"), list_subject=F("payload__subject")).order_by("sent_at", "dedupe_key")
    for email in emails:
        projections[email.company_id]["emails"].append(email)
    latest_emails = [item["emails"][-1] for item in projections.values() if item["emails"]]
    extraction_ids = Extraction.objects.filter(email_id__in=[email.pk for email in latest_emails]).order_by().values(
        "email_id").annotate(latest_id=Max("pk")).values("latest_id")
    summaries = dict(Extraction.objects.filter(pk__in=extraction_ids).values_list("email_id", "facts__message_summary"))
    for email in latest_emails:
        projections[email.company_id]["summary"] = summaries[email.pk]
    contacts = Contact.objects.filter(company_id__in=ids).filter(
        Q(messages__business_classification="business") | Q(messages__isnull=True)).distinct()
    for contact in contacts:
        projections[contact.company_id]["contacts"].append(contact)
    for selected in CompanySettings.objects.filter(company_id__in=ids, primary_contact__company_id=F("company_id")).select_related("primary_contact"):
        projections[selected.company_id]["settings"] = selected
    # Scan eligible analyses once and aggregate by company to avoid repeated correlated-subquery reads of large JSON payloads.
    analysis_ids = Analysis.objects.filter(snapshot__company_id__in=ids,
        snapshot__invalidation__isnull=True, payload__status="completed").order_by().values(
        "snapshot__company_id").annotate(list_analysis_id=Max("pk")).values("list_analysis_id")
    analyses = list(Analysis.objects.filter(pk__in=analysis_ids).select_related("snapshot"))
    for analysis in analyses:
        projections[analysis.snapshot.company_id]["analysis"] = analysis
    needs_enrichment = {analysis.snapshot.company_id for analysis in analyses
                        if "company_enrichment" in analysis.snapshot.payload.get("business_context", {})}
    enrichment = resolve_many([company for company in companies if company.pk in needs_enrichment]) if needs_enrichment else {}
    for company_id, current in enrichment.items():
        projections[company_id]["enrichment"] = current
    if analyses:
        latest_score = Score.objects.filter(analysis_id__in=[analysis.pk for analysis in analyses])
        if settings.ANALYSIS_PROVIDER == "agent":
            latest_score = latest_score.filter(score_version="score-v2")
        score_ids = latest_score.order_by().values("analysis_id").annotate(list_score_id=Max("pk")).values("list_score_id")
        company_ids = {analysis.pk: analysis.snapshot.company_id for analysis in analyses}
        for score in Score.objects.filter(pk__in=score_ids):
            projections[company_ids[score.analysis_id]]["score"] = score
    latest_job = Job.objects.filter(company_id=OuterRef("pk")).order_by("-enqueued_at").values("pk")[:1]
    job_ids = Company.objects.filter(pk__in=ids).annotate(list_job_id=Subquery(latest_job)).values("list_job_id")
    for job in Job.objects.filter(pk__in=job_ids):
        projections[job.company_id]["job"] = job
    return projections


# Function: Generate Page A filtering, sorting, pagination, and global statistics.
# Inputs: `companies` is the current user company QuerySet and `params` are query parameters.
# Outputs: Paginated results, total count, statistics, and current time zone.
# Logic: Project after bulk-reading authorized set; calculate global statistics before filtering; sort by score, urgency, and company ID with empty scores last, then paginate.
# Constraints: Retains global sorting and statistic semantics; memory still grows with authorized companies and emails, and logs contain only scale and duration.
def list_companies(companies, params):
    from rest_framework.exceptions import ValidationError
    started = perf_counter()
    companies = list(companies.exclude(business_settings__archived=True).filter(emails__business_classification="business").distinct())
    projections = list_projection(companies)
    rows = [company_row(company, projection=projections[company.pk]) for company in companies]
    today = timezone.localdate()
    day_start = timezone.make_aware(datetime.combine(today, time.min))
    stats = {"companies": len(rows), "unregistered": sum(row["crm_status"] == "unregistered" for row in rows),
             "new_emails_today": sum(day_start <= email.received_at < day_start + timedelta(days=1)
                                     for projection in projections.values() for email in projection["emails"])}
    for key in ["industry", "size_band", "signal", "crm_status"]:
        if params.get(key):
            allowed = params[key].split(",")
            rows = [row for row in rows if row[key] in allowed]
    if params.get("q"):
        query = params["q"].casefold()
        rows = [row for row in rows if query in " ".join([row["company_name"] or "", *row["domains"], *[c["contact_email"] for c in row["contacts"]]]).casefold()]
    rows.sort(key=lambda row: (row["score"] is None, -(row["score"] or 0),
                              -next((item["contribution"] for item in row["score_reasons"] if item["feature"] == "urgency"), 0),
                              row["company_id"]))
    try:
        page, size = int(params.get("page", 1)), int(params.get("page_size", 20))
    except (ValueError, TypeError):
        raise ValidationError("分页必须使用整数。") from None
    if page < 1 or size < 1 or size > 100:
        raise ValidationError("page ≥ 1，page_size 必须在 1–100。")
    page_rows = rows[(page - 1) * size:page * size]
    logger.debug("company_list_projection candidates=%d matched=%d returned=%d elapsed_ms=%.1f",
                 len(companies), len(rows), len(page_rows), (perf_counter() - started) * 1000)
    return {"results": page_rows, "count": len(rows), "page": page,
            "page_size": size, "stats": stats, "timezone": str(timezone.get_current_timezone())}
