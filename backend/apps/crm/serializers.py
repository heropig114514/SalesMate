"""职责：校验当前 Agent README 业务载荷及浏览器请求。
实现：显式声明协议字段并校验事实证据、状态与分数一致性；服务层再校验归属与来源范围。
关联：API 与规则占位共用校验，OpenAPI 以这些声明生成。
目录：
- StrictSerializer：严格拒绝未声明字段，避免授权令牌或拼错字段被静默接收。
- StrictSerializer.to_internal_value：校验输入对象及其字段集合。
- EmailSubmissionSerializer：声明单封邮件标准载荷。
- EmailSubmissionSerializer.get_fields：增加 Python 保留字 from 对应的协议字段。
- EmailSubmissionSerializer.validate：核对邮件天然键、抽取状态和逐字证据。
- compact_evidence_text：移除证据定位允许忽略的空白和格式字符。
- evidence_is_locatable：按 Agent 相同规则判断证据是否来自主题或正文。
- validate_extraction：校验抽取事实的完整字段及可定位证据。
- FactsResubmissionSerializer：声明失败事实补交载荷。
- AnalysisInputSerializer：声明 Agent 原样归档的 L2 输入。
- EvidenceSerializer：声明带来源的事实或解释证据。
- InferenceSerializer：声明与事实分离的推断。
- DimensionSerializer：声明七个分析维度共用结构。
- ProfileSerializer：声明三维客户画像。
- DimensionsSerializer：声明四维客户分析。
- ConflictSerializer：声明事实冲突或明确变更。
- DetailSerializer：声明详情输出结构。
- FeatureValueField：声明可空的 0–3 整数特征值。
- FeatureValueField.to_internal_value：验证评分特征的联合类型。
- FeatureValueField.to_representation：输出已经验证的特征值。
- FeatureSerializer：声明 L4 使用的单个特征。
- FeaturesSerializer：声明 L4 三个模型特征。
- ListViewSerializer：声明公司列表投影。
- AnalysisSerializer：声明 L3 分析整体输出。
- AnalysisSerializer.validate：检查分析状态与载荷的对应关系。
- ScoreSerializer：声明 L4 分数与贡献说明。
- ScoreSerializer.validate：检查贡献解释能否与分数对账。
- SyncStateSerializer：声明同步游标的乐观锁写入。
- ClaimSerializer：声明任务领取请求。
- JobReportSerializer：声明任务完成回报。
- RegisterSerializer：声明显式 CRM 建档输入。
- SimulateSerializer：声明前端手工输入的模拟邮件。
- MailboxSerializer：声明邮件业务邮箱创建。
- MailboxSyncClaimSerializer：声明员工邮箱同步领取数量。
- MailboxSyncReportSerializer：声明员工邮箱同步最终回报。
变量索引：
- AnalysisInputSerializer.built_at：L2 快照构建时间
- AnalysisInputSerializer.business_context：后端客户、工单、报价和订单快照
- AnalysisInputSerializer.company：公司、域名和联系人归组快照
- AnalysisInputSerializer.company_id：后端分配的公司 UUID
- AnalysisInputSerializer.external_snapshot_version：后端 CRM 快照版本的原样回显
- AnalysisInputSerializer.facts：可定位的事实结构，失败时按协议为 null
- AnalysisInputSerializer.input_version：Agent 计算并原样提交的输入版本
- AnalysisInputSerializer.latest_message_summary：最近一封已完成抽取邮件的摘要
- AnalysisInputSerializer.member_dedupe_keys：参与本次分析的完整邮件去重键集合
- AnalysisInputSerializer.merge_version：L2 确定性归并规则版本
- AnalysisInputSerializer.metrics：L2 往来计数、时间间隔和 CRM 状态
- AnalysisInputSerializer.unparsed_message_count：未完成抽取的邮件数量
- AnalysisSerializer.analysis_base_time：允许参与分析的业务事实时间上界
- AnalysisSerializer.analysis_prompt_version：L3 模型提示词或规则生产者版本
- AnalysisSerializer.company_id：后端分配的公司 UUID
- AnalysisSerializer.detail_view：三维画像与四维分析的详情投影
- AnalysisSerializer.error：显式失败说明，不作为成功结果展示
- AnalysisSerializer.generated_at：分析生成时间，前端据此标示旧结果
- AnalysisSerializer.input_version：Agent 计算并原样提交的输入版本
- AnalysisSerializer.list_view：公司列表的轻量分析投影
- AnalysisSerializer.status：当前协议载荷或任务状态，具体允许值见字段声明
- ClaimSerializer.lease_seconds：调用方显式指定的租期秒数，10–600
- ClaimSerializer.limit：单批领取任务数量，1–50
- ConflictSerializer.field：发生变化或冲突的事实字段
- ConflictSerializer.kind：value_changed 或 source_disagree 冲突类型
- ConflictSerializer.source_refs：可定位的邮件或业务记录 ID 数组
- ConflictSerializer.summary：变化或冲突的文字解释
- DetailSerializer.analysis：评分所属分析外键；序列化器中为四维分析结果
- DetailSerializer.conflicts：至少引用两个来源的冲突与变化清单
- DetailSerializer.context_completeness：未解析邮件数和上下文不完整说明
- DetailSerializer.missing_fields：分析所需但当前上下文没有提供的信息
- DetailSerializer.profile：三维客户画像
- DimensionSerializer.facts：可定位的事实结构，失败时按协议为 null
- DimensionSerializer.inferences：与事实分开保存的有依据推断
- DimensionSerializer.missing_fields：该维度或全局仍缺少的信息
- DimensionsSerializer.guidance：客户分析中的下一步引导维度
- DimensionsSerializer.opportunity：客户分析中的商机与需求维度
- DimensionsSerializer.risk：客户分析中的风险和约束维度
- DimensionsSerializer.timeline：客户分析中的历史时间轴
- EmailSubmissionSerializer.body_text：保留逐字证据的原始纯文本正文
- EmailSubmissionSerializer.cc：完整抄送人邮箱数组
- EmailSubmissionSerializer.contact_email：L1 提交的主要外部联系人邮箱
- EmailSubmissionSerializer.dedupe_key：邮箱地址与 Gmail 消息 ID 组成的天然幂等键
- EmailSubmissionSerializer.direction：邮件入站 inbound、出站 outbound 或未知 unknown
- EmailSubmissionSerializer.extract_error：抽取失败摘要，成功时为空
- EmailSubmissionSerializer.extract_prompt_version：L1 提示词或规则版本
- EmailSubmissionSerializer.extract_status：单封抽取的完成、失败或非业务跳过状态
- EmailSubmissionSerializer.facts：可定位的事实结构，失败时按协议为 null
- EmailSubmissionSerializer.gmail_message_id：Gmail 原始消息标识，模拟数据使用独立样例标识
- EmailSubmissionSerializer.mailbox_id：后端分配的业务邮箱 UUID
- EmailSubmissionSerializer.mailbox_address：已授权 Gmail 邮箱地址，也是 dedupe_key 的组成部分
- EmailSubmissionSerializer.non_business_hint：疑似非业务邮件标记
- EmailSubmissionSerializer.non_business_reason：非业务邮件判断依据
- EmailSubmissionSerializer.received_at：邮件接收时间，用于今日新邮件统计
- EmailSubmissionSerializer.sent_at：邮件或业务动作发生的带时区时间
- EmailSubmissionSerializer.source：真实、合成、研究或模拟数据来源标记
- EmailSubmissionSerializer.subject：邮件原始主题
- EmailSubmissionSerializer.thread_id：同线程邮件与响应间隔配对标识
- EmailSubmissionSerializer.to：完整收件人邮箱数组
- EvidenceSerializer.source_refs：可定位的邮件或业务记录 ID 数组
- EvidenceSerializer.text：事实或证据解释文本
- FACT_FIELDS：L1 中多值事实字段名称集合
- FactsResubmissionSerializer.dedupe_key：邮箱地址与 Gmail 消息 ID 组成的天然幂等键
- FactsResubmissionSerializer.extract_error：抽取失败摘要，成功时为空
- FactsResubmissionSerializer.extract_prompt_version：L1 提示词或规则版本
- FactsResubmissionSerializer.extract_status：单封抽取的完成、失败或非业务跳过状态
- FactsResubmissionSerializer.facts：可定位的事实结构，失败时按协议为 null
- FeatureSerializer.basis：推断或特征判断的依据说明
- FeatureSerializer.value：可空的 0–3 整数特征值
- FeaturesSerializer.decision_visibility：决策流程可见性特征
- FeaturesSerializer.demand_clarity：需求明确程度特征
- FeaturesSerializer.urgency：需求紧迫性特征
- INDUSTRIES：产品四个行业及 unknown 枚举
- InferenceSerializer.basis：推断或特征判断的依据说明
- InferenceSerializer.confidence：low/medium/high 推断置信等级，不是成交百分比
- JobReportSerializer.duration_ms：任务处理耗时毫秒
- JobReportSerializer.error：显式失败说明，不作为成功结果展示
- JobReportSerializer.input_version：Agent 计算并原样提交的输入版本
- JobReportSerializer.job_id：后端任务 UUID
- JobReportSerializer.produced：实际产出的分析、评分与邮件数量声明
- JobReportSerializer.status：当前协议载荷或任务状态，具体允许值见字段声明
- ListViewSerializer.headline_summary：列表最新消息摘要
- ListViewSerializer.industry：列表行业枚举
- ListViewSerializer.industry_evidence：行业判定的事实依据和来源
- ListViewSerializer.score_features：供 L4 使用的三个判断特征
- ListViewSerializer.signal：公司主业务信号
- ListViewSerializer.signal_evidence：信号判断的依据与来源
- ListViewSerializer.size_band：员工人数展示档位，未知保持 unknown
- ListViewSerializer.size_source：权威人数记录的来源说明
- ListViewSerializer.ticket_signals：逐工单信号数组，不从邮件伪造工单
- MailboxSerializer.address：业务邮箱展示地址，不作为 OAuth 验证证据
- MailboxSyncClaimSerializer.limit：一次性 Agent 领取员工邮箱同步请求的上限
- MailboxSyncReportSerializer.authorization：Agent 刷新后的 Google authorized user JSON
- MailboxSyncReportSerializer.error：邮箱同步失败的可显示错误
- MailboxSyncReportSerializer.mailbox_id：本次同步对应的员工邮箱 UUID
- MailboxSyncReportSerializer.status：completed 或 failed 同步状态
- MailboxSyncReportSerializer.sync_result：GmailSyncResult 汇总
- ProfileSerializer.company_ops：客户画像中的经营与决策情况维度
- ProfileSerializer.industry_context：客户画像中的行业情况维度
- ProfileSerializer.intent：客户画像中的采购意向维度
- RegisterSerializer.company_name：公司展示名，不参与自动归组
- RegisterSerializer.employee_count：有来源的准确员工人数，未知保持 null
- RegisterSerializer.employee_count_source：权威员工人数的事实来源
- RegisterSerializer.industry_from_crm：用户确认的 CRM 行业
- SIGNALS：公司业务信号枚举
- SIZES：不重叠的人数档位及 unknown 枚举
- ScoreSerializer.company_id：后端分配的公司 UUID
- ScoreSerializer.input_version：Agent 计算并原样提交的输入版本
- ScoreSerializer.score：0–100 跟进优先级，缺失资料时为 null
- ScoreSerializer.score_reasons：评分贡献与解释，贡献和须等于 score
- ScoreSerializer.score_version：评分规则版本，规则占位与正式 Agent 版本分开
- ScoreSerializer.scored_at：该次评分的计算时间
- SimulateSerializer.body_text：保留逐字证据的原始纯文本正文
- SimulateSerializer.mailbox_id：后端分配的业务邮箱 UUID
- SimulateSerializer.sender：模拟来信的发送人邮箱
- SimulateSerializer.subject：邮件原始主题
- SyncStateSerializer.cursor：Gmail 同步游标，不包含访问凭证
- SyncStateSerializer.last_synced_at：最近成功同步时间
- SyncStateSerializer.mailbox_id：后端分配的业务邮箱 UUID
- SyncStateSerializer.scope：显式同步标签与历史起点范围
- SyncStateSerializer.status：当前协议载荷或任务状态，具体允许值见字段声明
- SyncStateSerializer.version：同步状态的乐观锁版本
"""
import unicodedata

