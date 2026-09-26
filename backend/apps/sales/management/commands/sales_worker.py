"""Responsibility: Process explicitly approved external actions and due follow-up reminders.
Implementation: Claim approved actions each iteration; after SIGTERM finish the current action and stop claiming. Interrupted running actions are not retried automatically.
Relationships: actions performs external calls; services.notify_due sends reminders; common.execution selects local or Celery execution; common.shutdown handles deployment stop signals.
Directory:
- Command: Sales task worker.
- Command.add_arguments: Declare single-iteration and polling parameters.
- Command.handle: Loop over approved actions and reminders.
Variable index:
- logger: Worker lifecycle and error logs.
- Command.help: Command help text.
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


# Function: Run a standalone sales task worker.
# Logic: Atomic claims of approved actions support multiple processes; report current-iteration errors directly.
# Constraints: No automatic retries of failed actions; do not process L1-L4 analysis tasks.
class Command(BaseCommand):
    help = "执行已批准的销售动作并生成到期提醒；失败和未知结果不会自动重试。"

    # Function: Declare command arguments.
    # Inputs: `parser`: Django command argument parser.
    # Outputs: None; register --once and --poll.
    # Logic: Poll every 5 seconds by default; explicit single-iteration execution supports deployment checks.
    # Constraints: Preserve existing Agent polling parameters.
    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll", type=float, default=5)

    # Function: Loop over explicitly approved tasks.
    # Inputs: `args`: positional arguments; `options`: once/poll.
    # Outputs: None; errors raise CommandError and interruptions exit normally.
    # Logic: Clean connections, generate reminders, and await approved actions one at a time through the explicit executor. On SIGTERM finish the current action, then stop claiming at the next boundary.
    # Constraints: Never execute unapproved actions; leave unknown outcomes for reconciliation. Stop state affects shutdown only, preserving polling parameters.
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
