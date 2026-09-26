"""Responsibility: Serially consume workspace chat requests for all active employees.
Implementation: Explicitly retire legacy active company requests at startup, then rotate employees from the pending queue and claim, read, and report through HTTP under a one-time temporary identity; shutdown signal takes effect at job boundaries.
Relationships: Agent ``process_chat_once``, ``crm.dispatch.scoped_backend``, and ``common.shutdown``; HTTP permissions and atomic claiming remain unchanged.
Directory:
- next_owner: Select next active employee with a pending chat request.
- Command: Chat consumer.
- Command.add_arguments: Declare one-shot and polling arguments.
- Command.handle: Serial consumption and safe-failure logging.
Variable index:
- logger: Records only request state and exception type.
- Command.help: Command purpose.
"""

import logging
import time

from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.db import connections
from django.db.models import Exists, OuterRef

from agent.config import load_environment
from agent.workflows.chat import process_chat_once
from apps.crm.dispatch import scoped_backend
from apps.chat.models import AnswerRequest
from apps.chat.services import retire_legacy_requests
from common.shutdown import graceful_shutdown

logger = logging.getLogger("salesmate.chat_worker")


# Function: Select next active employee with a request awaiting answer.
# Inputs: ``after`` is the primary key of the last scheduled employee and starts at zero.
# Outputs: Employee object or ``None``.
# Logic: ``Exists`` discovers only pending requests; select after cursor by primary key and wrap at the end to prevent one employee from monopolizing work.
# Constraints: Does not claim, reset, or retry jobs; inactive employees do not participate and owner lock and HTTP authentication still protect actual claim.
def next_owner(after=0):
    pending = AnswerRequest.objects.filter(owner_id=OuterRef("pk"), status="pending")
    owners = (
        get_user_model()
        .objects.filter(is_active=True)
        .annotate(chat_waiting=Exists(pending))
        .filter(chat_waiting=True)
        .order_by("pk")
    )
    return owners.filter(pk__gt=after).first() or owners.first()


# Function: Run independent serial chat consumer.
# Logic: Rotate by employee and reuse independent identity and Agent HTTP protocol without directly writing model answers.
# Constraints: One-time credential binds only selected employee and revokes on exit; preserves single-job serialization and does not retry failed requests automatically.
class Command(BaseCommand):
    help = "串行处理所有有效员工的聊天请求；须先启动 HTTP 后端。"

    # Function: Define explicit runtime options.
    # Inputs: Django argument parser ``parser``.
    # Outputs: None; registers once and poll.
    # Logic: ``once`` handles at most one request; resident mode polls after idle seconds, defaulting to two seconds.
    # Constraints: Does not modify Agent model parameters, prompts, or defaults for any email workflow.
    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll", type=float, default=2)

    # Function: Execute serially and record safe results.
    # Inputs: Positional ``args`` and ``options`` containing once and poll.
    # Outputs: None; exits nonzero when claim or report fails.
    # Logic: Retire legacy company work at startup while retaining history; choose a pending employee each pass, process at most one request under temporary identity, and revoke credential; wait only for an empty queue and SIGTERM does not interrupt current report.
    # Constraints: ``report_failed`` retains state for review and stops; does not log model content or original exception text.
    def handle(self, *args, **options):
        if not 0 < options["poll"] <= 60:
            raise CommandError("poll 必须在 (0,60]。")
        load_environment()
        retired = retire_legacy_requests()
        logger.info("chat_worker_legacy_cleanup retired=%s", retired)
        last_owner = 0
        logger.info("chat_worker_started scope=all_active_owners")
        try:
            with graceful_shutdown() as stop:
                while not stop["requested"]:
                    owner = None
                    try:
                        owner = next_owner(last_owner)
                        result = None
                        if owner is not None:
                            last_owner = owner.pk
                            logger.info("chat_work_scheduled owner_id=%s", owner.pk)
                            with scoped_backend(owner) as backend:
                                result = process_chat_once(backend=backend)
                    except Exception as error:
                        logger.error(
                            "chat_worker_failed owner_id=%s error_type=%s action=inspect_processing_requests",
                            owner.pk if owner else None,
                            type(error).__name__,
                        )
                        raise CommandError(
                            "聊天执行失败，请核对 processing 请求及服务日志。"
                        ) from None
                    finally:
                        connections.close_all()
                    if result:
                        logger.info(
                            "chat_worker_result owner_id=%s request_id=%s status=%s code=%s",
                            owner.pk,
                            result["request_id"],
                            result["status"],
                            (result["error"] or {}).get("code"),
                        )
                        if (result["error"] or {}).get("code") == "report_failed":
                            raise CommandError(
                                "回答回报未确认；请先查询请求状态，禁止自动重新入队。"
                            )
                    if options["once"]:
                        if result and result["status"] == "failed":
                            raise CommandError("本次回答失败，已保存安全错误。")
                        break
                    if result is None:
                        time.sleep(options["poll"])
        finally:
            logger.info(
                "chat_worker_stopped scope=all_active_owners last_owner_id=%s",
                last_owner,
            )
