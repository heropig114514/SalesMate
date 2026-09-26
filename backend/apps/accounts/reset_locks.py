"""Responsibility: Coordinate account reset with HTTP and server background work units.
Implementation: A dedicated PostgreSQL connection holds a shared or exclusive account advisory lock and releases it automatically when the connection closes.
Relationships: ``reset_middleware``, ``reset``, ``crm.worker``, ``crm.dispatch``, and ``sales.actions``.
Directory:
- ResetBusy: Mutual-exclusion state for cleanup or work.
- account_lock: Hold a cross-process account lock.
- account_work: Shared-lock decorator for background work functions.
- account_work.guarded: Execute one protected work unit.
Variable index:
- logger: Lock-contention logger that never emits business content.
"""
from contextlib import contextmanager
from functools import wraps
import logging

from django.db import connection

logger = logging.getLogger("salesmate.account_reset")


# Function: Represent an account performing a mutually exclusive operation.
# Logic: The caller explicitly reports that it is busy, without silently retrying or claiming that cleanup completed.
# Constraints: Carries no sensitive data.
class ResetBusy(Exception):
    pass


# Function: Acquire a cross-process shared or exclusive lock for an account.
# Inputs: ``owner_id`` is the internal account primary key; ``exclusive`` selects reset mode.
# Outputs: A context with no value; exclusive-lock contention or incomplete file cleanup raises ``ResetBusy``.
# Logic: A shared lock waits briefly for reset; an exclusive lock does not wait for running work; the dedicated connection is unaffected by business ``close_all`` calls.
# Constraints: SQLite permits only existing preview operations and rejects resets without cross-process protection; connection errors remain failures.
@contextmanager
def account_lock(owner_id, *, exclusive=False):
    if connection.vendor != "postgresql":
        if exclusive:
            raise ResetBusy("账户清空需要 PostgreSQL 的跨进程保护；当前预览数据库不支持。")
        yield
        return
    guard = connection.copy(alias="account-reset-guard")
    try:
        guard.ensure_connection()
        guard.set_autocommit(True)
        with guard.cursor() as cursor:
            # A negative single-bigint key is isolated from existing business row locks and the two-int advisory-key space.
            key = -int(owner_id)
            if exclusive:
                cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
                if not cursor.fetchone()[0]:
                    raise ResetBusy("该账号仍有请求或后台任务正在执行，请完成后重试清空。")
            else:
                cursor.execute("SELECT pg_advisory_lock_shared(%s)", [key])
        yield
    finally:
        guard.close()


# Function: Protect background-work entry points that access the database directly.
# Inputs: ``function`` is a background callable whose first argument represents the account owner.
# Outputs: A wrapper with the same signature; it returns ``False`` while file cleanup is incomplete, leaving the queue unexecuted.
# Logic: Hold a shared lock for the full work lifecycle so clearing can run only between work units.
# Constraints: Does not add job retries; exceptions remain handled by the original work function.
def account_work(function):
    # Function: Run a background unit under the account shared lock.
    # Inputs: ``owner``, ``args``, and ``kwargs`` are passed through unchanged to the wrapped function.
    # Outputs: The original function result, or ``False`` when it is not run.
    # Logic: Check persistent cleanup state after acquiring the lock to avoid regenerating business content while file cleanup has failed.
    # Constraints: The lock uses a dedicated connection, so closing the default connection in the original function cannot release it early.
    @wraps(function)
    def guarded(owner, *args, **kwargs):
        from .reset_models import AccountReset
        with account_lock(owner.pk):
            if AccountReset.objects.filter(owner=owner, cleaning=True).exists():
                logger.info("account_work_paused owner_id=%s action=resume_account_reset", owner.pk)
                return False
            return function(owner, *args, **kwargs)
    return guarded
