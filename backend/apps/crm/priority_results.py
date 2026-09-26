"""Responsibility: Validate formal L4 result and explanation structure, reconciliation relationships, and email evidence.
Implementation: Check Agent output contract only and do not recompute weights; accept explicit empty explanations for tentative scores; locate sources in the current company and input snapshot before saving.
Relationships: serializers calls structural validation, and results calls source validation after version and lease validation.
Directory:
- validate_priority_score: Check score-v2 contributions and optional explanation.
- validate_details: Check explanation breakdown, top three reasons, and evidence list.
- validate_priority_sources: Confirm explanations cite visible source text in current company input.
Variable index:
- FEATURES: Fixed set of formal scoring contribution fields.
- BREAKDOWN_FIELDS: Fields required in explanation breakdown.
- EMPTY_DETAILS: Complete empty explanation structure explicitly providing no breakdown basis.
- DETAIL_FIELDS: Complete field set for optional explanation object.
"""

import re

from rest_framework.exceptions import ValidationError

FEATURES = frozenset({"urgency", "buying_intent", "opportunity_value"})
BREAKDOWN_FIELDS = FEATURES | {"deal_value", "customer_fit", "contributions"}
EMPTY_DETAILS = {"score_breakdown": None, "top_reasons": [], "evidence": [], "recommended_next_action": None}
DETAIL_FIELDS = frozenset({"score_breakdown", "top_reasons", "evidence", "recommended_next_action"})


# Function: Check score-v2 contributions and optional explanation.
# Inputs: `data` is a score dictionary transformed by base ScoreSerializer.
# Outputs: None; raises ValidationError when the formal contract is not met.
# Logic: Nonempty score requires three unique non-negative integer contributions; explanations are allowed only for the formal version.
# Constraints: Legacy versions retain original validation; does not invent data for missing explanations or empty scores and does not access the database.
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


# Function: Check explanation breakdown, top three reasons, and evidence list.
# Inputs: `details` is an explanation dictionary and `score` is its complete score payload.
# Outputs: None; raises ValidationError for incorrect structure, range, or contribution relationship.
# Logic: Empty score must use empty explanation, and tentative scores may explicitly have no explanation; every other nonempty explanation fully validates breakdown, ordering, evidence, and contributions.
# Constraints: Does not validate model-reasoning truth or execute the scoring algorithm; the save transaction separately checks source ownership.
def validate_details(details, score):
    if not isinstance(details, dict) or set(details) != DETAIL_FIELDS:
        raise ValidationError("score_details 必须包含分项、原因、证据和建议动作四个字段。")
    if details == EMPTY_DETAILS:
        return
    if score["score"] is None:
        raise ValidationError("空分不得附带确定性的评分解释。")
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


# Function: Confirm explanations cite visible source text in current company input.
# Inputs: `data` is validated score, `company` is a locked authorized company, and `analysis` is current successful analysis.
# Outputs: None; raises ValidationError for out-of-scope or unlocatable evidence.
# Logic: Intersect snapshot members with current business emails, then locate source text using the Agent whitespace-normalization rule.
# Constraints: Version, authorization, and lease validation must complete before calling; does not log email content or modify the database.
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
