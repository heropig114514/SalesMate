"""职责：提供聊天事务、员工隔离、幂等保存与显式恢复。
实现：按员工锁串行化状态写入；上下文按请求冻结，终态结果精确比较。
关联：chat.views/Worker 调用，复用 sales Conversation/Message 与 crm 认证。
目录：
- lock_owner：取得既有员工锁。
- conversation_for：读取员工自有客户会话。
- request_for：核验请求及全部绑定。
- submit：事务内保存问题与待回答任务。
- retry：为失败任务创建唯一新尝试。
- claim：原子领取并冻结历史。
- context_for：保存并返回一次性证据快照。
- save_answer：幂等写入助手消息与引用。
- request_data：输出浏览器安全状态和引用。
- interrupt：显式终止中断请求。
变量索引：
- logger：仅记录任务标识、员工与状态的日志。
"""

import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.crm.access import Conflict, InvalidState
from apps.crm.models import Company
from apps.sales.models import Conversation, Message, CompanySettings
from . import contracts
from .context import build_context
from .models import AnswerRequest, Citation

logger = logging.getLogger("salesmate.chat")


# 功能：取得与现有业务服务一致的员工锁。
# 输入：`owner` 已认证用户。
# 输出：无，当前事务持有用户行锁。
# 逻辑：所有聊天写入先锁员工，再锁请求和公司，避免重复领取或提交。
# 约束：必须在事务内调用；不把锁保持到模型执行阶段。
def lock_owner(owner):
    get_user_model().objects.select_for_update().get(pk=owner.pk, is_active=True)


# 功能：读取可用于完整客户问答的会话。
# 输入：`owner` 当前员工，`conversation_id` UUID。
# 输出：已关联公司的 Conversation；越权返回 404。
# 逻辑：员工同时拥有会话和公司，不将团队业务共享扩展为邮箱授权。
# 约束：归档公司或会话不接受新任务和上下文读取。
def conversation_for(owner, conversation_id):
    conversation = (
        Conversation.objects.select_related("company")
        .filter(pk=conversation_id, owner=owner, company__owner=owner, archived=False)
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


# 功能：验证回答请求的全链路归属。
# 输入：`owner` 当前员工，`request_id` UUID，`lock` 是否取得请求行锁。
# 输出：带会话和消息的 AnswerRequest；越权或绑定异常返回 404。
# 逻辑：检查请求、公司、会话、用户消息及可选助手消息的员工和会话身份。
# 约束：锁定查询只锁请求表，避免可空助手关联导致数据库锁错误。
def request_for(owner, request_id, lock=False):
    query = AnswerRequest.objects.select_related(
        "company", "conversation", "user_message", "assistant_message"
    ).filter(pk=request_id, owner=owner, company__owner=owner)
    if lock:
        query = query.select_for_update(of=("self",))
    request = query.first()
    if request is None:
        raise NotFound("回答请求不存在。")
    conversation_for(owner, request.conversation_id)
    if (
        request.conversation.company_id != request.company_id
        or request.user_message.conversation_id != request.conversation_id
        or request.user_message.owner_id != owner.pk
        or request.user_message.role != "user"
        or (
            request.assistant_message_id
            and (
                request.assistant_message.owner_id != owner.pk
                or request.assistant_message.conversation_id != request.conversation_id
            )
        )
    ):
        raise NotFound("回答请求绑定不可访问。")
    return request


# 功能：创建一次用户提问及回答请求。
# 输入：`owner` 认证用户，`data` 含 conversation_id/content/client_key。
# 输出：AnswerRequest 与是否首次创建的布尔值。
# 逻辑：客户端幂等键先于活动任务检查，同内容重传返回原请求；事务写入消息及任务。
# 约束：普通历史消息不会被自动升级为模型请求；活动会话拒绝第二个新问题。
@transaction.atomic
def submit(owner, data):
    contracts.fields(data, {"conversation_id", "content", "client_key"})
    conversation_id = contracts.identifier(data["conversation_id"])
    client_key = contracts.identifier(data["client_key"])
    content = contracts.text(data["content"], "content")
    lock_owner(owner)
    conversation = conversation_for(owner, conversation_id)
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
        company=conversation.company,
        conversation=conversation,
        user_message=message,
    )
    logger.info("chat_submitted request_id=%s owner_id=%s", request.pk, owner.pk)
    return request, True


