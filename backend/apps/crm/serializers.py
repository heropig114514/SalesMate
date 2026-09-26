"""Responsibility: Validate Agent business payloads and browser requests.
Implementation: Declare protocol fields explicitly and validate fact evidence, state, and score consistency. Formal scores submit explanations atomically; the service layer validates ownership and source scope afterward.
Relationships: API endpoints and rules placeholders share these validators, and OpenAPI is generated from their declarations.
Directory:
- StrictSerializer: Reject undeclared fields so credentials and misspelled fields are never accepted silently.
- StrictSerializer.to_internal_value: Validate the input object and its declared field set.
- EmailSubmissionSerializer: Declare the standard payload for one email.
- EmailSubmissionSerializer.get_fields: Add the protocol field corresponding to Python's reserved `from` word.
- EmailSubmissionSerializer.validate: Verify the email natural key, extraction status, and verbatim evidence.
- compact_evidence_text: Remove whitespace and formatting characters that evidence matching may ignore.
- evidence_is_locatable: Determine whether evidence comes from the subject or body using the Agent's rules.
- validate_extraction: Validate complete extracted-fact fields and locatable evidence.
- FactsResubmissionSerializer: Declare the payload for resubmitting failed facts.
- AnalysisInputSerializer: Declare the L2 input archived unchanged by the Agent.
- EvidenceSerializer: Declare fact or explanatory evidence with its source.
- InferenceSerializer: Declare inferences that remain separate from facts.
- DimensionSerializer: Declare the shared structure for seven analysis dimensions.
- ProfileSerializer: Declare the three-dimension customer profile.
- DimensionsSerializer: Declare the four-dimension customer analysis.
- ConflictSerializer: Declare fact conflicts or explicit changes.
- DetailSerializer: Declare the detailed result structure.
- FeatureValueField: Declare nullable integer feature values from 0 to 3.
- FeatureValueField.to_internal_value: Validate the union type used for scoring features.
- FeatureValueField.to_representation: Emit a validated feature value.
- FeatureSerializer: Declare one feature used by L4.
- FeaturesSerializer: Declare the three L4 model features.
- ListViewSerializer: Declare the company-list projection.
- AnalysisSerializer: Declare the complete L3 analysis output.
- AnalysisSerializer.validate: Check that analysis state corresponds to its payload.
- ScoreSerializer: Declare the L4 score and contribution explanations.
- ScoreSerializer.validate: Check whether contribution explanations reconcile with the score.
- SyncStateSerializer: Declare optimistic-lock writes for synchronization cursors.
- ClaimSerializer: Declare a job-claim request.
- JobReportSerializer: Declare a job-completion report.
- RegisterSerializer: Declare explicit CRM registration input.
- SimulateSerializer: Declare a simulated email entered manually in the frontend.
- MailboxSerializer: Declare business mailbox creation.
- MailboxSyncClaimSerializer: Declare the number of employee-mailbox synchronizations to claim.
- MailboxSyncReportSerializer: Declare the final employee-mailbox synchronization report.
Variable index:
- ScoreSerializer.score_details: Optional score-v2 breakdown, reasons, evidence, and recommended action; omission means no explanation was submitted.
- RegisterSerializer.country: Optional authoritative customer country or region; a missing value is not inferred automatically.
- AnalysisInputSerializer.built_at: L2 snapshot construction time.
- AnalysisInputSerializer.business_context: Backend customer, ticket, quote, and order snapshot.
- AnalysisInputSerializer.company: Company, domain, and contact-grouping snapshot.
- AnalysisInputSerializer.company_id: Backend-assigned company UUID.
- AnalysisInputSerializer.external_snapshot_version: Unchanged echo of the backend CRM snapshot version.
- AnalysisInputSerializer.facts: Locatable fact structure; null on failure as required by the protocol.
- AnalysisInputSerializer.input_version: Input version calculated and submitted unchanged by the Agent.
- AnalysisInputSerializer.latest_message_summary: Summary of the latest email whose extraction completed.
- AnalysisInputSerializer.member_dedupe_keys: Complete set of email deduplication keys participating in this analysis.
- AnalysisInputSerializer.merge_version: L2 deterministic merge-rule version.
- AnalysisInputSerializer.metrics: L2 interaction counts, time intervals, and CRM state.
- AnalysisInputSerializer.unparsed_message_count: Number of emails whose extraction is unfinished.
- AnalysisSerializer.analysis_base_time: Upper time bound for business facts permitted in the analysis.
- AnalysisSerializer.analysis_prompt_version: L3 model-prompt or rules-producer version.
- AnalysisSerializer.company_id: Backend-assigned company UUID.
- AnalysisSerializer.detail_view: Detailed projection of the three-dimension profile and four-dimension analysis.
- AnalysisSerializer.error: Explicit failure description that is not displayed as a successful result.
- AnalysisSerializer.generated_at: Analysis generation time used by the frontend to mark stale results.
- AnalysisSerializer.input_version: Input version calculated and submitted unchanged by the Agent.
- AnalysisSerializer.list_view: Lightweight analysis projection for the company list.
- AnalysisSerializer.status: Current protocol-payload or task state; the field declaration defines permitted values.
- ClaimSerializer.lease_seconds: Lease duration explicitly specified by the caller, from 10 to 600 seconds.
- ClaimSerializer.limit: Number of jobs claimed in one batch, from 1 to 50.
- ConflictSerializer.field: Fact field that changed or conflicts.
- ConflictSerializer.kind: `value_changed` or `source_disagree` conflict type.
- ConflictSerializer.source_refs: Array of locatable email or business-record IDs.
- ConflictSerializer.summary: Textual explanation of a change or conflict.
- DetailSerializer.analysis: Foreign key to the analysis being scored; the serializer holds its four-dimension analysis result.
- DetailSerializer.conflicts: Conflict and change list citing at least two sources.
- DetailSerializer.context_completeness: Unparsed-email count and explanation of incomplete context.
- DetailSerializer.missing_fields: Information required for analysis but absent from the current context.
- DetailSerializer.profile: Three-dimension customer profile.
- DimensionSerializer.facts: Locatable fact structure; null on failure as required by the protocol.
- DimensionSerializer.inferences: Grounded inferences stored separately from facts.
- DimensionSerializer.missing_fields: Information still missing for this dimension or globally.
- DimensionsSerializer.guidance: Next-step guidance dimension in customer analysis.
- DimensionsSerializer.opportunity: Opportunity and demand dimension in customer analysis.
- DimensionsSerializer.risk: Risk and constraint dimension in customer analysis.
- DimensionsSerializer.timeline: Historical timeline dimension in customer analysis.
- EmailSubmissionSerializer.body_text: Original plain-text body retaining verbatim evidence.
- EmailSubmissionSerializer.cc: Complete CC-recipient email array.
- EmailSubmissionSerializer.contact_email: Primary external contact email in an L1 submission.
- EmailSubmissionSerializer.dedupe_key: Natural idempotency key formed from mailbox address and Gmail message ID.
- EmailSubmissionSerializer.direction: Email direction: inbound, outbound, or unknown.
- EmailSubmissionSerializer.extract_error: Extraction failure summary; empty on success.
- EmailSubmissionSerializer.extract_prompt_version: L1 prompt or rules version.
- EmailSubmissionSerializer.extract_status: Completed, failed, or non-business-skipped state for one extraction.
- EmailSubmissionSerializer.facts: Locatable fact structure; null on failure as required by the protocol.
- EmailSubmissionSerializer.gmail_message_id: Gmail source-message identifier; samples use an independent sample identifier.
- EmailSubmissionSerializer.mailbox_id: Backend-assigned business-mailbox UUID.
- EmailSubmissionSerializer.mailbox_address: Authorized Gmail address and a component of `dedupe_key`.
- EmailSubmissionSerializer.non_business_hint: Flag indicating a suspected non-business email.
- EmailSubmissionSerializer.non_business_reason: Basis for the non-business-email judgement.
- EmailSubmissionSerializer.received_at: Email receipt time used for today's new-email statistics.
- EmailSubmissionSerializer.sent_at: Timezone-aware time when the email or business action occurred.
- EmailSubmissionSerializer.source: Real, synthetic, research, or simulated data-origin marker.
- EmailSubmissionSerializer.subject: Original email subject.
- EmailSubmissionSerializer.thread_id: Identifier pairing messages in one thread and their response intervals.
- EmailSubmissionSerializer.to: Complete recipient email array.
- EvidenceSerializer.source_refs: Array of locatable email or business-record IDs.
- EvidenceSerializer.text: Fact or explanatory-evidence text.
- FACT_FIELDS: Set of multi-value fact-field names in L1.
- PURCHASE_STAGES: Six-level purchasing-stage enumeration for extract-v7.
- FactsResubmissionSerializer.dedupe_key: Natural idempotency key formed from mailbox address and Gmail message ID.
- FactsResubmissionSerializer.extract_error: Extraction failure summary; empty on success.
- FactsResubmissionSerializer.extract_prompt_version: L1 prompt or rules version.
- FactsResubmissionSerializer.extract_status: Completed, failed, or non-business-skipped state for one extraction.
- FactsResubmissionSerializer.facts: Locatable fact structure; null on failure as required by the protocol.
- FeatureSerializer.basis: Explanation supporting an inference or feature judgement.
- FeatureSerializer.value: Nullable integer feature value from 0 to 3.
- FeaturesSerializer.decision_visibility: Decision-process visibility feature.
- FeaturesSerializer.demand_clarity: Demand-clarity feature.
- FeaturesSerializer.urgency: Demand-urgency feature.
- INDUSTRIES: Enumeration of four product industries plus unknown.
- InferenceSerializer.basis: Explanation supporting an inference or feature judgement.
- InferenceSerializer.confidence: Low, medium, or high inference-confidence level; it is not a deal percentage.
- JobReportSerializer.duration_ms: Job processing duration in milliseconds.
- JobReportSerializer.error: Explicit failure description that is not displayed as a successful result.
- JobReportSerializer.input_version: Input version calculated and submitted unchanged by the Agent.
- JobReportSerializer.job_id: Backend job UUID.
- JobReportSerializer.produced: Declaration of the analyses, scores, and emails actually produced.
- JobReportSerializer.status: Current protocol-payload or task state; the field declaration defines permitted values.
- ListViewSerializer.headline_summary: Latest-message summary for the list.
- ListViewSerializer.industry: Industry enumeration for the list.
- ListViewSerializer.industry_evidence: Fact basis and source for the industry judgement.
- ListViewSerializer.score_features: Three judgement features used by L4.
- ListViewSerializer.signal: Company's primary business signal.
- ListViewSerializer.signal_evidence: Basis and source for the signal judgement.
- ListViewSerializer.size_band: Employee-count display band; an unknown count remains unknown.
- ListViewSerializer.size_source: Source explanation for the authoritative employee-count record.
- ListViewSerializer.ticket_signals: Per-ticket signal array; tickets are never fabricated from email.
- MailboxSerializer.address: Business-mailbox display address, not OAuth verification evidence.
- MailboxSyncClaimSerializer.limit: Maximum employee-mailbox synchronization requests claimed by a one-shot Agent.
- MailboxSyncReportSerializer.authorization: Google authorized-user JSON refreshed by the Agent.
- MailboxSyncReportSerializer.error: Displayable mailbox-synchronization failure error.
- MailboxSyncReportSerializer.mailbox_id: Employee-mailbox UUID for this synchronization.
- MailboxSyncReportSerializer.status: Completed or failed synchronization state.
- MailboxSyncReportSerializer.sync_result: GmailSyncResult summary.
- ProfileSerializer.company_ops: Operations and decision dimension in the customer profile.
- ProfileSerializer.industry_context: Industry-context dimension in the customer profile.
- ProfileSerializer.intent: Purchasing-intent dimension in the customer profile.
- RegisterSerializer.company_name: Company display name; it does not participate in automatic grouping.
- RegisterSerializer.employee_count: Exact sourced employee count; unknown remains null.
- RegisterSerializer.employee_count_source: Fact source for the authoritative employee count.
- RegisterSerializer.industry_from_crm: CRM industry confirmed by the user.
- SIGNALS: Company business-signal enumeration.
- SIZES: Non-overlapping employee-count bands plus unknown.
- ScoreSerializer.company_id: Backend-assigned company UUID.
- ScoreSerializer.input_version: Input version calculated and submitted unchanged by the Agent.
- ScoreSerializer.score: Follow-up priority from 0 to 100; null when information is missing.
- ScoreSerializer.score_reasons: Score contributions and explanations whose sum must equal `score`.
- ScoreSerializer.score_version: Scoring-rule version separating rules placeholders from formal Agent versions.
- ScoreSerializer.scored_at: Calculation time for this score.
- SimulateSerializer.body_text: Original plain-text body retaining verbatim evidence.
- SimulateSerializer.mailbox_id: Backend-assigned business-mailbox UUID.
- SimulateSerializer.sender: Sender email of the simulated incoming message.
- SimulateSerializer.subject: Original email subject.
- SyncStateSerializer.cursor: Gmail synchronization cursor without access credentials.
- SyncStateSerializer.last_synced_at: Most recent successful synchronization time.
- SyncStateSerializer.mailbox_id: Backend-assigned business-mailbox UUID.
- SyncStateSerializer.scope: Explicit synchronization labels and historical starting scope.
- SyncStateSerializer.status: Current protocol-payload or task state; the field declaration defines permitted values.
- SyncStateSerializer.version: Optimistic-lock version of the synchronization state.
"""
import unicodedata

