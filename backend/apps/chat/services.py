"""Responsibility: Provide chat transactions, employee isolation, idempotent saving, and explicit recovery.
Implementation: Experiment mode reads across accounts and continues conversations while continuation retains original conversation ownership; employee locks serialize workspace jobs; legacy company conversations retain only history, reports validate structure only, and terminal results compare exactly.
Relationships: Called by ``chat.views`` and Worker; reuses sales ``Conversation`` and ``Message`` and crm authentication.
Directory:
- lock_owner: Acquire existing employee lock.
- conversation_for: Read employee-owned general or customer conversation.
- request_for: Validate request and all bindings.
- require_workspace: Reject creating or continuing chat task in a legacy company conversation.
- submit: Save question and awaiting-answer task in transaction.
- retry: Create unique new attempt for a failed task.
- retire_legacy_requests: End legacy company active tasks under employee lock while retaining history.
- claim: Atomically claim and freeze history.
- context_for: Persist and return one-time evidence snapshot.
- save_answer: Idempotently write answer and citations after structural validation; unregistered sources retain metadata only.
- request_data: Render status and citations for an authorized request.
- interrupt: Explicitly terminate an interrupted request.
Variable index:
- logger: Logs only task identifier, employee, and status.
"""

from common.laboratory import enabled, owner_scope

import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.crm.access import Conflict, InvalidState
from apps.sales.models import Conversation, Message, CompanySettings
from . import contracts
from .context import build_context
from .models import AnswerRequest, Citation

logger = logging.getLogger("salesmate.chat")


# Function: Acquire the employee lock used by existing business services.
# Inputs: Authenticated user ``owner``.
# Outputs: None; current transaction holds the user row lock.
# Logic: Every chat write locks employee before request and company, preventing duplicate claim or submission.
# Constraints: Must run in a transaction and does not hold lock through model-execution phase.
def lock_owner(owner):
    get_user_model().objects.select_for_update().get(pk=owner.pk, is_active=True)


# Function: Read a conversation usable for general or customer question answering.
# Inputs: Current employee ``owner`` and UUID ``conversation_id``.
# Outputs: ``Conversation`` with nullable company; unauthorized access returns 404.
# Logic: Production mode validates employee and company ownership; experiment mode opens cross-account conversations.
# Constraints: Archived company or conversation accepts no new task or context read.
def conversation_for(owner, conversation_id):
    conversation = (
        Conversation.objects.select_related("company")
        .filter(owner_scope(owner), pk=conversation_id, archived=False)
        .filter(Q() if enabled() else (Q(company__isnull=True) | Q(company__owner=owner)))
        .first()
    )
    if (
        conversation is None
        or CompanySettings.objects.filter(
            company=conversation.company, archived=True
        ).exists()
    ):
        raise NotFound("会话不存在或当前不可访问。")
    return conversation


# Function: Validate end-to-end ownership of an answer request.
# Inputs: Current employee ``owner``, UUID ``request_id``, and whether ``lock`` acquires request row lock.
# Outputs: ``AnswerRequest`` with conversation and message; unauthorized access or invalid binding returns 404.
# Logic: Check original ownership and conversation binding of message and request; production mode additionally restricts caller ownership, while experiment mode permits cross-account access.
# Constraints: Locked query locks only request table, avoiding database lock error from nullable assistant relation.
def request_for(owner, request_id, lock=False):
    query = (
        AnswerRequest.objects.select_related(
            "company", "conversation", "user_message", "assistant_message"
        )
        .filter(owner_scope(owner), pk=request_id)
        .filter(Q() if enabled() else (Q(company__isnull=True) | Q(company__owner=owner)))
    )
    if lock:
        query = query.select_for_update(of=("self",))
    request = query.first()
    if request is None:
        raise NotFound("回答请求不存在。")
    conversation_for(owner, request.conversation_id)
    if (
        request.conversation.company_id != request.company_id
        or request.user_message.conversation_id != request.conversation_id
        or request.user_message.owner_id != request.owner_id
        or request.user_message.role != "user"
        or (
            request.assistant_message_id
            and (
                request.assistant_message.owner_id != request.owner_id
                or request.assistant_message.conversation_id != request.conversation_id
            )
        )
    ):
        raise NotFound("回答请求绑定不可访问。")
    return request


# Function: Ensure execution path uses only workspace conversation without preselected company.
# Inputs: ``conversation`` that passed employee permission checks.
# Outputs: None; legacy company-bound conversation raises ``InvalidState``.
# Logic: Retains historical read capability but requires user to explicitly resubmit question in workspace.
# Constraints: Does not silently rewrite a question depending on legacy customer context into a whole-workspace question.
def require_workspace(conversation):
    if conversation.company_id is not None:
        raise InvalidState("客户绑定聊天已停用，请在工作空间新建会话并重新提问。")