from rest_framework import serializers as s
from drf_spectacular.utils import extend_schema_field

FACT_FIELDS = ("contact_name", "contact_title", "company_self_reported", "business_background",
               "employee_scale_hint", "product_need", "quantity", "budget", "delivery_time",
               "decision_process", "concerns", "quote_reference", "order_reference")
SIGNALS = ("repeat_purchase", "quoted_not_closed", "inquiry_intent", "new_lead_no_profile", "unknown")
INDUSTRIES = ("半导体检测", "精密量测", "光学检测", "工业检测", "unknown")
SIZES = ("lt_50", "50_100", "100_200", "200_500", "gte_500", "unknown")


# 功能：严格拒绝未声明字段，避免授权令牌或拼错字段被静默接收。
# 逻辑：在 DRF 常规校验前比较字段集合。
# 约束：JSON 扩展字段的内部结构由其专门校验负责。
class StrictSerializer(s.Serializer):
    # 功能：校验输入对象及其字段集合。
    # 输入：`data` 为客户端原始 JSON。
    # 输出：DRF 验证后的字段字典；未知字段抛 ValidationError。
    # 逻辑：仅接收字典并排除未声明字段；对象级错误使用 DRF non_field_errors 字典以正确返回 400。
    # 约束：无数据库或日志副作用。
    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields):
            raise s.ValidationError({s.api_settings.NON_FIELD_ERRORS_KEY: ["必须为对象，且不得包含未声明字段。"]})
        return super().to_internal_value(data)