from rest_framework import serializers as s
from drf_spectacular.utils import extend_schema_field

FACT_FIELDS = ("contact_name", "contact_title", "company_self_reported", "business_background",
               "employee_scale_hint", "product_need", "quantity", "budget", "delivery_time",
               "decision_process", "concerns", "quote_reference", "order_reference")
PURCHASE_STAGES = frozenset({
    "L1 Exploring", "L2 Interested", "L3 Qualified", "L4 Evaluating",
    "L5 Negotiating", "L6 Purchase Ready",
})
SIGNALS = ("repeat_purchase", "quoted_not_closed", "inquiry_intent", "new_lead_no_profile", "unknown")
INDUSTRIES = ("半导体检测", "精密量测", "光学检测", "工业检测", "unknown")
SIZES = ("lt_50", "50_100", "100_200", "200_500", "gte_500", "unknown")


# Function: Strictly reject undeclared fields so authorization tokens or misspelled fields are not silently accepted.
# Logic: Compare field sets before ordinary DRF validation.
# Constraints: Dedicated validation owns internal structure of JSON extension fields.
class StrictSerializer(s.Serializer):
    # Function: Validate an input object and its field set.
    # Inputs: `data` is client raw JSON.
    # Outputs: DRF-validated field dictionary; unknown fields raise ValidationError.
    # Logic: Accept dictionaries only and exclude undeclared fields; object errors use DRF non_field_errors dictionary for correct 400 response.
    # Constraints: Has no database or log side effects.
    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields):
            raise s.ValidationError({s.api_settings.NON_FIELD_ERRORS_KEY: ["必须为对象，且不得包含未声明字段。"]})
        return super().to_internal_value(data)


