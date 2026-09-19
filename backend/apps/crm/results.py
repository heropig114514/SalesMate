"""职责：保存分析输入、判断与评分并验证并发和来源。
实现：在公司行锁下验证 revision、租约、引用及不可变键；正式评分解释与分数原子保存并核验邮件证据。
关联：API 与规则占位共用该入口，selectors 从这些快照投影页面。
目录：
- save_input：保存 L2 原始输入快照。
- validate_refs：递归核对每条来源引用属于当前业务快照。
- save_analysis：保存经验证的 L3 分析。
- save_score：保存与当前成功分析对应的评分。
- cached_analysis：查询指定版本的分析缓存元数据。
变量索引：
- logger：模块脱敏诊断日志记录器
- DEAL_PROBABILITY_PATTERN：识别并禁止 Agent 输出成交概率或胜率表述。
"""
import json
import logging
import re
from datetime import datetime, timezone

from django.db import transaction
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError

from .access import Conflict, InvalidState, check_version, company_for, plain
from .jobs import require_lease
from .models import Analysis, AnalysisInput, Score
from .priority_results import validate_priority_sources
from .selectors import context_pair
from .serializers import AnalysisInputSerializer, AnalysisSerializer, ScoreSerializer, FACT_FIELDS

logger = logging.getLogger("salesmate.analysis")

DEAL_PROBABILITY_PATTERN = re.compile(
    r"(?:成交|成单|签约|赢单)(?:的)?(?:概率|可能性|可能|成功率)|"
    r"(?:成交率|赢单率|胜率)"
)


# 功能：保存 L2 原始输入快照。
# 输入：`owner` 为认证用户；`payload` 为 AnalysisInput；`expected` 为读取版本；`job_id`、`token` 为租约凭证。
# 输出：原始快照载荷。
# 逻辑：核查当前成员、外部版本、未解析数量与事实全集，并为当前 revision 登记精确抽取来源边。
# 约束：后端不重算 input_version；相同版本的实质内容不可变，built_at 不作为业务内容比较。
@transaction.atomic
def save_input(owner, payload, expected, job_id, token):
    serializer = AnalysisInputSerializer(data=payload)
    serializer.is_valid(raise_exception=True)
    data = plain(serializer.validated_data)
    company = company_for(owner, data["company_id"], lock=True)
    check_version(expected, company.revision)
    require_lease(company, job_id, token)
    grouping, context = context_pair(company)
    members = data["member_dedupe_keys"]
    if len(members) != len(set(members)) or set(members) != set(grouping["member_dedupe_keys"]) or data["external_snapshot_version"] != context["external_snapshot_version"]:
        raise Conflict("输入成员范围或外部快照版本已变化。")
    emails = {item["dedupe_key"]: item for item in context["emails"]}
    if data["unparsed_message_count"] != sum(item["extract_status"] != "completed" for item in emails.values()):
        raise ValidationError("未解析数量与上下文不一致。")
    expected_company = {
        "company_name": grouping["company_name"],
        "crm_status": grouping["crm_status"],
        "domains": grouping["domains"],
        "contacts": grouping["contacts"],
    }
    expected_business = {
        "customer": context["customer"],
        "tickets": context["tickets"],
        "quotes": context["quotes"],
        "orders": context["orders"],
    }
    if data["company"] != expected_company or data["business_context"] != expected_business:
        raise Conflict("L2 公司资料或业务上下文与当前后端快照不一致。")
    completed_emails = [item for item in emails.values() if item["extract_status"] == "completed"]
    latest_summary = None
    if completed_emails:
        latest = max(
            completed_emails,
            key=lambda item: (
                parse_datetime(item["sent_at"])
                if item["sent_at"] is not None
                else datetime.min.replace(tzinfo=timezone.utc),
                item["dedupe_key"],
            ),
        )
        latest_summary = latest["facts"]["message_summary"]
    if data["latest_message_summary"] != latest_summary:
        raise ValidationError("latest_message_summary 与当前邮件上下文不一致。")
    expected_facts = []
    actual_facts = []
    for source in emails.values():
        if source["extract_status"] == "completed":
            for field in FACT_FIELDS:
                for fact in source["facts"][field]:
                    expected_facts.append((field, source["dedupe_key"], fact["value"], tuple(fact["evidences"])))
    for field, items in data["facts"].items():
        if field not in FACT_FIELDS:
            raise ValidationError("未知的归并事实字段。")
        for item in items:
            source = emails.get(item.get("dedupe_key"))
            facts = ((source or {}).get("facts") or {}).get(field, [])
            matches = [
                fact for fact in facts
                if item.get("value") == fact.get("value")
                and item.get("evidences") == fact.get("evidences")
            ]
            fact_time = item.get("fact_time")
            source_time = source.get("sent_at") if source else None
            times_differ = (fact_time is None) != (source_time is None) or (
                fact_time is not None
                and parse_datetime(str(fact_time)) != parse_datetime(source_time)
            )
            if not source or len(matches) != 1 or times_differ:
                raise ValidationError("归并事实与当前邮件事实不一致。")
            actual_facts.append((field, item["dedupe_key"], item["value"], tuple(item["evidences"])))
    if sorted(actual_facts) != sorted(expected_facts):
        raise ValidationError("L2 必须完整保留已完成抽取的事实，不得遗漏或重复。")
    snapshot, created = AnalysisInput.objects.get_or_create(company=company, input_version=data["input_version"], revision=company.revision, defaults={"payload": data})
    if not created and (snapshot.revision != company.revision or {k: v for k, v in snapshot.payload.items() if k != "built_at"} != {k: v for k, v in data.items() if k != "built_at"}):
        raise Conflict("相同 input_version 对应不同内容或后端 revision。")
    from .lineage import bind_sources
    bind_sources(snapshot, company)
    logger.info("analysis_input_saved company_id=%s revision=%s created=%s", company.pk, company.revision, created)
    return snapshot.payload


