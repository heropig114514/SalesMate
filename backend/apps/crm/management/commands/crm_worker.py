"""Responsibility: Consume mailbox synchronization and company-profile jobs for every active employee in a shared process.
Implementation: Rotate employees independently for synchronization and profiling while retaining global concurrency; stop claiming work after SIGTERM and drain in-flight units.
Relationships: dispatch discovers employees and manages identity, worker executes business work, common.execution explicitly selects local threads or Celery, and common.shutdown handles stopping. External sales actions are not consumed.
Directory:
- Command: Configure and run durable queue consumers.
- Command.add_arguments: Declare single-pass, polling, and profile-concurrency parameters.
- Command.handle: Poll and coordinate mailbox and profile work units.
Variable index:
- logger: Process lifecycle logger.
- Command.help: Management-command description.
"""
from common.execution import work_executor
import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from common.shutdown import graceful_shutdown
from agent.config import load_environment
from apps.crm.dispatch import next_owner
from apps.crm.worker import run_analysis, run_sync

logger = logging.getLogger("salesmate.crm_worker")


# Function: Independently consume mailbox and profile database jobs for all active employees.
# Logic: Retain each channel's most recent employee cursor and schedule round-robin; backend claim services ensure profile mutual exclusion.
# Constraints: Runs only in agent mode; does not create an OS service, and failed jobs require explicit retry.
class Command(BaseCommand):
    help = "Run the shared Gmail/QQ and profile worker for all employees; start the HTTP backend first."

    # Function: Declare worker scheduling parameters.
    # Inputs: `parser` is the Django argument parser.
    # Outputs: None; adds once, poll, and analysis-workers.
    # Logic: Defaults to two profile workers and one-second polling; L1 continues with the Agent's established four lanes.
    # Constraints: Does not alter models, scoring, Gmail scan limits, or job leases.
    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll", type=float, default=1)
        parser.add_argument("--analysis-workers", type=int, default=2)

    # Function: Schedule independent synchronization and analysis channels.
    # Inputs: `args` are positional arguments; `options` contains once, poll, and analysis_workers.
    # Outputs: None; a single pass exits after draining currently claimable work.
    # Logic: pending maps Futures to channels and employees, while last_owner keeps separate fair round-robin cursors.
    # It reselects an employee from the durable queue whenever filling a free channel; work_executor explicitly selects threads or Celery,
    # propagates message-submission failures, and waits for pending results after SIGTERM.
    # Constraints: Reports job exceptions even while stopping, requires Web availability while waiting, and does not change concurrency, leases, or business retry semantics.
    def handle(self, *args, **options):
        if settings.ANALYSIS_PROVIDER != "agent":
            raise CommandError("crm_worker is only for ANALYSIS_PROVIDER=agent; rules mode remains an explicit page demonstration.")
        if not 0 < options["poll"] <= 60 or not 1 <= options["analysis_workers"] <= 4:
            raise CommandError("poll must be in (0,60], and analysis-workers must be in 1–4.")
        load_environment()
        logger.info("crm_worker_started scope=all_active_owners analysis_workers=%s", options["analysis_workers"])
        pending = {}
        last_owner = {"sync": 0, "analysis": 0}
        try:
            with graceful_shutdown() as stop, work_executor(max_workers=options["analysis_workers"] + 1, thread_name_prefix="crm") as pool:
                while not stop["requested"]:
                    close_old_connections()
                    for future in list(pending):
                        if future.done():
                            future.result()
                            del pending[future]
                    for kind, capacity, execute in (("sync", 1, run_sync), ("analysis", options["analysis_workers"], run_analysis)):
                        free = capacity - sum(channel == kind for channel, _owner_id in pending.values())
                        for _slot in range(free):
                            if stop["requested"]:
                                break
                            owner = next_owner(kind, last_owner[kind])
                            if owner is None:
                                break
                            last_owner[kind] = owner.pk
                            pending[pool.submit(execute, owner)] = (kind, owner.pk)
                            logger.info("crm_work_scheduled channel=%s owner_id=%s", kind, owner.pk)
                    if options["once"] and not pending:
                        break
                    time.sleep(options["poll"])
                for future in pending:
                    future.result()
                if stop["requested"]:
                    logger.info("crm_worker_stopped reason=deployment_signal pending=finished")
        except KeyboardInterrupt:
            logger.info("crm_worker_stopped reason=keyboard_interrupt")
        logger.info("crm_worker_finished scope=all_active_owners")
