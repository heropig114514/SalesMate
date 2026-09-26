"""职责：生成公司、上下文与页面查询投影。
实现：列表在调用者授权集合内批量读取关联数据，保留全局筛选与评分排序；正式评分上下文按 owner 构建，agent 只展示 score-v2，失效结果不展示。
关联：API 在授权后调用；ingestion 和 results 使用同一快照表示；sales 设置人工主要联系人及客户归档。
目录：
- latest_extraction：选择邮件最近创建的抽取版本。
- email_data：返回标准邮件与当前抽取合并的协议表示。
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

from .models import Analysis, Company, Contact, Email, Job, Score
from .enrichment import resolve, snapshot_current

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


# 功能：构建一致的 Grouping 和 CompanyContext。
# 输入：`company` 为已授权公司；`include_priority` 默认 True，控制评分背景；`projection` 为本次列表预先读取的关联数据，默认 None 表示独立查询。
# 输出：Grouping、CompanyContext 二元组。
# 逻辑：仅业务邮件进入成员键；人工主要联系人优先；列表复用已读取关联数据，Agent 输入仍按需查询背景资料。
# 约束：projection 仅在一次读取内使用，必须对应 company；缺失键直接报错，不自动重新查询掩盖装载错误。
def context_pair(company, include_priority=True, projection=None):
    emails = (projection["emails"] if projection is not None else
              list(company.emails.filter(business_classification="business").select_related("contact", "mailbox").prefetch_related("extractions").order_by("sent_at", "dedupe_key")))
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
# 逻辑：批量读取与独立查询都验证血缘、实验资料及可见邮件；agent 分数只取 score-v2，同一分析下不回退旧算法。
# 约束：实验来源仍逐次核验，不跨请求缓存；最新成功分析若不可见即返回空，不回退更旧分析。
def latest_result(company, projection=None):
    analysis = (projection["analysis"] if projection is not None else
                Analysis.objects.filter(snapshot__company=company, snapshot__invalidation__isnull=True, payload__status="completed").select_related("snapshot").order_by("-id").first())
    if analysis and not snapshot_current(analysis.snapshot, company):
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
# 逻辑：摘要和来源从实际业务邮件获取；列表复用批量投影；画像、分数与评分版本来自同一 Analysis。
# 约束：规则输出展示 provider，旧分析展示 stale，未知分值保持 null。
def company_row(company, projection=None):
    grouping, context = context_pair(company, include_priority=False, projection=projection)
    analysis, score = latest_result(company, projection=projection)
    view = analysis.payload["list_view"] if analysis else {}
    emails = context["emails"]
    inbound = [item for item in emails if item["direction"] == "inbound"]
    latest = emails[-1] if emails else None
    job = projection["job"] if projection is not None else company.jobs.order_by("-enqueued_at").first()
    return {"company_id": str(company.pk), "company_name": company.name,
            "domains": company.domains, "contacts": grouping["contacts"], "crm_status": company.crm_status,
            "revision": company.revision, "email_count": len(emails),
            "email_sources": sorted({item.get("source") for item in emails if item.get("source")}),
            "last_message_at": latest["sent_at"] if latest else None,
            "last_inbound_at": inbound[-1]["sent_at"] if inbound else None,
            "headline_summary": ((latest.get("facts") or {}).get("message_summary") or latest["subject"]) if latest else "暂无邮件",
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
# 输出：以 company.pk 为键的字典，每项包含 emails、contacts、settings、analysis、score、job。
# 逻辑：邮件与抽取批量预取；按公司/分析分组取最大 ID 选择最新成功分析与有效版本评分，任务仍按入队时间选择。
# 约束：所有查询均限于传入公司 ID；不缓存跨请求状态、不变更分页或评分规则，实验来源核验仍由 latest_result 执行。
def list_projection(companies):
    projections = {company.pk: {"emails": [], "contacts": [], "settings": None,
                               "analysis": None, "score": None, "job": None} for company in companies}
    if not projections:
        return projections
    ids = list(projections)
    emails = Email.objects.filter(company_id__in=ids, business_classification="business").select_related(
        "contact", "mailbox").prefetch_related("extractions").order_by("sent_at", "dedupe_key")
    for email in emails:
        projections[email.company_id]["emails"].append(email)
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
