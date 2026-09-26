"""Responsibility: Enforce user approval for request-bound chat writes and resume the suspended answer.
Implementation: Freeze tool arguments, target version, and Agent loop state before any mutation; a Session-only decision executes once in the request transaction. Laboratory mode never skips this gate.
Relationships: tool_reads proposes writes; services claims continuations and projects status; ApprovalDecisionView accepts browser decisions, independently of background jobs and ordinary Tool/MCP calls.
Directory:
- approval_data: Project reviewable operation content without the Agent checkpoint.
- target_fingerprint: Resolve the current experimental target version.
- propose: Suspend a processing request and persist its exact pending write.
- resume_data: Reconstruct canonical evidence and the approved operation result.
- decide: Validate the browser decision and atomically execute or reject the write.
- ApprovalDecisionView: Session-only approval HTTP boundary.
- ApprovalDecisionView.post: Dispatch an explicit decision and return current chat state.
Variable index:
- logger: State-transition diagnostics without argument values or message content.
- CONTINUATION_SCHEMA: Closed checkpoint contract preserving the original Agent loop budget.
- ApprovalDecisionView.authentication_classes: Browser Session authentication; Agent/Tool and public laboratory identities cannot approve.
"""

import logging
from datetime import timedelta

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema, OpenApiTypes
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.agent_tools import services as tool_services
from apps.agent_tools.registry import build_registry
from apps.agent_tools.schemas import object_schema, validate
from apps.crm.access import Conflict
from apps.sales.experiments import load_batch, table_rows
from . import services
from .models import ChatApproval, ToolRead

logger = logging.getLogger("salesmate.chat.approvals")
CONTINUATION_SCHEMA = object_schema({
    "next_turn": {"type": "integer", "minimum": 1},
    "observations": {"type": "array", "items": {"type": "object"}},
    "signatures": {"type": "array", "items": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 2}},
}, ["next_turn", "observations", "signatures"])


# Function: Present a concrete operation for user review.
# Inputs: Persisted ChatApproval ``approval``.
# Outputs: JSON-safe identifiers, frozen arguments, status, deadline, and target version.
# Logic: Exclude internal loop state and include the intended approver for useful UI errors.
# Constraints: Visibility alone grants no authority to submit a decision.
def approval_data(approval):
    return {
        "id": str(approval.pk), "request_id": str(approval.request_id),
        "tool": approval.tool, "arguments": approval.arguments,
        "status": approval.status, "expires_at": approval.expires_at.isoformat(),
        "target_fingerprint": approval.target_fingerprint,
        "approver_id": approval.request.requested_by_id or approval.request.owner_id,
    }


# Function: Read the exact current row version for experiment modification or deletion.
# Inputs: ``name`` is a registered chat tool and ``arguments`` its validated parameters.
# Outputs: SHA-256 fingerprint or empty string when no existing target is involved.
# Logic: Resolve only manifest-listed rows through the existing experiment boundary.
# Constraints: No mutation; laboratory mode still supplies a current fingerprint for this approval.
def target_fingerprint(name, arguments):
    if name not in {"experiments.update", "experiments.delete"}:
        return ""
    rows = table_rows(load_batch(arguments["batch"]), arguments["model"])
    row = next((row for row in rows if str(row["pk"]) == str(arguments["pk"])), None)
    if row is None:
        raise NotFound("待审批记录不存在。")
    return row["fingerprint"]


# Function: Freeze a chat mutation without executing any business handler.
# Inputs: Locked processing ``request``, live ``spec``, exact ``arguments``, and Agent ``continuation``.
# Outputs: HTTP-202 receipt with approval_required and reviewable operation.
# Logic: Validate the checkpoint and target version, then atomically suspend the request and create its pending approval.
# Constraints: Requires caller's transaction; no fallback for older Agents without resumable state and no laboratory bypass.
def propose(request, spec, arguments, continuation):
    validate(continuation, CONTINUATION_SCHEMA)
    fingerprint = target_fingerprint(spec["name"], arguments)
    if fingerprint and arguments.get("expected", fingerprint) != fingerprint:
        raise Conflict("记录已变化，请重新读取后申请审批。")
    approval = ChatApproval.objects.create(
        request=request, tool=spec["name"], arguments=arguments, schema=spec["inputSchema"],
        target_fingerprint=fingerprint, continuation=continuation,
        expires_at=timezone.now() + timedelta(hours=24),
    )
    request.status = "awaiting_approval"
    request.save(update_fields=["status"])
    logger.info("chat_approval_required request_id=%s approval_id=%s tool=%s owner_id=%s", request.pk, approval.pk, approval.tool, request.owner_id)
    return {"request_id": str(request.pk), "tool": approval.tool, "status": "approval_required",
            "http_status": 202, "approval": approval_data(approval)}


# Function: Restore a suspended Agent from persisted state and actual execution evidence.
# Inputs: ``request`` being claimed after explicit approval.
# Outputs: Resume object or None for a new question.
# Logic: Select the most recent approved write and aggregate canonical evidence already registered for this request.
# Constraints: Never rerun the approved handler or reconstruct evidence from Agent assertions.
def resume_data(request):
    approval = request.approvals.filter(status="approved").order_by("-created_at", "-id").first()
    if approval is None:
        return None
    return {
        "continuation": approval.continuation, "tool_result": approval.receipt,
        "arguments": approval.arguments,
        "evidence_items": [item for read in request.tool_reads.order_by("created_at", "id") for item in read.evidence_items],
    }


