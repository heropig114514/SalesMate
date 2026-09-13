"""职责：维护邮件业务分类及人工复核的有效判断。
实现：采用适配任务的映射，人工结果优先，分类变化使公司上下文版本失效。
关联：ingestion 更新机器分类，processing_views 提供复核；selectors 只投影业务邮件。
目录：
- automatic_classification：把抽取状态映射为业务分类。
- apply_classification：更新无人工覆盖的邮件分类。
- review_data：序列化原文证据和复核版本。
- review_email：在公司锁内保存人工决定和审计。
变量索引：
- logger：只记录实体、分类和操作类型的诊断日志。
"""
import logging

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from .access import check_version, company_for
from .models import Email

logger = logging.getLogger("salesmate.classification")


# 功能：按需求文档计算机器分类。
# 输入：`status` 为抽取状态，`facts` 为事实或 None，`payload` 为原始邮件载荷。
# 输出：分类、来源、理由三元组。
# 逻辑：规则跳过隐藏；non_sales 且无更新送复核；其余保持既有业务可见性。
# 约束：尚未约定的 unknown/failed 不额外隐藏，待后续规则补齐；不调用模型。
def automatic_classification(status, facts, payload):
    if status == "skipped_non_business":
        return "non_business", "rule", payload.get("non_business_reason") or "规则判定为非业务邮件。"
    if status == "completed" and facts and facts.get("intent_hint") == "non_sales" and not facts.get("has_substantive_update"):
        return "needs_review", "llm", "模型判断为非销售沟通，等待员工复核。"
    return "business", "llm" if status == "completed" else "rule", "保留业务往来；是否重算由实质更新字段决定。"


# 功能：保存机器分类且保留人工判断。
# 输入：`email` 为邮件实例，`extraction` 为本次抽取实例。
# 输出：无；有人工决定时不写入。
# 逻辑：分类字段独立保存，不修改不可变 payload 或抽取历史。
# 约束：调用者管理事务和公司 revision；不独立入队。
def apply_classification(email, extraction):
    if email.classification_source == "human":
        return
    email.business_classification, email.classification_source, email.classification_reason = automatic_classification(extraction.status, extraction.facts, email.payload)
    email.review_revision += 1
    email.save(update_fields=["business_classification", "classification_source", "classification_reason", "review_revision"])


# 功能：返回复核所需的邮件原文与证据。
# 输入：`email` 为已按邮箱 owner 授权的 Email。
# 输出：JSON 字典，不含 OAuth 凭证。
# 逻辑：选最新抽取展示分类依据，revision 用于并发确认。
# 约束：业务列表隐藏不影响复核原文可读性。
def review_data(email):
    extraction = email.extractions.order_by("-pk").first()
    facts = extraction.facts or {} if extraction else {}
    return {"email_id": email.pk, "mailbox_id": str(email.mailbox_id), "sender": email.payload.get("from"),
            "subject": email.payload.get("subject"), "body_text": email.payload.get("body_text"),
            "received_at": email.received_at.isoformat(), "classification": email.business_classification,
            "classification_source": email.classification_source, "reason": email.classification_reason,
            "review_status": email.review_status, "revision": email.review_revision,
            "intent_hint": facts.get("intent_hint"), "intent_evidences": facts.get("intent_evidences", [])}


# 功能：保存人工确认并使受影响画像失效。
# 输入：`owner` 为登录员工，`email_id` 为邮件键，`decision` 为确认状态，`expected` 为复核版本。
# 输出：更新后的复核表示。
# 逻辑：锁公司与邮件；相同决定幂等返回，否则保存人工优先决定、审计和版本，确认业务入队。
# 约束：确认业务即创建画像；原先跳过的抽取保持未解析，重新 L1 的争议流程留待后续，不篡改历史事实；非业务决定不生成新画像。
@transaction.atomic
def review_email(owner, email_id, decision, expected):
    from apps.sales.services import audit
    from .jobs import enqueue
    if decision not in {"confirmed_business", "confirmed_non_business"}:
        raise ValidationError("review_status 必须为 confirmed_business 或 confirmed_non_business。")
    candidate = Email.objects.filter(pk=email_id, mailbox__owner=owner).first()
    if candidate is None:
        raise NotFound("邮件不存在。")
    company = company_for(owner, candidate.company_id, lock=True)
    email = Email.objects.select_for_update().get(pk=email_id)
    check_version(expected, email.review_revision)
    if email.review_status == decision:
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
    # 人工否定后停止未执行任务；运行结果仍由既有 revision 校验拒绝。
    company.jobs.filter(status="pending").update(status="skipped", report={"reason": "classification_changed"})
    if decision == "confirmed_business":
        enqueue(company, "email_ingested")
    audit(owner, company, "email_reviewed", {"email_id": email.pk, "decision": decision})
    logger.info("email_reviewed company_id=%s classification=%s revision=%s", company.pk, email.business_classification, company.revision)
    return review_data(email)
