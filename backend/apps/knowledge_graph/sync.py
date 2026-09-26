"""Responsibility: Consume graph transaction events and atomically publish one user's graph.
Implementation: PostgreSQL consistent snapshots and per-user advisory locks prevent concurrent overwrites; acknowledge only events visible in this pass and persist failures explicitly.
Relationships: Migrations install source-table triggers, projection performs mapping, and graph_worker/graph_sync call this module.
Directory:
- require_capture: Check PostgreSQL and all source triggers.
- request_sync: Explicitly request backfill or failure requeueing.
- sync_owner: Process one user's pending events.
- pending_owners: Enumerate owners with processable events and no unresolved failures.
Variable index:
- logger: Synchronization logs containing only user identity, counts, and controlled error types.
"""

import logging
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.utils import timezone
from apps.accounts.reset_locks import account_lock
from apps.accounts.reset_models import AccountReset

from .models import Change, ProjectionState
from .projection import Projector, SOURCE_MODELS

logger = logging.getLogger("salesmate.knowledge_graph")


# Function: Ensure automatic capture is actually installed.
# Inputs: No external parameters; read database type and pg_trigger system metadata.
# Outputs: None; raise RuntimeError unless PostgreSQL and all required triggers are enabled.
# Logic: Check this version's capture triggers table by table, without substituting implicit polling table scans.
# Constraints: SQLite supports schema migrations only, not graph execution; all graph migrations must be applied.
def require_capture():
    if connection.vendor != "postgresql":
        raise RuntimeError("Knowledge graph requires PostgreSQL; SQLite capture is not implemented.")
    tables = [model._meta.db_table for model, _ in SOURCE_MODELS.values()]
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM pg_trigger t WHERE t.tgname IN ('salesmate_kg_capture', 'salesmate_kg_truncate') AND t.tgenabled IN ('O', 'A') AND t.tgrelid = ANY (SELECT to_regclass(x) FROM unnest(%s::text[]) AS x)", [tables])
        if cursor.fetchone()[0] != 2 * len(tables):
            raise RuntimeError("Knowledge graph capture triggers are missing or disabled; apply graph migrations.")


# Function: Explicitly request source-database backfill or failed-event recovery.
# Inputs: `owner_id`: existing user; `retry_failed`: default False, with requeueing only when explicitly True.
# Outputs: New BACKFILL event.
# Logic: Recover and enqueue in one transaction without modifying source business records.
# Constraints: Called by local operator commands; ordinary HTTP users cannot supply arbitrary owners.
def request_sync(owner_id, retry_failed=False):
    require_capture()
    get_user_model().objects.get(pk=owner_id)
    with transaction.atomic():
        if retry_failed:
            Change.objects.filter(owner_id=owner_id, status="failed").update(status="pending", error_code="")
        return Change.objects.create(owner_id=owner_id, kind="graph.backfill", source_id=str(owner_id), operation="BACKFILL")


# Function: Project and acknowledge one user's currently visible changes.
# Inputs: `owner_id`: owner ID from an event.
# Outputs: Statistics dictionary on success; None for no pending events, busy locks, or incomplete account reset; claimed failures are marked failed and the original exception is raised.
# Logic: Coordinate account reset with a shared account lock; outermost REPEATABLE READ gives cross-table consistency, a nonblocking graph lock excludes same-user concurrency, and versions/event completion commit together.
# Constraints: Call outside caller transactions; do not use auto-increment event IDs as commit cursors; retain newly committed events as pending and never retry failed events automatically.
def sync_owner(owner_id):
    require_capture()
    if connection.in_atomic_block:
        raise RuntimeError("sync_owner requires its own outer transaction.")
    event_ids = []
    try:
        with account_lock(owner_id), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                cursor.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))", [f"salesmate-kg:{owner_id}"])
                if not cursor.fetchone()[0]:
                    return None
            if AccountReset.objects.filter(owner_id=owner_id, cleaning=True).exists():
                logger.info("graph_sync_paused owner_id=%s reason=account_reset", owner_id)
                return None
            if Change.objects.filter(owner_id=owner_id, status="failed").exists():
                raise RuntimeError("Graph has failed events; inspect and explicitly request --retry-failed.")
            event_ids = list(Change.objects.filter(owner_id=owner_id, status="pending").values_list("pk", flat=True))
            if not event_ids:
                return None
            if not get_user_model().objects.filter(pk=owner_id).exists():
                Change.objects.filter(pk__in=event_ids).update(status="completed")
                logger.info("graph_deleted_owner_events_completed owner_id=%s count=%s", owner_id, len(event_ids))
                return {"deleted_owner": True, "events": len(event_ids)}
            result = Projector(owner_id).run()
            state, _ = ProjectionState.objects.get_or_create(owner_id=owner_id)
            state.ready, state.generation, state.synced_at = True, state.generation + 1, timezone.now()
            state.save(update_fields=["ready", "generation", "synced_at"])
            Change.objects.filter(pk__in=event_ids).update(status="completed", error_code="")
            result.update(events=len(event_ids), generation=state.generation)
        logger.info("graph_synced owner_id=%s events=%s entities=%s facts=%s", owner_id, result["events"], result["entities"], result["facts"])
        return result
    except Exception as exc:
        if event_ids:
            Change.objects.filter(pk__in=event_ids, status="pending").update(status="failed", error_code=type(exc).__name__[:100])
        logger.error("graph_sync_failed owner_id=%s event_count=%s error_type=%s action=inspect_and_explicit_retry", owner_id, len(event_ids), type(exc).__name__)
        raise


# Function: Find claimable per-user graph maintenance work.
# Inputs: No external parameters; read Change states.
# Outputs: Ordered owner ID list.
# Logic: Exclude owners with failed events and scan by state to avoid missing low-sequence events committed later.
# Constraints: Failed events require explicit recovery; sync_owner rechecks user locks.
def pending_owners():
    failed = Change.objects.filter(status="failed").values("owner_id")
    return list(Change.objects.filter(status="pending").exclude(owner_id__in=failed).order_by("owner_id").values_list("owner_id", flat=True).distinct())