# Function: Declare normalized payload for one email.
# Logic: Map from back to its original JSON name and require real time, direction, and origin; qq_real identifies QQ IMAP source text.
# Constraints: Service layer validates authorized mailbox ownership and does not trust self-reported payload identity.
class EmailSubmissionSerializer(StrictSerializer):
    dedupe_key = s.CharField(max_length=400)
    mailbox_id = s.UUIDField()
    mailbox_address = s.EmailField()
    gmail_message_id = s.CharField(max_length=200)
    thread_id = s.CharField(max_length=200, allow_null=True)
    to = s.ListField(child=s.EmailField())
    cc = s.ListField(child=s.EmailField())
    sent_at = s.DateTimeField(allow_null=True)
    received_at = s.DateTimeField(allow_null=True)
    subject = s.CharField(max_length=1000, allow_blank=True)
    body_text = s.CharField(max_length=200000, allow_blank=True, trim_whitespace=False)
    direction = s.ChoiceField(choices=["inbound", "outbound", "unknown"])
    source = s.ChoiceField(choices=["gmail_real", "qq_real", "synthetic_sample", "research_dataset", "simulated"])
    contact_email = s.EmailField(allow_null=True)
    non_business_hint = s.BooleanField()
    non_business_reason = s.CharField(allow_null=True, allow_blank=True)
    extract_status = s.ChoiceField(choices=["completed", "failed", "skipped_non_business"])
    extract_prompt_version = s.CharField(max_length=100)
    extract_error = s.CharField(allow_null=True, allow_blank=True)
    facts = s.JSONField(allow_null=True)

    # Function: Add the protocol field corresponding to Python reserved word from.
    # Inputs: Instance-declared field configuration with no external parameters.
    # Outputs: Field mapping containing from.
    # Logic: Extend fields copied by the parent class without modifying global declaration.
    # Constraints: Schema and actual validation share this mapping.
    def get_fields(self):
        fields = super().get_fields()
        fields["from"] = s.EmailField(allow_null=True)
        return fields

    # Function: Check email natural key, extraction state, and verbatim evidence.
    # Inputs: `attrs` are normalized fields including body and facts.
    # Outputs: Original attrs; contract mismatch raises ValidationError.
    # Logic: Key concatenates mailbox and message ID; validate completed facts item by item, accepting purchasing stage only from inbound origin.
    # Constraints: Does not call models, modify facts, or fill unknown values.
    def validate(self, attrs):
        expected_key = f"{attrs['mailbox_address'].casefold()}:{attrs['gmail_message_id']}"
        if attrs["dedupe_key"] != expected_key:
            raise s.ValidationError("dedupe_key 必须为 mailbox_address:gmail_message_id。")
        validate_extraction(attrs, attrs["subject"], attrs["body_text"], direction=attrs["direction"])
        if attrs["extract_status"] == "skipped_non_business" and not attrs["non_business_hint"]:
            raise s.ValidationError("跳过非业务邮件必须附 non_business_hint。")
        return attrs


