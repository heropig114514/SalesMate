"""职责：处理已明确批准的外部动作和到期跟进提醒。
实现：每轮领取 approved 动作；SIGTERM 后完成当前动作并停止领取，中断的 running 不自动重试。
关联：actions 负责外部调用，services.notify_due 提醒，common.execution 选择本地或 Celery 执行，common.shutdown 处理部署停止信号。
目录：
- Command：销售任务工作进程。
- Command.add_arguments：声明单轮及轮询参数。
- Command.handle：循环处理批准动作和提醒。
变量索引：
- logger：工作进程生命周期及错误日志。
- Command.help：命令帮助信息。
"""

import logging
import time
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from common.shutdown import graceful_shutdown
from common.execution import execute_sales
from apps.sales.actions import run_action
from apps.sales.models import ToolAction
from apps.sales.services import notify_due

logger = logging.getLogger("salesmate.worker")


# 功能：运行独立销售任务工作进程。
# 逻辑：批准动作的原子领取支持多个进程；当前轮错误直接报告。
# 约束：不自动重试失败动作，不处理 L1–L4 分析任务。
class Command(BaseCommand):
    help = "执行已批准的销售动作并生成到期提醒；失败和未知结果不会自动重试。"

    # 功能：声明命令参数。
    # 输入：`parser` 为 Django 命令参数解析器。
    # 输出：无，注册 --once 和 --poll。
    # 逻辑：默认每 5 秒查询，可显式单轮执行用于部署检查。
    # 约束：不改变既有 Agent 的轮询参数。
    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll", type=float, default=5)

    # 功能：循环处理明确批准的任务。
    # 输入：`args` 为位置参数，`options` 包含 once/poll。
    # 输出：无；错误抛 CommandError，中断正常退出。
    # 逻辑：清理连接、生成提醒并通过显式执行器逐项等待批准动作；SIGTERM 后完成当前动作，下一边界停止领取。
    # 约束：未批准动作不执行，未知结果留待核对；stop 状态只影响停止，不改变原轮询参数。
    def handle(self, *args, **options):
        if options["poll"] <= 0 or options["poll"] > 60:
            raise CommandError("--poll 必须大于 0 且不超过 60 秒。")
        logger.info("sales_worker_started once=%s", options["once"])
        try:
            with graceful_shutdown() as stop:
                while not stop["requested"]:
                    close_old_connections()
                    notify_due()
                    for key in list(
                        ToolAction.objects.filter(status="approved")
                        .order_by("approved_at")
                        .values_list("pk", flat=True)
                    ):
                        if stop["requested"]:
                            break
                        execute_sales(key, run_action)
                    if options["once"] or stop["requested"]:
                        break
                    time.sleep(options["poll"])
                if stop["requested"]:
                    logger.info("sales_worker_stopped reason=deployment_signal current_action=finished")
        except KeyboardInterrupt:
            logger.info("sales_worker_stopped reason=keyboard_interrupt")
        except Exception as exception:
            logger.error(
                "sales_worker_failed error_type=%s action=inspect_pending_and_running_records",
                type(exception).__name__,
            )
            raise CommandError(
                "销售任务进程失败；请检查数据库和动作状态，不要重试未知外部结果。"
            ) from exception
