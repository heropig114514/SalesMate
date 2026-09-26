"""Responsibility: Explicitly backfill or recompute the graph, or recover failed events.
Implementation: Require --owner or --all; requeue failures only with explicit --retry-failed.
Relationships: knowledge_graph.sync handles events and atomic projection without external services.
Directory:
- Command: Explicit graph synchronization command.
- Command.add_arguments: Declare scope and recovery arguments.
- Command.handle: Queue and process selected users.
Variable index:
- Command.help: Command purpose.
"""
import json
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from apps.knowledge_graph.sync import request_sync, sync_owner


# Function: Build the source-database graph within an explicit scope.
# Logic: Use an independent transaction per user; failure stops further command execution.
# Constraints: Do not modify business records, invoke LLMs automatically, or retry failed events.
class Command(BaseCommand):
    help = "回填本人或全部用户图谱；--retry-failed 明确恢复失败事件。"

    # Function: Define synchronization scope requiring explicit selection.
    # Inputs: `parser`: Django argument parser.
    # Outputs: None; register owner/all and retry-failed.
    # Logic: Make owner and all mutually exclusive to avoid unintended whole-database processing.
    # Constraints: Do not supply an implicit default owner.
    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--owner", type=int)
        group.add_argument("--all", action="store_true")
        parser.add_argument("--retry-failed", action="store_true")

    # Function: Queue and execute explicitly requested synchronization.
    # Inputs: `args`: positional arguments; `options`: owner, all, and retry_failed.
    # Outputs: Per-user JSON statistics; failures raise CommandError.
    # Logic: Call only graph services and retain controlled error types in details.
    # Constraints: When locks are busy, keep events pending and explicitly report queued instead of claiming completion.
    def handle(self, *args, **options):
        owners = list(get_user_model().objects.order_by("pk").values_list("pk", flat=True)) if options["all"] else [options["owner"]]
        try:
            for owner_id in owners:
                request_sync(owner_id, retry_failed=options["retry_failed"])
                result = sync_owner(owner_id)
                self.stdout.write(json.dumps({"owner_id": owner_id, "status": "completed" if result else "queued", "result": result}))
        except Exception as exc:
            raise CommandError(f"Graph sync failed ({type(exc).__name__}); inspect graph events and logs before explicit recovery.") from exc
