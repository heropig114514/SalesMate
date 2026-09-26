"""Responsibility: Run an independent automatic graph maintenance worker.
Implementation: Scan users with pending events, leave explicit failures for recovery, and follow existing SIGTERM draining semantics.
Relationships: Database triggers write Change records; sync_owner projects a consistent snapshot.
Directory:
- Command: Graph worker command.
- Command.add_arguments: Declare single-pass and polling-interval options.
- Command.handle: Consume events and finish the current transaction when stopping.
Variable index:
- logger: Worker lifecycle logging.
- Command.help: Command purpose.
"""
import logging
import time
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from common.shutdown import graceful_shutdown
from apps.knowledge_graph.sync import pending_owners, require_capture, sync_owner

logger = logging.getLogger("salesmate.knowledge_graph.worker")


# Function: Automatically maintain business tables included in the graph.
# Logic: Consume only transactional events and wait between polls; no external service calls.
# Constraints: Exit on failure without automatic restart, retry, or alternative synchronization mechanisms.
class Command(BaseCommand):
    help = "维护 PostgreSQL 业务图谱；失败事件需要显式 graph_sync --retry-failed。"

    # Function: Set worker scheduling options.
    # Inputs: `parser`: command parser.
    # Outputs: None; register once and poll.
    # Logic: --once processes users visible in this pass; otherwise check events every 2 seconds by default.
    # Constraints: This interval applies only to the new graph worker, preserving existing worker and experiment parameters.
    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll", type=float, default=2)

    # Function: Consume graph changes until explicitly stopped.
    # Inputs: `args`: positional arguments; `options`: once/poll.
    # Outputs: None; failures raise CommandError and retain failed events.
    # Logic: Verify capture installation and process users individually; after SIGTERM, do not claim another user.
    # Constraints: Polling must be positive and at most 60 seconds; defer busy user locks to normal scheduling.
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
