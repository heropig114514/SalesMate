"""Responsibility: Declare OpenAPI field structures for error and health-check responses.
Implementation: Uses DRF fields to express enumerations, arbitrary JSON detail, and nullable request IDs; does not probe databases or catch exceptions.
Relationships: Used by extend_schema in common.views and accounts.views; runtime views construct the corresponding dictionaries directly.

Directory:
- ErrorDetailSerializer: Declares code and detail fields for a standard error.
- ApiErrorSerializer: Declares the standard error response carrying a request ID.
- LivenessSerializer: Declares the service-liveness response.
- ReadinessSerializer: Declares the database-readiness response.

Variable index:
- ErrorDetailSerializer.code: Machine-readable error code.
- ErrorDetailSerializer.detail: Raw error detail that may contain nested structures.
- ApiErrorSerializer.error: Nested standard-error structure.
- ApiErrorSerializer.request_id: Request ID for correlating logs; may be null.
- LivenessSerializer.status: Liveness marker constrained to ok.
- LivenessSerializer.service: Service identifier string.
- ReadinessSerializer.status: Overall readiness-check status enumeration.
- ReadinessSerializer.database: Database status enumeration.
"""

from rest_framework import serializers


# Function: Declare code and detail fields for a standard error.
# Logic: Uses a string for code and JSONField for detail to preserve DRF's varied error shapes.
# Constraints: Does not determine exception types or generate HTTP status codes.
class ErrorDetailSerializer(serializers.Serializer):
    code = serializers.CharField()
    detail = serializers.JSONField()


# Function: Declare a standard error response with a request ID.
# Logic: Nests ErrorDetailSerializer and permits request_id to be None.
# Constraints: Describes the API structure only; api_exception_handler performs actual wrapping.
class ApiErrorSerializer(serializers.Serializer):
    error = ErrorDetailSerializer()
    request_id = serializers.CharField(allow_null=True)


# Function: Declare the service-liveness response.
# Logic: Constrains status to ok and stores the service identifier in service.
# Constraints: Does not concern database or migration state; expresses only liveness-view output.
class LivenessSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["ok"])
    service = serializers.CharField()


# Function: Declare the database-readiness response.
# Logic: Separately constrains status and database to ok or unavailable.
# Constraints: The serializer does not validate consistency between the fields; the associated view constructs matching values.
class ReadinessSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["ok", "unavailable"])
    database = serializers.ChoiceField(choices=["ok", "unavailable"])