# Function: Create one user question and answer request.
# Inputs: Authenticated user ``owner`` and ``data`` containing conversation_id, content, and client_key.
# Outputs: ``AnswerRequest`` and boolean indicating whether it was first created.
# Logic: Only workspace accepts new tasks; experiment mode resolves original ownership then locks only that account and rereads conversation, client idempotency key precedes active-task check, and same-content retransmission returns original request.
# Constraints: Ordinary historical messages are not automatically promoted to model requests; an active conversation rejects a second new question.
@transaction.atomic
def submit(owner, data):
    contracts.fields(data, {"conversation_id", "content", "client_key"})
    conversation_id = contracts.identifier(data["conversation_id"])
    client_key = contracts.identifier(data["client_key"])
    content = contracts.text(data["content"], "content")
    conversation = conversation_for(owner, conversation_id)
    if enabled():
        owner = conversation.owner
    lock_owner(owner)
    conversation = conversation_for(owner, conversation_id)
    require_workspace(conversation)
    existing = Message.objects.filter(
        conversation=conversation, client_key=client_key
    ).first()
    if existing:
        if (
            existing.owner_id != owner.pk
            or existing.role != "user"
            or existing.content != content
        ):
            raise Conflict("同一消息幂等键对应不同内容。")
        original = existing.chat_requests.filter(retry_of__isnull=True).first()
        if original is None:
            raise Conflict("该键已用于历史消息，请用新的提交键提问。")
        return original, False
    if AnswerRequest.objects.filter(
        conversation=conversation, status__in=["pending", "processing"]
    ).exists():
        raise Conflict("当前会话已有未完成回答，请等待完成。")
    message = Message.objects.create(
        owner=owner,
        conversation=conversation,
        role="user",
        content=content,
        client_key=client_key,
    )
    request = AnswerRequest.objects.create(
        owner=owner,
        conversation=conversation,
        user_message=message,
    )
    logger.info("chat_submitted request_id=%s owner_id=%s", request.pk, owner.pk)
    return request, True


# Function: Create a traceable new attempt for a failed answer.
# Inputs: Current employee ``owner`` and original-request UUID ``request_id``.
# Outputs: New request and first-creation flag; repeated click returns original successor.
# Logic: Only workspace permits retry; experiment mode resolves and locks original request ownership first and rejects active task or existing successor question.
# Constraints: Does not retry automatically, revive original request_id, or overwrite historical answer.
@transaction.atomic
def retry(owner, request_id):
    if enabled():
        owner = request_for(owner, request_id).owner
    lock_owner(owner)
    old = request_for(owner, request_id, lock=True)
    require_workspace(old.conversation)
    successor = AnswerRequest.objects.filter(retry_of=old).first()
    if successor:
        return successor, False
    if old.status != "failed":
        raise InvalidState("只有失败请求可以重新回答。")
    if AnswerRequest.objects.filter(
        conversation=old.conversation, status__in=["pending", "processing"]
    ).exists():
        raise Conflict("当前会话已有未完成回答。")
    if (
        Message.objects.filter(conversation=old.conversation, role="user")
        .filter(
            Q(created_at__gt=old.user_message.created_at)
            | Q(created_at=old.user_message.created_at, id__gt=old.user_message_id)
        )
        .exists()
    ):
        raise Conflict("会话已有后续问题，请重新提交需要回答的问题。")
    request = AnswerRequest.objects.create(
        owner=owner,
        conversation=old.conversation,
        user_message=old.user_message,
        retry_of=old,
    )
    logger.info(
        "chat_retried request_id=%s previous_id=%s owner_id=%s",
        request.pk,
        old.pk,
        owner.pk,
    )
    return request, True


# Function: End legacy company chat tasks that workspace Agent cannot continue to execute.
# Inputs: Optional authenticated employee ``owner``; when omitted Worker startup checks all employees.
# Outputs: Number of requests actually ended and retirement log without content.
# Logic: Lock each employee transactionally and update only company-bound pending or processing requests, retaining messages, evidence, and terminal history.
# Constraints: Does not redispatch or rewrite original question; old Worker must stop before startup, while ordinary workspace processing is unaffected.
def retire_legacy_requests(owner=None):
    candidates = AnswerRequest.objects.filter(
        status__in=["pending", "processing"]
    ).filter(Q(company__isnull=False) | Q(conversation__company__isnull=False))
    if owner is not None:
        candidates = candidates.filter(owner=owner)
    owner_ids = list(
        candidates.order_by("owner_id").values_list("owner_id", flat=True).distinct()
    )
    total = 0
    for owner_id in owner_ids:
        with transaction.atomic():
            get_user_model().objects.select_for_update().get(pk=owner_id)
            count = candidates.filter(owner_id=owner_id).update(
                status="failed",
                finished_at=timezone.now(),
                error={
                    "code": "workspace_chat_required",
                    "message": "客户绑定聊天已停用，请在工作空间新建会话并重新提问。",
                },
            )
            total += count
            logger.info("chat_legacy_retired owner_id=%s requests=%s", owner_id, count)
    return total


