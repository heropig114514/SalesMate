"""职责：验证商机结果的基本结构与关系，不验证算法正确性。
实现：仅要求有效商机；公司自动回填，分数和置信度检查边界，解释保持原始 JSON。
关联：通用资源、SDK/MCP 共用；从 sales.serializers 末尾注册。
目录：
- OpportunityResultSerializer：推导客户关系。
- OpportunityResultSerializer.validate：核对商机和公司一致性。
- OpportunitySignalSerializer：信号写入与读取。
- OpportunitySignalSerializer.Meta：信号字段。
- OpportunityPrioritySerializer：评分结果读写。
- OpportunityPrioritySerializer.Meta：结果字段。
变量索引：
- BASE_FIELDS：结果通用输出字段。
- READ_ONLY_FIELDS：服务器维护字段。
- OpportunityResultSerializer.company：只读派生公司。
- OpportunitySignalSerializer.confidence：可空 0–1 置信度。
- OpportunitySignalSerializer.Meta.model：信号模型。
- OpportunitySignalSerializer.Meta.fields：信号契约。
- OpportunitySignalSerializer.Meta.read_only_fields：不可写元数据。
- OpportunityPrioritySerializer.priority_score：可空 0–100 分数。
- OpportunityPrioritySerializer.Meta.model：评分模型。
- OpportunityPrioritySerializer.Meta.fields：评分契约。
- OpportunityPrioritySerializer.Meta.read_only_fields：不可写元数据。
"""

from rest_framework import serializers as s
from .models import OpportunitySignal, OpportunityPriority
from .serializers import StrictModelSerializer

BASE_FIELDS = ["id", "owner", "revision", "archived", "created_at", "updated_at", "company", "opportunity", "data_source"]
READ_ONLY_FIELDS = ["id", "owner", "revision", "archived", "created_at", "updated_at", "company"]


# 功能：提供商机结果的公共关系契约。
# 逻辑：只需要提交 opportunity，避免调用方重复维护 company。
# 约束：关系查询沿用业务权限；修改时禁止搬移至另一商机。
class OpportunityResultSerializer(StrictModelSerializer):
    company = s.PrimaryKeyRelatedField(read_only=True)

    # 功能：回填客户并保护商机归属。
    # 输入：`attrs` 为已验证字段。
    # 输出：含 company 的字段；关系迁移抛 400。
    # 逻辑：使用已授权商机对象，不从文本或客户端身份推断归属。
    # 约束：不检查算法的类型枚举、原因数量或分项权重。
    def validate(self, attrs):
        opportunity = attrs.get("opportunity", getattr(self.instance, "opportunity", None))
        if self.instance and opportunity.pk != self.instance.opportunity_id:
            raise s.ValidationError("请为另一商机新建结果，不移动已有结果。")
        attrs["company"] = opportunity.company
        return attrs


# 功能：接收结构化信号。
# 逻辑：保留原始 signal_value，元数据可逐步补全。
# 约束：不产生信号，不要求邮件以外来源满足邮件证据协议。
class OpportunitySignalSerializer(OpportunityResultSerializer):
    confidence = s.FloatField(min_value=0, max_value=1, allow_null=True, required=False)

    # 功能：列出信号字段。
    # 逻辑：服务器维护归属和版本，其余接受算法显式提交。
    # 约束：不能写入凭证或模型外字段。
    class Meta:
        model = OpportunitySignal
        fields = BASE_FIELDS + ["signal_type", "signal_value", "confidence", "source_type", "source_id", "evidence_text", "detected_at", "status"]
        read_only_fields = READ_ONLY_FIELDS


# 功能：接收可展示的评分输出。
# 逻辑：只有分数检查数值范围，解释与建议允许缺失。
# 约束：不改变已有公司级 L4 协议或算法计算。
class OpportunityPrioritySerializer(OpportunityResultSerializer):
    priority_score = s.IntegerField(min_value=0, max_value=100, allow_null=True, required=False)

    # 功能：列出评分字段。
    # 逻辑：保留 JSON 分项、原因及证据供前端和算法扩展。
    # 约束：不存在自动公式验证或后台重新计算。
    class Meta:
        model = OpportunityPriority
        fields = BASE_FIELDS + ["priority_score", "score_breakdown", "top_reasons", "evidence", "recommended_next_action", "scored_at", "score_version"]
        read_only_fields = READ_ONLY_FIELDS
