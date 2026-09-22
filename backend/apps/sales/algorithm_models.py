"""职责：承载算法提交的商机信号与商机级评分，不执行算法。
实现：关联已有商机；保留灵活 JSON 解释、来源标签与版本化归档，支持逐条提交和历史查询。
关联：由 sales.models 注册，algorithm_serializers 和通用业务工具读写。
目录：
- OpportunitySignal：保存算法识别的信号。
- OpportunityPriority：保存一次商机优先级结果。
变量索引：
- OpportunitySignal.opportunity：信号所属商机。
- OpportunitySignal.signal_type：调用方的信号类型，不固定枚举。
- OpportunitySignal.signal_value：原始结构化值。
- OpportunitySignal.confidence：可空置信度。
- OpportunitySignal.source_type：来源类型。
- OpportunitySignal.source_id：来源标识。
- OpportunitySignal.evidence_text：原文证据。
- OpportunitySignal.detected_at：信号记录时间。
- OpportunitySignal.status：调用方维护的状态。
- OpportunitySignal.data_source：manual、agent 或 synthetic 来源标签。
- OpportunityPriority.opportunity：评分所属商机。
- OpportunityPriority.priority_score：可空的 0–100 分数，由调用方计算。
- OpportunityPriority.score_breakdown：原始分项对象。
- OpportunityPriority.top_reasons：调用方提供的主要原因。
- OpportunityPriority.evidence：来源证据列表。
- OpportunityPriority.recommended_next_action：建议动作文本。
- OpportunityPriority.scored_at：算法计算时间。
- OpportunityPriority.score_version：调用方的算法版本。
- OpportunityPriority.data_source：结果来源标签。
"""

from django.db import models
from django.utils import timezone
from .models import CompanyRecord


# 功能：保存商机信号。
# 逻辑：客户归属由商机推导，类型和值由算法侧决定。
# 约束：不推断有效期、不读取外部来源、不执行证据文本。
class OpportunitySignal(CompanyRecord):
    opportunity = models.ForeignKey("sales.Opportunity", on_delete=models.CASCADE, related_name="signals")
    signal_type = models.CharField(max_length=100)
    signal_value = models.JSONField(null=True, blank=True)
    confidence = models.FloatField(null=True, blank=True)
    source_type = models.CharField(max_length=50, blank=True)
    source_id = models.CharField(max_length=500, blank=True)
    evidence_text = models.TextField(blank=True)
    detected_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=50, default="ACTIVE")
    data_source = models.CharField(max_length=30, default="agent")


# 功能：保存一次商机优先级计算输出。
# 逻辑：每次 POST 新增结果，最新有效结果按 scored_at、created_at、id 确定；PATCH 可显式修订。
# 约束：不重算分项权重，不强制要求完整解释；synthetic 仅为联调占位。
class OpportunityPriority(CompanyRecord):
    opportunity = models.ForeignKey("sales.Opportunity", on_delete=models.CASCADE, related_name="priority_results")
    priority_score = models.PositiveSmallIntegerField(null=True, blank=True)
    score_breakdown = models.JSONField(default=dict, blank=True)
    top_reasons = models.JSONField(default=list, blank=True)
    evidence = models.JSONField(default=list, blank=True)
    recommended_next_action = models.TextField(blank=True)
    scored_at = models.DateTimeField(default=timezone.now)
    score_version = models.CharField(max_length=100, blank=True)
    data_source = models.CharField(max_length=30, default="agent")
