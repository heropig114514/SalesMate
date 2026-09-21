"""职责：维护邮件业务分类及人工复核的有效判断。
实现：实验模式使用公开跨账号业务范围；无采购阶段的入站邮件进入复核，人工结果优先；分类变化沿血缘失效并自动修正。
关联：ingestion 更新机器分类，processing_views 按邮箱展示带来源的原文与复核；selectors 只投影业务邮件。
目录：
- automatic_classification：把抽取状态映射为业务分类。
- apply_classification：更新无人工覆盖的邮件分类。
- review_data：序列化原文证据和复核版本。
- review_email：在公司锁内保存人工决定和审计。
变量索引：
- logger：只记录实体、分类和操作类型的诊断日志。
"""

from common.laboratory import owner_scope
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
# 逻辑：规则跳过隐藏；无采购阶段的入站邮件送复核，不再以有无实质更新区分。
# 约束：外发无阶段及 failed 不额外隐藏；不调用模型。
def automatic_classification(status, facts, payload):
    if status == "skipped_non_business":
        return "non_business", "rule", payload.get("non_business_reason") or "规则判定为非业务邮件。"
    if status == "completed" and facts:
        if facts.get("intent_hint") is None and payload.get("direction") == "inbound":
            return "needs_review", "llm", "未识别到采购阶段，等待员工判断是否属于业务邮件。"
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
# 输出：原文、来源、日期和分类的 JSON 字典，不含 OAuth 凭证。
# 逻辑：来源取邮件本体，选最新抽取展示分类依据及补抽取状态，revision 用于并发确认；错误为受控代码。
# 约束：业务列表隐藏不影响复核原文可读性。
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


# 功能：保存人工确认并使受影响画像失效。
# 输入：`owner` 为登录员工，`email_id` 为邮件键，`decision` 为确认状态，`expected` 为复核版本。
# 输出：更新后的复核表示。
# 逻辑：按当前模式查找邮件，实验模式跨账号；锁公司与邮件后保存人工决定并传播失效，保留补抽取及剩余来源重算流程。
# 约束：同一决定不改变版本，但再次确认业务可明确重排失败补抽取；无剩余业务邮件则停止画像。
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
    # 更改决定撤销旧补抽取，运行中的模型回报通过状态与 review_revision 拒绝。
    email.repairs.filter(status__in=["pending", "running", "failed"]).update(status="skipped")
    invalidate_email(email, "classification_changed")
    if decision == "confirmed_business":
        request_repair(email)
    schedule_analysis(company)
    audit(owner, company, "email_reviewed", {"email_id": email.pk, "decision": decision})
    logger.info("email_reviewed company_id=%s classification=%s revision=%s", company.pk, email.business_classification, company.revision)
    return review_data(email)
