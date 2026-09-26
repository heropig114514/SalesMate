"""Responsibility: Provide unauthenticated service-liveness and default-database-readiness checks.
Implementation: The liveness check does not access the database; the readiness check executes SELECT 1 and returns 503 with sanitized diagnostics for database exceptions or unexpected results.
Relationships: Exposed by config.urls and depends on connection configuration, response serializers, and middleware request_id; does not validate migrations or pgvector.

Directory:
- LivenessView: Provides a liveness check requiring no database or authentication.
- LivenessView.get: Returns liveness state for the backend process.
- ReadinessView: Provides a default database-connection round-trip check.
- ReadinessView.get: Checks whether the default database returns the expected query result.

Variable index:
- logger: salesmate.health logger that emits sanitized database diagnostics only.
- LivenessView.authentication_classes: Empty list preventing liveness probes from triggering session authentication.
- LivenessView.permission_classes: AllowAny permits anonymous liveness checks.
- ReadinessView.authentication_classes: Empty list preventing readiness probes from triggering session authentication.
- ReadinessView.permission_classes: AllowAny permits anonymous database probes.
"""

import logging

from django.db import DatabaseError, connections
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import LivenessSerializer, ReadinessSerializer

logger = logging.getLogger("salesmate.health")


# Function: Provide a liveness check requiring no database or authentication.
# Logic: Explicitly clears authenticators and permits anonymous access; GET returns a fixed service identifier.
# Constraints: Proves only that a request can pass through application processing, not that the database, migrations, or external dependencies are ready.
class LivenessView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    # Function: Return backend-process liveness state.
    # Inputs: `request` is a DRF Request; this method does not read its business data.
    # Outputs: 200 Response with status ok and service salesmate-backend.
    # Logic: Constructs a fixed dictionary and performs no database query or session authentication.
    # Constraints: Makes no business writes; database unavailability does not affect the returned liveness state.
    @extend_schema(responses=LivenessSerializer, tags=["health"])
    def get(self, request):
        return Response({"status": "ok", "service": "salesmate-backend"})


# Function: Provide a default database-connection round-trip check.
# Logic: Permits anonymous GET, executes a probe through the connection context, and reports database failures consistently.
# Constraints: Does not validate migration completion, tables, vector extensions, or other external services.
class ReadinessView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    # Function: Check whether the default database returns the expected query result.
    # Inputs: `request` is a DRF Request; failure logs use request_id set by middleware.
    # Outputs: 200 and ok on success; 503 and unavailable for DatabaseError.
    # Logic: Executes SELECT 1 in a cursor context and requires result (1,); unexpected results also become DatabaseError, while failure logs retain only exception type and diagnostic guidance.
    # Constraints: Reads the database without writing business tables; non-DatabaseError exceptions are not caught here. DATABASES controls connection wait time and the context closes the cursor.
    @extend_schema(
        responses={200: ReadinessSerializer, 503: ReadinessSerializer},
        tags=["health"],
        description="Check the configured default database. Does not verify migrations.",
    )
    def get(self, request):
        try:
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT 1")
                # A connection that did not raise is insufficient for success; a result outside the probe contract takes the same failure path.
                if cursor.fetchone() != (1,):
                    raise DatabaseError("Unexpected database readiness response.")
        except DatabaseError as exc:
            # A connection error can include credentials or host details: record its type only.
            logger.error(
                "database_readiness_failed request_id=%s alias=default error_type=%s "
                "action=check_default_database_and_DATABASE_URL",
                request.request_id,
                type(exc).__name__,
            )
            return Response({"status": "unavailable", "database": "unavailable"}, status=503)
        return Response({"status": "ok", "database": "ok"})