# 功能：为失败回答创建可追踪的新尝试。
# 输入：`owner` 当前员工，`request_id` 原请求 UUID。
# 输出：新请求与首次创建标志；重复点击返回原后继。
# 逻辑：保留原用户消息及失败记录，拒绝会话有活动任务或已有后续问题时重试旧问题。
# 约束：不自动重试，不复活原 request_id，不覆盖历史回答。
@transaction.atomic
def retry(owner, request_id):
    lock_owner(owner)
    old = request_for(owner, request_id, lock=True)
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
        company=old.company,
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


# 功能：原子领取当前员工的一条任务并冻结历史。
# 输入：`owner` Agent 服务令牌绑定员工。
# 输出：严格六字段请求或 None。
# 逻辑：员工锁保证并发领取不重复；历史截止于原问题之前，最近 20 条恢复时间正序。
# 约束：撤销/归档请求显式失败，不阻挡后续有效任务；模型不在事务内调用。
@transaction.atomic
def claim(owner):
    lock_owner(owner)
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
                owner=owner,
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
            "company_id": str(request.company_id),
            "user_message_id": str(message.pk),
            "question": message.content,
            "recent_history": request.recent_history,
        }
    return None


# 功能：返回请求绑定的稳定证据。
# 输入：`owner` 当前 Agent 员工，`request_id` 请求 UUID，`scope` internal/external。
# 输出：首次生成并持久化的 internal AnswerContext。
# 逻辑：只对 processing 提供上下文；公司行锁与既有版本更新协调，后续读取返回同一快照。
# 约束：external 尚未启用时明确拒绝；不吞数据库错误或伪装空资料。
@transaction.atomic
def context_for(owner, request_id, scope):
    if scope not in ("internal", "external"):
        raise ValidationError("scope 必须为 internal 或 external。")
    lock_owner(owner)
    request = request_for(owner, request_id, lock=True)
    if request.status != "processing":
        raise InvalidState("只能读取处理中的请求上下文。")
    if scope == "external":
        raise InvalidState("当前未启用外部知识。")
    if request.context_snapshot is None:
        request.company = Company.objects.select_for_update().get(
            pk=request.company_id, owner=owner
        )
        request.context_snapshot = build_context(request)
        request.save(update_fields=["context_snapshot"])
        logger.info(
            "chat_context_frozen request_id=%s revision=%s customer_items=%s knowledge_items=%s",
            request.pk,
            request.company.revision,
            len(request.context_snapshot["customer_context"]),
            len(request.context_snapshot["context_items"]),
        )
    return request.context_snapshot


# 功能：保存 Agent 的一次最终结果。
# 输入：`owner` 令牌绑定员工，`data` 严格回报对象。
# 输出：request_id/saved/duplicate/assistant_message_id。
# 逻辑：终态精确去重，引用仅从该请求证据快照匹配；消息、引用和状态同事务写入。
# 约束：不要求零引用回答预先读取上下文；不创建失败助手正文；拒绝迟到或异值结果。
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
        allowed = {
            tuple(
                row[key] for key in ("source_id", "source_type", "title_or_label")
            ): row
            for row in [
                *snapshot.get("customer_context", []),
                *snapshot.get("context_items", []),
            ]
        }
        cited = []
        for citation in result["citations"]:
            key = tuple(
                citation[field]
                for field in ("source_id", "source_type", "title_or_label")
            )
            if key not in allowed:
                raise ValidationError("引用不属于本请求实际提供的证据。")
            cited.append(allowed[key])
        if result["status"] == "completed":
            request.assistant_message = Message.objects.create(
                owner=owner,
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
        "assistant_message_id": str(request.assistant_message_id)
        if request.assistant_message_id
        else None,
    }


# 功能：投影已授权请求的浏览器状态。
# 输入：`request` 已由权限服务校验的 AnswerRequest。
# 输出：安全字段、时间、助手消息标识及有序引用证据。
# 逻辑：失败不回传未经验证的文本，引用内容来自后端快照。
# 约束：不输出内部历史、完整 context_snapshot、凭证或原始异常。
def request_data(request):
    return {
        "request_id": str(request.pk),
        "conversation_id": str(request.conversation_id),
        "user_message_id": str(request.user_message_id),
        "assistant_message_id": str(request.assistant_message_id)
        if request.assistant_message_id
        else None,
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


# 功能：人工确认后终止中断任务。
# 输入：`owner` 指定员工，`request_id` 明确任务 UUID。
# 输出：已失败请求。
# 逻辑：终态不变，processing 变为 failed，拒绝旧 Agent 随后的任何新结果。
# 约束：只能由管理命令在维护者确认中断后调用，不隐式回队或重新调用模型。
@transaction.atomic
def interrupt(owner, request_id):
    lock_owner(owner)
    request = (
        AnswerRequest.objects.select_for_update()
        .filter(pk=request_id, owner=owner)
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
