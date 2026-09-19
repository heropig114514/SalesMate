"""职责：沿邮件、抽取、L2、L3 和 L4 血缘自动失效并修复结果。
实现：来源边及失效记录持久化；人工误判纠正先补 L1，成功后才排队公司分析。
关联：classification/ingestion 触发失效，results 登记来源，worker 消费补抽取。
目录：
- bind_sources：保存当前 L2 的邮件和抽取来源。
- invalidate_email：失效所有依赖此邮件的快照。
- schedule_analysis：合并重算或取消无业务邮件公司的待办。
- request_repair：排队人工确认后的补抽取。
- claim_repair：领取补抽取并处理过期任务。
- complete_repair：核验来源和人工版本后保存新抽取。
- run_repair：消费一项人工补抽取任务。
变量索引：
- logger：脱敏血缘与修复日志。
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


# 功能：在保存 L2 时登记版本化来源边。
# 输入：`snapshot` 为已验证快照，`company` 为锁定公司。
# 输出：无；创建快照的完整邮件来源边。
# 逻辑：包括无事实邮件，因它们也影响计数和完整度；每封绑定最新抽取。
# 约束：调用者管理事务；不以当前抽取回填旧快照而伪造历史。
def bind_sources(snapshot, company):
    for email in company.emails.filter(pk__in=snapshot.payload["member_dedupe_keys"]).prefetch_related("extractions"):
        SnapshotSource.objects.get_or_create(snapshot=snapshot, email=email, defaults={
            "extraction": max(email.extractions.all(), key=lambda item: item.pk), "review_revision": email.review_revision})


# 功能：使依赖变更邮件的所有分析输入及其下游不可用。
# 输入：`email` 为变更邮件，`reason` 为受控事件名称。
# 输出：失效快照数量。
# 逻辑：新快照按关系边查找；旧快照用已存成员键定位，不猜测旧抽取版本。
# 约束：调用者持有公司锁并增加 revision；保留原文、事实、画像与分数。
def invalidate_email(email, reason):
    snapshots = email.company.inputs.filter(Q(sources__email=email) | Q(payload__member_dedupe_keys__contains=[email.pk])).distinct()
    count = 0
    for snapshot in snapshots:
        _, created = SnapshotInvalidation.objects.get_or_create(snapshot=snapshot, defaults={"reason": reason})
        count += created
    logger.info("lineage_invalidated company_id=%s snapshots=%s reason=%s", email.company_id, count, reason)
    return count


# 功能：按剩余业务来源决定重算或停止待办。
# 输入：`company` 为锁定且版本已递增的公司。
# 输出：新建或合并的 Job，无业务邮件返回 None。
# 逻辑：有业务邮件就重算；无邮件时取消待办，旧运行结果由 revision 拒绝。
# 约束：修复尚未完成的公司由领取端阻塞；不修改评分规则。
def schedule_analysis(company):
    if company.emails.filter(business_classification="business").exists():
        return enqueue(company, "email_ingested")
    company.jobs.filter(status="pending").update(status="skipped", report={"reason": "no_business_sources"})
    return None


# 功能：为人工确认且尚无事实的邮件排队补抽取。
# 输入：`email` 为锁定、已人工确认业务的邮件。
# 输出：活动修复任务；已有完成事实返回 None。
# 逻辑：保留旧抽取；重复调用复用活动工作，失败任务仅在此明确操作后重排。
# 约束：不会调用 Gmail/LLM；调用者持有公司锁。
def request_repair(email):
    source = email.extractions.order_by("-pk").first()
    if source.status == "completed":
        return None
    active = email.repairs.filter(status__in=["pending", "running"]).first()
    if active:
        return active
    email.repairs.filter(status="failed").update(status="skipped")
    repair = ExtractionRepair.objects.create(email=email, source=source, review_revision=email.review_revision)
    logger.info("extraction_repair_queued repair_id=%s company_id=%s", repair.pk, email.company_id)
    return repair


# 功能：领取一个人工补抽取任务。
# 输入：`owner` 为 Worker 绑定员工。
# 输出：运行任务或 None。
# 逻辑：先锁公司再锁修复；过期任务显式失败，不自动重试。
# 约束：复用同步任务的 600 秒租期；不持事务跨模型调用。
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
    if repair.email.review_revision != repair.review_revision or repair.email.review_status != "confirmed_business":
        repair.status = "skipped"
    else:
        repair.status, repair.lease_until = "running", timezone.now() + timedelta(seconds=600)
    repair.save(update_fields=["status", "lease_until"])
    return repair if repair.status == "running" else None


# 功能：原子保存人工补抽取并传播失效。
# 输入：`repair` 为执行任务，`facts` 为已按原文校验的 L1 事实。
# 输出：无；新抽取及重算任务持久保存。
# 逻辑：校验租期、人工决定与原抽取未变化，以任务 ID 作为修复代次保留历史和真实提示词版本。
# 约束：任何过期结果均拒绝；不修改邮件 payload 或原有抽取版本。
@transaction.atomic
def complete_repair(repair, facts):
    company = company_for(repair.email.mailbox.owner, repair.email.company_id, lock=True)
    current = ExtractionRepair.objects.select_for_update().get(pk=repair.pk)
    email = Email.objects.select_for_update().get(pk=repair.email_id)
    if current.status != "running" or current.lease_until <= timezone.now() or email.review_revision != current.review_revision or email.review_status != "confirmed_business" or email.extractions.order_by("-pk").first().pk != current.source_id:
        raise Conflict("补抽取依据已变化或租约过期。")
    Extraction.objects.create(email=email, prompt_version=EXTRACT_PROMPT_VERSION, repair_generation=current.pk, status="completed", facts=facts, error=None)
    invalidate_email(email, "extraction_repaired")
    company.revision += 1
    company.save(update_fields=["revision"])
    current.status = "completed"
    current.save(update_fields=["status"])
    schedule_analysis(company)
    logger.info("extraction_repair_completed repair_id=%s revision=%s", current.pk, company.revision)


# 功能：从数据库原文执行一个人工授权的 L1 补抽取。
# 输入：`owner` 为 Worker 员工。
# 输出：是否领取工作；异常转换为持久失败供明确重试。
# 逻辑：已明确确认业务，直接使用原 L1 提示词抽取并校验，不再应用自动邮件跳过规则。
# 约束：不重新拉 Gmail、不更改模型或提示词；旧记录仅有 body_text 时明确使用该持久正文。
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
        logger.error("extraction_repair_failed repair_id=%s error_type=%s action=confirm_business_to_retry", repair.pk, type(error).__name__)
    finally:
        connections.close_all()
    return True