# 功能：声明单封邮件标准载荷。
# 逻辑：将 from 映射回原始 JSON 名称，要求实际时间、方向与来源；qq_real 标记 QQ IMAP 原文。
# 约束：授权邮箱所有权在服务层验证，不信任载荷自报身份。
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

    # 功能：增加 Python 保留字 from 对应的协议字段。
    # 输入：实例声明的字段配置，无外部参数。
    # 输出：含 from 的字段映射。
    # 逻辑：扩展父类复制出的字段，不修改全局声明。
    # 约束：Schema 与实际校验共用该映射。
    def get_fields(self):
        fields = super().get_fields()
        fields["from"] = s.EmailField(allow_null=True)
        return fields

    # 功能：核对邮件天然键、抽取状态和逐字证据。
    # 输入：`attrs` 为标准化字段，包括正文与 facts。
    # 输出：原 attrs；契约不符时抛 ValidationError。
    # 逻辑：键由邮箱与消息 ID 拼接；已完成事实逐项校验。
    # 约束：不调用模型、不更改事实或补齐未知值。
    def validate(self, attrs):
        expected_key = f"{attrs['mailbox_address'].casefold()}:{attrs['gmail_message_id']}"
        if attrs["dedupe_key"] != expected_key:
            raise s.ValidationError("dedupe_key 必须为 mailbox_address:gmail_message_id。")
        validate_extraction(attrs, attrs["subject"], attrs["body_text"])
        if attrs["extract_status"] == "skipped_non_business" and not attrs["non_business_hint"]:
            raise s.ValidationError("跳过非业务邮件必须附 non_business_hint。")
        return attrs


