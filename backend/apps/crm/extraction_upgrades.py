"""职责：提供按授权客户预览及显式排队历史 L1 升级的服务。
实现：按共享契约区分事实结构和合成来源版本，复用持久修复队列；不改旧事实、不拉邮箱。
关联：CompanyViewSet 暴露会话接口，lineage 执行重抽取，jobs 阻塞未完成修复的分析。
目录：
- upgrade_summary：读取当前客户的版本分布和修复状态。
- queue_upgrades：在公司版本锁内排队旧事实升级。
变量索引：
- logger：仅输出 owner、公司身份及升级数量的诊断日志。
"""
import logging

from django.db import transaction
from agent.workflows.l1_email import EXTRACT_PROMPT_VERSION

from integrations.extraction_contract import compatible_extraction

from .access import check_version, company_for
from .lineage import request_repair, schedule_analysis
from .selectors import latest_extraction

logger = logging.getLogger("salesmate.extraction_upgrades")


# 功能：读取客户当前业务邮件的版本兼容性和修复进度。
# 输入：`company` 为已授权公司；调用方持公司锁以保证一致读取。
# 输出：当前目标版本、各版本计数、不兼容邮件数及修复状态计数。
# 逻辑：仅检查最新抽取；符合当前结构的合成来源不需要重新调用模型。
# 约束：GET 不入队、不调用模型；不返回邮件正文或跨账户数据。
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


# 功能：显式排队一个 owner 下指定客户的旧版事实升级。
# 输入：`owner` 为认证员工，`company_id` 为客户 UUID，`expected` 为 If-Match revision。
# 输出：更新后的预览和本次新建、复用的任务数量及公司 revision。
# 逻辑：先验证公司归属及版本；仅排队结构不兼容的业务邮件；失败任务在此明确请求后重排。
# 约束：不修改人工决定、不删除旧抽取、不调用模型；排队递增 revision，阻止旧分析写回。
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