# Function: Remove whitespace and format characters ignored by evidence location.
# Inputs: `value` is an evidence or source-email string.
# Outputs: Compact string retaining the original order of every visible character.
# Logic: Remove isspace characters and Unicode Cf format characters.
# Constraints: Does not perform case, punctuation, NFKC, or full-width/half-width conversion.
def compact_evidence_text(value):
    return "".join(
        character
        for character in value
        if not character.isspace() and unicodedata.category(character) != "Cf"
    )


# Function: Determine under the same Agent rules whether evidence comes from subject or body.
# Inputs: `evidence` is model evidence and `subject` and `body_text` are current email boundaries.
# Outputs: True on exact match or after removing only whitespace and Unicode format characters.
# Logic: Check verbatim substring first, then separately compact evidence, subject, and body; never concatenate across subject/body boundaries.
# Constraints: Does not apply NFKC, case, punctuation, or full-width/half-width conversion to avoid accepting model rewrites.
def evidence_is_locatable(evidence, subject, body_text):
    if evidence in subject or evidence in body_text:
        return True

    compact_evidence = compact_evidence_text(evidence)
    return bool(
        compact_evidence
        and (
            compact_evidence in compact_evidence_text(subject)
            or compact_evidence in compact_evidence_text(body_text)
        )
    )