# 功能：移除证据定位允许忽略的空白和格式字符。
# 输入：`value` 为证据或邮件原文字符串。
# 输出：保留所有可见字符原顺序的紧凑字符串。
# 逻辑：移除 isspace 字符和 Unicode Cf 格式字符。
# 约束：不执行大小写、标点、NFKC 或全半角转换。
def compact_evidence_text(value):
    return "".join(
        character
        for character in value
        if not character.isspace() and unicodedata.category(character) != "Cf"
    )


# 功能：按 Agent 相同规则判断证据是否来自主题或正文。
# 输入：`evidence` 为模型证据；`subject` 和 `body_text` 为当前邮件边界。
# 输出：精确匹配，或仅移除空白与 Unicode 格式字符后匹配时返回 True。
# 逻辑：先检查逐字子串，再分别压缩证据、主题和正文；不跨主题/正文边界拼接。
# 约束：不做 NFKC、大小写、标点或全半角转换，避免接受模型改写。
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


# 功能：校验抽取事实的完整字段及可定位证据。
# 输入：`data` 包含 extract_status、facts、extract_error；`subject` 和 `body_text` 为当前邮件原文。
# 输出：无返回值；不符合契约抛 ValidationError。
# 逻辑：完成状态要求全部事实字段；证据按 Agent 允许的空白和不可见格式差异定位。
# 约束：仅验证可定位性，不声称证明模型语义正确。
def validate_extraction(data, subject, body_text):
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
    if facts["intent_hint"] not in ["purchase_inquiry", "meeting", "support", "non_sales", "unknown"]:
        raise s.ValidationError("intent_hint 无效。")
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


