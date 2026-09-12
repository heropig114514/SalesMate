"""职责：生成公司、上下文与页面查询投影。
实现：从同一分析载荷派生列表和详情，保留旧结果时间及 stale 标记。
关联：API 在授权后调用；ingestion 和 results 使用同一快照表示。
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
from django.utils.dateparse import parse_datetime

from .models import Analysis


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
# 逻辑：本体保持不变，当前抽取覆盖抽取字段。
# 约束：必须先验证公司或邮箱访问权限。
def email_data(email):
    extraction = latest_extraction(email)
    return {**email.payload, "extract_status": extraction.status,
            "extract_prompt_version": extraction.prompt_version,
            "extract_error": extraction.error, "facts": extraction.facts}


# 功能：构建一致的 Grouping 和 CompanyContext。
# 输入：`company` 为已授权且在修改场景已锁定的公司。
# 输出：Grouping、CompanyContext 二元组。
# 逻辑：按事实时间排序邮件，联系人主要标记按往来数及邮箱稳定选择。
# 约束：revision 由 HTTP ETag 传递，协议 JSON 字段保持 README 名称。
def context_pair(company):
    emails = list(company.emails.select_related("contact").prefetch_related("extractions").order_by("sent_at", "dedupe_key"))
    contacts = []
    for contact in company.contacts.all():
        contacts.append({"contact_email": contact.email, "contact_name": contact.name,
                         "interaction_count": sum(email.contact_id == contact.pk for email in emails),
                         "is_primary": False})
    contacts.sort(key=lambda item: (-item["interaction_count"], item["contact_email"]))
    if contacts:
        contacts[0]["is_primary"] = True
    grouping = {"company_id": str(company.pk), "company_name": company.name, "crm_status": company.crm_status,
                "domains": company.domains, "contacts": contacts,
                "member_dedupe_keys": [email.dedupe_key for email in emails]}
    customer = {"customer_id": None, "industry_from_crm": None, "employee_count": None,
                "employee_count_source": None, "first_deal_at": None, **company.customer}
    context = {"company_id": str(company.pk), "external_snapshot_version": f"ext-{company.external_version}",
               "emails": [email_data(email) for email in emails], "customer": customer,
               "tickets": company.tickets, "quotes": company.quotes, "orders": company.orders}
    return grouping, context


# 功能：选择最近存储的成功分析及其最新评分。
# 输入：`company` 为已授权公司。
# 输出：Analysis 或 None，Score 或 None。
# 逻辑：允许展示旧 revision 的结果但由调用者显式标记陈旧。
# 约束：失败结果不能覆盖已完成结果的页面展示。
def latest_result(company):
    analysis = Analysis.objects.filter(snapshot__company=company, payload__status="completed").select_related("snapshot").order_by("-id").first()
    return analysis, analysis.scores.order_by("-id").first() if analysis else None


# 功能：生成前端公司列表行。
# 输入：`company` 为已授权公司。
# 输出：身份、摘要、信号、评分与处理状态组成的字典。
# 逻辑：新邮件摘要从当前抽取获取；画像与分数来自同一 Analysis。
# 约束：规则输出展示 provider，旧分析展示 stale，未知分值保持 null。
def company_row(company):
    grouping, context = context_pair(company)
    analysis, score = latest_result(company)
    view = analysis.payload["list_view"] if analysis else {}
    emails = context["emails"]
    inbound = [item for item in emails if item["direction"] == "inbound"]
    latest = emails[-1] if emails else None
    job = company.jobs.order_by("-enqueued_at").first()
    return {"company_id": str(company.pk), "company_name": company.name,
            "domains": company.domains, "contacts": grouping["contacts"], "crm_status": company.crm_status,
            "revision": company.revision, "email_count": len(emails),
            "last_message_at": latest["sent_at"] if latest else None,
            "last_inbound_at": inbound[-1]["sent_at"] if inbound else None,
            "headline_summary": ((latest.get("facts") or {}).get("message_summary") or latest["subject"]) if latest else "暂无邮件",
            "signal": view.get("signal", "unknown"), "industry": view.get("industry", "unknown"),
            "size_band": view.get("size_band", "unknown"), "score": score.value if score else None,
            "score_reasons": score.payload["score_reasons"] if score else [],
            "provider": analysis.provider if analysis else None,
            "generated_at": analysis.payload["generated_at"] if analysis else None,
            "stale": analysis is not None and analysis.snapshot.revision != company.revision,
            "job_status": job.status if job else None,
            "job_error": job.report.get("error") if job and job.report else None}


# 功能：生成页面 A 的筛选、排序、分页及全局统计。
# 输入：`companies` 为当前用户公司 QuerySet；`params` 为查询参数。
# 输出：分页结果、总数、统计及当前时区。
# 逻辑：跨维度 AND、同维度逗号多选 OR；空分最后，同分按最近入站时间降序。
# 约束：MVP 在授权数据集内内存投影；规模扩大后替换查询实现而不改响应契约。
def list_companies(companies, params):
    from rest_framework.exceptions import ValidationError
    rows = [company_row(company) for company in companies]
    today = timezone.localdate()
    day_start = timezone.make_aware(datetime.combine(today, time.min))
    stats = {"companies": len(rows), "unregistered": sum(row["crm_status"] == "unregistered" for row in rows),
             "new_emails_today": sum(company.emails.filter(received_at__gte=day_start, received_at__lt=day_start + timedelta(days=1)).count() for company in companies)}
    for key in ["industry", "size_band", "signal", "crm_status"]:
        if params.get(key):
            allowed = params[key].split(",")
            rows = [row for row in rows if row[key] in allowed]
    if params.get("q"):
        query = params["q"].casefold()
        rows = [row for row in rows if query in " ".join([row["company_name"] or "", *row["domains"], *[c["contact_email"] for c in row["contacts"]]]).casefold()]
    rows.sort(key=lambda row: (row["score"] is not None, row["score"] if row["score"] is not None else -1,
                              parse_datetime(row["last_inbound_at"]).timestamp() if row["last_inbound_at"] else float("-inf"), row["company_id"]), reverse=True)
    try:
        page, size = int(params.get("page", 1)), int(params.get("page_size", 20))
    except (ValueError, TypeError):
        raise ValidationError("分页必须使用整数。") from None
    if page < 1 or size < 1 or size > 100:
        raise ValidationError("page ≥ 1，page_size 必须在 1–100。")
    return {"results": rows[(page - 1) * size:page * size], "count": len(rows), "page": page,
            "page_size": size, "stats": stats, "timezone": str(timezone.get_current_timezone())}
