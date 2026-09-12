"""职责：提供可移除的显式规则占位，帮助前后端在 Agent 未接入时联调。
实现：只抽取带中文标签的原文行，确定性归并与七维模板输出；通过正式保存服务校验载荷。
关联：仅 ANALYSIS_PROVIDER=rules 时由页面动作调用；agent 模式只入队，不隐式回退。
目录：
- extract_email：将显式模拟邮件转换为 L1 标准提交。
- build_input：按照 README 的事实时间线规则归并 L2 输入。
- dimension：将已有事实映射为一个展示维度。
- generate_analysis：生成可替换的七维规则占位及列表投影。
- compute_score：生成独立版本的规则占位分数。
- run_company：执行当前公司的一次规则任务。
变量索引：
- ANALYSIS_VERSION：规则占位 L3 版本 rules-analysis-v1
- EXTRACT_VERSION：与真实 L1 契约一致的 extract-v6
- LABELS：保守提取的中文行标签与事实字段映射
- MERGE_VERSION：与真实 L2 契约一致的 merge-v2
- SCORE_VERSION：独立占位评分版本 rules-score-v1，不修改正式权重
- logger：模块脱敏诊断日志记录器
"""
import hashlib
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone as datetime_timezone

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .access import company_for
from .jobs import claim, report
from .results import cached_analysis, save_analysis, save_input, save_score
from .selectors import context_pair
from .serializers import FACT_FIELDS, INDUSTRIES

logger = logging.getLogger("salesmate.rules")
EXTRACT_VERSION = "extract-v6"
MERGE_VERSION = "merge-v2"
ANALYSIS_VERSION = "rules-analysis-v1"
SCORE_VERSION = "rules-score-v1"
LABELS = {"contact_name": "联系人", "contact_title": "职位", "company_self_reported": "公司",
          "business_background": "行业", "employee_scale_hint": "人数", "product_need": "需求",
          "quantity": "数量", "budget": "预算", "delivery_time": "交期", "decision_process": "决策流程",
          "concerns": "顾虑", "quote_reference": "报价记录", "order_reference": "订单记录"}


# 功能：将显式模拟邮件转换为 L1 标准提交。
# 输入：`mailbox` 为业务邮箱；`sender`、`subject`、`body` 为人工文本；`message_id` 和 `sent_at` 可指定合成样例身份与时间。
# 输出：source=synthetic_sample 的 EmailSubmission。
# 逻辑：仅提取“标签：内容”行，采购关键词只决定单封倾向；未提及事实字段保持空数组。
# 约束：不读取 Gmail，不解析真实邮箱授权，不推断币种、日期或公司关系。
def extract_email(mailbox, sender, subject, body, message_id=None, sent_at=None):
    facts = {field: [] for field in FACT_FIELDS}
    for field, label in LABELS.items():
        match = re.search(r"(?m)^" + label + r"[：:]\s*([^\n\r]+)", body)
        if match:
            facts[field] = [{"value": match.group(1).strip(), "evidences": [match.group(0)]}]
    intent_match = re.search(r"询价|采购|购买|报价|需求", body)
    facts.update({"has_substantive_update": any(facts[key] for key in ["product_need", "quantity", "budget", "delivery_time", "decision_process", "concerns", "quote_reference", "order_reference"]),
                  "message_summary": subject[:80], "intent_hint": "purchase_inquiry" if intent_match else "unknown",
                  "intent_evidences": [intent_match.group(0)] if intent_match else []})
    message_id = message_id or f"sample-{uuid.uuid4()}"
    sent_at = sent_at or timezone.now().isoformat()
    return {"dedupe_key": f"{mailbox.address.casefold()}:{message_id}", "mailbox_id": str(mailbox.pk),
            "mailbox_address": mailbox.address, "gmail_message_id": message_id,
            "thread_id": f"sample-{sender.lower()}", "from": sender, "to": [mailbox.address], "cc": [],
            "sent_at": sent_at, "received_at": sent_at, "subject": subject, "body_text": body,
            "direction": "inbound", "source": "synthetic_sample", "contact_email": sender,
            "non_business_hint": False, "non_business_reason": None, "extract_status": "completed",
            "extract_prompt_version": EXTRACT_VERSION, "extract_error": None, "facts": facts}