# 功能：声明失败事实补交载荷。
# 逻辑：仅允许提交成功状态，正文证据从已存邮件校验。
# 约束：失败到成功的一次转换由事务服务执行。
class FactsResubmissionSerializer(StrictSerializer):
    dedupe_key = s.CharField()
    extract_prompt_version = s.CharField(max_length=100)
    extract_status = s.ChoiceField(choices=["completed"])
    extract_error = s.CharField(allow_null=True)
    facts = s.JSONField()


# 功能：声明 Agent 原样归档的 L2 输入。
# 逻辑：保留规范字段，JSON 事实与指标在服务层核对来源。
# 约束：不替 Agent 计算 input_version。
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


# 功能：声明带来源的事实或解释证据。
# 逻辑：事实要求非空引用，服务层检查引用属于当前快照。
# 约束：文本本身不代表经过人工真实性审核。
class EvidenceSerializer(StrictSerializer):
    text = s.CharField()
    source_refs = s.ListField(child=s.CharField(), allow_empty=False)


# 功能：声明与事实分离的推断。
# 逻辑：保留依据、置信等级和来源。
# 约束：置信等级不映射为成交百分比。
class InferenceSerializer(EvidenceSerializer):
    basis = s.CharField()
    confidence = s.ChoiceField(choices=["low", "medium", "high"])


# 功能：声明七个分析维度共用结构。
# 逻辑：明确分开事实、推断与缺失信息。
# 约束：空数组是合法未知结果。
class DimensionSerializer(StrictSerializer):
    facts = EvidenceSerializer(many=True)
    inferences = InferenceSerializer(many=True)
    missing_fields = s.ListField(child=s.CharField())


# 功能：声明三维客户画像。
# 逻辑：字段名沿用 README，不增加概率字段。
# 约束：缺失维度校验失败。
class ProfileSerializer(StrictSerializer):
    industry_context = DimensionSerializer()
    company_ops = DimensionSerializer()
    intent = DimensionSerializer()


# 功能：声明四维客户分析。
# 逻辑：分开时间轴、商机、风险与引导建议。
# 约束：不自动填充模型内容。
class DimensionsSerializer(StrictSerializer):
    timeline = DimensionSerializer()
    opportunity = DimensionSerializer()
    risk = DimensionSerializer()
    guidance = DimensionSerializer()


# 功能：声明事实冲突或明确变更。
# 逻辑：至少引用两个来源。
# 约束：不能将一般多值事实自动认定为冲突。
class ConflictSerializer(StrictSerializer):
    field = s.ChoiceField(choices=FACT_FIELDS)
    kind = s.ChoiceField(choices=["value_changed", "source_disagree"])
    summary = s.CharField()
    source_refs = s.ListField(child=s.CharField(), min_length=2)