# 功能：递归核对每条来源引用属于当前业务快照。
# 输入：`value` 为 JSON 结果节点；`allowed` 为当前允许的来源 ID 集合。
# 输出：无；发现越界来源抛 ValidationError。
# 逻辑：遍历对象与数组，检查每个 source_refs。
# 约束：引用可定位不等于陈述已被人工核实。
def validate_refs(value, allowed):
    if isinstance(value, dict):
        if "source_refs" in value:
            refs = value["source_refs"]
            if not isinstance(refs, list) or any(not isinstance(ref, str) or ref not in allowed for ref in refs):
                raise ValidationError("分析包含当前快照之外的来源。")
        for child in value.values():
            validate_refs(child, allowed)
    elif isinstance(value, list):
        for child in value:
            validate_refs(child, allowed)


# 功能：保存经验证的 L3 分析。
# 输入：`owner`、`payload`、`expected`、`job_id`、`token` 指定用户、分析、版本与任务；`provider` 标识 rules 或 agent。
# 输出：已保存 Analysis 原始载荷。
# 逻辑：当前 revision 与未失效快照一致后检查来源、时间、缺失信息和强信号门槛。
# 约束：失败不覆盖成功，不接受未知来源或详情中的成交概率；原文业务百分比可以保留。
@transaction.atomic
def save_analysis(owner, payload, expected, job_id, token, provider="agent"):
    serializer = AnalysisSerializer(data=payload)
    serializer.is_valid(raise_exception=True)
    data = plain(serializer.validated_data)
    company = company_for(owner, data["company_id"], lock=True)
    check_version(expected, company.revision)
    require_lease(company, job_id, token)
    snapshot = company.inputs.filter(input_version=data["input_version"], revision=company.revision, invalidation__isnull=True).first()
    if snapshot is None:
        raise Conflict("请先保存当前 revision 的 AnalysisInput。")
    _, context = context_pair(company)
    base_time = parse_datetime(data["analysis_base_time"])
    if any(
        item["sent_at"] is not None and parse_datetime(item["sent_at"]) > base_time
        for item in context["emails"]
    ):
        raise ValidationError("分析基准时间早于输入邮件，存在时间穿越。")
    if base_time > parse_datetime(data["generated_at"]):
        raise ValidationError("分析基准时间不得晚于生成时间。")
    allowed = set(snapshot.payload["member_dedupe_keys"]) | {str(company.pk)}
    allowed.update(str(item["contact_email"]) for item in snapshot.payload["company"]["contacts"])
    customer_id = context["customer"].get("customer_id")
    if customer_id:
        allowed.add(str(customer_id))
    for key, id_field in [("tickets", "ticket_id"), ("quotes", "quote_id"), ("orders", "order_id")]:
        allowed.update(str(item[id_field]) for item in context[key])
    validate_refs(data, allowed)
    if data["status"] == "completed":
        if DEAL_PROBABILITY_PATTERN.search(
            json.dumps(data["detail_view"], ensure_ascii=False)
        ):
            raise ValidationError("详情输出不允许成交概率。")
        count = snapshot.payload["unparsed_message_count"]
        completeness = data["detail_view"]["context_completeness"]
        missing_fields = data["detail_view"]["missing_fields"]
        if completeness.get("unparsed_message_count") != count or (count and (not completeness.get("note") or not missing_fields)):
            raise ValidationError("必须如实说明未解析邮件和缺失项。")
        view = data["list_view"]
        signal = view["signal"]
        if signal != "unknown" and not view["signal_evidence"].get("source_refs"):
            raise ValidationError("已知信号必须包含来源。")
        if signal == "quoted_not_closed" and not any(q.get("evidence_type") == "actual_outbound" and q.get("status") == "sent" for q in context["quotes"]):
            raise ValidationError("已报价信号缺少实际外发证据。")
        if signal == "repeat_purchase" and not context["orders"]:
            raise ValidationError("复购信号缺少历史订单。")
        if signal == "new_lead_no_profile" and (company.crm_status != "unregistered" or sum(e["direction"] == "inbound" for e in context["emails"]) != 1):
            raise ValidationError("新线索信号必须为首次来信且未建档。")
        if view["size_band"] != "unknown" and context["customer"]["employee_count"] is None:
            raise ValidationError("规模档位缺少权威人数。")
    result, created = Analysis.objects.get_or_create(snapshot=snapshot, prompt_version=data["analysis_prompt_version"], defaults={"payload": data, "provider": provider})
    if not created and result.payload != data:
        if result.payload["status"] == "failed" and data["status"] == "completed":
            # 仅在显式新任务的有效租约内补齐失败结果；成功结果保持不可变。
            result.payload, result.provider = data, provider
            result.save(update_fields=["payload", "provider"])
        else:
            raise Conflict("同一分析提示词与输入版本的成功结果不可覆盖。")
    logger.info("analysis_saved company_id=%s revision=%s status=%s provider=%s", company.pk, company.revision, data["status"], provider)
    return result.payload