# Function: Atomically claim one task for current employee and freeze history.
# Inputs: Employee ``owner`` bound by Agent service token.
# Outputs: Five-field workspace request or ``None``; does not output company_id.
# Logic: Claim pending request for selected account and freeze conversation history under that request's original ownership; does not mix queues across accounts.
# Constraints: Legacy company and revoked or archived requests do not block later tasks and are not redispatched implicitly; model is not called in transaction.
@transaction.atomic
def claim(owner):
    lock_owner(owner)
    retire_legacy_requests(owner)
    for candidate in AnswerRequest.objects.filter(
        owner=owner, status="pending"
    ).order_by("created_at", "id"):
        try:
            request = request_for(owner, candidate.pk, lock=True)
        except NotFound:
            candidate.status = "failed"
            candidate.finished_at = timezone.now()
            candidate.error = {
                "code": "access_revoked",
                "message": "客户或会话已不可访问。",
            }
            candidate.save(update_fields=["status", "finished_at", "error"])
            logger.warning(
                "chat_access_revoked request_id=%s owner_id=%s", candidate.pk, owner.pk
            )
            continue
        message = request.user_message
        history = (
            Message.objects.filter(
                owner=request.owner,
                conversation=request.conversation,
                archived=False,
                role__in=["user", "assistant"],
            )
            .filter(
                Q(created_at__lt=message.created_at)
                | Q(created_at=message.created_at, id__lt=message.pk)
            )
            .order_by("-created_at", "-id")[:20]
        )
        request.recent_history = [
            {"role": row.role, "content": row.content}
            for row in reversed(list(history))
            if row.content.strip()
        ]
        request.status = "processing"
        request.processing_started_at = timezone.now()
        request.save(
            update_fields=["recent_history", "status", "processing_started_at"]
        )
        logger.info("chat_claimed request_id=%s owner_id=%s", request.pk, owner.pk)
        return {
            "request_id": str(request.pk),
            "conversation_id": str(request.conversation_id),
            "user_message_id": str(message.pk),
            "question": message.content,
            "recent_history": request.recent_history,
        }
    return None


# Function: Return stable evidence bound to a request.
# Inputs: ``owner`` is the current Agent employee, ``request_id`` is a request UUID, and ``scope`` is internal or external.
# Outputs: First-generated and persisted internal ``AnswerContext``.
# Logic: Freeze caller's knowledge only for processing workspace request and return same snapshot subsequently; read-only tools query customer data.
# Constraints: Explicitly rejects external while unavailable and does not swallow database errors or fabricate empty information.
@transaction.atomic
def context_for(owner, request_id, scope):
    if scope not in ("internal", "external"):
        raise ValidationError("scope 必须为 internal 或 external。")
    lock_owner(owner)
    request = request_for(owner, request_id, lock=True)
    require_workspace(request.conversation)
    if request.status != "processing":
        raise InvalidState("只能读取处理中的请求上下文。")
    if scope == "external":
        raise InvalidState("当前未启用外部知识。")
    if request.context_snapshot is None:
        request.context_snapshot = build_context(request)
        request.save(update_fields=["context_snapshot"])
        logger.info(
            "chat_context_frozen request_id=%s customer_items=%s knowledge_items=%s",
            request.pk,
            len(request.context_snapshot["customer_context"]),
            len(request.context_snapshot["context_items"]),
        )
    return request.context_snapshot


