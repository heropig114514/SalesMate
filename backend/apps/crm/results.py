"""Responsibility: Save analysis input, judgment, and score while validating concurrency and CRM/experiment sources.
Implementation: Use safe reason codes to distinguish version, snapshot, and immutable-payload conflicts; validate revision, lease, references, immutable keys, and experiment-data freshness under the company row lock. Atomically save formal score explanations and score after verifying email evidence.
Relationships: API and rules placeholders share this entry point, and selectors projects pages from these snapshots.
Directory:
- save_input: Save L2 source input snapshot.
- validate_refs: Recursively verify every source reference belongs to current business snapshot.
- save_analysis: Save validated L3 analysis.
- save_score: Save score corresponding to current successful analysis.
- cached_analysis: Query analysis-cache metadata for a specified version.
Variable index:
- logger: Redacted diagnostic logger for this module.
- DEAL_PROBABILITY_PATTERN: Recognizes and prohibits Agent statements of deal probability or win rate.
"""
import json
import logging
import re
from datetime import datetime, timezone

from django.db import transaction
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError

from .access import InvalidState, company_for, plain
from .analysis_errors import analysis_conflict, check_analysis_version
from .jobs import require_lease
from .models import Analysis, AnalysisInput, Score
from .priority_results import validate_priority_sources
from .selectors import context_pair
from .enrichment import snapshot_current
from integrations.company_enrichment import input_version, employee_size, source_refs
from .serializers import AnalysisInputSerializer, AnalysisSerializer, ScoreSerializer, FACT_FIELDS

logger = logging.getLogger("salesmate.analysis")

DEAL_PROBABILITY_PATTERN = re.compile(
    r"(?:成交|成单|签约|赢单)(?:的)?(?:概率|可能性|可能|成功率)|"
    r"(?:成交率|赢单率|胜率)"
)


# Function: Save L2 source input snapshot.
# Inputs: `owner` is authenticated, `payload` is AnalysisInput, `expected` is the read version, and `job_id` and `token` are lease credentials.
# Outputs: Source snapshot payload; conflicts include distinguishable safe reason code and stage log.
# Logic: Verify current members, external version, unparsed count, and complete facts; revalidate independent experiment data and its input version and register exact extraction source edges.
# Constraints: Validate an input_version that includes enrichment when it is enabled; legacy clients retain original protocol and built_at is excluded from substantive content comparison.
@transaction.atomic
def save_input(owner, payload, expected, job_id, token):
    serializer = AnalysisInputSerializer(data=payload)
    serializer.is_valid(raise_exception=True)
    data = plain(serializer.validated_data)
    company = company_for(owner, data["company_id"], lock=True)
    check_analysis_version(expected, company, job_id, "save_input", data["input_version"])
    require_lease(company, job_id, token)
    grouping, context = context_pair(company)
    members = data["member_dedupe_keys"]
    if len(members) != len(set(members)) or set(members) != set(grouping["member_dedupe_keys"]) or data["external_snapshot_version"] != context["external_snapshot_version"]:
        raise analysis_conflict("analysis_snapshot_changed", company, job_id, "save_input", expected, data["input_version"])
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
    if "company_enrichment" in data["business_context"]:
        expected_business["company_enrichment"] = context["company_enrichment"]
        if data["input_version"] != input_version(context["emails"], data["merge_version"], context["external_snapshot_version"], context["company_enrichment"]):
            raise analysis_conflict("analysis_snapshot_changed", company, job_id, "save_input", expected, data["input_version"])
    if data["company"] != expected_company or data["business_context"] != expected_business:
        raise analysis_conflict("analysis_snapshot_changed", company, job_id, "save_input", expected, data["input_version"])
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
        raise analysis_conflict("analysis_input_conflict", company, job_id, "save_input", expected, data["input_version"])
    from .lineage import bind_sources
    bind_sources(snapshot, company)
    logger.info("analysis_input_saved company_id=%s revision=%s created=%s enrichment_status=%s", company.pk, company.revision, created,
                data["business_context"].get("company_enrichment", {}).get("status", "not_requested"))
    return snapshot.payload


# Function: Recursively verify every source reference belongs to current business snapshot.
# Inputs: `value` is a JSON result node and `allowed` is the currently allowed source-ID set.
# Outputs: None; raises ValidationError for an out-of-scope source.
# Logic: Traverse objects and arrays and inspect every source_refs.
# Constraints: A locatable reference does not mean a statement was human-verified.
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


