"""Responsibility: Provide an identity-query endpoint for the currently authenticated user.
Implementation: Inherit global ``SessionAuthentication`` and ``IsAuthenticated`` and serialize ``request.user``; does not implement login or mailbox authorization.
Relationships: Calls ``CurrentUserSerializer``; ``common.exceptions`` consistently wraps error structures.

Directory:
- CurrentUserView: Query public identity information for an authenticated user.
- CurrentUserView.get: Return the identity of the user that passed the current permission check.

Variable index:
- None
"""

from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from common.serializers import ApiErrorSerializer

from .serializers import CurrentUserSerializer


# Function: Query public identity information for an authenticated user.
# Logic: Use the project's default authentication and permission configuration; GET delegates to the allowlist serializer.
# Constraints: Provides no login flow or team or mailbox permission decision; DRF rejects unauthenticated requests before entering ``get``.
class CurrentUserView(APIView):
    # Function: Return the identity of the user that passed the current permission check.
    # Inputs: ``request`` is a DRF Request whose user was processed by authentication and permissions.
    # Outputs: HTTP-200 ``Response`` containing id, username, first_name, and last_name.
    # Logic: Serialize ``request.user`` directly, avoiding another query and exposure of all model fields.
    # Constraints: Does not write the database or change the session; relies on global ``IsAuthenticated`` and leaves serialization errors to framework handling.
    @extend_schema(
        responses={200: CurrentUserSerializer, 403: ApiErrorSerializer},
        tags=["accounts"],
        description="Return the current session user. Team and mailbox access rules are not implemented yet.",
    )
    def get(self, request):
        return Response(CurrentUserSerializer(request.user).data)