# 功能：按照 README 的事实时间线规则归并 L2 输入。
# 输入：`grouping`、`context` 为同一后端 revision 的两个协议对象。
# 输出：含 SHA-256 输入版本、事实历史和确定性指标的 AnalysisInput。
# 逻辑：保留每条已完成事实，按时间和来源稳定排序；响应间隔仅配对同线程先收后发。
# 约束：不做冲突语义判断，不修改原事实、不丢失被更新的预算。
def build_input(grouping, context):
    emails = sorted(
        context["emails"],
        key=lambda item: (
            parse_datetime(item["sent_at"])
            if item.get("sent_at")
            else datetime.min.replace(tzinfo=datetime_timezone.utc),
            item["dedupe_key"],
        ),
    )
    facts = {}
    for email in emails:
        if email["extract_status"] != "completed":
            continue
        for field in FACT_FIELDS:
            for fact in email["facts"][field]:
                facts.setdefault(field, []).append({**fact, "dedupe_key": email["dedupe_key"], "fact_time": email["sent_at"]})
    for field in FACT_FIELDS:
        facts.setdefault(field, [])
    inbound = [item for item in emails if item["direction"] == "inbound"]
    outbound = [item for item in emails if item["direction"] == "outbound"]
    gap = None
    for received in reversed(inbound):
        if not received.get("thread_id") or not received.get("sent_at"):
            continue
        reply = next(
            (
                item
                for item in outbound
                if item.get("thread_id") == received["thread_id"]
                and item.get("sent_at")
                and parse_datetime(item["sent_at"]) > parse_datetime(received["sent_at"])
            ),
            None,
        )
        if reply:
            gap = round((parse_datetime(reply["sent_at"]) - parse_datetime(received["sent_at"])).total_seconds() / 86400, 2)
            break
    identity = {"emails": sorted((item["dedupe_key"], item["extract_prompt_version"], item["extract_status"]) for item in emails),
                "merge_version": MERGE_VERSION, "external_snapshot_version": context["external_snapshot_version"]}
    version = "sha256:" + hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    timed_emails = [item for item in emails if item.get("sent_at")]
    timed_inbound = [item for item in inbound if item.get("sent_at")]
    timed_outbound = [item for item in outbound if item.get("sent_at")]
    return {"company_id": grouping["company_id"], "input_version": version, "merge_version": MERGE_VERSION,
            "external_snapshot_version": context["external_snapshot_version"], "built_at": timezone.now().isoformat(),
            "company": {"company_name": grouping["company_name"], "crm_status": grouping["crm_status"],
                        "domains": grouping["domains"], "contacts": grouping["contacts"]},
            "business_context": {"customer": context["customer"], "tickets": context["tickets"],
                                 "quotes": context["quotes"], "orders": context["orders"]},
            "latest_message_summary": ((emails[-1].get("facts") or {}).get("message_summary") or emails[-1]["subject"]) if emails else None,
            "member_dedupe_keys": grouping["member_dedupe_keys"],
            "unparsed_message_count": sum(item["extract_status"] != "completed" for item in emails), "facts": facts,
            "metrics": {"inbound_count": len(inbound), "outbound_count": len(outbound),
                        "substantive_inbound_count": sum((item.get("facts") or {}).get("has_substantive_update", False) for item in inbound),
                        "first_contact_at": timed_emails[0]["sent_at"] if timed_emails else None,
                        "last_inbound_at": timed_inbound[-1]["sent_at"] if timed_inbound else None,
                        "last_outbound_at": timed_outbound[-1]["sent_at"] if timed_outbound else None,
                        "response_gap_days": gap, "has_history_order": bool(context["orders"]), "crm_status": grouping["crm_status"]}}


# 功能：将已有事实映射为一个展示维度。
# 输入：`snapshot` 为 L2 快照；`fields` 为要展示的事实字段数组。
# 输出：Dimension，包含逐条原文引用和缺失标签。
# 逻辑：保留全部历史值，模板只添加字段标签。
# 约束：不生成推断或概率，不宣称已完成 Agent 分析。
def dimension(snapshot, fields):
    facts = []
    missing = []
    for field in fields:
        items = snapshot["facts"].get(field, [])
        if not items:
            missing.append(LABELS[field])
        facts.extend({"text": f"{LABELS[field]}：{item['value']}", "source_refs": [item["dedupe_key"]]} for item in items)
    return {"facts": facts, "inferences": [], "missing_fields": missing}