# Function: Save validated L3 analysis.
# Inputs: `owner`, `payload`, `expected`, `job_id`, and `token` specify user, analysis, version, and job; `provider` identifies rules or agent.
# Outputs: Saved Analysis source payload; version, snapshot, and immutable-payload conflicts separately state reasons.
# Logic: After current revision agrees with still-valid experiment snapshot, check sources, times, missing information, and strong signals; allow independent experiment employee counts while retaining their source labels.
# Constraints: Failure does not overwrite success and unknown sources or deal probability in details are rejected; source business percentages may remain.
@transaction.atomic
def save_analysis(owner, payload, expected, job_id, token, provider="agent"):
    serializer = AnalysisSerializer(data=payload)
    serializer.is_valid(raise_exception=True)
    data = plain(serializer.validated_data)
    company = company_for(owner, data["company_id"], lock=True)
    check_analysis_version(expected, company, job_id, "save_analysis", data["input_version"])
    require_lease(company, job_id, token)
    snapshot = company.inputs.filter(input_version=data["input_version"], revision=company.revision, invalidation__isnull=True).first()
    if snapshot is None:
        raise analysis_conflict("analysis_snapshot_changed", company, job_id, "save_analysis", expected, data["input_version"])
    _, context = context_pair(company)
    if not snapshot_current(snapshot, company, context["company_enrichment"]):
        raise analysis_conflict("analysis_snapshot_changed", company, job_id, "save_analysis", expected, data["input_version"])
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
    allowed.update(source_refs(snapshot.payload["business_context"]))
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
        count, source = employee_size(snapshot.payload["business_context"])
        if view["size_band"] != "unknown" and count is None:
            raise ValidationError("规模档位缺少 CRM 或已验证的实验人数。")
        if source == "synthetic_sample":
            view["size_source"] = source
    result, created = Analysis.objects.get_or_create(snapshot=snapshot, prompt_version=data["analysis_prompt_version"], defaults={"payload": data, "provider": provider})
    if not created and result.payload != data:
        if result.payload["status"] == "failed" and data["status"] == "completed":
            # Complete failed results only under a valid lease from an explicit new job; successful results remain immutable.
            result.payload, result.provider = data, provider
            result.save(update_fields=["payload", "provider"])
        else:
            raise analysis_conflict("analysis_result_conflict", company, job_id, "save_analysis", expected, data["input_version"])
    logger.info("analysis_saved company_id=%s revision=%s status=%s provider=%s", company.pk, company.revision, data["status"], provider)
    return result.payload


# Function: Save score corresponding to current successful analysis.
# Inputs: `owner`, `payload`, `expected`, `job_id`, and `token` provide identity, Score, backend version, and job credential.
# Outputs: Score source payload; version conflicts and same-key different-score conflicts state separate reasons.
# Logic: Bind successful analysis whose current input and experiment source remain valid; formal results are independent of legacy features, and after explanation source validation save with score atomically; deduplicate by rule version and scored_at.
# Constraints: Does not accept score without valid analysis; only legacy versions continue validating legacy features and same-key explanations cannot be overwritten.
@transaction.atomic
def save_score(owner, payload, expected, job_id, token):
    serializer = ScoreSerializer(data=payload)
    serializer.is_valid(raise_exception=True)
    data = plain(serializer.validated_data)
    company = company_for(owner, data["company_id"], lock=True)
    check_analysis_version(expected, company, job_id, "save_score", data["input_version"])
    require_lease(company, job_id, token)
    analysis = Analysis.objects.filter(snapshot__company=company, snapshot__revision=company.revision, snapshot__invalidation__isnull=True,
                                       snapshot__input_version=data["input_version"], payload__status="completed").order_by("-id").first()
    if analysis is None or not snapshot_current(analysis.snapshot, company):
        raise InvalidState("当前输入尚无成功分析。")
    view = analysis.payload["list_view"]
    if data["score_version"] != "score-v2" and data["score"] is not None and (view["signal"] == "unknown" or any(item["value"] is None for item in view["score_features"].values())):
        raise ValidationError("缺失评分特征必须返回 null。")
    validate_priority_sources(data, company, analysis)
    existing = analysis.scores.filter(score_version=data["score_version"], payload__scored_at=data["scored_at"]).first()
    if existing:
        if existing.payload != data:
            raise analysis_conflict("analysis_score_conflict", company, job_id, "save_score", expected, data["input_version"])
        return existing.payload
    Score.objects.create(analysis=analysis, payload=data, score_version=data["score_version"], value=data["score"])
    logger.info("score_saved company_id=%s revision=%s score_version=%s", company.pk, company.revision, data["score_version"])
    return data


# Function: Query analysis-cache metadata for a specified version.
# Inputs: `company` is authorized, `input_version` is the Agent input key, and `prompt_version` may limit prompt version.
# Outputs: README CachedAnalysis, including old result time on a miss when available.
# Logic: A hit requires no invalidation, current revision, input version, successful state, and optional prompt match; experiment data must also agree with current source.
# Constraints: Old results have hit false and never impersonate new analysis.
def cached_analysis(company, input_version, prompt_version=None):
    query = Analysis.objects.filter(snapshot__company=company, snapshot__invalidation__isnull=True, payload__status="completed").select_related("snapshot")
    exact = query.filter(snapshot__input_version=input_version, snapshot__revision=company.revision)
    if prompt_version:
        exact = exact.filter(prompt_version=prompt_version)
    result = exact.order_by("-id").first()
    hit = result is not None and snapshot_current(result.snapshot, company)
    result = result or query.order_by("-id").first()
    return {"company_id": str(company.pk), "input_version": result.snapshot.input_version if result else input_version,
            "analysis_prompt_version": result.prompt_version if result else None,
            "generated_at": result.payload["generated_at"] if result else None,
            "status": "completed" if result else "pending", "hit": hit,
            "analysis": result.payload if hit else None}
