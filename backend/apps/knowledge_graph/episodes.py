"""Responsibility: Coordinate external input, same-user association context, model calls, and rebuildable graph publication.
Implementation: Call the model outside transactions; recheck the complete context digest before commit to reject stale concurrent associations; source keys are idempotent and records immutable.
Relationships: Shared business schema, model protocol, Episode, and existing synchronizer; HTTP and management commands share this service, with same-user context defining association scope.
Directory:
- context_snapshot: Obtain consistent, current association context within explicit size bounds.
- ingest: Accept one natural-language or structured input and build the graph automatically.
- retract: Withdraw the current user's source and request graph rebuilding.
- episode_data: Return ingestion and projection state.
Variable index:
- logger: Input, concurrency, and publication logs without original text.
- MAX_CONTEXT_CHARACTERS: Explicit model-context character limit; reject excess input instead of truncating.
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


# Function: Read linkable entities, identity fields, and existing observed facts from a synchronized graph.
# Inputs: `owner_id`: authenticated identity; `locked`: whether the caller already holds the user graph lock, default False.
# Outputs: Context dictionary and digest; explicitly reject unready graphs or excessive size.
# Logic: Entities, fields, and generation come from one REPEATABLE READ snapshot and include only the current identity's business types.
# Constraints: No silent candidate truncation or other-user reads; do not give the model account credentials or all original text.
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


# Function: Automatically associate external input with the internal graph.
# Inputs: `owner_id`: authenticated user; `source_key`: immutable idempotency key; `observed_at`: timezone-aware source time; exactly one of `text` or `records`; `email_id`: supplied only by the internal email command.
# Outputs: Episode; model/validation failures raise explicit exceptions without persistence, retaining candidates only in private diagnostic attributes excluded from HTTP serialization; post-commit synchronization failures retain source and failure events.
# Logic: Synchronize existing sources, build context, validate model text, and run separate entity resolution while retaining original suggestions; recheck under lock and atomically create the source.
# Constraints: Do not modify business tables or retry the model; a shared account lock prevents reset during processing, with persistent reset state still checked under lock.
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
                # Available only to explicit controlled diagnostics; excluded from API detail and generic logs.
                error.graph_candidate = result
                error.graph_model_audit = audit
                raise error from exc
        else:
            result, _ = build_records(records, context["entities"])
            audit = {"protocol": VERSION, "mode": "schema_observation", "model_called": False}
        # Do not hold a database transaction across the model call; commit uses the same per-user lock as graph workers.
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


# Function: Explicitly withdraw one external source.
# Inputs: `owner_id`: current identity; `episode_id`: source UUID to withdraw.
# Outputs: Updated Episode; nonexistent or inaccessible sources raise DoesNotExist.
# Logic: Mark only the current user's source withdrawn; triggers create maintenance events and synchronization removes its independent support.
# Constraints: Do not delete other sources or historical lineage, or fall back to another model.
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


# Function: Return the source processing summary.
# Inputs: `episode`: source retrieved under current-user authorization.
# Outputs: Source identity, candidates, model audit data, and current graph synchronization state.
# Logic: Distinguish a saved source from a currently readable graph; include original text for manual verification.
# Constraints: Do not describe model associations as human-confirmed or expose other owners' data.
def episode_data(episode):
    return {"id": str(episode.pk), "source_key": episode.source_key, "observed_at": episode.observed_at,
            "created_at": episode.created_at, "retracted": episode.retracted, "text": episode.text,
            "extraction": episode.extraction, "model_audit": episode.model_audit, "sync": graph_status(episode.owner_id),
            "interpretation": "Evidence-backed observations; model associations are not verified business transactions"}
