"""职责：生成公司、上下文与页面查询投影。
实现：列表批量读取必要邮件字段和最新摘要，一次核验共享实验资料，保留全局筛选与评分排序；详情和分析上下文仍读取完整邮件，失效结果不展示。
关联：API 在授权后调用；ingestion 和 results 使用同一快照表示；sales 设置人工主要联系人及客户归档。
目录：
- latest_extraction：选择邮件最近创建的抽取版本。
- email_data：返回标准邮件与当前抽取合并的协议表示。
- contact_rows：根据可见业务邮件构造联系人统计与主要联系人。
- context_pair：构建一致的 Grouping 和 CompanyContext。
- latest_result：选择最近存储的成功分析及其最新评分。
- company_row：生成前端公司列表行。
- list_projection：批量读取列表所需的邮件、联系人、设置、最新分析、评分与任务。
- list_companies：生成页面 A 的筛选、排序、分页及全局统计。
变量索引：
- logger：记录列表投影规模与耗时，不记录邮件正文或客户资料。
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

# 功能：选择邮件最近创建的抽取版本。
# 输入：`email` 为 Email 实例，可有预取的 extractions。
# 输出：Extraction 实例。
# 逻辑：按数据库单调 ID 选最新记录，不按提示词名称排序。
# 约束：邮件入库事务保证至少有一份抽取。
def latest_extraction(email):
    return max(email.extractions.all(), key=lambda item: item.pk)


# 功能：返回标准邮件与当前抽取合并的协议表示。
# 输入：`email` 为持久化邮件。
# 输出：含正文与 facts 的 EmailSubmission 字典。
# 逻辑：本体保持不变，当前抽取覆盖抽取字段；mailbox_address 从邮箱关系补齐旧记录的传输字段。
# 约束：必须先验证公司或邮箱访问权限。
def email_data(email):
    extraction = latest_extraction(email)
    return {**email.payload, "mailbox_address": email.mailbox.address, "extract_status": extraction.status,
            "extract_prompt_version": extraction.prompt_version,
            "extract_error": extraction.error, "facts": extraction.facts}


# 功能：构建联系人展示与交互统计。
# 输入：`company` 为已授权公司；`emails` 为按既定可见性筛选的业务邮件；`projection` 为本次列表预读数据或 None。
# 输出：按交互数和邮箱排序、标记主要联系人的字典列表。
# 逻辑：人工主要联系人必须仍在可见集合中，否则使用既有首联系人规则。
# 约束：列表与完整上下文共用相同实现；不访问邮件正文或修改联系人。
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


# 功能：构建一致的 Grouping 和 CompanyContext。
# 输入：`company` 为已授权公司；`include_priority` 默认 True，控制评分背景。
# 输出：Grouping、CompanyContext 二元组。
# 逻辑：完整读取可见业务邮件及抽取；联系人统计共用 contact_rows，分析背景仍按需查询。
# 约束：本函数服务完整上下文，列表使用必要字段投影；不截断 Agent 输入或改变邮件顺序。
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


# 功能：选择最近存储的成功分析及其最新评分。
# 输入：`company` 为已授权公司；`projection` 可传入本次列表的关联记录，默认 None 表示独立查询。
# 输出：Analysis 或 None，Score 或 None。
# 逻辑：批量路径复用本次核验的实验资料，独立路径即时核验；二者均验证血缘与可见邮件，agent 只取 score-v2。
# 约束：不跨请求缓存；最新成功分析若不可见即返回空，不回退更旧分析。
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


# 功能：生成前端公司列表行。
# 输入：`company` 为已授权公司；`projection` 为可选的一次性关联记录，默认 None 时独立读取。
# 输出：身份、摘要、邮件来源集合、信号、评分与处理状态组成的字典。
# 逻辑：只构造展示所需联系人与邮件元数据，摘要取最新邮件的最新抽取；画像、分数与评分版本来自同一 Analysis。
# 约束：不构造或缩减完整 Agent 上下文；规则输出展示 provider，旧分析展示 stale，未知分值保持 null。
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


# 功能：批量读取列表所需关联记录，消除逐公司关系查询。
# 输入：`companies` 为已授权、已排除归档及无业务邮件公司的已求值序列。
# 输出：以 company.pk 为键的字典，含 emails、summary、contacts、settings、analysis、score、job、enrichment。
# 逻辑：邮件仅读取列表所用字段，摘要仅查询每家公司最新邮件的最新抽取；分析和评分按最大 ID 选择，实验资料一次批量核验。
# 约束：查询限定公司集合，不跨请求缓存、不改变分页和评分；原始摘要缺失保持空值，缺少抽取记录明确失败。
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
    # 一次扫描符合条件的分析并按公司聚合，避免相关子查询反复读取大型 JSON payload。
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


# 功能：生成页面 A 的筛选、排序、分页及全局统计。
# 输入：`companies` 为当前用户公司 QuerySet；`params` 为查询参数。
# 输出：分页结果、总数、统计及当前时区。
# 逻辑：批量读取授权集合后投影；全局统计在筛选前计算；排序按分数、紧急度、公司 ID，空分最后，最后分页。
# 约束：保留全局排序和统计语义；内存规模仍随授权公司及邮件增长，日志仅包含规模和耗时。
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
