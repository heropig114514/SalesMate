"""职责：校验 README v1.11 业务载荷及浏览器请求。
实现：显式声明协议字段并校验事实证据、状态与分数一致性；服务层再校验归属与来源范围。
关联：API 与规则占位共用校验，OpenAPI 以这些声明生成。
目录：
- StrictSerializer：严格拒绝未声明字段，避免授权令牌或拼错字段被静默接收。
- StrictSerializer.to_internal_value：校验输入对象及其字段集合。
- EmailSubmissionSerializer：声明单封邮件标准载荷。
- EmailSubmissionSerializer.get_fields：增加 Python 保留字 from 对应的协议字段。
- EmailSubmissionSerializer.validate：核对邮件天然键、抽取状态和逐字证据。
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
- FeatureValueField：声明整数或 unknown 的协议联合类型。
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
变量索引：
- AnalysisInputSerializer.built_at：L2 快照构建时间
- AnalysisInputSerializer.company_id：后端分配的公司 UUID
- AnalysisInputSerializer.external_snapshot_version：后端 CRM 快照版本的原样回显
- AnalysisInputSerializer.facts：可定位的事实结构，失败时按协议为 null
- AnalysisInputSerializer.input_version：Agent 计算并原样提交的输入版本
- AnalysisInputSerializer.member_dedupe_keys：参与本次分析的完整邮件去重键集合
- AnalysisInputSerializer.merge_version：L2 确定性归并规则版本
- AnalysisInputSerializer.metrics：L2 往来计数、时间间隔和 CRM 状态
- AnalysisInputSerializer.unparsed_message_count：未完成抽取的邮件数量
- AnalysisSerializer.analysis_base_time：允许参与分析的业务事实时间上界
- AnalysisSerializer.analysis_prompt_version：L3 模型提示词或规则生产者版本
- AnalysisSerializer.company_id：后端分配的公司 UUID
- AnalysisSerializer.context_completeness：未解析邮件数和上下文不完整说明
- AnalysisSerializer.detail_view：三维画像与四维分析的详情投影
- AnalysisSerializer.error：显式失败说明，不作为成功结果展示
- AnalysisSerializer.generated_at：分析生成时间，前端据此标示旧结果
- AnalysisSerializer.input_version：Agent 计算并原样提交的输入版本
- AnalysisSerializer.list_view：公司列表的轻量分析投影
- AnalysisSerializer.missing_fields：该维度或全局仍缺少的信息
- AnalysisSerializer.status：当前协议载荷或任务状态，具体允许值见字段声明
- ClaimSerializer.lease_seconds：调用方显式指定的租期秒数，10–600
- ClaimSerializer.limit：单批领取任务数量，1–50
- ConflictSerializer.field：发生变化或冲突的事实字段
- ConflictSerializer.kind：value_changed 或 source_disagree 冲突类型
- ConflictSerializer.source_refs：可定位的邮件或业务记录 ID 数组
- ConflictSerializer.summary：变化或冲突的文字解释
- DetailSerializer.analysis：评分所属分析外键；序列化器中为四维分析结果
- DetailSerializer.conflicts：至少引用两个来源的冲突与变化清单
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
- EmailSubmissionSerializer.dedupe_key：邮箱 ID 与 Gmail 消息 ID 组成的天然幂等键
- EmailSubmissionSerializer.direction：邮件入站 inbound 或出站 outbound
- EmailSubmissionSerializer.extract_error：抽取失败摘要，成功时为空
- EmailSubmissionSerializer.extract_prompt_version：L1 提示词或规则版本
- EmailSubmissionSerializer.extract_status：单封抽取的完成、失败或非业务跳过状态
- EmailSubmissionSerializer.facts：可定位的事实结构，失败时按协议为 null
- EmailSubmissionSerializer.gmail_message_id：Gmail 原始消息标识，模拟数据使用独立样例标识
- EmailSubmissionSerializer.mailbox_id：后端分配的业务邮箱 UUID
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
- FACT_FIELDS：L1 中 value/evidence 事实字段名称集合
- FactsResubmissionSerializer.dedupe_key：邮箱 ID 与 Gmail 消息 ID 组成的天然幂等键
- FactsResubmissionSerializer.extract_error：抽取失败摘要，成功时为空
- FactsResubmissionSerializer.extract_prompt_version：L1 提示词或规则版本
- FactsResubmissionSerializer.extract_status：单封抽取的完成、失败或非业务跳过状态
- FactsResubmissionSerializer.facts：可定位的事实结构，失败时按协议为 null
- FeatureSerializer.basis：推断或特征判断的依据说明
- FeatureSerializer.value：可空分值或整数/unknown 特征值
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
    # 逻辑：仅接收字典并排除不在声明中的字段。
    # 约束：无数据库或日志副作用。
    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields):
            raise s.ValidationError("必须为对象，且不得包含未声明字段。")
        return super().to_internal_value(data)


# 功能：声明单封邮件标准载荷。
# 逻辑：将 from 映射回原始 JSON 名称，要求实际时间、方向与来源。
# 约束：授权邮箱所有权在服务层验证，不信任载荷自报身份。
class EmailSubmissionSerializer(StrictSerializer):
    dedupe_key = s.CharField(max_length=400)
    mailbox_id = s.UUIDField()
    gmail_message_id = s.CharField(max_length=200)
    thread_id = s.CharField(max_length=200)
    to = s.ListField(child=s.EmailField(), allow_empty=False)
    cc = s.ListField(child=s.EmailField())
    sent_at = s.DateTimeField()
    received_at = s.DateTimeField()
    subject = s.CharField(max_length=1000, allow_blank=True)
    body_text = s.CharField(max_length=200000, allow_blank=True, trim_whitespace=False)
    direction = s.ChoiceField(choices=["inbound", "outbound"])
    source = s.ChoiceField(choices=["gmail_real", "synthetic_sample", "research_dataset", "simulated"])
    contact_email = s.EmailField()
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
        fields["from"] = s.EmailField()
        return fields

    # 功能：核对邮件天然键、抽取状态和逐字证据。
    # 输入：`attrs` 为标准化字段，包括正文与 facts。
    # 输出：原 attrs；契约不符时抛 ValidationError。
    # 逻辑：键由邮箱与消息 ID 拼接；已完成事实逐项校验。
    # 约束：不调用模型、不更改事实或补齐未知值。
    def validate(self, attrs):
        if attrs["dedupe_key"] != f"{attrs['mailbox_id']}:{attrs['gmail_message_id']}":
            raise s.ValidationError("dedupe_key 必须为 mailbox_id:gmail_message_id。")
        validate_extraction(attrs, attrs["body_text"] + "\n" + attrs["subject"])
        if attrs["extract_status"] == "skipped_non_business" and not attrs["non_business_hint"]:
            raise s.ValidationError("跳过非业务邮件必须附 non_business_hint。")
        return attrs


# 功能：校验抽取事实的完整字段及可定位证据。
# 输入：`data` 包含 extract_status、facts、extract_error；`text` 为原始正文和主题。
# 输出：无返回值；不符合契约抛 ValidationError。
# 逻辑：完成状态要求全部事实字段，未知值与证据同时为空；非空证据必须逐字存在。
# 约束：仅验证可定位性，不声称证明模型语义正确。
def validate_extraction(data, text):
    facts = data["facts"]
    if data["extract_status"] != "completed":
        if facts is not None or (data["extract_status"] == "failed" and not data["extract_error"]):
            raise s.ValidationError("失败需错误摘要，未完成抽取 facts 必须为 null。")
        return
    required = set(FACT_FIELDS) | {"has_substantive_update", "message_summary", "intent_hint", "intent_evidence"}
    if not isinstance(facts, dict) or set(facts) != required or data["extract_error"] is not None:
        raise s.ValidationError("完成抽取必须包含完整 facts 且 extract_error 为 null。")
    if type(facts["has_substantive_update"]) is not bool or not isinstance(facts["message_summary"], str) or len(facts["message_summary"]) > 80:
        raise s.ValidationError("实质更新必须为布尔值，摘要不得超过 80 字。")
    if facts["intent_hint"] not in ["purchase_inquiry", "meeting", "support", "non_sales", "unknown"]:
        raise s.ValidationError("intent_hint 无效。")
    evidence = facts["intent_evidence"]
    if evidence is not None and (not isinstance(evidence, str) or not evidence or evidence not in text):
        raise s.ValidationError("意向证据必须可定位。")
    for field in FACT_FIELDS:
        item = facts[field]
        if not isinstance(item, dict) or set(item) != {"value", "evidence"}:
            raise s.ValidationError(f"{field} 必须包含 value 和 evidence。")
        if item["value"] is None and item["evidence"] is None:
            continue
        if not isinstance(item["value"], str) or not item["value"] or not isinstance(item["evidence"], str) or not item["evidence"] or item["evidence"] not in text:
            raise s.ValidationError(f"{field} 的值与原文证据无效。")


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


# 功能：声明整数或 unknown 的协议联合类型。
# 逻辑：使用显式 oneOf 同时描述数字与未知枚举，避免将二者强制转换成字符串。
# 约束：布尔值不作为整数特征接收。
@extend_schema_field({"oneOf": [{"type": "integer", "minimum": 0, "maximum": 3}, {"type": "string", "enum": ["unknown"]}]})
class FeatureValueField(s.Field):
    # 功能：验证评分特征的联合类型。
    # 输入：`data` 为原始 JSON 数值或 unknown。
    # 输出：原值；类型或范围错误抛 ValidationError。
    # 逻辑：只接收真正整数 0–3 或精确 unknown 字符串。
    # 约束：不把缺失字段默认为零。
    def to_internal_value(self, data):
        if (type(data) is int and 0 <= data <= 3) or data == "unknown":
            return data
        raise s.ValidationError("特征必须为 0–3 整数或 unknown。")

    # 功能：输出已经验证的特征值。
    # 输入：`value` 为整数或 unknown。
    # 输出：同类型 JSON 原值。
    # 逻辑：不进行字符串化或舍入。
    # 约束：数据应已通过输入校验。
    def to_representation(self, value):
        return value


# 功能：声明 L4 使用的单个特征。
# 逻辑：数值限定 0 至 3，unknown 表示没有依据。
# 约束：unknown 不等同于零。
class FeatureSerializer(StrictSerializer):
    value = FeatureValueField()
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
    missing_fields = s.ListField(child=s.CharField(), required=False)
    context_completeness = s.DictField(required=False)
    error = s.DictField(allow_null=True, required=False)

    # 功能：检查分析状态与载荷的对应关系。
    # 输入：`attrs` 为已校验字段。
    # 输出：attrs；状态矛盾抛 ValidationError。
    # 逻辑：成功要求完整结果，失败要求错误对象且两段为空。
    # 约束：不自动将失败转换为规则输出。
    def validate(self, attrs):
        if attrs["status"] == "completed":
            if attrs["list_view"] is None or attrs["detail_view"] is None or "missing_fields" not in attrs or "context_completeness" not in attrs or attrs.get("error"):
                raise s.ValidationError("成功分析必须包含完整结果、缺失项和上下文说明。")
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