# 功能：生成可替换的七维规则占位及列表投影。
# 输入：`snapshot` 为 L2；`grouping`、`context` 为同版后端数据。
# 输出：README Analysis，提示词版本显式使用 rules 前缀。
# 逻辑：仅依据明确采购关键词、权威 CRM 和订单决定信号；未知特征不填零。
# 约束：不输出冲突推断或新闻；标签模板不能替代真实 Agent 质量验证。
def generate_analysis(snapshot, grouping, context):
    emails = context["emails"]
    inquiry = [item for item in emails if item["direction"] == "inbound" and (item.get("facts") or {}).get("intent_hint") == "purchase_inquiry"]
    signal, refs, signal_text = "unknown", [], "暂无足够证据"
    if inquiry:
        signal, refs, signal_text = "inquiry_intent", [inquiry[-1]["dedupe_key"]], "邮件出现明确采购或询价表达（规则占位）"
        if context["orders"]:
            signal, refs = "repeat_purchase", refs + [context["orders"][0]["order_id"]]
            signal_text = "已有订单记录且出现新的采购表达（规则占位）"
    elif snapshot["metrics"]["inbound_count"] == 1 and grouping["crm_status"] == "unregistered":
        signal, refs, signal_text = "new_lead_no_profile", [item["dedupe_key"] for item in emails if item["direction"] == "inbound"], "首次来信且尚未建档"
    industry, industry_refs = "unknown", []
    crm_industry = context["customer"]["industry_from_crm"]
    if crm_industry in INDUSTRIES and crm_industry != "unknown":
        industry, industry_refs = crm_industry, [grouping["company_id"]]
    else:
        for item in snapshot["facts"].get("business_background", []):
            if item["value"] in INDUSTRIES:
                industry, industry_refs = item["value"], [item["dedupe_key"]]
    count = context["customer"]["employee_count"]
    size = "unknown" if count is None else "lt_50" if count < 50 else "50_100" if count < 100 else "100_200" if count < 200 else "200_500" if count < 500 else "gte_500"
    features = {}
    for feature, required in [("demand_clarity", ["product_need", "quantity", "budget"]), ("urgency", ["delivery_time"]), ("decision_visibility", ["decision_process"])]:
        known = all(snapshot["facts"].get(field) for field in required)
        features[feature] = {"value": 1 if known else None, "basis": "原文提供相关字段；规则仅确认存在，不判断强弱" if known else "信息不足"}
    profile = {"industry_context": dimension(snapshot, ["business_background"]),
               "company_ops": dimension(snapshot, ["contact_name", "contact_title", "decision_process"]),
               "intent": dimension(snapshot, ["product_need", "quantity", "budget", "delivery_time"])}
    timeline = {"facts": [{"text": f"{item['sent_at'][:10] if item.get('sent_at') else '时间未知'} · {item['subject']}", "source_refs": [item["dedupe_key"]]} for item in emails], "inferences": [], "missing_fields": []}
    missing = sorted(set(profile["company_ops"]["missing_fields"] + profile["intent"]["missing_fields"]))
    if count is None:
        missing.append("员工规模")
    unparsed = snapshot["unparsed_message_count"]
    note = f"有 {unparsed} 封邮件未解析，以下事实不包含其内容。" if unparsed else None
    if note:
        missing.append(note)
    return {"company_id": snapshot["company_id"], "input_version": snapshot["input_version"],
            "analysis_prompt_version": ANALYSIS_VERSION, "generated_at": timezone.now().isoformat(),
            "analysis_base_time": snapshot["built_at"], "status": "completed",
            "list_view": {"signal": signal, "signal_evidence": {"text": signal_text, "source_refs": refs},
                          "ticket_signals": [], "industry": industry,
                          "industry_evidence": {"text": "已提供的行业字段" if industry_refs else "未知", "source_refs": industry_refs},
                          "size_band": size, "size_source": context["customer"]["employee_count_source"] or "unknown",
                          "headline_summary": ((emails[-1].get("facts") or {}).get("message_summary") or emails[-1]["subject"]) if emails else "暂无邮件",
                          "score_features": features},
            "detail_view": {"conflicts": [], "profile": profile,
                            "analysis": {"timeline": timeline, "opportunity": dimension(snapshot, ["product_need", "quantity", "budget"]),
                                         "risk": dimension(snapshot, ["concerns", "decision_process", "delivery_time"]),
                                         "guidance": {"facts": [], "inferences": [], "missing_fields": missing}},
                            "missing_fields": missing,
                            "context_completeness": {"unparsed_message_count": unparsed, "note": note}},
            "error": None}


