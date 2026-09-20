"""职责：协调账户重置与 HTTP、服务器后台工作单元。
实现：PostgreSQL 独立连接持有账号共享/独占 advisory lock，连接关闭自动释放。
关联：reset_middleware、reset、crm.worker、crm.dispatch 与 sales.actions。
目录：
- ResetBusy：清理或工作互斥状态。
- account_lock：持有跨进程账号锁。
- account_work：后台工作函数的共享锁装饰器。
- account_work.guarded：执行一个受保护工作单元。
变量索引：
- logger：锁竞争日志，不输出业务正文。
"""
from contextlib import contextmanager
from functools import wraps
import logging

from django.db import connection

logger = logging.getLogger("salesmate.account_reset")


# 功能：表示账号正在执行互斥操作。
# 逻辑：调用方明确报告忙碌，不静默重试或声称清理完成。
# 约束：不携带敏感数据。
class ResetBusy(Exception):
    pass


# 功能：为一个账号取得跨进程共享或独占锁。
# 输入：`owner_id` 为内部账号主键；`exclusive` 为重置模式。
# 输出：上下文无值；独占锁竞争或未完成文件清理抛 ResetBusy。
# 逻辑：共享锁等待短暂的重置；独占锁不等待正在运行的工作；独立连接不受业务 close_all 影响。
# 约束：SQLite 仅允许既有预览操作，拒绝不具备跨进程保护的重置；连接异常保持失败。
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
            # 单 bigint 的负数键与现有业务行锁及双 int advisory key 空间隔离。
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


# 功能：保护直接访问数据库的后台工作入口。
# 输入：`function` 为首参数是 owner 的函数。
# 输出：同签名包装函数；文件清理未完成时返回 False，保持队列不执行。
# 逻辑：工作完整生命周期持有共享锁，清空只能在工作单元之间执行。
# 约束：不增加任务重试；异常仍由原工作函数处理。
def account_work(function):
    # 功能：在账号共享锁下运行后台单元。
    # 输入：`owner`、`args`、`kwargs` 原样传递。
    # 输出：原函数结果或未执行时 False。
    # 逻辑：取得锁后检查持久清理状态，避免文件失败期间重新生成业务内容。
    # 约束：锁使用独立连接，原函数关闭默认连接不会提前释放。
    @wraps(function)
    def guarded(owner, *args, **kwargs):
        from .reset_models import AccountReset
        with account_lock(owner.pk):
            if AccountReset.objects.filter(owner=owner, cleaning=True).exists():
                logger.info("account_work_paused owner_id=%s action=resume_account_reset", owner.pk)
                return False
            return function(owner, *args, **kwargs)
    return guarded
