"""Responsibility: Validate basic opportunity-result structure and relationships, not algorithm correctness.
Implementation: Require a valid opportunity and derive its company; check score/confidence bounds while retaining original JSON explanations.
Relationships: Shared by generic resources and SDK/MCP, registered at the end of sales.serializers.
Directory:
- OpportunityResultSerializer: Derive customer relationships.
- OpportunityResultSerializer.validate: Validate opportunity/company consistency.
- OpportunitySignalSerializer: Read and write signals.
- OpportunitySignalSerializer.Meta: Signal fields.
- OpportunityPrioritySerializer: Read and write scoring results.
- OpportunityPrioritySerializer.Meta: Result fields.
Variable index:
- BASE_FIELDS: Common result output fields.
- READ_ONLY_FIELDS: Server-maintained fields.
- OpportunityResultSerializer.company: Read-only derived company.
- OpportunitySignalSerializer.confidence: Nullable confidence from 0 to 1.
- OpportunitySignalSerializer.Meta.model: Signal model.
- OpportunitySignalSerializer.Meta.fields: Signal contract.
- OpportunitySignalSerializer.Meta.read_only_fields: Nonwritable metadata.
- OpportunityPrioritySerializer.priority_score: Nullable score from 0 to 100.
- OpportunityPrioritySerializer.Meta.model: Scoring model.
- OpportunityPrioritySerializer.Meta.fields: Scoring contract.
- OpportunityPrioritySerializer.Meta.read_only_fields: Nonwritable metadata.
"""

from rest_framework import serializers as s
from .models import OpportunitySignal, OpportunityPriority
from .serializers import StrictModelSerializer

BASE_FIELDS = ["id", "owner", "revision", "archived", "created_at", "updated_at", "company", "opportunity", "data_source"]
READ_ONLY_FIELDS = ["id", "owner", "revision", "archived", "created_at", "updated_at", "company"]


# Function: Provide the shared relationship contract for opportunity results.
# Logic: Require only opportunity so callers need not maintain company redundantly.
# Constraints: Relationship queries retain business permissions; updates cannot move results to another opportunity.
class OpportunityResultSerializer(StrictModelSerializer):
    company = s.PrimaryKeyRelatedField(read_only=True)

    # Function: Derive the customer and protect opportunity ownership.
    # Inputs: `attrs`: validated fields.
    # Outputs: Fields including company; relationship moves raise 400.
    # Logic: Use the authorized opportunity object rather than inferring ownership from text or client identity.
    # Constraints: Do not validate algorithm type enums, reason counts, or component weights.
    def validate(self, attrs):
        opportunity = attrs.get("opportunity", getattr(self.instance, "opportunity", None))
        if self.instance and opportunity.pk != self.instance.opportunity_id:
            raise s.ValidationError("请为另一商机新建结果，不移动已有结果。")
        attrs["company"] = opportunity.company
        return attrs


# Function: Accept structured signals.
# Logic: Preserve original signal_value and allow metadata to be completed incrementally.
# Constraints: Do not generate signals or impose email evidence contracts on non-email sources.
class OpportunitySignalSerializer(OpportunityResultSerializer):
    confidence = s.FloatField(min_value=0, max_value=1, allow_null=True, required=False)

    # Function: List signal fields.
    # Logic: The server maintains ownership/version; remaining fields require explicit algorithm submissions.
    # Constraints: Do not allow credentials or fields outside the model.
    class Meta:
        model = OpportunitySignal
        fields = BASE_FIELDS + ["signal_type", "signal_value", "confidence", "source_type", "source_id", "evidence_text", "detected_at", "status"]
        read_only_fields = READ_ONLY_FIELDS


# Function: Accept displayable scoring output.
# Logic: Only scores are range-checked; explanations and suggestions may be absent.
# Constraints: Do not change the existing company-level L4 protocol or algorithm calculation.
class OpportunityPrioritySerializer(OpportunityResultSerializer):
    priority_score = s.IntegerField(min_value=0, max_value=100, allow_null=True, required=False)

    # Function: List scoring fields.
    # Logic: Retain JSON components, reasons, and evidence for frontend/algorithm extension.
    # Constraints: No automatic formula validation or background recalculation.
    class Meta:
        model = OpportunityPriority
        fields = BASE_FIELDS + ["priority_score", "score_breakdown", "top_reasons", "evidence", "recommended_next_action", "scored_at", "score_version"]
        read_only_fields = READ_ONLY_FIELDS