# 功能：保存与当前成功分析对应的评分。
# 输入：`owner`、`payload`、`expected`、`job_id`、`token` 为身份、Score、后端版本与任务凭证。
# 输出：Score 原始载荷。
# 逻辑：绑定当前输入未失效的成功分析；正式结果独立于旧特征，解释来源验证后与分数原子保存；按规则版本和 scored_at 去重。
# 约束：无有效分析不接收分数；只有旧版本继续校验旧特征，相同键的解释也不可覆盖。
@transaction.atomic
def save_score(owner, payload, expected, job_id, token):
    serializer = ScoreSerializer(data=payload)
    serializer.is_valid(raise_exception=True)
    data = plain(serializer.validated_data)
    company = company_for(owner, data["company_id"], lock=True)
    check_version(expected, company.revision)
    require_lease(company, job_id, token)
    analysis = Analysis.objects.filter(snapshot__company=company, snapshot__revision=company.revision, snapshot__invalidation__isnull=True,
                                       snapshot__input_version=data["input_version"], payload__status="completed").order_by("-id").first()
    if analysis is None:
        raise InvalidState("当前输入尚无成功分析。")
    view = analysis.payload["list_view"]
    if data["score_version"] != "score-v2" and data["score"] is not None and (view["signal"] == "unknown" or any(item["value"] is None for item in view["score_features"].values())):
        raise ValidationError("缺失评分特征必须返回 null。")
    validate_priority_sources(data, company, analysis)
    existing = analysis.scores.filter(score_version=data["score_version"], payload__scored_at=data["scored_at"]).first()
    if existing:
        if existing.payload != data:
            raise Conflict("相同评分版本与时间的内容不可修改。")
        return existing.payload
    Score.objects.create(analysis=analysis, payload=data, score_version=data["score_version"], value=data["score"])
    logger.info("score_saved company_id=%s revision=%s score_version=%s", company.pk, company.revision, data["score_version"])
    return data


# 功能：查询指定版本的分析缓存元数据。
# 输入：`company` 为授权公司；`input_version` 为 Agent 输入键；`prompt_version` 可限定提示词版本。
# 输出：README CachedAnalysis，未命中可附旧结果时间。
# 逻辑：命中要求未失效、当前 revision、输入版本、成功状态及可选提示词一致。
# 约束：旧结果 hit 为 false，不冒充新分析。
def cached_analysis(company, input_version, prompt_version=None):
    query = Analysis.objects.filter(snapshot__company=company, snapshot__invalidation__isnull=True, payload__status="completed").select_related("snapshot")
    exact = query.filter(snapshot__input_version=input_version, snapshot__revision=company.revision)
    if prompt_version:
        exact = exact.filter(prompt_version=prompt_version)
    result = exact.order_by("-id").first()
    hit = result is not None
    result = result or query.order_by("-id").first()
    return {"company_id": str(company.pk), "input_version": result.snapshot.input_version if result else input_version,
            "analysis_prompt_version": result.prompt_version if result else None,
            "generated_at": result.payload["generated_at"] if result else None,
            "status": "completed" if result else "pending", "hit": hit,
            "analysis": result.payload if hit else None}