# Function: Validate complete extraction facts and locatable evidence.
# Inputs: `data` contains version, status, facts, and error; `subject` and `body_text` are source; `direction` is required email direction.
# Outputs: None; raises ValidationError when the contract is not met.
# Logic: Completed state requires complete fields; only inbound may declare purchasing stage; evidence locates under allowed whitespace and invisible-format differences.
# Constraints: Validates location only and does not claim to prove model semantic correctness.
def validate_extraction(data, subject, body_text, *, direction):
    if data["extract_prompt_version"] != "extract-v7":
        raise s.ValidationError("仅接受 extract-v7 邮件事实结构。")
    facts = data["facts"]
    if data["extract_status"] != "completed":
        if facts is not None or (data["extract_status"] == "failed" and not data["extract_error"]):
            raise s.ValidationError("失败需错误摘要，未完成抽取 facts 必须为 null。")
        return
    required = set(FACT_FIELDS) | {"has_substantive_update", "message_summary", "intent_hint", "intent_evidences"}
    if not isinstance(facts, dict) or set(facts) != required or data["extract_error"] is not None:
        raise s.ValidationError("完成抽取必须包含完整 facts 且 extract_error 为 null。")
    if type(facts["has_substantive_update"]) is not bool or not isinstance(facts["message_summary"], str) or len(facts["message_summary"]) > 80:
        raise s.ValidationError("实质更新必须为布尔值，摘要不得超过 80 字。")
    intent_hint = facts["intent_hint"]
    if intent_hint is not None and not isinstance(intent_hint, str):
        raise s.ValidationError("intent_hint 无效。")
    if intent_hint is not None and intent_hint not in PURCHASE_STAGES:
        raise s.ValidationError("intent_hint 无效。")
    if intent_hint is not None and direction != "inbound":
        raise s.ValidationError("只有 inbound 客户邮件可以声明采购阶段。")
    intent_evidences = facts["intent_evidences"]
    if not isinstance(intent_evidences, list):
        raise s.ValidationError("intent_evidences 必须是数组。")
    seen_intent_evidences = set()
    for index, evidence in enumerate(intent_evidences):
        if (
            not isinstance(evidence, str)
            or not evidence.strip()
            or evidence in seen_intent_evidences
            or not evidence_is_locatable(evidence, subject, body_text)
        ):
            raise s.ValidationError(
                f"intent_evidences[{index}] 必须是可定位且不重复的原文证据。"
            )
        seen_intent_evidences.add(evidence)
    if (intent_hint is None and intent_evidences) or (intent_hint is not None and not intent_evidences):
        raise s.ValidationError("extract-v7 的采购阶段与原文证据不一致。")
    for field in FACT_FIELDS:
        groups = facts[field]
        if not isinstance(groups, list):
            raise s.ValidationError(f"{field} 必须是数组。")
        seen_values = set()
        for index, item in enumerate(groups):
            if not isinstance(item, dict) or set(item) != {"value", "evidences"}:
                raise s.ValidationError(f"{field}[{index}] 必须包含 value 和 evidences。")
            value, evidences = item["value"], item["evidences"]
            if not isinstance(value, str) or not value.strip() or value in seen_values:
                raise s.ValidationError(f"{field}[{index}].value 为空或重复。")
            if not isinstance(evidences, list) or not evidences:
                raise s.ValidationError(f"{field}[{index}].evidences 至少需要一条证据。")
            seen_evidences = set()
            for evidence_index, evidence in enumerate(evidences):
                if (
                    not isinstance(evidence, str)
                    or not evidence.strip()
                    or evidence in seen_evidences
                    or not evidence_is_locatable(evidence, subject, body_text)
                ):
                    raise s.ValidationError(
                        f"{field}[{index}].evidences[{evidence_index}] 必须是可定位且不重复的原文证据。"
                    )
                seen_evidences.add(evidence)
            seen_values.add(value)


