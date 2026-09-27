"""Responsibility: Clear the current account's internal data while retaining account, password, and authenticated identity.
Implementation: Explicit ownership rules include chat approvals and independent action proposals through their request owner, graph evidence, and dependent joins; deferred constraints protect cross-account references, and files/sessions are cleaned after commit.
Relationships: ``AccountReset``, ``ResetView``, and the account exclusive lock; covers accounts, crm, sales, chat, agent_tools, vectors, and knowledge_graph.
Directory:
- reset_error: Construct a standard error response with a request ID.
- scoped_records: Build an account-scoped query for every business model.
- clear_sessions: Clear business cache from all database sessions for the current account.
- clean_files: Delete registered private attachments.
- reset_account: Perform or continue an idempotent cleanup.
- ResetView: Current-account cleanup entry point.
- ResetView.post: Execute cleanup and return version and browser-cleanup instructions.
Variable index:
- logger: Stage, count, and safe-error logger.
- BUSINESS_APPS: Business applications participating in reset.
- INDIRECT_OWNERS: Ownership paths for models that do not declare ``owner`` directly.
- AUTH_KEYS: Django login-session fields that must be retained.
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
BUSINESS_APPS = {"accounts", "crm", "sales", "chat", "agent_tools", "vectors", "knowledge_graph"}
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
    "chat.Citation": "request__owner", "chat.ToolRead": "request__owner", "chat.ChatApproval": "request__owner",
    "chat.ActionProposal": "request__owner",
    "knowledge_graph.Support": "fact__owner", "knowledge_graph.Change": "owner_id",
}
AUTH_KEYS = {SESSION_KEY, BACKEND_SESSION_KEY, HASH_SESSION_KEY}


# Function: Construct an account-cleanup error using the standard contract.
# Inputs: ``request`` is the current request, ``code`` is the error code, ``detail`` is safe detail, and ``status`` is the HTTP status.
# Outputs: ``Response`` containing ``error`` and ``request_id``.
# Logic: Use only the server-generated request ID to correlate cleanup-stage logs.
# Constraints: Does not emit database, file, or credential content.
def reset_error(request, code, detail, status):
    return Response({"error": {"code": code, "detail": detail}, "request_id": getattr(request, "request_id", None)}, status=status)


# Function: Enumerate business records explicitly owned by the current account.
# Inputs: ``owner`` is the authenticated account.
# Outputs: A list of models and scoped QuerySets; a new model without an ownership rule raises ``ValueError``.
# Logic: Prefer direct owner, use explicit paths for child tables, filter graph events by scalar ``owner_id``, explicitly include dependent join tables, and remove memberships and grants in their original scopes.
# Constraints: Does not extend business ownership through visible permissions or select User, auth groups, or reset-coordination state.
def scoped_records(owner):
    selections = []
    for model in apps.get_models():
        if model._meta.app_label not in BUSINESS_APPS or model._meta.label in {"accounts.User", "accounts.AccountReset"}:
            continue
        fields = {field.name for field in model._meta.fields}
        path = "owner" if "owner" in fields else INDIRECT_OWNERS.get(model._meta.label)
        if path is None:
            raise ValueError(f"Missing reset ownership: {model._meta.label}")
        scope = Q(**{path: owner.pk if path == "owner_id" else owner})
        if model._meta.label == "sales.Membership":
            scope |= Q(user=owner) | Q(team__owner=owner)
        if model._meta.label == "sales.CompanyGrant":
            scope |= Q(team__owner=owner) | Q(company__owner=owner)
        if model._meta.label == "sales.Notification":
            scope |= Q(follow_up__owner=owner)
        selections.append((model, model.objects.filter(scope)))
    graph_inputs = apps.get_model("knowledge_graph", "Derivation").inputs.through
    selections.append((graph_inputs, graph_inputs.objects.filter(derivation__owner=owner)))
    return selections


# Function: Remove business cache and unfinished OAuth state from the account's database sessions.
# Inputs: ``owner`` and ``generation``; ``current`` is the Django session that initiated the operation.
# Outputs: None; persistent and current-request sessions retain only authentication keys and the new version.
# Logic: Decode only unexpired sessions to identify the account; do not call global ``cache.clear`` or delete sessions for other accounts.
# Constraints: Account and password hash remain unchanged; stale pages still need version headers and broadcast handling for in-memory state.
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


# Function: Clean actual attachments in the committed manifest.
# Inputs: ``state`` is reset-state data holding the account exclusive lock.
# Outputs: None; on failure the complete manifest remains so a later explicit request can continue.
# Logic: Verify each path belongs to ``private_uploads/owner`` and treat a missing file as already cleaned.
# Constraints: Does not delete directories or follow paths outside the owner root; reset can be reported complete only after every manifest entry succeeds.
def clean_files(state):
    root = (Path(settings.BASE_DIR) / "private_uploads").resolve()
    owner_root = root / str(state.owner_id)
    for key in state.pending_files:
        target = (root / key).resolve()
        if not target.is_relative_to(owner_root) or target == owner_root:
            raise ValueError("Invalid account attachment path")
        target.unlink(missing_ok=True)


# Function: Perform database, file, and session cleanup under the account exclusive lock.
# Inputs: ``owner`` and idempotent UUID ``key``; ``session`` is the current logged-in session.
# Outputs: ``AccountReset``; database errors roll back, while attachment errors retain ``cleaning`` state.
# Logic: Return immediately for a historic idempotency key; freeze primary-key sets and clear mutable assignee relations on other records, delete and validate foreign keys in one transaction, and permit explicit file-cleanup recovery.
# Constraints: Deletes only selected records and the owner's graph events created in the same transaction; direct SQL avoids ORM implicit cascades to other accounts; does not disable constraints or modify ``User``.
def reset_account(owner, key, session):
    state, _ = AccountReset.objects.get_or_create(owner=owner)
    if not state.cleaning and str(key) in state.keys:
        return state
    if not state.cleaning:
        with transaction.atomic():
            selections = [(model, list(query.values_list("pk", flat=True))) for model, query in scoped_records(owner)]
            # Clear mutable assignee relations from other records to prevent due reminders from creating this account's data again.
            for model, _ in selections:
                if any(field.name == "assigned_to" for field in model._meta.fields):
                    model.objects.filter(assigned_to=owner).exclude(owner=owner).update(assigned_to=None)
            attachments = apps.get_model("sales", "Attachment")
            state.pending_files = list(attachments.objects.filter(owner=owner).values_list("storage_key", flat=True))
            with connection.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS ALL DEFERRED")
                for model, ids in selections:
                    # Bind primary-key values as parameters; identifiers come from Django model metadata rather than request content.
                    table = connection.ops.quote_name(model._meta.db_table)
                    column = connection.ops.quote_name(model._meta.pk.column)
                    for offset in range(0, len(ids), 1000):
                        chunk = ids[offset:offset + 1000]
                        cursor.execute(f"DELETE FROM {table} WHERE {column} IN ({','.join(['%s'] * len(chunk))})", chunk)
                # Source deletion triggers new events; clear events from this pass too, avoiding residual source identifiers.
                apps.get_model("knowledge_graph", "Change").objects.filter(owner_id=owner.pk).delete()
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


# Function: Provide an internal-data clearing endpoint that retains login identity.
# Logic: Inherit session authentication and CSRF protection, take the account only from ``request.user``, and do not validate business content.
# Constraints: Does not accept Agent or Tool identity; returns ``completed`` only after database, attachment, and session cleanup succeed.
class ResetView(APIView):
    # Function: Execute reset for the current account.
    # Inputs: Login state and ``Idempotency-Key`` from ``request``; no business content is required.
    # Outputs: 200 on completion, 400 for invalid idempotency key, 409 for busy or cross-account references, and 503 when attachment or session cleanup must continue.
    # Logic: The exclusive lock covers transaction and attachment stages; error logs contain only type and account; success requires the client to clear in-memory state and cache.
    # Constraints: ``Clear-Site-Data`` clears only HTTP cache and retains the login cookie; the frontend owns account storage and cross-page broadcast.
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
