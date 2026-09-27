"""Responsibility: Prepare and execute employee-confirmed chat order and email proposals; generic reads use MCP dispatch.
Implementation: Freeze validated content without business writes; distinguish customer and Gmail authorization failures with actionable codes. Session decisions revalidate and commit once; existing workers alone send.
Relationships: action_contract publishes inputs; tool_reads persists evidence in the preparation transaction; sales services own business validation, audits and dependency invalidation.
Directory:
- strict_company_ids: Resolve genuine business grants independently of laboratory bypass.
- lock_operation_owners: Serialize business snapshots with ordinary writers in a stable lock order.
- require_request: Verify the employee and private workspace binding.
- order_plan: Validate a complete order change and compute its frozen preview.
- email_plan: Validate Gmail ownership, content and stored send authorization without network calls.
- proposal_data: Project the closed Agent response using authoritative execution state.
- proposal_for: Resolve a proposal inside one employee conversation.
- prepare: Persist or replay an unexecuted proposal.
- decide: Apply one explicit browser decision atomically.
- execute_order: Commit validated order and line changes through business services.
- execute_email: Create a business draft and approved send action after confirmation.
Variable index:
- logger: Redacted proposal transition diagnostics.
- PROPOSAL_LIFETIME: Existing approval lifetime reused for new proposals.
"""

import copy
import hashlib
import json
import logging
import re
import uuid
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as ModelValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from common.laboratory import owner_only
from apps.agent_tools.schemas import validate
from apps.crm.access import Conflict, InvalidState
from apps.crm.models import Company
from apps.sales import actions, integrations, models as sales, serializers, services
from .action_contract import PREPARE_ORDER, PREPARE_EMAIL, catalog
from .models import ActionProposal

logger = logging.getLogger("salesmate.chat.actions")
PROPOSAL_LIFETIME = timedelta(hours=24)


# Function: Lock employee and business owners before locking requests, proposals or records.
# Inputs: Employee `actor`, tool `name`, and untrusted object `args`.
# Outputs: None; locks held by the caller transaction.
# Logic: A syntactically valid order target adds its actual order/company owners; sorted user locks match all chat preparation and decision paths.
# Constraints: Malformed targets are validated later as tool errors; this preliminary lookup returns no business data and grants no permission.
def lock_operation_owners(actor, name, args):
    owner_ids = {actor.pk}
    if name == PREPARE_ORDER:
        try:
            target = uuid.UUID(str(args.get("order_id")))
        except (ValueError, TypeError, AttributeError):
            target = None
        if target is not None:
            for order_owner, company_owner in sales.SalesOrder.objects.filter(pk=target).values_list("owner_id", "company__owner_id"):
                owner_ids.update((order_owner, company_owner))
    list(get_user_model().objects.select_for_update().filter(pk__in=owner_ids).order_by("pk"))


# Function: Compute real business visibility or edit grants without laboratory access.
# Inputs: Authenticated `actor` and boolean `write` selecting edit grants.
# Outputs: Company-ID queryset for owned companies and eligible team grants.
# Logic: Respect existing owner-only configuration, otherwise require live membership and explicit grants.
# Constraints: Does not grant mailbox or private conversation access and never consults laboratory bypass.
def strict_company_ids(actor, write=False):
    if not actor.is_active:
        raise NotFound("员工不可用。")
    query = Q(owner=actor)
    if not owner_only():
        memberships = sales.Membership.objects.filter(user=actor, archived=False)
        if write:
            memberships = memberships.filter(role__in=["editor", "manager"])
        teams = sales.Team.objects.filter(archived=False).filter(
            Q(owner=actor) | Q(pk__in=memberships.values("team_id")))
        grants = sales.CompanyGrant.objects.filter(team__in=teams, archived=False)
        if write:
            grants = grants.filter(role="editor")
        query |= Q(pk__in=grants.values("company_id"))
    return Company.objects.filter(query).values("pk")


