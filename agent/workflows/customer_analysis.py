"""职责：生成和验证 L3 画像、规模与可追溯来源。
实现：保持原有 L1/L4 规则，资料经后端核验后独立传递，人数优先 CRM。
关联：后端公司上下文、共享 enrichment 契约与分析编排；不新增授权令牌。
目录：
- AnalysisValidationError：表示模型输出契约错误。
- bailian_analysis_provider：调用模型生成 L3。
- generate_analysis：生成并验证 L3 分析。
- validate_analysis_payload：验证模型负责的列表与详情。
- _validate_business_rules：复核信号门槛和人数档位。
- _size_band：确定员工规模档位。
- _authoritative_size_source：确定规模来源标签。
- _conflict_field：规范冲突字段名称。
- _validate_list_view：核对列表输出。
- _validate_detail_view：核对七维详情和上下文完整性。
- _dimension_group：校验维度组。
- _dimension：校验事实和推断维度。
- _allowed_source_refs：收集本次输入允许引用的来源。
- _evidence_block：校验证据块。
- _source_refs：规范并核对引用列表。
- _analysis_model_input：精简模型输入。
- _analysis_business_context：移除仅供后端校验的补充资料元数据。
- _decode_model_json：解析模型 JSON。
- _canonical_source_ref：规范可确认的引用前缀。
- _contains_deal_probability：检测不允许的成交概率表述。
- _as_document：将输入转为 L2 字典。
- _clock_text：读取明确时区的构建时钟。
- _object：要求对象类型。
- _array：要求数组类型。
- _keys：核对精确字段集合。
- _nonblank：判断非空字符串。
- _enum：核对非空枚举。
- _strings：校验无重复字符串数组。
变量索引：
- ANALYSIS_DIMENSIONS：四个分析维度。
- ANALYSIS_PROMPT：L3 Skill 指令正文。
- ANALYSIS_PROMPT_VERSION：当前 L3 缓存隔离版本。
- CONFIDENCES：推断置信度枚举。
- CONFLICT_FIELDS：允许冲突事实字段。
- CONFLICT_FIELD_ALIASES：模型常用冲突字段别名。
- CONFLICT_KINDS：冲突类别。
- INDUSTRIES：行业枚举。
- PROFILE_DIMENSIONS：三个画像维度。
- SCORE_FEATURES：兼容输出特征名称。
- SIGNALS：列表信号枚举。
- SIZE_BANDS：规模档位枚举。
- _CUSTOMER_ANALYSIS_SKILL：加载的 L3 Skill。
- _DEAL_PROBABILITY_PATTERN：成交概率限制的既定正则。
- _JSON_FENCE：单层 JSON 围栏正则。
- _SOURCE_REF_PREFIXES：允许规范化的已知引用前缀。
- __all__：公开导出的符号。
- logger：脱敏诊断日志。
"""

from __future__ import annotations

import copy
import json
import logging
import re
from datetime import datetime
from time import perf_counter
from typing import Any, Callable, Mapping

from agent.llm.bailian import generate_json
from agent.skills import load_skill
from integrations.company_enrichment import employee_size, source_refs
from agent.workflows.l1_email import MULTI_VALUE_FACT_FIELDS


_CUSTOMER_ANALYSIS_SKILL = load_skill("customer-analysis")
ANALYSIS_PROMPT_VERSION = _CUSTOMER_ANALYSIS_SKILL.version
logger = logging.getLogger("salesmate.agent.customer_analysis")

SIGNALS = frozenset(
    {
        "repeat_purchase",
        "quoted_not_closed",
        "inquiry_intent",
        "new_lead_no_profile",
        "unknown",
    }
)
INDUSTRIES = frozenset(
    {"半导体检测", "精密量测", "光学检测", "工业检测", "unknown"}
)
SIZE_BANDS = frozenset(
    {"lt_50", "50_100", "100_200", "200_500", "gte_500", "unknown"}
)
CONFIDENCES = frozenset({"low", "medium", "high"})
CONFLICT_KINDS = frozenset({"value_changed", "source_disagree"})
CONFLICT_FIELDS = frozenset(MULTI_VALUE_FACT_FIELDS)
CONFLICT_FIELD_ALIASES = {"company_name": "company_self_reported"}
PROFILE_DIMENSIONS = ("industry_context", "company_ops", "intent")
ANALYSIS_DIMENSIONS = ("timeline", "opportunity", "risk", "guidance")
SCORE_FEATURES = ("demand_clarity", "urgency", "decision_visibility")

ANALYSIS_PROMPT = _CUSTOMER_ANALYSIS_SKILL.instructions

