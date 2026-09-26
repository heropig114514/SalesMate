"""Responsibility: Store algorithm-submitted opportunity signals and opportunity-level scores without running algorithms.
Implementation: Link existing opportunities, retain flexible JSON explanations/source labels/versioned archival, and support individual submissions and history queries.
Relationships: Registered by sales.models and accessed through algorithm_serializers and shared business tools.
Directory:
- OpportunitySignal: Store an algorithm-detected signal.
- OpportunityPriority: Store one opportunity-priority result.
Variable index:
- OpportunitySignal.opportunity: Opportunity associated with the signal.
- OpportunitySignal.signal_type: Caller-defined signal type without a fixed enum.
- OpportunitySignal.signal_value: Original structured value.
- OpportunitySignal.confidence: Nullable confidence.
- OpportunitySignal.source_type: Source type.
- OpportunitySignal.source_id: Source identifier.
- OpportunitySignal.evidence_text: Original evidence.
- OpportunitySignal.detected_at: Signal recording time.
- OpportunitySignal.status: Caller-maintained status.
- OpportunitySignal.data_source: manual, agent, or synthetic origin label.
- OpportunityPriority.opportunity: Opportunity associated with the score.
- OpportunityPriority.priority_score: Nullable caller-computed score from 0 to 100.
- OpportunityPriority.score_breakdown: Original component object.
- OpportunityPriority.top_reasons: Caller-provided main reasons.
- OpportunityPriority.evidence: Source evidence list.
- OpportunityPriority.recommended_next_action: Suggested action text.
- OpportunityPriority.scored_at: Algorithm computation time.
- OpportunityPriority.score_version: Caller-supplied algorithm version.
- OpportunityPriority.data_source: Result origin label.
"""

from django.db import models
from django.utils import timezone
from .models import CompanyRecord


# Function: Store an opportunity signal.
# Logic: Derive customer ownership from the opportunity; algorithms determine types and values.
# Constraints: Do not infer validity periods, read external sources, or execute evidence text.
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


# Function: Store one opportunity-priority computation output.
# Logic: Each POST creates a result; select the latest active result by scored_at, created_at, and id, while PATCH permits explicit revision.
# Constraints: Do not recompute component weights or require complete explanations; synthetic is only an integration placeholder.
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
