"""Responsibility: Centrally control unauthenticated identity and cross-account business access in laboratory environments.
Implementation: Personal-workspace isolation takes priority over the laboratory switch; when explicitly enabled, uses a session, existing token, or public identity-selection header, assigning anonymous requests to a dedicated laboratory account.
Relationships: Shared by Session, Agent, Tool authentication, and business scopes; disabling the switch restores original authorization at each entry point.
Directory:
- owner_only: Reads personal-workspace isolation policy.
- enabled: Reads the laboratory-mode switch.
- identity: Resolves a laboratory actor whose identity need not be proved.
- owner_scope: Builds a business-ownership query for the current mode.
- LaboratoryAuthentication: Supplies unauthenticated DRF identity in laboratory mode.
- LaboratoryAuthentication.authenticate: Resolves identity according to the switch.
- LaboratoryAuthenticationSchema: Declares the optional laboratory identity header.
- LaboratoryAuthenticationSchema.get_security_definition: Describes laboratory identity selection.
Variable index:
- logger: Records laboratory-account creation without outputting tokens.
- LaboratoryAuthenticationSchema.target_class: Authenticator path.
- LaboratoryAuthenticationSchema.name: OpenAPI identity-selection name.
"""

import hashlib
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import NotFound

logger = logging.getLogger("salesmate.laboratory")


# Function: Read the personal-workspace isolation policy.
# Inputs: No external parameters; reads Django settings.
# Outputs: bool.
# Logic: Explicit activation disables cross-account experiments and team sharing.
# Constraints: Supplies policy only and does not read business records itself.
def owner_only():
    return getattr(settings, "WORKSPACE_OWNER_ONLY", False)


# Function: Read the laboratory-mode switch.
# Inputs: No external parameters; reads Django settings.
# Outputs: bool.
# Logic: Disabled by default; when personal-workspace isolation is enabled, legacy true laboratory switches do not grant access.
# Constraints: Does not depend on DEBUG or connect to the database.
def enabled():
    return not owner_only() and getattr(settings, "LAB_OPEN_ACCESS", False)


# Function: Resolve a laboratory actor whose identity does not need proof.
# Inputs: `request` is a DRF or Django request.
# Outputs: User or None when the mode is disabled.
# Logic: Explicit X-Lab-User takes priority, followed by an existing session, recognizable service credentials, and finally the dedicated laboratory account.
# Constraints: This identity is used only for ownership and logs and does not prove caller identity; it does not alter the Django admin session or output secrets.
def identity(request):
    if not enabled():
        return None
    users = get_user_model().objects
    selected = request.headers.get("X-Lab-User")
    if selected:
        actor = users.filter(username=selected).first()
        if actor is None:
            raise NotFound("X-Lab-User 指定的账号不存在。")
        return actor
    raw = getattr(request, "_request", request)
    session_user = getattr(raw, "user", None)
    if session_user is not None and session_user.is_authenticated:
        return session_user
    parts = request.headers.get("Authorization", "").split()
    if len(parts) == 2:
        from apps.crm.models import AgentCredential
        from apps.agent_tools.models import ToolCredential
        model = {"agent": AgentCredential, "tool": ToolCredential}.get(parts[0].lower())
        if model:
            credential = model.objects.select_related("owner").filter(digest=hashlib.sha256(parts[1].encode()).hexdigest()).first()
            if credential:
                return credential.owner
    actor, created = users.get_or_create(username=settings.LAB_DEFAULT_USER,
                                        defaults={"password": "!", "is_active": True})
    if created:
        logger.warning("laboratory_actor_created user_id=%s authentication_disabled=true", actor.pk)
    return actor


# Function: Build a business-ownership query for the current mode.
# Inputs: `owner` is the original owning user; `path` is the owner relation path and defaults to owner.
# Outputs: Q condition.
# Logic: Laboratory mode does not restrict ownership; production mode retains original relationship filtering.
# Constraints: Used only for explicit business queries, never for passwords, OAuth, or administrator-permission decisions.
def owner_scope(owner, path="owner"):
    return Q() if enabled() else Q(**{path: owner})


# Function: Provide unauthenticated DRF identity in laboratory mode.
# Logic: When enabled, takes precedence over Session authentication without triggering its CSRF check.
# Constraints: Returns None when disabled so the existing authentication chain continues.
class LaboratoryAuthentication(BaseAuthentication):
    # Function: Resolve identity according to the switch.
    # Inputs: `request` is the HTTP request.
    # Outputs: Authentication tuple or None.
    # Logic: Shares the identity policy and does not issue sessions or tokens.
    # Constraints: Affects only business APIs that use this authenticator.
    def authenticate(self, request):
        actor = identity(request)
        return (actor, None) if actor is not None else None


# Function: Declare the optional laboratory identity header.
# Logic: Documentation explicitly states that it is not an authentication credential.
# Constraints: Grants no permission when laboratory mode is disabled.
class LaboratoryAuthenticationSchema(OpenApiAuthenticationExtension):
    target_class = "common.laboratory.LaboratoryAuthentication"
    name = "laboratoryIdentity"

    # Function: Describe laboratory identity selection.
    # Inputs: `auto_schema` is schema context.
    # Outputs: OpenAPI Header description.
    # Logic: Publishes optional X-Lab-User selection semantics.
    # Constraints: Does not read the database or supply passwords or tokens.
    def get_security_definition(self, auto_schema):
        return {"type": "apiKey", "in": "header", "name": "X-Lab-User",
                "description": "仅 LAB_OPEN_ACCESS=true 时可选的实验归属用户名，无需令牌；省略使用实验默认身份。正式模式不可用。"}
