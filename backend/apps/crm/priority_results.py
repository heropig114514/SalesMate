"""职责：验证正式 L4 结果及解释的结构、对账关系和邮件证据。
实现：仅检查 Agent 输出契约，不重算权重；保存前在当前公司和输入快照内定位来源。
关联：serializers 调用结构校验，results 在版本及租约验证后调用来源校验。
目录：
- validate_priority_score：检查 score-v2 的贡献及可选解释。
- validate_details：检查解释分项、前三原因和证据列表。
- validate_priority_sources：确认解释引用当前公司输入内的可见原文。
变量索引：
- FEATURES：正式评分贡献字段的固定集合。
- BREAKDOWN_FIELDS：解释分项必须包含的字段集合。
- DETAIL_FIELDS：可选解释对象的完整字段集合。
"""

import re

from rest_framework.exceptions import ValidationError

FEATURES = frozenset({"urgency", "buying_intent", "opportunity_value"})
BREAKDOWN_FIELDS = FEATURES | {"deal_value", "customer_fit", "contributions"}
DETAIL_FIELDS = frozenset({"score_breakdown", "top_reasons", "evidence", "recommended_next_action"})


# 功能：检查 score-v2 的贡献及可选解释。
# 输入：`data` 为经基础 ScoreSerializer 转换的评分字典。
# 输出：无；不符合正式契约时抛 ValidationError。
# 逻辑：非空分数要求三种唯一非负整数贡献；解释只允许正式版本提交。
# 约束：旧版本沿用原校验；不把缺失解释或空分补成虚构数据，不访问数据库。
def validate_priority_score(data):
    if data["score_version"] != "score-v2":
        if "score_details" in data:
            raise ValidationError("score_details 仅用于 score-v2。")
        return
    reasons = data["score_reasons"]
    if data["score"] is not None:
        if len(reasons) != 3 or {item["feature"] for item in reasons} != FEATURES:
            raise ValidationError("score-v2 必须包含三项且不重复的正式评分贡献。")
        if any(type(item["contribution"]) is not int or not 0 <= item["contribution"] <= 100 for item in reasons):
            raise ValidationError("score-v2 贡献必须为 0–100 的整数。")
        if sum(item["contribution"] for item in reasons) != data["score"]:
            raise ValidationError("score-v2 贡献之和必须严格等于 score。")
    if "score_details" in data:
        validate_details(data["score_details"], data)


# 功能：检查解释分项、前三原因和证据列表。
# 输入：`details` 为解释字典，`score` 为对应的完整评分载荷。
# 输出：无；结构、范围或贡献关系错误时抛 ValidationError。
# 逻辑：空分仅接受空解释；非空分检查分项、排序、证据形状及贡献与主结果一致。
# 约束：不验证模型推理真实性，不执行评分算法；来源归属由保存事务另行检查。
def validate_details(details, score):
    if not isinstance(details, dict) or set(details) != DETAIL_FIELDS:
        raise ValidationError("score_details 必须包含分项、原因、证据和建议动作四个字段。")
    if score["score"] is None:
        if details != {"score_breakdown": None, "top_reasons": [], "evidence": [], "recommended_next_action": None}:
            raise ValidationError("空分不得附带确定性的评分解释。")
        return
    breakdown = details["score_breakdown"]
    if not isinstance(breakdown, dict) or set(breakdown) != BREAKDOWN_FIELDS:
        raise ValidationError("评分分项字段不完整。")
    if any(type(breakdown[key]) is not int or not 0 <= breakdown[key] <= 100 for key in BREAKDOWN_FIELDS - {"contributions"}):
        raise ValidationError("原始分项必须为 0–100 的整数。")
    contributions = {item["feature"]: item["contribution"] for item in score["score_reasons"]}
    if (breakdown["contributions"] != contributions
            or not isinstance(breakdown["contributions"], dict)
            or any(type(value) is not int for value in breakdown["contributions"].values())):
        raise ValidationError("解释贡献与 score_reasons 不一致。")
    reasons, evidence = details["top_reasons"], details["evidence"]
    if not isinstance(reasons, list) or not 1 <= len(reasons) <= 3 or not isinstance(evidence, list):
        raise ValidationError("解释应包含 1–3 项原因及证据数组。")
    for reason in reasons:
        if not isinstance(reason, dict) or set(reason) != {"type", "title", "evidence", "source_id", "impact_score"}:
            raise ValidationError("解释原因字段不符合契约。")
        if any(not isinstance(reason[key], str) or not reason[key].strip() for key in ("type", "title", "evidence")):
            raise ValidationError("解释原因的类型、标题和说明不得为空。")
        if reason["source_id"] is not None and (not isinstance(reason["source_id"], str) or not reason["source_id"].strip()):
            raise ValidationError("原因来源必须为邮件标识或 null。")
        if type(reason["impact_score"]) is not int or not 0 <= reason["impact_score"] <= 100:
            raise ValidationError("原因贡献必须为非负整数。")
        feature = "opportunity_value" if reason["type"] == "DEAL_VALUE" else "buying_intent" if reason["type"] == "BUYING_INTENT" else "urgency"
        if reason["impact_score"] != contributions[feature]:
            raise ValidationError("原因贡献与对应分项不一致。")
        if reason["type"] != "DEAL_VALUE" and reason["source_id"] is None:
            raise ValidationError("邮件信号原因必须带来源。")
        if reason["type"] == "DEAL_VALUE" and reason["source_id"] is not None:
            raise ValidationError("业务金额说明不得伪称邮件证据。")
    if [item["impact_score"] for item in reasons] != sorted((item["impact_score"] for item in reasons), reverse=True):
        raise ValidationError("前三原因必须按影响降序排列。")
    expected = [{"source_id": item["source_id"], "text": item["evidence"]} for item in reasons if item["source_id"] is not None]
    if evidence != expected:
        raise ValidationError("证据列表必须与带邮件来源的原因逐项一致。")
    if not isinstance(details["recommended_next_action"], str) or not details["recommended_next_action"].strip():
        raise ValidationError("有分值的解释必须提供建议动作。")


# 功能：确认解释引用当前公司输入内的可见原文。
# 输入：`data` 为已校验评分，`company` 为已锁定授权公司，`analysis` 为当前成功分析。
# 输出：无；越界或无法定位的证据抛 ValidationError。
# 逻辑：取快照成员与当前业务邮件交集，再按 Agent 的空白归一规则定位原文。
# 约束：调用前须完成版本、权限和租约校验；不记录邮件内容，不修改数据库。
def validate_priority_sources(data, company, analysis):
    details = data.get("score_details")
    if not details:
        return
    members = analysis.snapshot.payload["member_dedupe_keys"]
    messages = dict(company.emails.filter(business_classification="business", dedupe_key__in=members).values_list("dedupe_key", "payload"))
    for item in details["evidence"]:
        message = messages.get(item["source_id"])
        if message is None:
            raise ValidationError("评分证据不属于当前公司可见输入。")
        content = "\n".join(message.get(key) or "" for key in ("subject", "body_text"))
        if re.sub(r"\s+", "", item["text"]) not in re.sub(r"\s+", "", content):
            raise ValidationError("评分证据无法定位至来源邮件。")
