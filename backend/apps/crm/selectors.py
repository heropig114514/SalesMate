"""职责：生成公司、上下文与页面查询投影。
实现：业务分类约束邮件范围，正式评分上下文按 owner 构建；agent 模式只展示 score-v2，血缘或实验来源失效结果不展示，公司分析上下文附带已匹配的共享资料。
关联：API 在授权后调用；ingestion 和 results 使用同一快照表示；sales 设置人工主要联系人及客户归档。
目录：
- latest_extraction：选择邮件最近创建的抽取版本。
- email_data：返回标准邮件与当前抽取合并的协议表示。
- context_pair：构建一致的 Grouping 和 CompanyContext。
- latest_result：选择最近存储的成功分析及其最新评分。
- company_row：生成前端公司列表行。
- list_companies：生成页面 A 的筛选、排序、分页及全局统计。
变量索引：
- 无
"""
from datetime import datetime, time, timedelta

from django.utils import timezone
from django.conf import settings
from django.db.models import Q
from apps.sales.models import CompanySettings

from .models import Analysis
from .enrichment import resolve, snapshot_current


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
# 输入：`company` 为已授权且在 Agent 输入场景已锁定的公司；`include_priority` 默认 True，列表内部可显式跳过评分背景和实验资料查询。
# 输出：Grouping、CompanyContext 二元组。
# 逻辑：仅业务邮件进入成员键；主要联系人使用人工设置；按需附加同 owner 商机、历史订单、销售方目标画像与跨账号获准实验资料。
# 约束：revision 由 HTTP ETag 传递，协议 JSON 字段保持 README 名称。
def context_pair(company, include_priority=True):
    emails = list(company.emails.filter(business_classification="business").select_related("contact", "mailbox").prefetch_related("extractions").order_by("sent_at", "dedupe_key"))
    contacts = []
    for contact in company.contacts.filter(Q(messages__business_classification="business") | Q(messages__isnull=True)).distinct():
        contacts.append({"contact_email": contact.email, "contact_name": contact.name,
                         "interaction_count": sum(email.contact_id == contact.pk for email in emails),
                         "is_primary": False})
    contacts.sort(key=lambda item: (-item["interaction_count"], item["contact_email"]))
    selected = CompanySettings.objects.filter(company=company, primary_contact__company=company).select_related("primary_contact").first()
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
# 输入：`company` 为已授权公司。
# 输出：Analysis 或 None，Score 或 None。
# 逻辑：只选择未被血缘失效且实验资料仍一致的结果；agent 模式的分数只取 score-v2，同一分析下不回退旧算法。
# 约束：失败结果不能覆盖成功画像；旧快照继续标记 stale，隐藏邮件导致结果不可展示。
def latest_result(company):
    analysis = Analysis.objects.filter(snapshot__company=company, snapshot__invalidation__isnull=True, payload__status="completed").select_related("snapshot").order_by("-id").first()
    if analysis and not snapshot_current(analysis.snapshot, company):
        return None, None
    if analysis:
        visible = set(company.emails.filter(business_classification="business").values_list("dedupe_key", flat=True))
        if set(analysis.snapshot.payload.get("member_dedupe_keys", [])) - visible:
            return None, None
    scores = analysis.scores.all() if analysis else None
    if scores is not None and settings.ANALYSIS_PROVIDER == "agent":
        scores = scores.filter(score_version="score-v2")
    return analysis, scores.order_by("-id").first() if scores is not None else None


# 功能：生成前端公司列表行。
# 输入：`company` 为已授权公司。
# 输出：身份、摘要、邮件来源集合、信号、评分与处理状态组成的字典。
# 逻辑：摘要和来源从实际业务邮件获取，不为列表重复计算背景；画像、分数与评分版本来自同一 Analysis。
# 约束：规则输出展示 provider，旧分析展示 stale，未知分值保持 null。
def company_row(company):
    grouping, context = context_pair(company, include_priority=False)
    analysis, score = latest_result(company)
    view = analysis.payload["list_view"] if analysis else {}
    emails = context["emails"]
    inbound = [item for item in emails if item["direction"] == "inbound"]
    latest = emails[-1] if emails else None
    job = company.jobs.order_by("-enqueued_at").first()
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


# 功能：生成页面 A 的筛选、排序、分页及全局统计。
# 输入：`companies` 为当前用户公司 QuerySet；`params` 为查询参数。
# 输出：分页结果、总数、统计及当前时区。
# 逻辑：排除人工归档及无业务邮件公司；筛选保留原语义，排序按分数降序、紧急度贡献降序、公司 ID 升序，空分最后。
# 约束：MVP 在授权数据集内内存投影；规模扩大后替换查询实现而不改响应契约。
def list_companies(companies, params):
    from rest_framework.exceptions import ValidationError
    companies = companies.exclude(business_settings__archived=True).filter(emails__business_classification="business").distinct()
    rows = [company_row(company) for company in companies]
    today = timezone.localdate()
    day_start = timezone.make_aware(datetime.combine(today, time.min))
    stats = {"companies": len(rows), "unregistered": sum(row["crm_status"] == "unregistered" for row in rows),
             "new_emails_today": sum(company.emails.filter(business_classification="business", received_at__gte=day_start, received_at__lt=day_start + timedelta(days=1)).count() for company in companies)}
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
    return {"results": rows[(page - 1) * size:page * size], "count": len(rows), "page": page,
            "page_size": size, "stats": stats, "timezone": str(timezone.get_current_timezone())}
