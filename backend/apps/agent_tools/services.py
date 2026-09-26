"""Responsibility: Execute tool authorization, strict input, idempotent receipts, and human-confirmation protocol.
Implementation: Ordinary write calls and unique receipts save atomically; graph writes use source idempotency and inference does not occupy a transaction; production management operations create proposals and experiment mode executes directly.
Relationships: ``registry`` defines capabilities and ``dispatch`` reuses business logic; only Session views call ``decide`` in production mode, while experiment mode uses public identity.
Directory:
- catalog: Return the current identity's tool catalog.
- authorize: Recheck tool authorization.
- response_data: Normalize a business-response receipt.
- invoke: Execute a query or idempotent write.
- proposal_data: Project a user proposal.
- decide: Approve or cancel a frozen proposal.
Variable index:
- logger: Records only tool, user, receipt, stage, and exception type.
"""

import hashlib
import json
import logging
import uuid
from common.laboratory import enabled
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from apps.crm.access import Conflict, plain
from .authentication import check_credential
from .dispatch import execute
from .models import ToolCall, ToolCredential, ToolProposal
from .registry import build_registry
from .schemas import UUID, validate

logger = logging.getLogger("salesmate.agent_tools")


# Function: List available tools.
# Inputs: Logged-in user ``actor``, optional tool credential ``credential``, and optional category ``category``.
# Outputs: List of public tool descriptions.
# Logic: Production mode filters by credential allowlist and experiment mode publishes the complete catalog; exposes source-idempotency scope without exposing internal handlers.
# Constraints: The catalog grants no permission and returns no business data or credentials.
def catalog(actor, credential=None, category=None):
    if not enabled() and not actor.is_active:
        raise PermissionDenied("用户已停用。")
    if credential:
        check_credential(credential)
    return [
        {
            key: value
            for key, value in spec.items()
            if key
            in {
                "name",
                "description",
                "inputSchema",
                "executionMode",
                "category",
                "annotations",
                "idempotency_required",
                "idempotency_scope",
            }
        }
        for name, spec in build_registry().items()
        if (enabled() or not credential or name in credential.allowed_tools)
        and (not category or spec["category"] == category)
    ]


# Function: Recheck execution identity.
# Inputs: ``actor``, ``credential``, and tool name ``name``.
# Outputs: None.
# Logic: Experiment mode does not check user state or the tool allowlist; production mode checks identity and authorization ownership.
# Constraints: Actual handlers continue to check business-entity permissions.
def authorize(actor, credential, name):
    if not enabled() and not actor.is_active:
        raise PermissionDenied("用户已停用。")
    if credential and not enabled():
        if credential.owner_id != actor.pk:
            raise PermissionDenied("工具授权归属不一致。")
        check_credential(credential, name)


# Function: Generate a result envelope.
# Inputs: Existing handler response ``response``.
# Outputs: Persistable dictionary.
# Logic: Retain business HTTP status, ETag, and content; explicitly mark ``confirmation_required`` when an external action awaits confirmation.
# Constraints: ``accepted`` or ``pending`` is not successful external execution.
def response_data(response):
    status = "accepted" if response.status_code == 202 else "completed"
    if (
        isinstance(response.data, dict)
        and response.data.get("status") == "pending_confirmation"
    ):
        status = "confirmation_required"
    return {
        "status": status,
        "http_status": response.status_code,
        "revision": response.get("ETag"),
        "data": plain(response.data),
    }