# 功能：声明详情输出结构。
# 逻辑：组合三维画像、四维分析和冲突说明。
# 约束：来源范围在保存结果前验证。
class DetailSerializer(StrictSerializer):
    conflicts = ConflictSerializer(many=True)
    profile = ProfileSerializer()
    analysis = DimensionsSerializer()
    missing_fields = s.ListField(child=s.CharField())
    context_completeness = s.DictField()


# 功能：声明可空的 0–3 整数特征值。
# 逻辑：保留 JSON null 表示信息不足，不把缺失值强制转换成数字。
# 约束：布尔值不作为整数特征接收。
@extend_schema_field({"type": "integer", "minimum": 0, "maximum": 3, "nullable": True})
class FeatureValueField(s.Field):
    # 功能：验证评分特征的联合类型。
    # 输入：`data` 为原始 JSON 整数；JSON null 由字段的 allow_null 处理。
    # 输出：原值；类型或范围错误抛 ValidationError。
    # 逻辑：只接收真正整数 0–3。
    # 约束：不把缺失字段默认为零。
    def to_internal_value(self, data):
        if type(data) is int and 0 <= data <= 3:
            return data
        raise s.ValidationError("特征必须为 0–3 整数或 null。")

    # 功能：输出已经验证的特征值。
    # 输入：`value` 为 0–3 整数。
    # 输出：同类型 JSON 原值。
    # 逻辑：不进行字符串化或舍入。
    # 约束：数据应已通过输入校验。
    def to_representation(self, value):
        return value


# 功能：声明 L4 使用的单个特征。
# 逻辑：数值限定 0 至 3，JSON null 表示没有依据。
# 约束：null 不等同于零。
class FeatureSerializer(StrictSerializer):
    value = FeatureValueField(allow_null=True)
    basis = s.CharField()


# 功能：声明 L4 三个模型特征。
# 逻辑：字段沿用 README。
# 约束：全部字段必填。
class FeaturesSerializer(StrictSerializer):
    demand_clarity = FeatureSerializer()
    urgency = FeatureSerializer()
    decision_visibility = FeatureSerializer()


# 功能：声明公司列表投影。
# 逻辑：限制产品枚举并保留依据。
# 约束：信号来源的业务门槛由结果服务校验。
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


# 功能：声明 L3 分析整体输出。
# 逻辑：失败时轻重段为空，成功时保留完整七维结果。
# 约束：时间边界、引用与业务状态在事务服务校验。
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

    # 功能：检查分析状态与载荷的对应关系。
    # 输入：`attrs` 为已校验字段。
    # 输出：attrs；状态矛盾抛 ValidationError。
    # 逻辑：成功要求完整结果，失败要求错误对象且两段为空。
    # 约束：不自动将失败转换为规则输出。
    def validate(self, attrs):
        if attrs["status"] == "completed":
            if attrs["list_view"] is None or attrs["detail_view"] is None or attrs.get("error"):
                raise s.ValidationError("成功分析必须包含完整 list_view 和 detail_view。")
        elif attrs["list_view"] is not None or attrs["detail_view"] is not None or not attrs.get("error"):
            raise s.ValidationError("失败分析必须两段为空并包含 error。")
        return attrs


# 功能：声明 L4 分数与贡献说明。
# 逻辑：校验空分语义和贡献和。
# 约束：实际规则版本由生产者提交，不重写用户已定权重。
class ScoreSerializer(StrictSerializer):
    company_id = s.UUIDField()
    input_version = s.CharField(max_length=160)
    score = s.IntegerField(min_value=0, max_value=100, allow_null=True)
    score_reasons = s.ListField(child=s.DictField(), allow_empty=False)
    score_version = s.CharField(max_length=100)
    scored_at = s.DateTimeField()

    # 功能：检查贡献解释能否与分数对账。
    # 输入：`attrs` 为评分载荷。
    # 输出：attrs；不一致时抛 ValidationError。
    # 逻辑：有分值要求贡献和相等，无分值要求唯一 insufficient_data 原因。
    # 约束：不将空分填零，不修改分数。
    def validate(self, attrs):
        reasons = attrs["score_reasons"]
        for item in reasons:
            if set(item) != {"feature", "contribution", "note"} or not isinstance(item["feature"], str) or not isinstance(item["note"], str) or type(item["contribution"]) not in (int, float):
                raise s.ValidationError("评分原因必须包含 feature、数值 contribution 和 note。")
        if attrs["score"] is None:
            if len(reasons) != 1 or reasons[0]["feature"] != "insufficient_data" or reasons[0]["contribution"] != 0:
                raise s.ValidationError("空分仅允许 insufficient_data 原因。")
        elif abs(sum(item["contribution"] for item in reasons) - attrs["score"]) > 0.00001:
            raise s.ValidationError("贡献和必须等于 score。")
        return attrs


