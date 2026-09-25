"""职责：协调外部信息输入、同用户关联上下文、模型调用和可重建图谱发布。
实现：模型调用在事务外执行；提交前复核整个上下文摘要，拒绝并发过期关联；来源键幂等且记录不可变。
关联：业务 schema、模型协议、Episode 及现有同步器；HTTP/管理命令共用，同用户上下文决定关联范围。
目录：
- context_snapshot：获取一致、当前且限定规模的关联上下文。
- ingest：接收一份自然语言或结构化输入并自动建图。
- retract：撤回本人来源并请求图谱重建。
- episode_data：返回输入处理及投影状态。
变量索引：
- logger：输入、并发和发布日志，不记录原文。
- MAX_CONTEXT_CHARACTERS：明确的模型上下文字符上限，超过时拒绝，不截断。
"""
import hashlib
import json
import logging
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError
from apps.accounts.reset_locks import account_lock
from apps.accounts.reset_models import AccountReset
from apps.crm.access import Conflict
from .business_schema import catalog, mail_text
from .models import Entity, Episode, Fact
from .projection import canonical
from .semantic_contract import messages, validate_extraction, VERSION
from .semantic_provider import generate
from .entity_resolution import resolve_entities
from .structured_input import build_records, IDENTITY_FIELDS
from .sync import sync_owner, require_capture, request_sync
from .views import begin_read, graph_status, GraphUnavailable

logger = logging.getLogger("salesmate.graph.episodes")
MAX_CONTEXT_CHARACTERS = 80000


# 功能：读取已同步图谱的可关联实体、身份字段和既有观察事实。
# 输入：`owner_id` 为认证身份；`locked` 表示调用方已持有用户图谱锁，默认 False。
# 输出：context 字典及摘要；未就绪或规模超过上限时明确拒绝。
# 逻辑：实体、字段和代次来自同一 REPEATABLE READ 快照；只包含当前身份的业务类型。
# 约束：不做静默候选截断，不读取其他用户，不向模型提供账号凭据或全部原文。
def context_snapshot(owner_id, *, locked=False):
    schema = catalog()
    with transaction.atomic():
        if locked:
            require_capture()
            status = graph_status(owner_id)
            if not status["current"]:
                raise GraphUnavailable()
        else:
            status = begin_read(owner_id)
        nodes = list(Entity.objects.filter(owner_id=owner_id, active=True).order_by("id"))
        candidates = {str(node.pk): {"id": str(node.pk), "kind": node.kind.removeprefix("external."), "label": node.label,
                                   "source_id": node.source_id if not node.kind.startswith("external.") else None, "fields": {}}
                      for node in nodes if node.kind.removeprefix("external.") in schema}
        for fact in Fact.objects.filter(owner_id=owner_id, status="active", object__isnull=True).order_by("id"):
            key = str(fact.subject_id)
            if key in candidates:
                field = fact.predicate.removeprefix("field:").rsplit(".", 1)[-1]
                if field in IDENTITY_FIELDS and isinstance(fact.value, str):
                    candidates[key]["fields"][field] = fact.value
        facts = [{"subject": str(fact.subject_id), "predicate": fact.predicate, "object": str(fact.object_id) if fact.object_id else None,
                  "value": fact.value, "status": fact.status} for fact in Fact.objects.filter(owner_id=owner_id, origin="extraction").exclude(status="unsupported").order_by("id")
                 if str(fact.subject_id) in candidates and (fact.object_id is None or str(fact.object_id) in candidates)]
        compact_schema = {kind: {name: field.get("target", field["type"]) for name, field in fields.items()} for kind, fields in schema.items()}
        context = {"schema": compact_schema, "entities": list(candidates.values()), "facts": facts, "generation": status["generation"]}
    encoded = canonical(context)
    if len(encoded) > MAX_CONTEXT_CHARACTERS:
        raise ValidationError("当前图谱超出首版关联上下文上限；请显式扩展分层候选检索，不会静默裁剪。")
    return context, hashlib.sha256(encoded.encode()).hexdigest()


