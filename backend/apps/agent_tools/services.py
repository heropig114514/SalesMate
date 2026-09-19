"""职责：执行工具授权、严格输入、幂等回执和真人确认协议。
实现：查询不写业务；写调用以数据库唯一回执串行化；高影响操作只创建冻结提案。
关联：registry 定义能力，dispatch 复用业务逻辑；仅 Session 视图可以调用 decide。
目录：
- catalog：返回当前身份的工具目录。
- authorize：复核工具授权。
- response_data：规范业务响应回执。
- invoke：执行查询或幂等写入。
- proposal_data：投影用户提案。
- decide：确认或取消冻结提案。
变量索引：
- logger：只记录工具、用户、回执、阶段和异常类型。
"""

import hashlib
import json
import logging
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


# 功能：列出可用工具。
# 输入：`actor` 已登录用户、`credential` 可选工具凭证、`category` 可选分类。
# 输出：公开工具描述列表。
# 逻辑：同一名单决定发现和执行，不暴露内部处理器。
# 约束：不通过目录授予权限，不返回业务数据或凭证。
def catalog(actor, credential=None, category=None):
    if not actor.is_active:
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
            }
        }
        for name, spec in build_registry().items()
        if (not credential or name in credential.allowed_tools)
        and (not category or spec["category"] == category)
    ]


# 功能：复核执行身份。
# 输入：`actor`、`credential`、`name` 工具名称。
# 输出：无。
# 逻辑：确认用户有效且授权属于相同用户，再复核白名单。
# 约束：业务实体权限继续由实际处理器检查。
def authorize(actor, credential, name):
    if not actor.is_active:
        raise PermissionDenied("用户已停用。")
    if credential:
        if credential.owner_id != actor.pk:
            raise PermissionDenied("工具授权归属不一致。")
        check_credential(credential, name)


# 功能：生成结果信封。
# 输入：`response` 为既有处理器响应。
# 输出：可持久化字典。
# 逻辑：保留业务 HTTP 状态、ETag 和内容；外部动作待确认时明示 confirmation_required。
# 约束：accepted/pending 状态不是外部执行成功。
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


# 功能：调用一个授权业务工具。
# 输入：`actor`、`credential`、`name`、`arguments`、`idempotency_key` 可选 UUID。
# 输出：带工具名和回执的结果。
# 逻辑：先校验和授权，写操作与回执同事务；仅锁凭证自身，避免先锁用户再等待回执形成倒序锁；确认工具仅冻结提案。
# 约束：所有写入要求幂等键；失败回滚、不重试；缓存回执是用户自己的历史，不当作当前数据。
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
        if spec["executionMode"] == "read":
            if idempotency_key is not None:
                raise ValidationError("只读工具不接受幂等键。")
            result = {"tool": name, **response_data(execute(actor, spec, arguments))}
        else:
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


# 功能：投影待确认内容。
# 输入：`proposal` 已授权提案。
# 输出：工具输入、期限、状态和用户确认接口。
# 逻辑：以冻结输入供界面完整预览。
# 约束：确认接口只接受 Session；工具凭证不能代替用户批准。
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
            "confirmation_authentication": "user_session_csrf",
        }
    )


# 功能：提交真人决定。
# 输入：`actor` 已登录用户、`proposal_id`、`decision` 为 approve/cancel。
# 输出：提案状态及真实业务结果。
# 逻辑：锁定提案，核对期限、原授权与最新权限，执行冻结参数。
# 约束：调用方必须为 Session-only 视图；版本冲突回滚并保持 pending，不修改冻结输入。
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
