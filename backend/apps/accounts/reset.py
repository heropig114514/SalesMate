"""职责：清空当前账号内部数据，保留账号、密码及认证身份。
实现：显式归属规则生成删除集合；延迟外键约束保护跨账号引用；事务提交后清理文件和会话。
关联：AccountReset、ResetView、账号独占锁；模型列表覆盖 accounts/crm/sales/chat/agent_tools/vectors。
目录：
- reset_error：构造带请求 ID 的标准错误响应。
- scoped_records：生成每个业务模型的账号限定查询。
- clear_sessions：清理当前账号全部数据库会话的业务缓存。
- clean_files：删除已登记的私有附件。
- reset_account：执行或继续一次幂等清理。
- ResetView：当前账号的清理入口。
- ResetView.post：执行清理并返回版本及浏览器清理指示。
变量索引：
- logger：阶段、数量与安全错误日志。
- BUSINESS_APPS：参与重置的业务应用。
- INDIRECT_OWNERS：不直接声明 owner 的模型归属路径。
- AUTH_KEYS：必须保留的 Django 登录会话字段。
"""
import logging
from pathlib import Path
import uuid

from django.apps import apps
from django.conf import settings
from django.contrib.auth import SESSION_KEY, BACKEND_SESSION_KEY, HASH_SESSION_KEY
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.db import connection, transaction, IntegrityError
from django.db.models import Q
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiTypes
from common.serializers import ApiErrorSerializer

from .reset_models import AccountReset
from .reset_locks import account_lock, ResetBusy

logger = logging.getLogger("salesmate.account_reset")
BUSINESS_APPS = {"accounts", "crm", "sales", "chat", "agent_tools", "vectors"}
INDIRECT_OWNERS = {
    "crm.GmailCredential": "mailbox__owner", "crm.QQCredential": "mailbox__owner",
    "crm.QQSyncCheckpoint": "mailbox__owner", "crm.Contact": "company__owner",
    "crm.Email": "mailbox__owner", "crm.Extraction": "email__mailbox__owner",
    "crm.AnalysisInput": "company__owner", "crm.Analysis": "snapshot__company__owner",
    "crm.Score": "analysis__snapshot__company__owner", "crm.Job": "company__owner",
    "crm.MailboxSyncRun": "mailbox__owner", "crm.EmailProcessingJob": "run__mailbox__owner",
    "crm.StoredMessage": "mailbox__owner", "crm.SyncCheckpoint": "mailbox__owner",
    "crm.SnapshotSource": "snapshot__company__owner",
    "crm.SnapshotInvalidation": "snapshot__company__owner",
    "crm.ExtractionRepair": "email__mailbox__owner",
    "chat.Citation": "request__owner", "chat.ToolRead": "request__owner",
}
AUTH_KEYS = {SESSION_KEY, BACKEND_SESSION_KEY, HASH_SESSION_KEY}


# 功能：构造统一协议的账号清理错误。
# 输入：`request` 为当前请求，`code` 为错误码，`detail` 为安全说明，`status` 为 HTTP 状态。
# 输出：包含 error 和 request_id 的 Response。
# 逻辑：只采用服务端已生成的请求 ID，便于关联清理阶段日志。
# 约束：不输出数据库、文件或凭证正文。
def reset_error(request, code, detail, status):
    return Response({"error": {"code": code, "detail": detail}, "request_id": getattr(request, "request_id", None)}, status=status)


# 功能：枚举明确归属当前账号的业务记录。
# 输入：`owner` 为已认证账号。
# 输出：模型及限定 QuerySet 列表；新模型缺少归属规则时抛 ValueError。
# 逻辑：直接 owner 优先，子表使用显式路径；成员、授权和依赖通知随账号或其团队解除。
# 约束：不沿可见权限扩展业务所有权，不选择 User、认证组或重置协调状态。
def scoped_records(owner):
    selections = []
    for model in apps.get_models():
        if model._meta.app_label not in BUSINESS_APPS or model._meta.label in {"accounts.User", "accounts.AccountReset"}:
            continue
        fields = {field.name for field in model._meta.fields}
        path = "owner" if "owner" in fields else INDIRECT_OWNERS.get(model._meta.label)
        if path is None:
            raise ValueError(f"Missing reset ownership: {model._meta.label}")
        scope = Q(**{path: owner})
        if model._meta.label == "sales.Membership":
            scope |= Q(user=owner) | Q(team__owner=owner)
        if model._meta.label == "sales.CompanyGrant":
            scope |= Q(team__owner=owner) | Q(company__owner=owner)
        if model._meta.label == "sales.Notification":
            scope |= Q(follow_up__owner=owner)
        selections.append((model, model.objects.filter(scope)))
    return selections