_DEAL_PROBABILITY_PATTERN = re.compile(
    r"(?:成交|成单|签约|赢单)(?:的)?(?:概率|可能性|可能|成功率)|"
    r"(?:成交率|赢单率|胜率)"
)

_SOURCE_REF_PREFIXES = (
    "dedupe_key:",
    "email:",
    "company_id:",
    "customer_id:",
    "contact_email:",
    "ticket_id:",
    "quote_id:",
    "order_id:",
)


# 功能：表示模型输出契约错误。
# 逻辑：继承 ValueError，供既定重试边界区分业务校验。
# 约束：无网络和数据库副作用。
class AnalysisValidationError(ValueError):
    """百炼返回的 L3 数据不符合 MVP 契约。"""


# 功能：调用模型生成 L3。
# 输入：`analysis_input` 为L2 输入对象；`validation_error` 为可选上次校验说明。
# 输出：JSON 文本。
# 逻辑：发送精简输入、允许来源及可选上次错误，使用 Skill 固定预算并记录耗时。
# 约束：异常记录脱敏上下文后重新抛出，不在此重试。
def bailian_analysis_provider(
    analysis_input: Mapping[str, Any],
    *,
    validation_error: str | None = None,
) -> str:
    """调用百炼生成 L3 JSON 文本。"""
    allowed_refs = sorted(_allowed_source_refs(analysis_input))
    model_input = _analysis_model_input(analysis_input)
    retry_instruction = ""
    if validation_error:
        retry_instruction = (
            "上一次分析未通过业务或输出契约校验。请重新生成完整 JSON，并修正以下问题：\n"
            f"{validation_error}\n"
        )
    user_text = retry_instruction + (
        "ALLOWED_SOURCE_REFS（source_refs 只能逐字复制这里的完整字符串）：\n"
        + json.dumps(allowed_refs, ensure_ascii=False, separators=(",", ":"))
        + "\nANALYSIS_INPUT：\n"
        + json.dumps(model_input, ensure_ascii=False, separators=(",", ":"))
    )
    started = perf_counter()
    try:
        result = generate_json(
            ANALYSIS_PROMPT,
            user_text,
            max_tokens=_CUSTOMER_ANALYSIS_SKILL.max_tokens,
        )
    except Exception:
        logger.warning(
            "l3_model_call_failed company_id=%s retry=%s duration_ms=%s input_chars=%s",
            analysis_input.get("company_id"),
            bool(validation_error),
            round((perf_counter() - started) * 1000),
            len(user_text),
        )
        raise
    logger.info(
        "l3_model_call_completed company_id=%s retry=%s duration_ms=%s input_chars=%s output_chars=%s",
        analysis_input.get("company_id"),
        bool(validation_error),
        round((perf_counter() - started) * 1000),
        len(user_text),
        len(result),
    )
    return result