# Function: Declare failed-fact resubmission payload.
# Logic: Permit completed-state submission only and validate body evidence from stored email.
# Constraints: Transactional service performs the one failed-to-completed transition.
class FactsResubmissionSerializer(StrictSerializer):
    dedupe_key = s.CharField()
    extract_prompt_version = s.CharField(max_length=100)
    extract_status = s.ChoiceField(choices=["completed"])
    extract_error = s.CharField(allow_null=True)
    facts = s.JSONField()


# Function: Declare L2 input archived unchanged from Agent.
# Logic: Retain normalized fields while service layer verifies sources for JSON facts and metrics.
# Constraints: Does not calculate input_version for Agent.
class AnalysisInputSerializer(StrictSerializer):
    company_id = s.UUIDField()
    input_version = s.CharField(max_length=160)
    merge_version = s.CharField(max_length=100)
    external_snapshot_version = s.CharField()
    built_at = s.DateTimeField()
    company = s.DictField()
    business_context = s.DictField()
    latest_message_summary = s.CharField(allow_null=True, allow_blank=True)
    member_dedupe_keys = s.ListField(child=s.CharField())
    unparsed_message_count = s.IntegerField(min_value=0)
    facts = s.DictField(child=s.ListField(child=s.DictField()))
    metrics = s.DictField()


# Function: Declare sourced fact or explanatory evidence.
# Logic: Facts require nonempty references and service layer checks references belong to current snapshot.
# Constraints: Text itself does not represent human truth verification.
class EvidenceSerializer(StrictSerializer):
    text = s.CharField()
    source_refs = s.ListField(child=s.CharField(), allow_empty=False)


# Function: Declare inference separated from facts.
# Logic: Retain basis, confidence, and sources.
# Constraints: Confidence does not map to a deal percentage.
class InferenceSerializer(EvidenceSerializer):
    basis = s.CharField()
    confidence = s.ChoiceField(choices=["low", "medium", "high"])


# Function: Declare shared structure for seven analysis dimensions.
# Logic: Explicitly separate facts, inferences, and missing information.
# Constraints: Empty arrays are valid unknown results.
class DimensionSerializer(StrictSerializer):
    facts = EvidenceSerializer(many=True)
    inferences = InferenceSerializer(many=True)
    missing_fields = s.ListField(child=s.CharField())


# Function: Declare three-dimension customer profile.
# Logic: Retain README field names and do not add probability fields.
# Constraints: Missing dimensions fail validation.
class ProfileSerializer(StrictSerializer):
    industry_context = DimensionSerializer()
    company_ops = DimensionSerializer()
    intent = DimensionSerializer()


# Function: Declare four-dimension customer analysis.
# Logic: Separate timeline, opportunity, risk, and guidance.
# Constraints: Does not automatically fill model content.
class DimensionsSerializer(StrictSerializer):
    timeline = DimensionSerializer()
    opportunity = DimensionSerializer()
    risk = DimensionSerializer()
    guidance = DimensionSerializer()


# Function: Declare fact conflict or explicit change.
# Logic: Cite at least two sources.
# Constraints: Does not automatically classify ordinary multi-value facts as conflict.
class ConflictSerializer(StrictSerializer):
    field = s.ChoiceField(choices=FACT_FIELDS)
    kind = s.ChoiceField(choices=["value_changed", "source_disagree"])
    summary = s.CharField()
    source_refs = s.ListField(child=s.CharField(), min_length=2)


# Function: Declare detail-output structure.
# Logic: Combine three-dimension profile, four-dimension analysis, and conflict description.
# Constraints: Validate source scope before saving results.
class DetailSerializer(StrictSerializer):
    conflicts = ConflictSerializer(many=True)
    profile = ProfileSerializer()
    analysis = DimensionsSerializer()
    missing_fields = s.ListField(child=s.CharField())
    context_completeness = s.DictField()


