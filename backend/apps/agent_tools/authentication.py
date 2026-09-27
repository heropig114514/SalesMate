"""Responsibility: Validate independent credentials with limited business-tool permissions.
Implementation: Accept only Tool credentials and validate revocation, expiry, active ownership, and explicit delegation scope in every environment.
Relationships: Tool endpoints allow this authentication and browser Session; authorization creation and proposal confirmation permit only Session.
Directory:
- ToolAuthentication: Independent tool authentication.
- ToolAuthentication.authenticate: Validate digest and authorization state.
- ToolAuthentication.authenticate_header: Declare the authentication scheme.
- check_credential: Validate current credential usability and a specific tool permission.
- ToolAuthenticationSchema: OpenAPI authentication declaration.
- ToolAuthenticationSchema.get_security_definition: Publish the authentication-header contract.
Variable index:
- ToolAuthenticationSchema.target_class: Authentication implementation path.
- ToolAuthenticationSchema.name: Security-scheme name.
"""

import hashlib
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from .models import ToolCredential


# Function: Verify a valid delegation.
# Inputs: Authorization record ``credential`` and optional tool name ``name``.
# Outputs: None; an expired or unauthorized credential raises an authentication or permission exception.
# Logic: Check expiry, revocation, inactivity, and delegation scope regardless of laboratory settings.
# Constraints: Does not accept wildcards or read employee identity from model input.
def check_credential(credential, name=None):
    if (
        credential.revoked_at
        or credential.expires_at <= timezone.now()
        or not credential.owner.is_active
    ):
        raise AuthenticationFailed("工具授权已失效，请由用户重新授权。")
    if name is not None and name not in credential.allowed_tools:
        raise PermissionDenied("该授权不包含此工具。")


# Function: Distinguish business delegation from existing Worker identity.
# Logic: Accept only an independent Tool token; browser requests continue through Session authentication.
# Constraints: Tokens never enter logs or query parameters.
class ToolAuthentication(BaseAuthentication):
    # Function: Authenticate a tool request.
    # Inputs: HTTP ``request``.
    # Outputs: User and authorization record, or ``None``.
    # Logic: Validate current credential state after a digest lookup.
    # Constraints: Explicitly reject invalid or foreign Authorization schemes; public identity headers confer no permissions.
    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if not parts:
            return None
        if len(parts) != 2 or parts[0].lower() != b"tool":
            raise AuthenticationFailed("请使用独立的 Tool 授权。")
        digest = hashlib.sha256(parts[1]).hexdigest()
        credential = (
            ToolCredential.objects.select_related("owner").filter(digest=digest).first()
        )
        if credential is None:
            raise AuthenticationFailed("工具授权无效。")
        check_credential(credential)
        return credential.owner, credential

    # Function: Declare the authentication scheme.
    # Inputs: ``request``.
    # Outputs: ``Tool``.
    # Logic: Returns a fixed value.
    # Constraints: No side effects.
    def authenticate_header(self, request):
        return "Tool"


# Function: Declare the tool-authentication contract.
# Logic: Independent Authorization scheme.
# Constraints: Does not authenticate or contain a real token.
class ToolAuthenticationSchema(OpenApiAuthenticationExtension):
    target_class = "apps.agent_tools.authentication.ToolAuthentication"
    name = "businessToolCredential"

    # Function: Describe the authentication header.
    # Inputs: ``auto_schema`` context.
    # Outputs: OpenAPI object.
    # Logic: Requires the ``Tool`` prefix.
    # Constraints: Session-only authorization and confirmation endpoints do not use this scheme.
    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "header",
            "name": "Authorization",
            "description": "Tool <user-scoped-token>",
        }