# 功能：生成并验证 L3 分析。
# 输入：`analysis_input` 为L2 输入对象；`analysis_provider` 为模型调用函数；`clock` 为返回带时区 datetime 的时钟。
# 输出：completed 或 failed 分析字典。
# 逻辑：构建时间和版本，调用 provider；仅默认 provider 的 JSON 或契约失败按既有行为纠正一次。
# 约束：自定义 provider 或网络异常不自动重试；不保存后端。
def generate_analysis(
    analysis_input: object,
    *,
    analysis_provider: Callable[[Mapping[str, Any]], str] = bailian_analysis_provider,
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    """生成并校验 L3；失败时返回结构稳定且便于本地调试的结果。"""
    try:
        document = _as_document(analysis_input)
        generated_at = _clock_text(clock)
    except Exception as error:
        logger.warning(
            "l3_analysis_failed stage=input error_type=%s reason=%s",
            type(error).__name__,
            error if isinstance(error, AnalysisValidationError) else "invalid_input",
        )
        return {
            "company_id": "",
            "input_version": "",
            "analysis_prompt_version": ANALYSIS_PROMPT_VERSION,
            "generated_at": None,
            "analysis_base_time": None,
            "status": "failed",
            "list_view": None,
            "detail_view": None,
            "error": {
                "code": "analysis_failed",
                "message": f"{type(error).__name__}: {error}",
            },
        }
    company_id = str(document.get("company_id", ""))
    input_version = str(document.get("input_version", ""))
    base = {
        "company_id": company_id,
        "input_version": input_version,
        "analysis_prompt_version": ANALYSIS_PROMPT_VERSION,
        "generated_at": generated_at,
        "analysis_base_time": document.get("built_at"),
    }
    started = perf_counter()
    logger.info(
        "l3_analysis_started company_id=%s input_version=%s prompt_version=%s",
        company_id, input_version, ANALYSIS_PROMPT_VERSION,
    )

    try:
        raw_text = analysis_provider(document)
        if not isinstance(raw_text, str):
            raise AnalysisValidationError("模型必须返回 JSON 文本。")
        candidate = _decode_model_json(raw_text)
        validated = validate_analysis_payload(candidate, document)
    except Exception as first_error:
        final_error = first_error
        logger.warning(
            "l3_analysis_attempt_failed company_id=%s error_type=%s reason=%s",
            company_id, type(first_error).__name__,
            first_error if isinstance(first_error, (json.JSONDecodeError, AnalysisValidationError)) else "provider_unavailable",
        )
        # 默认百炼输出若只是 JSON 或业务契约不合格，携带具体原因修正一次。
        # 网络、配置和自定义 provider 错误保持原行为，交由显式任务重试。
        if analysis_provider is bailian_analysis_provider and isinstance(
            first_error,
            (json.JSONDecodeError, AnalysisValidationError),
        ):
            try:
                raw_text = bailian_analysis_provider(
                    document,
                    validation_error=str(first_error),
                )
                if not isinstance(raw_text, str):
                    raise AnalysisValidationError("模型必须返回 JSON 文本。")
                candidate = _decode_model_json(raw_text)
                validated = validate_analysis_payload(candidate, document)
            except Exception as retry_error:
                final_error = retry_error
                logger.warning(
                    "l3_analysis_retry_failed company_id=%s error_type=%s reason=%s duration_ms=%s",
                    company_id, type(retry_error).__name__,
                    retry_error if isinstance(retry_error, (json.JSONDecodeError, AnalysisValidationError)) else "provider_unavailable",
                    round((perf_counter() - started) * 1000),
                )
            else:
                logger.info(
                    "l3_analysis_completed company_id=%s retry=True duration_ms=%s signal=%s",
                    company_id, round((perf_counter() - started) * 1000),
                    validated["list_view"].get("signal"),
                )
                return {
                    **base,
                    "status": "completed",
                    "list_view": validated["list_view"],
                    "detail_view": validated["detail_view"],
                    "error": None,
                }

        logger.warning(
            "l3_analysis_failed company_id=%s error_type=%s duration_ms=%s",
            company_id, type(final_error).__name__, round((perf_counter() - started) * 1000),
        )
        return {
            **base,
            "status": "failed",
            "list_view": None,
            "detail_view": None,
            "error": {
                "code": "analysis_failed",
                "message": f"{type(final_error).__name__}: {final_error}",
            },
        }

    logger.info(
        "l3_analysis_completed company_id=%s retry=False duration_ms=%s signal=%s",
        company_id, round((perf_counter() - started) * 1000),
        validated["list_view"].get("signal"),
    )
    return {
        **base,
        "status": "completed",
        "list_view": validated["list_view"],
        "detail_view": validated["detail_view"],
        "error": None,
    }


# 功能：验证模型负责的列表与详情。
# 输入：`candidate` 为模型解析结果；`analysis_input` 为L2 输入对象。
# 输出：规范 list_view/detail_view 字典。
# 逻辑：要求两段封闭结构、无成交概率，校验允许来源、信号和完整性。
# 约束：错误抛 AnalysisValidationError；不验证后端凭证。
def validate_analysis_payload(
    candidate: object,
    analysis_input: Mapping[str, Any],
) -> dict[str, Any]:
    """校验模型负责的 list/detail 两段，并返回隔离的普通字典。"""
    root = _object(candidate, "analysis")
    _keys(root, {"list_view", "detail_view"}, "analysis")
    if _contains_deal_probability(root):
        raise AnalysisValidationError("分析中不能包含成交概率或百分比。")

    allowed_refs = _allowed_source_refs(analysis_input)
    list_view = _validate_list_view(root["list_view"], allowed_refs, analysis_input)
    _validate_business_rules(list_view, analysis_input)
    detail_view = _validate_detail_view(
        root["detail_view"],
        allowed_refs,
        int(analysis_input.get("unparsed_message_count", 0)),
    )
    return {"list_view": list_view, "detail_view": detail_view}


# 功能：复核信号门槛和人数档位。
# 输入：`list_view` 为已校验列表结果；`analysis_input` 为L2 输入对象。
# 输出：无返回值。
# 逻辑：核对订单、采购、报价、新线索、人数档位及工单集合。
# 约束：人数优先 CRM，否则使用已验证实验资料；档位阈值保持原值。
def _validate_business_rules(
    list_view: Mapping[str, Any],
    analysis_input: Mapping[str, Any],
) -> None:
    """对能由输入直接判断的信号门槛和规模档位做确定性复核。"""
    business = analysis_input.get("business_context", {})
    business = business if isinstance(business, Mapping) else {}
    facts = analysis_input.get("facts", {})
    facts = facts if isinstance(facts, Mapping) else {}
    metrics = analysis_input.get("metrics", {})
    metrics = metrics if isinstance(metrics, Mapping) else {}
    company = analysis_input.get("company", {})
    company = company if isinstance(company, Mapping) else {}

    signal = list_view["signal"]
    actual_quotes = [
        quote
        for quote in business.get("quotes", [])
        if isinstance(quote, Mapping) and quote.get("evidence_type") == "actual_outbound"
    ]
    orders = [item for item in business.get("orders", []) if isinstance(item, Mapping)]
    purchase_facts = any(
        isinstance(facts.get(field), list) and bool(facts.get(field))
        for field in ("product_need", "quantity", "budget", "delivery_time")
    )
    if signal == "quoted_not_closed" and not actual_quotes:
        raise AnalysisValidationError("quoted_not_closed 缺少真实外发报价。")
    if signal == "repeat_purchase" and (not orders or not purchase_facts):
        raise AnalysisValidationError("repeat_purchase 缺少历史订单或本次采购动作。")
    if signal == "inquiry_intent" and not purchase_facts:
        raise AnalysisValidationError("inquiry_intent 缺少采购事实。")
    if signal == "new_lead_no_profile" and (
        company.get("crm_status") != "unregistered"
        or metrics.get("inbound_count") != 1
    ):
        raise AnalysisValidationError("new_lead_no_profile 不符合未建档首次来信条件。")

    employee_count, _ = employee_size(business)
    expected_band = _size_band(employee_count)
    if list_view["size_band"] != expected_band:
        raise AnalysisValidationError("size_band 与后端员工人数不一致。")

    ticket_ids = {
        str(ticket["ticket_id"])
        for ticket in business.get("tickets", [])
        if isinstance(ticket, Mapping) and ticket.get("ticket_id")
    }
    returned_ids = {item["ticket_id"] for item in list_view["ticket_signals"]}
    if not returned_ids.issubset(ticket_ids):
        raise AnalysisValidationError("ticket_signals 包含不存在的工单。")


# 功能：确定员工规模档位。
# 输入：`value` 为待检查值。
# 输出：档位字符串。
# 逻辑：按 50、100、200、500 的原阈值划分人数。
# 约束：非法人数返回 unknown，不做估计。
def _size_band(value: object) -> str:
    if type(value) is not int or value < 0:
        return "unknown"
    if value < 50:
        return "lt_50"
    if value < 100:
        return "50_100"
    if value < 200:
        return "100_200"
    if value < 500:
        return "200_500"
    return "gte_500"


# 功能：确定规模来源标签。
# 输入：`analysis_input` 为L2 输入对象。
# 输出：crm、synthetic_sample、已有标签或 unknown。
# 逻辑：委托共享人数选择规则，CRM 优先于 matched 实验资料。
# 约束：不相信模型自行填写的来源。
def _authoritative_size_source(analysis_input: Mapping[str, Any]) -> str:
    """按 CRM 优先顺序使用后端已验证人数，实验补充保留独立标签。"""
    business = analysis_input.get("business_context")
    return employee_size(business if isinstance(business, Mapping) else {})[1]


# 功能：规范冲突字段名称。
# 输入：`value` 为待检查值；`path` 为错误定位路径。
# 输出：规范字段名。
# 逻辑：将既有公司别名映射为 L1 字段，再核对枚举。
# 约束：不增加事实类别。
def _conflict_field(value: object, path: str) -> str:
    """规范常见模型别名，并确保最终字段符合后端 L1 事实枚举。"""
    field = _nonblank(value, path)
    normalized = CONFLICT_FIELD_ALIASES.get(field, field)
    return _enum(normalized, CONFLICT_FIELDS, path)


# 功能：核对列表输出。
# 输入：`value` 为待检查值；`allowed_refs` 为允许引用来源集合；`analysis_input` 为L2 输入对象。
# 输出：独立列表字典。
# 逻辑：检查封闭字段、枚举、来源、工单与特征；人数来源使用确定性规则。
# 约束：模型自由改写来源不会制造虚假来源。
def _validate_list_view(
    value: object,
    allowed_refs: set[str],
    analysis_input: Mapping[str, Any],
) -> dict[str, Any]:
    item = _object(value, "list_view")
    required = {
        "signal",
        "signal_evidence",
        "ticket_signals",
        "industry",
        "industry_evidence",
        "size_band",
        "size_source",
        "headline_summary",
        "score_features",
    }
    _keys(item, required, "list_view")
    signal = _enum(item["signal"], SIGNALS, "list_view.signal")
    industry = _enum(item["industry"], INDUSTRIES, "list_view.industry")
    size_band = _enum(item["size_band"], SIZE_BANDS, "list_view.size_band")
    signal_evidence = _evidence_block(
        item["signal_evidence"], allowed_refs, "list_view.signal_evidence"
    )
    industry_evidence = _evidence_block(
        item["industry_evidence"], allowed_refs, "list_view.industry_evidence"
    )
    if signal != "unknown" and not signal_evidence["source_refs"]:
        raise AnalysisValidationError("非 unknown 信号必须有来源。")
    if industry != "unknown" and not industry_evidence["source_refs"]:
        raise AnalysisValidationError("非 unknown 行业必须有来源。")

    tickets = _array(item["ticket_signals"], "list_view.ticket_signals")
    ticket_signals = []
    for index, raw in enumerate(tickets):
        path = f"list_view.ticket_signals[{index}]"
        ticket = _object(raw, path)
        _keys(ticket, {"ticket_id", "signal", "reason"}, path)
        ticket_signals.append(
            {
                "ticket_id": _nonblank(ticket["ticket_id"], f"{path}.ticket_id"),
                "signal": _enum(ticket["signal"], SIGNALS, f"{path}.signal"),
                "reason": _nonblank(ticket["reason"], f"{path}.reason"),
            }
        )

    raw_features = _object(item["score_features"], "list_view.score_features")
    _keys(raw_features, set(SCORE_FEATURES), "list_view.score_features")
    features: dict[str, Any] = {}
    for name in SCORE_FEATURES:
        feature = _object(raw_features[name], f"list_view.score_features.{name}")
        _keys(feature, {"value", "basis"}, f"list_view.score_features.{name}")
        score_value = feature["value"]
        if score_value is not None and (type(score_value) is not int or score_value not in range(4)):
            raise AnalysisValidationError(f"{name}.value 必须是 0-3 或 null。")
        features[name] = {
            "value": score_value,
            "basis": _nonblank(feature["basis"], f"{name}.basis"),
        }

    return {
        "signal": signal,
        "signal_evidence": signal_evidence,
        "ticket_signals": ticket_signals,
        "industry": industry,
        "industry_evidence": industry_evidence,
        "size_band": size_band,
        # 规模来源由 CRM 或经后端核验的实验资料确定。模型仍需输出该键，
        # 但空值或自由改写不会再让整份画像失败或制造虚假来源。
        "size_source": _authoritative_size_source(analysis_input),
        "headline_summary": _nonblank(
            item["headline_summary"], "list_view.headline_summary"
        ),
        "score_features": features,
    }


# 功能：核对七维详情和上下文完整性。
# 输入：`value` 为待检查值；`allowed_refs` 为允许引用来源集合；`expected_unparsed` 为真实未解析邮件数。
# 输出：详情字典。
# 逻辑：验证冲突至少两来源、三画像四分析及未解析计数。
# 约束：缺失邮件须明确说明，不生成新事实。
def _validate_detail_view(
    value: object,
    allowed_refs: set[str],
    expected_unparsed: int,
) -> dict[str, Any]:
    detail = _object(value, "detail_view")
    required = {
        "conflicts",
        "profile",
        "analysis",
        "missing_fields",
        "context_completeness",
    }
    _keys(detail, required, "detail_view")

    conflicts = []
    for index, raw in enumerate(_array(detail["conflicts"], "detail_view.conflicts")):
        path = f"detail_view.conflicts[{index}]"
        conflict = _object(raw, path)
        _keys(conflict, {"field", "kind", "summary", "source_refs"}, path)
        refs = _source_refs(conflict["source_refs"], allowed_refs, f"{path}.source_refs")
        if len(refs) < 2:
            raise AnalysisValidationError(f"{path} 至少需要两个来源。")
        conflicts.append(
            {
                "field": _conflict_field(conflict["field"], f"{path}.field"),
                "kind": _enum(conflict["kind"], CONFLICT_KINDS, f"{path}.kind"),
                "summary": _nonblank(conflict["summary"], f"{path}.summary"),
                "source_refs": refs,
            }
        )

    profile = _dimension_group(detail["profile"], PROFILE_DIMENSIONS, allowed_refs, "profile")
    analysis = _dimension_group(
        detail["analysis"], ANALYSIS_DIMENSIONS, allowed_refs, "analysis"
    )
    missing_fields = _strings(detail["missing_fields"], "detail_view.missing_fields")
    completeness = _object(detail["context_completeness"], "context_completeness")
    _keys(completeness, {"unparsed_message_count", "note"}, "context_completeness")
    count = completeness["unparsed_message_count"]
    if type(count) is not int or count != expected_unparsed:
        raise AnalysisValidationError("unparsed_message_count 必须与 L2 一致。")
    note = completeness["note"]
    if note is not None and (not isinstance(note, str) or not note.strip()):
        raise AnalysisValidationError("context_completeness.note 格式无效。")
    if count > 0 and note is None:
        raise AnalysisValidationError("存在未解析邮件时必须说明上下文不完整。")

    return {
        "conflicts": conflicts,
        "profile": profile,
        "analysis": analysis,
        "missing_fields": missing_fields,
        "context_completeness": {"unparsed_message_count": count, "note": note},
    }


# 功能：校验维度组。
# 输入：`value` 为待检查值；`dimensions` 为要求的维度名称；`allowed_refs` 为允许引用来源集合；`path` 为错误定位路径。
# 输出：维度字典。
# 逻辑：要求 dimensions 指定的精确字段并逐项校验。
# 约束：path 定位错误，不增加维度。
def _dimension_group(
    value: object,
    dimensions: tuple[str, ...],
    allowed_refs: set[str],
    path: str,
) -> dict[str, Any]:
    group = _object(value, path)
    _keys(group, set(dimensions), path)
    return {
        name: _dimension(group[name], allowed_refs, f"{path}.{name}")
        for name in dimensions
    }


# 功能：校验事实和推断维度。
# 输入：`value` 为待检查值；`allowed_refs` 为允许引用来源集合；`path` 为错误定位路径。
# 输出：规范维度字典。
# 逻辑：逐项检查文本、依据、置信度及非空允许来源。
# 约束：引用必须来自当前输入。
def _dimension(value: object, allowed_refs: set[str], path: str) -> dict[str, Any]:
    dimension = _object(value, path)
    _keys(dimension, {"facts", "inferences", "missing_fields"}, path)
    facts = []
    for index, raw in enumerate(_array(dimension["facts"], f"{path}.facts")):
        item_path = f"{path}.facts[{index}]"
        fact = _object(raw, item_path)
        _keys(fact, {"text", "source_refs"}, item_path)
        refs = _source_refs(fact["source_refs"], allowed_refs, f"{item_path}.source_refs")
        if not refs:
            raise AnalysisValidationError(f"{item_path} 必须有来源。")
        facts.append({"text": _nonblank(fact["text"], f"{item_path}.text"), "source_refs": refs})

    inferences = []
    for index, raw in enumerate(_array(dimension["inferences"], f"{path}.inferences")):
        item_path = f"{path}.inferences[{index}]"
        inference = _object(raw, item_path)
        _keys(inference, {"text", "basis", "confidence", "source_refs"}, item_path)
        refs = _source_refs(
            inference["source_refs"], allowed_refs, f"{item_path}.source_refs"
        )
        if not refs:
            raise AnalysisValidationError(f"{item_path} 必须有来源。")
        inferences.append(
            {
                "text": _nonblank(inference["text"], f"{item_path}.text"),
                "basis": _nonblank(inference["basis"], f"{item_path}.basis"),
                "confidence": _enum(
                    inference["confidence"], CONFIDENCES, f"{item_path}.confidence"
                ),
                "source_refs": refs,
            }
        )
    return {
        "facts": facts,
        "inferences": inferences,
        "missing_fields": _strings(dimension["missing_fields"], f"{path}.missing_fields"),
    }


# 功能：收集本次输入允许引用的来源。
# 输入：`analysis_input` 为L2 输入对象。
# 输出：来源集合。
# 逻辑：合并公司、邮件、联系人、业务记录及 matched 实验来源。
# 约束：排除空字符串，不向后端请求额外数据。
def _allowed_source_refs(analysis_input: Mapping[str, Any]) -> set[str]:
    refs = {str(analysis_input.get("company_id", ""))}
    refs.update(str(value) for value in analysis_input.get("member_dedupe_keys", []))
    company = analysis_input.get("company", {})
    if isinstance(company, Mapping):
        for contact in company.get("contacts", []):
            if isinstance(contact, Mapping) and contact.get("contact_email"):
                refs.add(str(contact["contact_email"]))
    business = analysis_input.get("business_context", {})
    if isinstance(business, Mapping):
        refs.update(source_refs(business))
        customer = business.get("customer", {})
        if isinstance(customer, Mapping) and customer.get("customer_id"):
            refs.add(str(customer["customer_id"]))
        for collection, identifier in (
            ("tickets", "ticket_id"),
            ("quotes", "quote_id"),
            ("orders", "order_id"),
        ):
            for item in business.get(collection, []):
                if isinstance(item, Mapping) and item.get(identifier):
                    refs.add(str(item[identifier]))
    refs.discard("")
    return refs


# 功能：校验证据块。
# 输入：`value` 为待检查值；`allowed_refs` 为允许引用来源集合；`path` 为错误定位路径。
# 输出：证据字典。
# 逻辑：核对 text/source_refs 并规范来源。
# 约束：来源必须属于允许集合。
def _evidence_block(value: object, allowed_refs: set[str], path: str) -> dict[str, Any]:
    block = _object(value, path)
    _keys(block, {"text", "source_refs"}, path)
    return {
        "text": _nonblank(block["text"], f"{path}.text"),
        "source_refs": _source_refs(block["source_refs"], allowed_refs, f"{path}.source_refs"),
    }


# 功能：规范并核对引用列表。
# 输入：`value` 为待检查值；`allowed` 为允许的来源或枚举集合；`path` 为错误定位路径。
# 输出：引用字符串列表。
# 逻辑：去除可确认的已知前缀，验证集合成员后稳定去重。
# 约束：不存在的来源立即报错。
def _source_refs(value: object, allowed: set[str], path: str) -> list[str]:
    refs = [
        _canonical_source_ref(_nonblank(ref, f"{path}[{index}]"), allowed)
        for index, ref in enumerate(_array(value, path))
    ]
    invalid = [ref for ref in refs if ref not in allowed]
    if invalid:
        raise AnalysisValidationError(f"{path} 包含输入中不存在的来源：{invalid[0]}")
    return list(dict.fromkeys(refs))


# 功能：精简模型输入。
# 输入：`analysis_input` 为L2 输入对象。
# 输出：模型输入字典。
# 逻辑：去除重复事实证据和缓存字段，保留独立业务补充资料。
# 约束：不修改原 L2 或扩大来源范围。
def _analysis_model_input(analysis_input: Mapping[str, Any]) -> dict[str, Any]:
    """移除仅供缓存和证据复核使用的重复字段，缩短 L3 模型输入。"""
    compact_facts: dict[str, list[dict[str, Any]]] = {}
    facts = analysis_input.get("facts", {})
    if isinstance(facts, Mapping):
        for field, raw_groups in facts.items():
            groups = []
            if isinstance(raw_groups, list):
                for raw_group in raw_groups:
                    if not isinstance(raw_group, Mapping):
                        continue
                    groups.append(
                        {
                            key: raw_group[key]
                            for key in ("value", "dedupe_key", "fact_time")
                            if key in raw_group
                        }
                    )
            compact_facts[str(field)] = groups

    return {
        "company_id": analysis_input.get("company_id"),
        "built_at": analysis_input.get("built_at"),
        "company": analysis_input.get("company", {}),
        "business_context": _analysis_business_context(
            analysis_input.get("business_context", {})
        ),
        "latest_message_summary": analysis_input.get("latest_message_summary"),
        "unparsed_message_count": analysis_input.get("unparsed_message_count", 0),
        "facts": compact_facts,
        "metrics": analysis_input.get("metrics", {}),
    }


# 功能：移除仅供后端校验的补充资料元数据。
# 输入：`value` 为 L2 business_context。
# 输出：适合发送给 L3 模型的独立业务上下文。
# 逻辑：保留原业务集合；matched 补充资料仅暴露事实、独立来源 ID 和虚构标记，其他状态仅暴露状态及失败原因。
# 约束：完整来源、指纹与版本仍保留在 L2 快照中；不改变引用白名单或后端保存载荷。
def _analysis_business_context(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    compact = copy.deepcopy(dict(value))
    enrichment = value.get("company_enrichment")
    if not isinstance(enrichment, Mapping):
        return compact

    status = enrichment.get("status")
    compact_enrichment: dict[str, Any] = {"status": status}
    if status == "matched":
        facts = enrichment.get("facts")
        source = enrichment.get("source")
        compact_enrichment["facts"] = (
            copy.deepcopy(dict(facts)) if isinstance(facts, Mapping) else {}
        )
        if isinstance(source, Mapping):
            compact_enrichment["source"] = {
                "source_id": source.get("source_id"),
                "synthetic": source.get("synthetic") is True,
            }
    elif isinstance(enrichment.get("reason"), str):
        compact_enrichment["reason"] = enrichment["reason"]
    compact["company_enrichment"] = compact_enrichment
    return compact


_JSON_FENCE = re.compile(r"^\s*```(?:json)?\s*(\{.*\})\s*```\s*$", re.IGNORECASE | re.DOTALL)


# 功能：解析模型 JSON。
# 输入：`raw_text` 为模型返回文本。
# 输出：解析对象。
# 逻辑：支持既有单层 JSON Markdown 围栏后交由 json.loads。
# 约束：不修补非法 JSON。
def _decode_model_json(raw_text: str) -> object:
    """接受 JSON Object，兼容模型偶发添加的单层 Markdown 代码围栏。"""
    match = _JSON_FENCE.fullmatch(raw_text)
    return json.loads(match.group(1) if match else raw_text)


# 功能：规范可确认的引用前缀。
# 输入：`value` 为待检查值；`allowed` 为允许的来源或枚举集合。
# 输出：来源字符串。
# 逻辑：先接受原字符串，再仅在去前缀精确命中允许集合时替换。
# 约束：不模糊匹配或猜测主键。
def _canonical_source_ref(value: str, allowed: set[str]) -> str:
    """仅在去掉模型常加的类型前缀后能精确命中来源时进行规范化。"""
    if value in allowed:
        return value
    for prefix in _SOURCE_REF_PREFIXES:
        if value.startswith(prefix):
            candidate = value[len(prefix):]
            if candidate in allowed:
                return candidate
    return value


# 功能：检测不允许的成交概率表述。
# 输入：`value` 为待检查值。
# 输出：bool。
# 逻辑：递归扫描字符串、Mapping 和数组。
# 约束：保持原正则，不禁止普通业务百分比。
def _contains_deal_probability(value: object) -> bool:
    if isinstance(value, str):
        return _DEAL_PROBABILITY_PATTERN.search(value) is not None
    if isinstance(value, Mapping):
        return any(_contains_deal_probability(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_deal_probability(item) for item in value)
    return False


# 功能：将输入转为 L2 字典。
# 输入：`value` 为待检查值。
# 输出：dict。
# 逻辑：可调用 to_dict 后要求 Mapping，复制顶层。
# 约束：非对象抛 AnalysisValidationError。
def _as_document(value: object) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        raise AnalysisValidationError("analysis_input 必须是对象。")
    return dict(value)


# 功能：读取明确时区的构建时钟。
# 输入：`clock` 为返回带时区 datetime 的时钟。
# 输出：ISO 时间字符串。
# 逻辑：调用 clock 并检查 datetime 时区。
# 约束：无效时钟抛校验异常。
def _clock_text(clock: Callable[[], datetime]) -> str:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise AnalysisValidationError("clock 必须返回带时区的 datetime。")
    return value.isoformat()


# 功能：要求对象类型。
# 输入：`value` 为待检查值；`path` 为错误定位路径。
# 输出：Mapping。
# 逻辑：检查 Mapping 后返回原对象。
# 约束：错误以 path 定位并抛 AnalysisValidationError。
def _object(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AnalysisValidationError(f"{path} 必须是对象。")
    return value


# 功能：要求数组类型。
# 输入：`value` 为待检查值；`path` 为错误定位路径。
# 输出：list。
# 逻辑：检查 list 后返回原数组。
# 约束：错误以 path 定位并抛 AnalysisValidationError。
def _array(value: object, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise AnalysisValidationError(f"{path} 必须是数组。")
    return value


# 功能：核对精确字段集合。
# 输入：`value` 为待检查值；`expected` 为要求的精确字段集合；`path` 为错误定位路径。
# 输出：无返回值。
# 逻辑：比较实际键和 expected。
# 约束：缺失或多余键抛 AnalysisValidationError。
def _keys(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    if set(value) != expected:
        raise AnalysisValidationError(f"{path} 字段必须与契约完全一致。")


# 功能：要求非空字符串。
# 输入：`value` 为待检查值；`path` 为错误定位路径。
# 输出：原字符串。
# 逻辑：验证类型与去空白后非空，返回原文。
# 约束：无效值抛 AnalysisValidationError。
def _nonblank(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AnalysisValidationError(f"{path} 必须是非空字符串。")
    return value


# 功能：核对非空枚举。
# 输入：`value` 为待检查值；`allowed` 为允许的来源或枚举集合；`path` 为错误定位路径。
# 输出：字符串。
# 逻辑：验证字符串后检查 allowed 集合。
# 约束：未知枚举抛 AnalysisValidationError。
def _enum(value: object, allowed: frozenset[str], path: str) -> str:
    text = _nonblank(value, path)
    if text not in allowed:
        raise AnalysisValidationError(f"{path} 枚举值无效。")
    return text


# 功能：校验无重复字符串数组。
# 输入：`value` 为待检查值；`path` 为错误定位路径。
# 输出：字符串数组。
# 逻辑：逐项验证非空字符串并比较去重大小。
# 约束：重复值抛 AnalysisValidationError。
def _strings(value: object, path: str) -> list[str]:
    items = _array(value, path)
    result = [_nonblank(item, f"{path}[{index}]") for index, item in enumerate(items)]
    if len(result) != len(set(result)):
        raise AnalysisValidationError(f"{path} 不能包含重复值。")
    return result


__all__ = [
    "ANALYSIS_PROMPT",
    "ANALYSIS_PROMPT_VERSION",
    "AnalysisValidationError",
    "bailian_analysis_provider",
    "generate_analysis",
    "validate_analysis_payload",
]