# 功能：生成独立版本的规则占位分数。
# 输入：`analysis` 为规则 Analysis。
# 输出：README Score；资料不足为 null。
# 逻辑：三个已知特征等权，每项 value/3 归一，贡献取两位小数并让末项吸收舍入差。
# 约束：使用 rules-score-v1，不修改 README 待校准 score-v1 权重；不可用于正式优先级评测。
def compute_score(analysis):
    features = analysis["list_view"]["score_features"]
    insufficient = analysis["list_view"]["signal"] == "unknown" or any(item["value"] is None for item in features.values())
    score = None if insufficient else round(sum(item["value"] for item in features.values()) / 9 * 100)
    reasons = [{"feature": "insufficient_data", "contribution": 0, "note": "资料不足，未评分"}]
    if score is not None:
        reasons = [{"feature": key, "contribution": round(value["value"] / 9 * 100, 2), "note": "规则占位：" + value["basis"]} for key, value in features.items()]
        reasons[-1]["contribution"] = round(score - sum(item["contribution"] for item in reasons[:-1]), 2)
    return {"company_id": analysis["company_id"], "input_version": analysis["input_version"], "score": score,
            "score_reasons": reasons, "score_version": SCORE_VERSION, "scored_at": timezone.now().isoformat()}


# 功能：执行当前公司的一次规则任务。
# 输入：`owner` 为会话用户；`company_id` 为已授权公司。
# 输出：最终 JobReport 简要响应；无待办时返回 None。
# 逻辑：正式领取任务、构建协议对象、调用共用结果服务；完整缓存复用，缺少评分时仅补评分。
# 约束：异常记录脱敏类型并显式失败后继续抛出；不重试、不调用真实模型、不降级其他模式。
def run_company(owner, company_id):
    jobs = claim(owner, 1, 120, company_id)
    if not jobs:
        return None
    job = jobs[0]
    start = time.monotonic()
    version = None
    try:
        with transaction.atomic():
            company = company_for(owner, company_id, lock=True)
            grouping, context = context_pair(company)
            snapshot = build_input(grouping, context)
            snapshot = save_input(owner, snapshot, job["expected_version"], job["job_id"], job["lease_token"])
        version = snapshot["input_version"]
        cache = cached_analysis(company, version, ANALYSIS_VERSION)
        produced_analysis, produced_score = False, False
        if not cache["hit"]:
            analysis = generate_analysis(snapshot, grouping, context)
            save_analysis(owner, analysis, job["expected_version"], job["job_id"], job["lease_token"], provider="rules")
            produced_analysis = True
        else:
            analysis = company.inputs.get(input_version=version).analyses.get(prompt_version=ANALYSIS_VERSION).payload
        stored_analysis = company.inputs.get(input_version=version).analyses.get(prompt_version=ANALYSIS_VERSION)
        if not stored_analysis.scores.exists():
            save_score(owner, compute_score(analysis), job["expected_version"], job["job_id"], job["lease_token"])
            produced_score = True
        return report(owner, {"job_id": job["job_id"], "status": "completed" if produced_analysis or produced_score else "skipped", "input_version": version,
                              "produced": {"analysis": produced_analysis, "score": produced_score, "emails_submitted": 0},
                              "error": None, "duration_ms": int((time.monotonic() - start) * 1000)}, job["lease_token"])
    except Exception as exc:
        logger.error("rule_job_failed job_id=%s error_type=%s action=inspect_contract_and_context", job["job_id"], type(exc).__name__)
        try:
            report(owner, {"job_id": job["job_id"], "status": "failed", "input_version": version, "produced": {},
                           "error": {"code": getattr(exc, "default_code", "invalid_state"), "message": f"规则处理失败：{type(exc).__name__}"},
                           "duration_ms": int((time.monotonic() - start) * 1000)}, job["lease_token"])
        except Exception as report_error:
            logger.error("rule_failure_report_rejected job_id=%s error_type=%s action=inspect_job_lease", job["job_id"], type(report_error).__name__)
        raise
