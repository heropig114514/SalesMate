"""Responsibility: Centralize business authorization, conflict errors, and JSON normalization.
Implementation: Validate service tokens, private ownership, and optimistic versions independently of experiment settings.
Relationships: Shared by browser business services and authenticated AgentCredential endpoints.
Directory:
- Conflict: Represent a version, idempotency, or lease conflict.
- InvalidState: Represent an operation incompatible with current state.
- AgentAuthentication: Authenticate high-entropy Agent service tokens used only by backend business interfaces.
- AgentAuthentication.authenticate: Validate an Authorization: Agent token.
- AgentAuthentication.authenticate_header: Declare the Agent authentication scheme.
- plain: Convert validated data to JSON-storable values.
- company_for: Resolve a company that belongs to the current user.
- mailbox_for: Resolve a business mailbox that belongs to the current user.
- check_version: Require the caller's optimistic-lock version to match the database.
Variable index:
- Conflict.default_code: Protocol error code used by client branches.
- Conflict.default_detail: Default actionable error description.
- Conflict.status_code: HTTP status for this exception.
- InvalidState.default_code: Protocol error code used by client branches.
- InvalidState.default_detail: Default actionable error description.
- InvalidState.status_code: HTTP status for this exception.
- logger: Redacted diagnostic logger for this module.
"""
import hashlib
import json
import logging
import uuid

from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import APIException, AuthenticationFailed, NotFound, ValidationError
from rest_framework.renderers import JSONRenderer

from .models import AgentCredential, Company, Mailbox
from common.laboratory import owner_scope

logger = logging.getLogger("salesmate.business")


# Function: Represent a version, idempotency, or lease conflict.
# Logic: Consistently use HTTP 409 and the protocol conflict error code.
# Constraints: Does not retry implicitly.
class Conflict(APIException):
    status_code = 409
    default_code = "conflict"
    default_detail = "数据已变化，请刷新后重试。"


# Function: Represent an operation incompatible with current state.
# Logic: Return machine-actionable invalid_state.
# Constraints: Does not disguise failure as success.
class InvalidState(APIException):
    status_code = 409
    default_code = "invalid_state"
    default_detail = "当前状态不允许此操作。"


# Function: Authenticate high-entropy Agent service tokens used only by backend business interfaces.
# Logic: Every Agent endpoint validates the service credential digest and active owner.
# Constraints: Caller-supplied identity headers cannot change credential ownership; deployments use HTTPS.
class AgentAuthentication(BaseAuthentication):
    # Function: Validate an Authorization: Agent token.
    # Inputs: `request` is a DRF request and reads its Authorization header.
    # Outputs: A validated user and credential tuple; returns None without a header and raises AuthenticationFailed for an invalid header.
    # Logic: Look up a SHA-256 digest and require the owner to remain active.
    # Constraints: Logs only failure type and never header or token content.
    def authenticate(self, request):
        header = get_authorization_header(request).split()
        if not header:
            return None
        if len(header) != 2 or header[0] != b"Agent":
            raise AuthenticationFailed("需要 Agent 服务凭证。")
        digest = hashlib.sha256(header[1]).hexdigest()
        credential = AgentCredential.objects.select_related("owner").filter(digest=digest, owner__is_active=True).first()
        if credential is None:
            logger.warning("agent_authentication_failed action=check_or_rotate_service_credential")
            raise AuthenticationFailed("Agent 服务凭证无效。")
        return credential.owner, credential

    # Function: Declare the Agent authentication scheme.
    # Inputs: `request` is an unauthenticated request and does not read its body.
    # Outputs: Scheme name used by WWW-Authenticate.
    # Logic: Return the fixed Agent string.
    # Constraints: Has no side effects.
    def authenticate_header(self, request):
        return "Agent"


# Function: Convert validated data to JSON-storable values.
# Inputs: `value` is DRF data containing UUIDs, dates, or Decimals.
# Outputs: A plain JSON data structure.
# Logic: Use the DRF renderer to keep HTTP representation consistent with database snapshots.
# Constraints: Does not execute network or database operations.
def plain(value):
    return json.loads(JSONRenderer().render(value))


# Function: Resolve a company belonging to the current user.
# Inputs: `owner` is the authenticated user; `company_id` is the company UUID; `lock` controls transactional row locking.
# Outputs: Company; invalid UUID format returns 400, while absent and unauthorized records return the same 404.
# Logic: Always filter by owner because CRM context contains private email evidence.
# Constraints: Callers must be in a transaction when lock=True.
def company_for(owner, company_id, lock=False):
    try:
        company_id = uuid.UUID(str(company_id))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError("company_id 必须为有效 UUID。") from None
    query = Company.objects.filter(owner_scope(owner))
    if lock:
        query = query.select_for_update()
    try:
        return query.get(pk=company_id)
    except (Company.DoesNotExist, ValueError):
        raise NotFound("公司不存在。") from None


# Function: Resolve a business mailbox belonging to the current user.
# Inputs: `owner` is the authenticated user; `mailbox_id` is the backend mailbox UUID; `lock` controls row locking.
# Outputs: Mailbox; invalid UUID format returns 400, while absent and unauthorized records return 404.
# Logic: Always query by owner and ID together.
# Constraints: Address existence does not mean Gmail OAuth is verified.
def mailbox_for(owner, mailbox_id, lock=False):
    try:
        mailbox_id = uuid.UUID(str(mailbox_id))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError("mailbox_id 必须为有效 UUID。") from None
    query = Mailbox.objects.filter(owner_scope(owner))
    if lock:
        query = query.select_for_update()
    try:
        return query.get(pk=mailbox_id)
    except (Mailbox.DoesNotExist, ValueError):
        raise NotFound("邮箱不存在。") from None


# Function: Require the caller to supply an optimistic-lock version equal to the database version.
# Inputs: `expected` is the HTTP version integer and `actual` is the entity's current version.
# Outputs: None; missing or malformed values raise ValidationError and stale values raise Conflict.
# Logic: Accept only non-negative integer strings or integers in every environment.
# Constraints: Callers must hold the relevant row lock during validation.
def check_version(expected, actual):
    if expected is None or not str(expected).isdigit():
        raise ValidationError("必须使用 If-Match 传入读取时的非负版本。")
    if int(expected) != actual:
        logger.warning("version_conflict expected=%s actual=%s action=reload_context", expected, actual)
        raise Conflict()
