"""职责：运行独立的自动图谱维护 Worker。
实现：按 pending 事件扫描用户，显式失败事件留待恢复；遵守既有 SIGTERM 排空语义。
关联：数据库触发器写入 Change，sync_owner 完成一致快照映射。
目录：
- Command：图谱 Worker 命令。
- Command.add_arguments：声明单轮和轮询间隔。
- Command.handle：消费事件并在停止时完成当前事务。
变量索引：
- logger：Worker 生命周期日志。
- Command.help：命令用途。
"""
import logging
import time
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from common.shutdown import graceful_shutdown
from apps.knowledge_graph.sync import pending_owners, require_capture, sync_owner

logger = logging.getLogger("salesmate.knowledge_graph.worker")


# 功能：自动维护已纳入图谱的业务表。
# 逻辑：只消费事务事件，轮询空闲等待；没有外部服务调用。
# 约束：失败退出，不自动重启、重试或改用其他同步机制。
class Command(BaseCommand):
    help = "维护 PostgreSQL 业务图谱；失败事件需要显式 graph_sync --retry-failed。"

    # 功能：设置 Worker 调度选项。
    # 输入：`parser` 为命令解析器。
    # 输出：无；注册 once 和 poll。
    # 逻辑：--once 处理本轮可见用户，默认每 2 秒检查事件。
    # 约束：该间隔仅用于新增图谱 Worker，不更改现有 Worker 或实验参数。
    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll", type=float, default=2)

    # 功能：消费图谱变更直到显式停止。
    # 输入：`args` 为位置参数；`options` 包含 once/poll。
    # 输出：无；失败抛 CommandError 并保留失败事件。
    # 逻辑：检查捕获安装，逐用户处理；SIGTERM 后不再领取新用户。
    # 约束：轮询必须大于 0 且不超过 60 秒；同一用户锁忙留待正常调度。
    def handle(self, *args, **options):
        if not 0 < options["poll"] <= 60:
            raise CommandError("--poll 必须大于 0 且不超过 60 秒。")
        try:
            require_capture()
            logger.info("graph_worker_started once=%s poll=%s", options["once"], options["poll"])
            with graceful_shutdown() as stop:
                while not stop["requested"]:
                    close_old_connections()
                    for owner_id in pending_owners():
                        if stop["requested"]:
                            break
                        sync_owner(owner_id)
                    if options["once"] or stop["requested"]:
                        break
                    time.sleep(options["poll"])
            logger.info("graph_worker_stopped")
        except KeyboardInterrupt:
            logger.info("graph_worker_stopped reason=keyboard_interrupt")
        except Exception as exc:
            logger.error("graph_worker_failed error_type=%s action=inspect_then_explicit_restart", type(exc).__name__)
            raise CommandError("图谱 Worker 失败；请检查日志与失败事件后显式恢复。") from exc