# 功能：删除账号各个数据库会话中的业务缓存和未完成 OAuth 状态。
# 输入：`owner`、`generation`；`current` 为发起操作的 Django session。
# 输出：无；持久会话和当前请求会话只保留认证键及新版本。
# 逻辑：仅解码未过期会话以核对账号；不调用全局 cache.clear 或删除其他账号会话。
# 约束：账号与密码哈希不变；旧页面仍需版本头和广播处理内存状态。
def clear_sessions(owner, generation, current):
    for record in Session.objects.filter(expire_date__gt=timezone.now()).iterator():
        payload = record.get_decoded()
        if str(payload.get(SESSION_KEY)) != str(owner.pk):
            continue
        kept = {key: value for key, value in payload.items() if key in AUTH_KEYS}
        kept["account_data_generation"] = generation
        record.session_data = SessionStore().encode(kept)
        record.save(update_fields=["session_data"])
    for key in list(current.keys()):
        if key not in AUTH_KEYS:
            del current[key]
    current["account_data_generation"] = generation


# 功能：清理已提交清单中的实际附件。
# 输入：`state` 为持有账号独占锁的 AccountReset。
# 输出：无；失败保持完整清单，下一次显式请求可继续。
# 逻辑：逐一核对路径属于 private_uploads/owner，缺失文件视为已清理。
# 约束：不删除目录、不跟随越界路径；清单全部成功后才可报告重置完成。
def clean_files(state):
    root = (Path(settings.BASE_DIR) / "private_uploads").resolve()
    owner_root = root / str(state.owner_id)
    for key in state.pending_files:
        target = (root / key).resolve()
        if not target.is_relative_to(owner_root) or target == owner_root:
            raise ValueError("Invalid account attachment path")
        target.unlink(missing_ok=True)


# 功能：在账号独占锁下执行数据库、文件、会话清理。
# 输入：`owner`、`key` 为幂等 UUID、`session` 为当前登录会话。
# 输出：AccountReset；数据库错误回滚，附件错误保留 cleaning 状态。
# 逻辑：历史幂等键直接返回；冻结主键集合并解除他人记录的可变负责人关联，同一事务删除及验证外键；文件清理可显式恢复。
# 约束：只删除选择集合，直接 SQL 避免 ORM 隐式级联到其他账号；不禁用约束、不修改 User。
def reset_account(owner, key, session):
    state, _ = AccountReset.objects.get_or_create(owner=owner)
    if not state.cleaning and str(key) in state.keys:
        return state
    if not state.cleaning:
        with transaction.atomic():
            selections = [(model, list(query.values_list("pk", flat=True))) for model, query in scoped_records(owner)]
            # 清空账号在他人记录上的可变负责人关系，防止到期提醒再次生成本账号数据。
            for model, _ in selections:
                if any(field.name == "assigned_to" for field in model._meta.fields):
                    model.objects.filter(assigned_to=owner).exclude(owner=owner).update(assigned_to=None)
            attachments = apps.get_model("sales", "Attachment")
            state.pending_files = list(attachments.objects.filter(owner=owner).values_list("storage_key", flat=True))
            with connection.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS ALL DEFERRED")
                for model, ids in selections:
                    # 参数绑定主键值；标识符来自 Django 模型元数据而非请求正文。
                    table = connection.ops.quote_name(model._meta.db_table)
                    column = connection.ops.quote_name(model._meta.pk.column)
                    for offset in range(0, len(ids), 1000):
                        chunk = ids[offset:offset + 1000]
                        cursor.execute(f"DELETE FROM {table} WHERE {column} IN ({','.join(['%s'] * len(chunk))})", chunk)
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            apps.get_model("admin", "LogEntry").objects.filter(user=owner).delete()
            state.generation += 1
            state.key = key
            state.keys.append(str(key))
            state.cleaning = True
            state.save()
        logger.info("account_reset_database_done owner_id=%s generation=%s records=%s", owner.pk, state.generation, sum(len(ids) for _, ids in selections))
    clean_files(state)
    clear_sessions(owner, state.generation, session)
    state.pending_files = []
    state.cleaning = False
    state.save(update_fields=["pending_files", "cleaning"])
    logger.info("account_reset_completed owner_id=%s generation=%s", owner.pk, state.generation)
    return state