# Function: Apply a browser user's decision to one frozen chat write.
# Inputs: Session-authenticated ``actor``, ``request_id``, ``approval_id``, and approve/reject ``decision``.
# Outputs: Locked AnswerRequest after cancellation or successful execution and requeue.
# Logic: Lock original owner then request and approval, verify the initiating user, expiration, live schema, and target version; execute and register evidence in the same transaction as approval and resume state.
# Constraints: No model calls, external sends, automatic retries, changed arguments, or rollback of earlier approved writes; execution failures roll back this decision and leave it reviewable.
@transaction.atomic
def decide(actor, request_id, approval_id, decision):
    from .tool_reads import ALLOWED_TOOLS, evidence_for

    if decision not in {"approve", "reject"}:
        raise ValidationError("decision 必须为 approve 或 reject。")
    original = services.request_for(actor, request_id)
    if actor.pk != (original.requested_by_id or original.owner_id):
        raise PermissionDenied("只有提交本次问题的登录用户可以批准或拒绝。")
    services.lock_owner(original.owner)
    request = services.request_for(actor, request_id, lock=True)
    approval = request.approvals.select_for_update().filter(pk=approval_id).first()
    if approval is None:
        raise NotFound("审批请求不存在。")
    target = "approved" if decision == "approve" else "rejected"
    if approval.status == target:
        return request
    if approval.status != "pending" or request.status != "awaiting_approval":
        raise Conflict("该审批已处理或聊天已结束。")
    if decision == "reject":
        request.status = "cancelled"
        request.finished_at = timezone.now()
    else:
        if approval.expires_at <= timezone.now():
            raise Conflict("审批已过期，请拒绝本次操作后重新提问。")
        spec = build_registry().get(approval.tool)
        if approval.tool not in ALLOWED_TOOLS or spec is None or spec["executionMode"] != "write" or spec["inputSchema"] != approval.schema:
            raise Conflict("工具定义已变化，请拒绝本次操作后重新提问。")
        tool_services.authorize(request.owner, None, approval.tool)
        validate(approval.arguments, spec["inputSchema"])
        if target_fingerprint(approval.tool, approval.arguments) != approval.target_fingerprint:
            raise Conflict("待审批记录已变化，本次批准不能执行；请拒绝后重新提问。")
        # Lock the manifest through execution so a concurrent experimental writer cannot invalidate the reviewed version.
        if approval.target_fingerprint:
            entry = load_batch(approval.arguments["batch"])
            get_user_model().objects.select_for_update().get(pk=entry.owner_id)
            type(entry).objects.select_for_update().get(pk=entry.pk)
            model = apps.get_model(approval.arguments["model"])
            if not model.objects.select_for_update().filter(pk=approval.arguments["pk"]).exists():
                raise Conflict("待审批记录已删除，请重新提问。")
            if target_fingerprint(approval.tool, approval.arguments) != approval.target_fingerprint:
                raise Conflict("待审批记录已变化，请重新申请审批。")
        business = tool_services.invoke(request.owner, None, approval.tool, approval.arguments, str(approval.pk))
        if business["status"] != "completed":
            raise Conflict("操作未完成，审批未提交。")
        read = ToolRead(request=request, tool=approval.tool, arguments=approval.arguments, result=business)
        read.evidence_items = evidence_for(read.pk, approval.tool, business["data"])
        read.save()
        approval.receipt = {**business, "request_id": str(request.pk), "read_id": str(read.pk), "evidence_items": read.evidence_items}
        request.status = "pending"
    approval.status, approval.decided_by, approval.decided_at = target, actor, timezone.now()
    approval.save(update_fields=["status", "decided_by", "decided_at", "receipt"])
    request.save(update_fields=["status", "finished_at"])
    logger.info("chat_approval_decided request_id=%s approval_id=%s actor_id=%s decision=%s next_status=%s", request.pk, approval.pk, actor.pk, decision, request.status)
    return request


# Function: Accept independent browser approval even when public laboratory access is enabled.
# Logic: Use SessionAuthentication exclusively; DRF enforces CSRF for the logged-in browser.
# Constraints: Neither Agent tokens, Tool tokens, nor laboratory headers are accepted as approval identity.
class ApprovalDecisionView(APIView):
    authentication_classes = [SessionAuthentication]

    # Function: Submit one explicit user decision.
    # Inputs: ``request`` contains only decision; URL ``request_id`` and ``approval_id`` bind the exact operation.
    # Outputs: Current request state with no-store caching, or the original permission/conflict error.
    # Logic: Validate a closed payload and delegate to the transaction service; log failures without sensitive arguments.
    # Constraints: Does not infer approval from conversation text or accept operation replacements.
    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT, operation_id="chat_approval_decide")
    def post(self, request, request_id, approval_id):
        services.contracts.fields(request.data, {"decision"})
        try:
            answer = decide(request.user, request_id, approval_id, request.data["decision"])
        except Exception as error:
            logger.warning("chat_approval_failed request_id=%s approval_id=%s actor_id=%s error_type=%s", request_id, approval_id, request.user.pk, type(error).__name__)
            raise
        response = Response(services.request_data(answer))
        response["Cache-Control"] = "no-store"
        return response