# Function: Save one final Agent result.
# Inputs: Token-bound employee ``owner`` and strict report object ``data``.
# Outputs: request_id, saved, duplicate, and assistant_message_id.
# Logic: Validate report Schema and deduplicate terminal states exactly; experiment mode may report across accounts while assistant message retains original request ownership; citations attach content after registration or store metadata only.
# Constraints: Does not query other records from source identifiers or decide content meaning or version binding; permission, state, and idempotency remain enforced and failures create no assistant message.
@transaction.atomic
def save_answer(owner, data):
    result = contracts.report(data)
    lock_owner(owner)
    request = request_for(owner, contracts.identifier(result["request_id"]), lock=True)
    duplicate = request.status in ("completed", "failed") and request.result == result
    if not duplicate:
        if request.status != "processing":
            raise Conflict("请求已结束或尚未领取，不能保存其他结果。")
        snapshot = request.context_snapshot or {}
        tool_evidence = [
            item
            for items in request.tool_reads.order_by("created_at", "id").values_list(
                "evidence_items", flat=True
            )
            for item in items
        ]
        snapshot_items = {
            tuple(
                row[key] for key in ("source_id", "source_type", "title_or_label")
            ): row
            for row in [
                *snapshot.get("customer_context", []),
                *snapshot.get("context_items", []),
                *tool_evidence,
            ]
        }
        cited = []
        metadata_only = 0
        for citation in result["citations"]:
            key = tuple(
                citation[field]
                for field in ("source_id", "source_type", "title_or_label")
            )
            if key in snapshot_items:
                cited.append(snapshot_items[key])
            else:
                # An unregistered source is Agent-declared metadata; it grants no information-read permission and does not fabricate evidence content.
                cited.append({**citation, "content": ""})
                metadata_only += 1
        if result["status"] == "completed":
            request.assistant_message = Message.objects.create(
                owner=request.owner,
                conversation=request.conversation,
                role="assistant",
                content=result["assistant_text"],
                client_key=request.pk,
            )
            Citation.objects.bulk_create(
                [
                    Citation(request=request, position=index, **row)
                    for index, row in enumerate(cited, 1)
                ]
            )
        request.status = result["status"]
        request.result = result
        request.chat_prompt_version = result["chat_prompt_version"]
        request.error = result["error"]
        request.finished_at = timezone.now()
        request.save(
            update_fields=[
                "assistant_message",
                "status",
                "result",
                "chat_prompt_version",
                "error",
                "finished_at",
            ]
        )
        logger.info(
            "chat_citations_saved request_id=%s owner_id=%s citations=%s metadata_only=%s",
            request.pk,
            owner.pk,
            len(cited),
            metadata_only,
        )
    logger.info(
        "chat_reported request_id=%s owner_id=%s status=%s duplicate=%s",
        request.pk,
        owner.pk,
        request.status,
        duplicate,
    )
    return {
        "request_id": str(request.pk),
        "saved": True,
        "duplicate": duplicate,
        "assistant_message_id": (
            str(request.assistant_message_id) if request.assistant_message_id else None
        ),
    }


# Function: Project browser state for an authorized request.
# Inputs: ``request`` is a request object validated by the permission service.
# Outputs: Status, times, assistant-message identifier, ordered citations, and Agent error text.
# Logic: Failure returns no assistant content; citation content comes from this request context or tool records and unregistered source content is empty.
# Constraints: Does not output internal history, complete snapshot, tool history, or credentials; Agent error returns unchanged and Agent owns its redaction.
def request_data(request):
    return {
        "request_id": str(request.pk),
        "conversation_id": str(request.conversation_id),
        "user_message_id": str(request.user_message_id),
        "assistant_message_id": (
            str(request.assistant_message_id) if request.assistant_message_id else None
        ),
        "status": request.status,
        "error": request.error,
        "created_at": request.created_at,
        "processing_started_at": request.processing_started_at,
        "finished_at": request.finished_at,
        "chat_prompt_version": request.chat_prompt_version,
        "citations": list(
            request.citations.order_by("position").values(
                "position", "source_id", "source_type", "title_or_label", "content"
            )
        ),
    }


# Function: Terminate interrupted task after human confirmation.
# Inputs: Specified employee ``owner`` and explicit task UUID ``request_id``.
# Outputs: Failed request.
# Logic: Production mode restricts selected employee while experiment mode permits explicitly selected other-account request; terminal state stays unchanged, processing becomes failed, and old Agent cannot write results again.
# Constraints: Callable only by management command after maintainer confirms interruption; does not implicitly requeue or call model again.
@transaction.atomic
def interrupt(owner, request_id):
    lock_owner(owner)
    request = (
        AnswerRequest.objects.select_for_update()
        .filter(owner_scope(owner), pk=request_id)
        .first()
    )
    if request is None:
        raise NotFound("回答请求不存在。")
    if request.status != "processing":
        raise InvalidState("只能终止处理中的请求。")
    request.status = "failed"
    request.finished_at = timezone.now()
    request.error = {
        "code": "worker_interrupted",
        "message": "回答进程已中断，请明确重试。",
    }
    request.save(update_fields=["status", "finished_at", "error"])
    logger.warning(
        "chat_interrupted request_id=%s owner_id=%s action=explicit_retry",
        request.pk,
        owner.pk,
    )
    return request