# Function: Enforce private ownership of the originating request even in laboratory mode.
# Inputs: `actor` credential employee and persisted `request` with related conversation and message.
# Outputs: None; mismatched identities or archived conversations raise 404.
# Logic: Require actual owner and submitter bindings before publishing private business capabilities.
# Constraints: Never accept an identity or confirmation flag from model arguments.
def require_request(actor, request):
    if (not actor.is_active or request.owner_id != actor.pk
            or request.requested_by_id not in (None, actor.pk)
            or request.conversation.owner_id != actor.pk or request.conversation.archived
            or request.conversation.company_id is not None
            or request.user_message.owner_id != actor.pk
            or request.user_message.conversation_id != request.conversation_id):
        raise NotFound("聊天请求不存在或不属于当前员工。")


# Function: Validate all proposed order mutations and derive exact before/after content.
# Inputs: Employee `actor`, frozen `args`, and `lock` selecting execution/preparation row locks.
# Outputs: Original order, ordered serializers for changed records, and JSON preview.
# Logic: Check all original revisions first; validate detached candidates using existing serializers and business rules; calculate net totals with existing per-line rounding.
# Constraints: No writes, implicit currency conversion, new lines or partial success; unchanged argument text is preserved in the preview.
def order_plan(actor, args, lock=False):
    query = sales.SalesOrder.objects.filter(pk=args["order_id"], company_id__in=strict_company_ids(actor, write=True), archived=False)
    if lock:
        query = query.select_for_update()
    order = query.first()
    if order is None:
        raise NotFound("订单不存在或无修改权限。")
    if order.revision != args["revision"]:
        raise Conflict("订单版本已变化，请重新读取。")
    if not args["changes"] and not args["line_changes"]:
        raise ValidationError("至少提供一项订单或明细修改。")
    if "number" in args["changes"] and not args["changes"]["number"].strip():
        raise ValidationError("订单编号不得为空。")
    if sales.CompanySettings.objects.filter(company=order.company, archived=True).exists():
        raise InvalidState("客户已归档。")
    query = order.lines.filter(archived=False).order_by("pk")
    if lock:
        query = query.select_for_update()
    lines = {str(row.pk): row for row in query}
    requested, seen = [(order, args["changes"], serializers.SalesOrderSerializer, "")], set()
    for change in args["line_changes"]:
        for field, value in change["changes"].items():
            if field != "description" and not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
                raise ValidationError("数量和金额必须是精确十进制字符串。")
        key = str(uuid.UUID(change["line_id"]))
        line = lines.get(key)
        if key in seen or line is None:
            raise ValidationError("明细必须属于当前订单，且不得重复。")
        seen.add(key)
        if line.revision != change["revision"]:
            raise Conflict("订单明细版本已变化，请重新读取。")
        requested.append((line, change["changes"], serializers.OrderLineSerializer, f"lines.{change['line_id']}."))
    edits, changes, candidates = [], [], dict(lines)
    for record, fields, serializer_type, prefix in requested:
        serializer = serializer_type(record, data=fields, partial=True, context={"request": SimpleNamespace(user=actor)})
        for field in serializer.fields.values():
            if hasattr(field, "trim_whitespace"):
                field.trim_whitespace = False
        serializer.is_valid(raise_exception=True)
        candidate = copy.copy(record)
        for field, value in serializer.validated_data.items():
            setattr(candidate, field, value)
        services.validate_record(candidate, actor, set(fields), False)
        try:
            candidate.full_clean()
        except ModelValidationError as error:
            raise ValidationError(error.message_dict) from error
        for field, value in fields.items():
            before = getattr(record, field)
            changes.append({"field": prefix + field, "before": str(before) if isinstance(before, Decimal) else before, "after": value})
        if prefix:
            candidates[str(record.pk)] = candidate
        if fields:
            edits.append(serializer)
    total = sum(((row.quantity * row.unit_price - row.discount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        for row in candidates.values()), Decimal("0.00"))
    preview = {"company_name": order.company.name, "order_number": order.number, "changes": changes,
        "currency": args["changes"].get("currency", order.currency),
        "total_before": serializers.SalesOrderSerializer(order).data["total"], "total_after": str(total)}
    return order, edits, preview


# Function: Validate frozen plaintext email and a genuine employee Gmail connection.
# Inputs: Employee `actor` and schema-validated message `args`.
# Outputs: Customer, connection and closed sender preview, or a specific customer/connection/authorization error.
# Logic: Check customer and Gmail ownership separately, validate individual addresses, and inspect encrypted scopes without refreshing credentials. Rejections log only employee/target IDs and stable reason codes.
# Constraints: No Draft, ToolAction, token refresh or provider call is allowed during preparation; credentials never enter previews or logs.
def email_plan(actor, args):
    company = Company.objects.filter(pk=args["company_id"], owner=actor).first()
    if company is None:
        logger.info("chat_email_preparation_rejected owner_id=%s company_id=%s reason=email_customer_unavailable", actor.pk, args["company_id"])
        raise NotFound("所选客户不存在或不属于当前发信员工。目录可见不代表可以用于发信；请选择当前账号的客户记录。", code="email_customer_unavailable")
    connection = sales.Connection.objects.filter(pk=args["connection_id"], owner=actor, provider="gmail", archived=False).first()
    if connection is None:
        logger.info("chat_email_preparation_rejected owner_id=%s connection_id=%s reason=email_connection_unavailable", actor.pk, args["connection_id"])
        raise NotFound("所选 Gmail 连接不存在、已归档或不属于当前发信员工。请在当前账号连接 Gmail，或登录该连接所属账号并新建聊天会话。邮件提案功能本身可用。", code="email_connection_unavailable")
    if sales.CompanySettings.objects.filter(company=company, archived=True).exists():
        raise InvalidState("客户已归档。")
    for address in [connection.account, *args["to"], *args["cc"], *args["bcc"]]:
        if not re.fullmatch(r"[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+", address):
            raise ValidationError("邮箱地址格式无效。")
        try:
            validate_email(address)
        except ModelValidationError:
            raise ValidationError("邮箱地址格式无效。") from None
    if not args["subject"].strip() or not args["body_text"].strip() or any(char in args["subject"] for char in "\r\n"):
        raise ValidationError("主题和正文不得为空，主题不得含换行。")
    try:
        document = json.loads(integrations.vault().decrypt(connection.encrypted_credentials.encode("ascii")))
        if not set(integrations.SCOPES["gmail"]).issubset(document.get("scopes", [])):
            raise ValueError("missing_scope")
    except Exception as error:
        logger.warning("chat_email_authorization_failed owner_id=%s connection_id=%s error_type=%s", actor.pk, connection.pk, type(error).__name__)
        raise InvalidState("Gmail 授权无效或缺少发信权限，请重新授权。", code="email_authorization_invalid") from None
    return company, connection, {"company_name": company.name, "from_address": connection.account}


# Function: Return the exact closed proposal structure accepted by the Agent.
# Inputs: Persisted `proposal`, optionally with an associated external action.
# Outputs: Eight-field JSON projection with truthful confirmation and execution state.
# Logic: Derive mail state directly from ToolAction and pending expiry from server time; preserve frozen arguments and preview.
# Constraints: Reading never sends, retries, rewrites content, or exposes internal credentials.
def proposal_data(proposal):
    status = proposal.action.status if proposal.action_id else proposal.status
    if status == "pending_confirmation" and proposal.expires_at <= timezone.now():
        status = "expired"
    return {"id": str(proposal.pk), "kind": "order_update" if proposal.tool == PREPARE_ORDER else "email_send",
        "status": status, "revision": proposal.revision, "expires_at": proposal.expires_at.isoformat(),
        "confirmed_by_employee": proposal.decision == "approve" and proposal.decided_by_id is not None,
        "arguments": proposal.arguments, "preview": proposal.preview}


# Function: Resolve an authorized proposal, including queries in later chat turns.
# Inputs: Employee `actor`, proposal `proposal_id`, optional `conversation_id`, and row-lock boolean `lock`.
# Outputs: Bound ActionProposal or 404.
# Logic: Require ownership of both original request and conversation, then check optional current-conversation binding.
# Constraints: Never rely on laboratory-expanded request visibility; the browser cannot select another employee.
def proposal_for(actor, proposal_id, conversation_id=None, lock=False):
    query = ActionProposal.objects.select_related("request__conversation", "request__user_message", "action").filter(
        pk=proposal_id, request__owner=actor, request__conversation__owner=actor)
    if conversation_id is not None:
        query = query.filter(request__conversation_id=conversation_id)
    if lock:
        query = query.select_for_update(of=("self",))
    proposal = query.first()
    if proposal is None:
        raise NotFound("提案不存在。")
    require_request(actor, proposal.request)
    return proposal


# Function: Freeze one validated operation or return its pending idempotent replay.
# Inputs: Employee `actor`, processing `request`, preparation `name`, and exact `args`.
# Outputs: Persisted ActionProposal and whether it was newly created.
# Logic: Canonical request/employee/tool/arguments hash identifies replays; pending proposals for the same conversation and target are explicitly cancelled when replaced.
# Constraints: Caller holds the employee/request transaction; business records remain unchanged and executed preparations cannot be replayed as pending.
def prepare(actor, request, name, args):
    require_request(actor, request)
    validate(args, catalog()[name]["inputSchema"])
    key = hashlib.sha256(json.dumps([actor.pk, str(request.pk), name, args], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    existing = ActionProposal.objects.filter(key=key).first()
    if existing:
        if proposal_data(existing)["status"] != "pending_confirmation":
            raise Conflict("提案已有决定或已过期，请查询当前状态。")
        return existing, False
    if name == PREPARE_ORDER:
        target, _, preview = order_plan(actor, args, lock=True)
    else:
        target, _, preview = email_plan(actor, args)
    proposal = ActionProposal.objects.create(request=request, tool=name, arguments=args, preview=preview,
        target_id=target.pk, key=key, expires_at=timezone.now() + PROPOSAL_LIFETIME)
    for previous in ActionProposal.objects.select_for_update().filter(request__owner=actor,
            request__conversation=request.conversation, tool=name, target_id=target.pk, status="pending_confirmation").exclude(pk=proposal.pk):
        previous.status, previous.revision = "cancelled", previous.revision + 1
        previous.save(update_fields=["status", "revision"])
        services.audit(actor, previous, "chat_proposal_superseded", {"replacement_id": str(proposal.pk)})
    services.audit(actor, proposal, "chat_proposal_prepared", {"tool": name, "target_id": str(target.pk)})
    logger.info("chat_proposal_prepared request_id=%s conversation_id=%s proposal_id=%s owner_id=%s tool=%s target_id=%s revision=%s status=%s",
        request.pk, request.conversation_id, proposal.pk, actor.pk, name, target.pk, proposal.revision, proposal.status)
    return proposal, True


# Function: Commit one explicitly reviewed employee decision, with idempotent replay.
# Inputs: Session employee `actor`, target `proposal_id`, string `decision`, and displayed `revision`.
# Outputs: Closed proposal data; invalid or stale decisions raise the original business exception.
# Logic: Serialize decisions with employee/business-owner locks, revalidate all data inside a savepoint, then record confirmation and its business writes atomically.
# Constraints: A conflict commits only the conflicted state, never partial business writes; approval does not call Gmail and repeated decisions do not repeat execution.
def decide(actor, proposal_id, decision, revision):
    validate({"decision": decision, "revision": revision}, {"type": "object", "properties": {
        "decision": {"enum": ["approve", "cancel"]}, "revision": {"type": "integer", "minimum": 0}}})
    failure = None
    with transaction.atomic():
        initial = proposal_for(actor, proposal_id)
        lock_operation_owners(actor, initial.tool, initial.arguments)
        proposal = proposal_for(actor, proposal_id, lock=True)
        if proposal.decision:
            if proposal.decision != decision or revision != proposal.revision - 1:
                raise Conflict("提案已经作出不同决定，或版本不匹配。")
            return proposal_data(proposal)
        if revision != proposal.revision or proposal.status != "pending_confirmation":
            raise Conflict("提案不可确认，请刷新状态。")
        if proposal.expires_at <= timezone.now():
            proposal.status = "expired"
            failure = Conflict("提案已过期，请重新准备。")
        elif decision == "cancel":
            proposal.status = "cancelled"
        else:
            try:
                with transaction.atomic():
                    validate(proposal.arguments, catalog()[proposal.tool]["inputSchema"])
                    if proposal.tool == PREPARE_ORDER:
                        execute_order(actor, proposal)
                    else:
                        execute_email(actor, proposal)
            except Conflict as error:
                proposal.status, failure = "conflicted", error
        if failure is None:
            proposal.decision, proposal.decided_by, proposal.decided_at = decision, actor, timezone.now()
        proposal.revision += 1
        proposal.save()
        services.audit(actor, proposal, "chat_proposal_" + proposal.status, {"decision": decision, "revision": proposal.revision})
        logger.info("chat_proposal_decided request_id=%s conversation_id=%s proposal_id=%s owner_id=%s tool=%s target_id=%s revision=%s status=%s",
            proposal.request_id, proposal.request.conversation_id, proposal.pk, actor.pk, proposal.tool, proposal.target_id, proposal.revision, proposal.status)
        result = proposal_data(proposal)
    if failure is not None:
        raise failure
    return result


# Function: Apply an entire order change after all original revisions are checked.
# Inputs: Confirming employee `actor` and locked frozen `proposal`.
# Outputs: None; proposal becomes succeeded after business updates.
# Logic: Validate original versions first, save header before lines to account for parent revision increments, and reuse auditing and dependency services.
# Constraints: Caller transaction rolls back every change on any error; no direct amount/status writes.
def execute_order(actor, proposal):
    _, edits, preview = order_plan(actor, proposal.arguments, lock=True)
    if preview != proposal.preview:
        raise Conflict("订单预览已变化，请重新准备并确认。")
    for serializer in edits:
        services.save_record(serializer, actor, serializer.instance.revision)
    proposal.status = "succeeded"


# Function: Materialize and authorize a frozen email only after employee approval.
# Inputs: Session employee `actor` and locked `proposal` with exact reviewed arguments.
# Outputs: None; proposal links one Draft and one approved ToolAction.
# Logic: Recheck customer/mailbox/scopes and sender, save Draft through business service, and authorize the existing action with the proposal UUID as its idempotency key.
# Constraints: No external calls; worker alone sends. Cc/Bcc live in the immutable action snapshot, not the legacy editable Draft recipient list.
def execute_email(actor, proposal):
    args = proposal.arguments
    company, connection, preview = email_plan(actor, args)
    if preview != proposal.preview:
        raise Conflict("客户或发件账户已变化，请重新准备并确认。")
    serializer = serializers.DraftSerializer(data={"conversation": str(proposal.request.conversation_id), "kind": "email",
        "recipients": args["to"], "subject": args["subject"], "content": args["body_text"]}, context={"request": SimpleNamespace(user=actor)})
    serializer.fields["subject"].trim_whitespace = False
    serializer.fields["content"].trim_whitespace = False
    serializer.is_valid(raise_exception=True)
    draft = services.save_record(serializer, actor)
    action = actions.create_action(actor, {"company": str(company.pk), "tool": "gmail.send",
        "conversation": str(proposal.request.conversation_id), "idempotency_key": str(proposal.pk),
        "parameters": {"connection_id": str(connection.pk), "draft_id": str(draft.pk)}})
    action.parameters.update({"cc": args["cc"], "bcc": args["bcc"], "chat_proposal_id": str(proposal.pk)})
    action.save(update_fields=["parameters"])
    action = actions.decide_action(action, actor, action.revision, "approved")
    proposal.draft, proposal.action, proposal.status = draft, action, "approved"