# Function: Invoke an authorized business tool.
# Inputs: ``actor``, ``credential``, ``name``, ``arguments``, and optional UUID ``idempotency_key``.
# Outputs: Result containing tool name and receipt.
# Logic: Validate and authorize first; graph-source writes manage their own transactions and are idempotent by ``source_key`` or ``episode_id``; other writes and ``ToolCall`` receipts share one transaction, while confirm tools only freeze a proposal.
# Constraints: Source-idempotent tools reject a transmitted UUID, return Episode audit, and create no ``ToolCall``; ordinary production writes still require an idempotency UUID; failures do not retry and a cached receipt is not current data.
def invoke(actor, credential, name, arguments, idempotency_key=None):
    spec = build_registry().get(name)
    if spec is None:
        raise NotFound("工具不存在。")
    authorize(actor, credential, name)
    validate(arguments, spec["inputSchema"])
    logger.info(
        "tool_call_started owner_id=%s tool=%s mode=%s",
        actor.pk,
        name,
        spec["executionMode"],
    )
    try:
        if spec.get("idempotency_scope") in {"source_key", "episode_id"}:
            if idempotency_key is not None:
                raise ValidationError("图谱写入使用来源键或观察ID幂等，不接受 idempotency_key。")
            result = {"tool": name, **response_data(execute(actor, spec, arguments))}
        elif spec["executionMode"] == "read":
            if idempotency_key is not None and not enabled():
                raise ValidationError("只读工具不接受幂等键。")
            result = {"tool": name, **response_data(execute(actor, spec, arguments))}
        else:
            if enabled() and idempotency_key is None:
                idempotency_key = str(uuid.uuid4())
            validate(idempotency_key, UUID)
            digest = hashlib.sha256(
                json.dumps(
                    arguments,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
            ).hexdigest()
            with transaction.atomic():
                if credential:
                    credential = (
                        ToolCredential.objects.select_for_update(of=("self",))
                        .select_related("owner")
                        .get(pk=credential.pk)
                    )
                    authorize(actor, credential, name)
                call, created = ToolCall.objects.get_or_create(
                    owner=actor,
                    key=idempotency_key,
                    defaults={"tool": name, "input_hash": digest, "result": {}},
                )
                if not created:
                    if call.tool != name or call.input_hash != digest:
                        raise Conflict("同一幂等键不能用于不同工具或内容。")
                    logger.info(
                        "tool_call_replayed owner_id=%s tool=%s call_id=%s",
                        actor.pk,
                        name,
                        call.pk,
                    )
                    return {**call.result, "replayed": True}
                if spec["executionMode"] == "confirm":
                    proposal = ToolProposal.objects.create(
                        owner=actor,
                        credential=credential,
                        tool=name,
                        arguments=arguments,
                        expires_at=timezone.now() + timedelta(hours=24),
                    )
                    result = {
                        "status": "confirmation_required",
                        "proposal": proposal_data(proposal),
                        "message": "仅保存提案，业务尚未执行；用户需在已登录会话中审阅确认。",
                    }
                else:
                    result = response_data(
                        execute(actor, spec, arguments, idempotency_key)
                    )
                result = {
                    "tool": name,
                    "call_id": str(call.pk),
                    "replayed": False,
                    **result,
                }
                call.result = result
                call.save(update_fields=["result"])
        logger.info(
            "tool_call_finished owner_id=%s tool=%s status=%s",
            actor.pk,
            name,
            result["status"],
        )
        return result
    except Exception as error:
        logger.warning(
            "tool_call_failed owner_id=%s tool=%s error_type=%s",
            actor.pk,
            name,
            type(error).__name__,
        )
        raise


# Function: Project pending-confirmation content.
# Inputs: Authorized ``proposal``.
# Outputs: Tool input, expiry, status, and user-confirmation endpoint.
# Logic: Present frozen input for complete interface preview.
# Constraints: Production confirmation endpoint accepts only Session; experiment-mode public identity can select original proposal ownership.
def proposal_data(proposal):
    return plain(
        {
            "id": str(proposal.pk),
            "tool": proposal.tool,
            "arguments": proposal.arguments,
            "status": proposal.status,
            "expires_at": proposal.expires_at,
            "expired": proposal.expires_at <= timezone.now(),
            "result": proposal.result,
            "decision_path": f"/api/v1/agent-tools/proposals/{proposal.pk}/decision/",
            "confirmation_authentication": "laboratory_identity" if enabled() else "user_session_csrf",
        }
    )


# Function: Submit a human decision.
# Inputs: Logged-in user ``actor``, ``proposal_id``, and ``decision`` of approve or cancel.
# Outputs: Proposal status and actual business result.
# Logic: Lock proposal, check expiry, original authorization, and current permissions, then execute frozen arguments.
# Constraints: Production caller must be a Session-only view and experiment mode uses public identity; version conflicts roll back and retain pending state without modifying frozen input.
@transaction.atomic
def decide(actor, proposal_id, decision):
    if decision not in {"approve", "cancel"}:
        raise ValidationError("决定须为 approve/cancel。")
    proposal = (
        ToolProposal.objects.select_for_update()
        .filter(pk=proposal_id, owner=actor)
        .first()
    )
    if proposal is None:
        raise NotFound("提案不存在。")
    target = "approved" if decision == "approve" else "cancelled"
    if proposal.status == target:
        return proposal_data(proposal)
    if proposal.status != "pending":
        raise Conflict("提案已经处理，不能改变决定。")
    if decision == "approve":
        if proposal.expires_at <= timezone.now():
            raise Conflict("提案已过期，请重新准备。")
        credential = None
        if proposal.credential_id:
            credential = (
                ToolCredential.objects.select_for_update(of=("self",))
                .select_related("owner")
                .get(pk=proposal.credential_id)
            )
        authorize(actor, credential, proposal.tool)
        spec = build_registry().get(proposal.tool)
        if not spec or spec["executionMode"] != "confirm":
            raise Conflict("工具定义已经变化，请重新准备提案。")
        validate(proposal.arguments, spec["inputSchema"])
        proposal.result = response_data(
            execute(actor, spec, proposal.arguments, proposal.pk)
        )
    proposal.status = target
    proposal.save(update_fields=["status", "result"])
    logger.info(
        "tool_proposal_decided owner_id=%s proposal_id=%s tool=%s decision=%s",
        actor.pk,
        proposal.pk,
        proposal.tool,
        decision,
    )
    return proposal_data(proposal)