# 功能：声明同步游标的乐观锁写入。
# 逻辑：保存业务游标，不接收 Gmail 凭证。
# 约束：expected_version 通过 HTTP If-Match 提供。
class SyncStateSerializer(StrictSerializer):
    mailbox_id = s.UUIDField()
    cursor = s.CharField(allow_null=True, allow_blank=True)
    scope = s.DictField()
    last_synced_at = s.DateTimeField(allow_null=True)
    status = s.ChoiceField(choices=["ok", "resync_required", "authorization_required"])
    version = s.IntegerField(min_value=0)


# 功能：声明任务领取请求。
# 逻辑：限制批量领取数量，租约秒数由请求显式提供。
# 约束：不定义自动重试策略。
class ClaimSerializer(StrictSerializer):
    limit = s.IntegerField(min_value=1, max_value=50)
    lease_seconds = s.IntegerField(min_value=10, max_value=600)


# 功能：声明任务完成回报。
# 逻辑：字段沿用 JobReport；租约凭证置于 HTTP 头。
# 约束：回报不会覆盖过期任务。
class JobReportSerializer(StrictSerializer):
    job_id = s.UUIDField()
    status = s.ChoiceField(choices=["completed", "failed", "skipped"])
    input_version = s.CharField(allow_null=True)
    produced = s.DictField()
    error = s.DictField(allow_null=True)
    duration_ms = s.IntegerField(min_value=0)


# 功能：声明显式 CRM 建档输入。
# 逻辑：只接收公司名、行业和有来源的人数。
# 约束：不会虚构报价或订单，业务快照通过独立协议提交。
class RegisterSerializer(StrictSerializer):
    company_name = s.CharField(max_length=240)
    industry_from_crm = s.ChoiceField(choices=INDUSTRIES)
    employee_count = s.IntegerField(min_value=0, allow_null=True)
    employee_count_source = s.CharField(allow_null=True)


# 功能：声明前端手工输入的模拟邮件。
# 逻辑：规则抽取仅处理该显式模拟入口。
# 约束：不把输入伪装为真实 Gmail 同步。
class SimulateSerializer(StrictSerializer):
    mailbox_id = s.UUIDField()
    sender = s.EmailField()
    subject = s.CharField(max_length=1000)
    body_text = s.CharField(max_length=200000, trim_whitespace=False)


# 功能：声明邮件业务邮箱创建。
# 逻辑：仅保存展示地址。
# 约束：真实 Gmail 接入前须补 OAuth 验证绑定。
class MailboxSerializer(StrictSerializer):
    address = s.EmailField()


# 功能：声明网页授权邮箱的一次性同步领取数量。
# 逻辑：Agent 每次只领取有限数量，处理结束即退出。
# 约束：不包含租约或长期 Worker 参数。
class MailboxSyncClaimSerializer(StrictSerializer):
    limit = s.IntegerField(min_value=1, max_value=10, default=5)


# 功能：声明 Agent 对员工 Gmail 同步任务的最终回报。
# 逻辑：保存汇总结果，并允许 Agent 回传刷新后的 Google 凭证。
# 约束：authorization 不会经浏览器接口返回。
class MailboxSyncReportSerializer(StrictSerializer):
    mailbox_id = s.UUIDField()
    status = s.ChoiceField(choices=["completed", "failed"])
    sync_result = s.DictField(required=False, default=dict)
    error = s.CharField(allow_null=True, required=False, default=None)
    authorization = s.DictField(allow_null=True, required=False, default=None)
