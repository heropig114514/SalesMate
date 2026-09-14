"""职责：执行邮件入库、归组、事实补交和同步游标事务。
实现：按 owner 串行化写入，独立保存业务分类；来源变化沿血缘失效并重算，保留原文及抽取版本。
关联：serializers 校验协议，jobs 创建任务，selectors 查询完整邮件；sales.CompanyAlias 提供已确认的人工归组。
目录：
- track_saved_email：把已提交邮件关联到活动批次，兼容旧 CLI。
- submit_emails：原子保存一批已授权邮件，返回每项创建或去重状态。
- resubmit_facts：将失败事实补交为已完成，并保留邮件本体。
- sync_state：读取业务邮箱的同步游标。
- save_sync_state：按乐观锁保存同步游标。
变量索引：
- EXTRACTION_KEYS：从不可变邮件本体剥离的版本化抽取字段
- PUBLIC_DOMAINS：MVP 公共邮箱域名清单，命中后按联系人独立归组
- logger：模块脱敏诊断日志记录器
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


# 功能：原子保存一批已授权邮件，返回每项创建或去重状态。
# 输入：`owner` 为认证用户；`payloads` 为 EmailSubmission 数组。
# 输出：每封邮件的 dedupe_key、company_id 和 created/updated/duplicate 状态。
# 逻辑：先验证再锁 owner；机器分类尊重人工，既有来源更改使快照失效；成功补交取消过时修复，新邮件按既定映射归组。
# 约束：任何一项失败回滚整批；不接收 Gmail 凭证、不调用模型、不静默覆盖事实。
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


# 功能：将失败事实补交为已完成，并保留邮件本体。
# 输入：`owner` 为认证用户；`payload` 为 FactsResubmission。
# 输出：公司 ID 与新 revision。
# 逻辑：锁公司和抽取记录，校验证据后执行 failed → completed，取消过时修复并沿血缘重算受影响公司。
# 约束：重复成功补交返回 conflict，不自动重读 Gmail。
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
    validate_extraction(data, email.payload["subject"], email.payload["body_text"])
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


# 功能：读取业务邮箱的同步游标。
# 输入：`mailbox` 为已授权邮箱实例。
# 输出：README SyncState；未同步时显式返回空游标。
# 逻辑：只合并初始表示和当前状态，不假称同步完成。
# 约束：scope 初值为空，首次 Gmail 扫描范围应由 Agent 显式提供。
def sync_state(mailbox):
    return {"mailbox_id": str(mailbox.pk), "cursor": None, "scope": {}, "last_synced_at": None,
            "status": "authorization_required", **mailbox.sync_state, "version": mailbox.version}


# 功能：按乐观锁保存同步游标。
# 输入：`owner` 为认证用户；`data` 为已验证 SyncState；`expected` 为 If-Match。
# 输出：写入后的 SyncState。
# 逻辑：锁邮箱、校验版本并递增；保留活动批次身份和运行状态，不以游标写入宣称批次完成。
# 约束：启用持久检查点的邮箱由独立 Worker 管理，拒绝旧 CLI 写入；不做令牌托管。
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


# 功能：在邮件保存事务内关联活动同步批次。
# 输入：`email` 为已保存邮件。
# 输出：无；更新活动批次的逐封终态及公司。
# 逻辑：正常提交和去重都登记实际落库结果，兼容尚未发送阶段事件的旧 CLI。
# 约束：没有活动批次时不创建进度；抽取失败仍是失败任务但邮件本体保留。
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