# 功能：将外部输入自动关联到内部图谱。
# 输入：`owner_id` 认证用户；`source_key` 不可变幂等键；`observed_at` 带时区来源时间；`text` 或 `records` 必须二选一；`email_id` 仅内部邮件命令提供。
# 输出：Episode；模型或校验失败为明确异常且不入库；异常私有诊断属性保留候选，HTTP 不序列化；提交后同步失败保留来源和失败事件。
# 逻辑：先同步已有来源，构造上下文；模型原文校验后执行独立实体解析并保留原建议；持锁复核后原子创建来源。
# 约束：不修改业务表；不重试模型；账号共享锁防止处理中清空，锁内仍检查持久清理状态。
def ingest(owner_id, source_key, observed_at, *, text=None, records=None, email_id=None):
    if not isinstance(source_key, str) or not source_key.strip() or len(source_key) > 200 or not isinstance(observed_at, timezone.datetime) or timezone.is_naive(observed_at):
        raise ValidationError("source_key 与带时区 observed_at 必须有效。")
    if (text is None) == (records is None) or (text is not None and (not isinstance(text, str) or not text.strip() or len(text) > 12000)):
        raise ValidationError("提供非空 text（最多12000字符）或 records，二者不能同时使用。")
    raw = text if text is not None else json.dumps(records, ensure_ascii=False, sort_keys=True)
    if len(raw) > 60000:
        raise ValidationError("结构化输入超过60000字符。")
    with account_lock(owner_id):
        if AccountReset.objects.filter(owner_id=owner_id, cleaning=True).exists():
            raise Conflict("账号清理尚未完成。")
        require_capture()
        source_email = None
        if email_id is not None:
            from apps.crm.models import Email
            email = Email.objects.get(pk=email_id, mailbox__owner_id=owner_id, direction="inbound", business_classification="business")
            if text != mail_text(email) or observed_at != email.sent_at:
                raise Conflict("邮件内容已变化；请重新读取原邮件后显式输入。")
            source_email = {"id": str(email.pk), "text_sha256": hashlib.sha256(text.encode()).hexdigest()}
        old = Episode.objects.filter(owner_id=owner_id, source_key=source_key).first()
        if old is not None:
            if old.text != raw or old.observed_at != observed_at:
                raise Conflict("来源键已用于不同内容；新观察请使用新的 source_key。")
            return old
        if not graph_status(owner_id)["ready"]:
            request_sync(owner_id)
        sync_owner(owner_id)
        context, fingerprint = context_snapshot(owner_id)
        logger.info("semantic_ingest_started owner_id=%s mode=%s", owner_id, "text" if text is not None else "records")
        if text is not None:
            stage = "local_model"
            result, audit = None, None
            try:
                result, audit = generate(messages(text, observed_at.isoformat(), context))
                stage = "validate_evidence"
                validate_extraction(result, text, context["entities"])
                stage = "resolve_entities"
                resolved, decisions = resolve_entities(result, context)
                validate_extraction(resolved, text, context["entities"])
                audit = {**audit, "raw_model_extraction": result, "entity_resolution": decisions}
                result = resolved
            except Exception as exc:
                reason = str(exc) if stage == "validate_evidence" and isinstance(exc, ValueError) else type(exc).__name__
                logger.error("semantic_ingest_failed owner_id=%s stage=%s reason=%s action=inspect_model_and_source", owner_id, stage, reason)
                error = APIException({"message": "本机模型生成或证据校验失败；未写入图谱，无自动重试。", "stage": stage, "reason": reason})
                error.status_code = 502
                # 仅供显式的受控诊断调用读取；不进入 API detail 或通用日志。
                error.graph_candidate = result
                error.graph_model_audit = audit
                raise error from exc
        else:
            result, _ = build_records(records, context["entities"])
            audit = {"protocol": VERSION, "mode": "schema_observation", "model_called": False}
        # 不跨模型调用持有数据库事务；提交阶段与图谱 Worker 使用同一用户锁。
        with transaction.atomic():
            from django.db import connection
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", [f"salesmate-kg:{owner_id}"])
            _, current = context_snapshot(owner_id, locked=True)
            if current != fingerprint:
                raise Conflict("关联期间业务图谱已变化；本次未写入，请显式重新提交。")
            old = Episode.objects.filter(owner_id=owner_id, source_key=source_key).first()
            if old is not None:
                if old.text != raw or old.observed_at != observed_at:
                    raise Conflict("来源键已存在且内容不同。")
                return old
            episode = Episode.objects.create(owner_id=owner_id, source_key=source_key, observed_at=observed_at, text=raw,
                                             extraction=result, model_audit={**audit, "context_sha256": fingerprint, "source_email": source_email})
        sync_owner(owner_id)
        logger.info("semantic_ingest_saved owner_id=%s episode=%s entities=%s facts=%s current=%s", owner_id, episode.pk, len(result["entities"]), len(result["facts"]), graph_status(owner_id)["current"])
        return episode


# 功能：显式撤回一份外部来源。
# 输入：`owner_id` 为当前身份；`episode_id` 为待撤回来源 UUID。
# 输出：更新后的 Episode；不存在或越权由 DoesNotExist 表示。
# 逻辑：仅将本人来源标记撤回，触发器生成维护事件，同步撤销独立支持。
# 约束：不删除其他来源、不回退模型、不删除历史血缘。
def retract(owner_id, episode_id):
    with account_lock(owner_id):
        if AccountReset.objects.filter(owner_id=owner_id, cleaning=True).exists():
            raise Conflict("账号清理尚未完成。")
        require_capture()
        episode = Episode.objects.get(owner_id=owner_id, pk=episode_id)
        episode.retracted = True
        episode.save(update_fields=["retracted"])
        sync_owner(owner_id)
        return episode


# 功能：返回来源处理摘要。
# 输入：`episode` 为已按当前用户授权取得的来源。
# 输出：来源身份、候选、模型审计及当前图谱同步状态。
# 逻辑：让调用方区别已保存来源与当前可读图谱；包含原文以支持人工核验。
# 约束：不把模型关联描述为人工确认，不暴露其他所有者数据。
def episode_data(episode):
    return {"id": str(episode.pk), "source_key": episode.source_key, "observed_at": episode.observed_at,
            "created_at": episode.created_at, "retracted": episode.retracted, "text": episode.text,
            "extraction": episode.extraction, "model_audit": episode.model_audit, "sync": graph_status(episode.owner_id),
            "interpretation": "Evidence-backed observations; model associations are not verified business transactions"}