# Function: Declare nullable integer feature value from 0 to 3.
# Logic: Retain JSON null for insufficient information and do not coerce missing values to numbers.
# Constraints: Does not accept booleans as integer features.
@extend_schema_field({"type": "integer", "minimum": 0, "maximum": 3, "nullable": True})
class FeatureValueField(s.Field):
    # Function: Validate the score-feature union type.
    # Inputs: `data` is raw JSON integer; field allow_null handles JSON null.
    # Outputs: Original value; incorrect type or range raises ValidationError.
    # Logic: Accept true integers from 0 to 3 only.
    # Constraints: Does not default missing fields to zero.
    def to_internal_value(self, data):
        if type(data) is int and 0 <= data <= 3:
            return data
        raise s.ValidationError("特征必须为 0–3 整数或 null。")

    # Function: Output an already validated feature value.
    # Inputs: `value` is an integer from 0 to 3.
    # Outputs: Same-type JSON source value.
    # Logic: Does not stringify or round.
    # Constraints: Data should have passed input validation.
    def to_representation(self, value):
        return value


# Function: Declare one feature used by L4.
# Logic: Limit value to 0 through 3 and use JSON null for no basis.
# Constraints: null is not zero.
class FeatureSerializer(StrictSerializer):
    value = FeatureValueField(allow_null=True)
    basis = s.CharField()


# Function: Declare three model features for L4.
# Logic: Retain README fields.
# Constraints: All fields are required.
class FeaturesSerializer(StrictSerializer):
    demand_clarity = FeatureSerializer()
    urgency = FeatureSerializer()
    decision_visibility = FeatureSerializer()


# Function: Declare company-list projection.
# Logic: Constrain product enumerations and retain basis.
# Constraints: Results service validates business threshold for signal sources.
class ListViewSerializer(StrictSerializer):
    signal = s.ChoiceField(choices=SIGNALS)
    signal_evidence = s.DictField()
    ticket_signals = s.ListField(child=s.DictField())
    industry = s.ChoiceField(choices=INDUSTRIES)
    industry_evidence = s.DictField()
    size_band = s.ChoiceField(choices=SIZES)
    size_source = s.CharField()
    headline_summary = s.CharField(allow_blank=True)
    score_features = FeaturesSerializer()


# Function: Declare complete L3 analysis output.
# Logic: List and detail sections are empty on failure and retain complete seven-dimension results on success.
# Constraints: Transactional service validates time boundaries, references, and business state.
class AnalysisSerializer(StrictSerializer):
    company_id = s.UUIDField()
    input_version = s.CharField(max_length=160)
    analysis_prompt_version = s.CharField(max_length=100)
    generated_at = s.DateTimeField()
    analysis_base_time = s.DateTimeField()
    status = s.ChoiceField(choices=["completed", "failed"])
    list_view = ListViewSerializer(allow_null=True)
    detail_view = DetailSerializer(allow_null=True)
    error = s.DictField(allow_null=True, required=False)

    # Function: Check correspondence between analysis state and payload.
    # Inputs: `attrs` are validated fields.
    # Outputs: attrs; contradictory state raises ValidationError.
    # Logic: Success requires complete result, while failure requires error object and empty sections.
    # Constraints: Does not automatically convert failure to rules output.
    def validate(self, attrs):
        if attrs["status"] == "completed":
            if attrs["list_view"] is None or attrs["detail_view"] is None or attrs.get("error"):
                raise s.ValidationError("成功分析必须包含完整 list_view 和 detail_view。")
        elif attrs["list_view"] is not None or attrs["detail_view"] is not None or not attrs.get("error"):
            raise s.ValidationError("失败分析必须两段为空并包含 error。")
        return attrs


