"""职责：消费图谱事务事件并原子发布单用户图谱。
实现：PostgreSQL 一致快照与用户级 advisory lock 防止并发覆盖；仅确认本次可见事件，失败明确持久化。
关联：migration 安装源表触发器，projection 负责映射，graph_worker/graph_sync 调用本模块。
目录：
- require_capture：检查 PostgreSQL 及全部来源触发器。
- request_sync：显式请求回填或失败重排。
- sync_owner：处理单用户 pending 事件。
- pending_owners：枚举有可处理事件且没有未解决失败的所有者。
变量索引：
- logger：同步日志，仅记录用户标识、数量及受控错误类型。
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


# 功能：确保自动捕获能力真实存在。
# 输入：无外部参数；读取数据库类型及 pg_trigger 系统目录。
# 输出：无；不是 PostgreSQL 或触发器未完整启用时抛 RuntimeError。
# 逻辑：逐表核对本版捕获触发器，没有隐式轮询表扫描替代。
# 约束：SQLite 仅允许迁移建表，不支持图谱运行；需应用全部图谱迁移。
def require_capture():
    if connection.vendor != "postgresql":
        raise RuntimeError("Knowledge graph requires PostgreSQL; SQLite capture is not implemented.")
    tables = [model._meta.db_table for model, _ in SOURCE_MODELS.values()]
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM pg_trigger t WHERE t.tgname IN ('salesmate_kg_capture', 'salesmate_kg_truncate') AND t.tgenabled IN ('O', 'A') AND t.tgrelid = ANY (SELECT to_regclass(x) FROM unnest(%s::text[]) AS x)", [tables])
        if cursor.fetchone()[0] != 2 * len(tables):
            raise RuntimeError("Knowledge graph capture triggers are missing or disabled; apply graph migrations.")


# 功能：明确请求源库回填或恢复失败事件。
# 输入：`owner_id` 为存在用户；`retry_failed` 默认 False，仅显式 True 才重排失败。
# 输出：新增 BACKFILL 事件。
# 逻辑：恢复和入队同事务；不修改任何源业务记录。
# 约束：调用者为本地运维命令，不向普通 HTTP 用户开放任意所有者参数。
def request_sync(owner_id, retry_failed=False):
    require_capture()
    get_user_model().objects.get(pk=owner_id)
    with transaction.atomic():
        if retry_failed:
            Change.objects.filter(owner_id=owner_id, status="failed").update(status="pending", error_code="")
        return Change.objects.create(owner_id=owner_id, kind="graph.backfill", source_id=str(owner_id), operation="BACKFILL")


# 功能：将一个用户当前可见变更投影并确认完成。
# 输入：`owner_id` 为事件中的所有者 ID。
# 输出：处理成功返回统计字典，无 pending、锁忙或账号清理未完成返回 None；已领取事件失败标为 failed 并抛原异常。
# 逻辑：账号共享锁协调清空；最外层 REPEATABLE READ 保证跨表一致；非阻塞图谱锁排除同用户并发；版本和事件完成同事务提交。
# 约束：必须在调用方事务外调用；不以自增事件 ID 作提交游标；新提交事件保留 pending，failed 不自动重试。
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


# 功能：找出可领取的用户图谱维护工作。
# 输入：无外部参数；读取 Change 的状态。
# 输出：有序 owner ID 列表。
# 逻辑：排除存在 failed 的所有者，按状态扫描避免漏掉较晚提交的低序号事件。
# 约束：失败事件需显式恢复；用户锁由 sync_owner 再次校验。
def pending_owners():
    failed = Change.objects.filter(status="failed").values("owner_id")
    return list(Change.objects.filter(status="pending").exclude(owner_id__in=failed).order_by("owner_id").values_list("owner_id", flat=True).distinct())