# 功能：提供保留登录身份的内部数据清空接口。
# 逻辑：继承会话认证与 CSRF，账号只取 request.user；不校验业务内容。
# 约束：不接受 Agent/Tool 身份；仅成功完成数据库、附件和会话清理才返回 completed。
class ResetView(APIView):
    # 功能：执行当前账号重置。
    # 输入：`request` 的登录态和 Idempotency-Key；无业务正文要求。
    # 输出：200 完成、400 非法幂等键、409 忙碌/跨账号引用、503 附件或会话清理待继续。
    # 逻辑：独占锁覆盖事务和附件阶段；错误日志只包含类型及账号；成功要求客户端清理内存和缓存。
    # 约束：Clear-Site-Data 只清理 HTTP cache，保留登录 cookie；前端负责账户存储和跨页广播。
    @extend_schema(
        tags=["accounts"], request=None,
        description="Clear the authenticated account's internal data and caches while preserving the user record, password and login. Retry the same operation with the same Idempotency-Key.",
        parameters=[OpenApiParameter("Idempotency-Key", OpenApiTypes.UUID, OpenApiParameter.HEADER, required=True)],
        responses={200: {"type": "object", "properties": {
            "status": {"type": "string", "enum": ["completed"]},
            "generation": {"type": "integer"}, "owner_id": {"type": "integer"},
            "reset_id": {"type": "string", "format": "uuid"},
        }, "required": ["status", "generation", "owner_id", "reset_id"]},
            400: ApiErrorSerializer, 403: ApiErrorSerializer, 409: ApiErrorSerializer, 503: ApiErrorSerializer},
    )
    def post(self, request):
        try:
            key = uuid.UUID(request.headers.get("Idempotency-Key", ""))
        except ValueError:
            return reset_error(request, "invalid_reset_key", "请提供 UUID 格式的 Idempotency-Key。", 400)
        try:
            with account_lock(request.user.pk, exclusive=True):
                state = reset_account(request.user, key, request.session)
        except ResetBusy as error:
            return reset_error(request, "account_reset_busy", str(error), 409)
        except IntegrityError:
            if AccountReset.objects.filter(owner=request.user, cleaning=True).exists():
                logger.error("account_reset_session_cleanup_failed owner_id=%s action=resume_cleanup", request.user.pk)
                return reset_error(request, "account_reset_incomplete", "业务数据已清空，但会话清理未完成，请使用同一操作继续。", 503)
            logger.error("account_reset_dependency_conflict owner_id=%s action=inspect_shared_references", request.user.pk)
            return reset_error(request, "account_reset_shared_reference", "存在其他账号引用的数据，本次数据库清理已回滚，请先处理共享关联。", 409)
        except (OSError, ValueError) as error:
            logger.error("account_reset_cleanup_failed owner_id=%s error_type=%s action=inspect_storage_and_repeat_reset", request.user.pk, type(error).__name__)
            return reset_error(request, "account_reset_incomplete", "清理未全部完成，请检查服务器文件权限或归属配置，再使用同一操作重试。", 503)
        response = Response({"status": "completed", "generation": state.generation, "owner_id": request.user.pk, "reset_id": str(state.key)})
        response["Clear-Site-Data"] = '"cache"'
        response["Cache-Control"] = "no-store"
        return response