# Function: Declare L4 score and contribution explanation.
# Logic: Validate empty-score semantics, contribution sum, and optional score-v2 explanation without relying on legacy L3 features.
# Constraints: Producer submits actual rule version and already chosen weights are not rewritten.
class ScoreSerializer(StrictSerializer):
    company_id = s.UUIDField()
    input_version = s.CharField(max_length=160)
    score = s.IntegerField(min_value=0, max_value=100, allow_null=True)
    score_reasons = s.ListField(child=s.DictField(), allow_empty=False)
    score_version = s.CharField(max_length=100)
    scored_at = s.DateTimeField()
    score_details = s.DictField(required=False)

    # Function: Check whether contribution explanation reconciles with score.
    # Inputs: `attrs` is score payload.
    # Outputs: attrs; mismatch raises ValidationError.
    # Logic: Scores require equal contribution sum, empty scores require only insufficient_data, and formal version additionally checks three integer contributions and explanation.
    # Constraints: Does not fill empty score with zero or modify score; rejects non-finite contributions and save transaction validates sources.
    def validate(self, attrs):
        from math import isfinite
        from .priority_results import validate_priority_score

        reasons = attrs["score_reasons"]
        for item in reasons:
            if set(item) != {"feature", "contribution", "note"} or not isinstance(item["feature"], str) or not isinstance(item["note"], str) or type(item["contribution"]) not in (int, float):
                raise s.ValidationError("评分原因必须包含 feature、数值 contribution 和 note。")
            if type(item["contribution"]) is float and not isfinite(item["contribution"]):
                raise s.ValidationError("评分贡献必须为有限数值。")
        if attrs["score"] is None:
            if len(reasons) != 1 or reasons[0]["feature"] != "insufficient_data" or reasons[0]["contribution"] != 0:
                raise s.ValidationError("空分仅允许 insufficient_data 原因。")
        elif abs(sum(item["contribution"] for item in reasons) - attrs["score"]) > 0.00001:
            raise s.ValidationError("贡献和必须等于 score。")
        validate_priority_score(attrs)
        return attrs


# Function: Declare optimistic-lock writes for synchronization cursor.
# Logic: Save business cursor and do not accept Gmail credentials.
# Constraints: HTTP If-Match supplies expected_version.
class SyncStateSerializer(StrictSerializer):
    mailbox_id = s.UUIDField()
    cursor = s.CharField(allow_null=True, allow_blank=True)
    scope = s.DictField()
    last_synced_at = s.DateTimeField(allow_null=True)
    status = s.ChoiceField(choices=["ok", "resync_required", "authorization_required"])
    version = s.IntegerField(min_value=0)


# Function: Declare job-claim request.
# Logic: Limit batch claim count and require request to explicitly provide lease seconds.
# Constraints: Does not define automatic retry policy.
class ClaimSerializer(StrictSerializer):
    limit = s.IntegerField(min_value=1, max_value=50)
    lease_seconds = s.IntegerField(min_value=10, max_value=600)


# Function: Declare job completion report.
# Logic: Retain JobReport fields and put lease credential in HTTP headers.
# Constraints: Reports do not overwrite expired jobs.
class JobReportSerializer(StrictSerializer):
    job_id = s.UUIDField()
    status = s.ChoiceField(choices=["completed", "failed", "skipped"])
    input_version = s.CharField(allow_null=True)
    produced = s.DictField()
    error = s.DictField(allow_null=True)
    duration_ms = s.IntegerField(min_value=0)


# Function: Declare explicit CRM record-creation input.
# Logic: Accept company name, industry, sourced employee count, and optional authoritative country or region; do not complete unknown region from email.
# Constraints: Does not fabricate quotes or orders; business snapshots submit through separate protocol.
class RegisterSerializer(StrictSerializer):
    company_name = s.CharField(max_length=240)
    industry_from_crm = s.ChoiceField(choices=INDUSTRIES)
    employee_count = s.IntegerField(min_value=0, allow_null=True)
    employee_count_source = s.CharField(allow_null=True)
    country = s.CharField(max_length=100, allow_null=True, required=False)


# Function: Declare sample email manually entered by frontend.
# Logic: Rules extraction processes this explicit sample entry point only.
# Constraints: Does not disguise input as real Gmail synchronization.
class SimulateSerializer(StrictSerializer):
    mailbox_id = s.UUIDField()
    sender = s.EmailField()
    subject = s.CharField(max_length=1000)
    body_text = s.CharField(max_length=200000, trim_whitespace=False)


# Function: Declare business-mailbox creation.
# Logic: Save display address only.
# Constraints: Real Gmail connection must add OAuth verified binding first.
class MailboxSerializer(StrictSerializer):
    address = s.EmailField()


# Function: Declare one-shot synchronization claim count for web-authorized mailbox.
# Logic: Agent claims only a limited number each time and exits after processing.
# Constraints: Contains no lease or long-running Worker parameters.
class MailboxSyncClaimSerializer(StrictSerializer):
    limit = s.IntegerField(min_value=1, max_value=10, default=5)


# Function: Declare Agent terminal report for employee Gmail synchronization task.
# Logic: Save summary result and permit Agent to return refreshed Google credential.
# Constraints: authorization never returns through browser interfaces.
class MailboxSyncReportSerializer(StrictSerializer):
    mailbox_id = s.UUIDField()
    status = s.ChoiceField(choices=["completed", "failed"])
    sync_result = s.DictField(required=False, default=dict)
    error = s.CharField(allow_null=True, required=False, default=None)
    authorization = s.DictField(allow_null=True, required=False, default=None)
