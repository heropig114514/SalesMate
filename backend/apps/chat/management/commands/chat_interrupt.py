"""Responsibility: Explicitly stop a chat request confirmed as interrupted.
Implementation: Target employee and request_id and require explicit confirmation that processing stopped.
Relationships: ``chat.services.interrupt``; browser submits any later retry separately.
Directory:
- Command: Manual recovery entry point.
- Command.add_arguments: Declare required identifiers and confirmation.
- Command.handle: Validate and close the request.
Variable index:
- Command.help: Operation purpose.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from rest_framework.exceptions import APIException
from apps.chat import contracts, services


# Function: Close an explicitly identified interrupted request.
# Logic: Does not batch-reset requests based on guessed timeout.
# Constraints: Command-line operator must already have checked the execution process.
class Command(BaseCommand):
    help = "确认进程中断后，将指定 processing 请求终止为 failed。"

    # Function: Declare recovery arguments.
    # Inputs: Argument parser ``parser``.
    # Outputs: None; registers owner, request-id, and confirm-interrupted.
    # Logic: Identity and request cannot be omitted; confirmation is an independent flag.
    # Constraints: Sets no automatic timeout or batch-recovery default.
    def add_arguments(self, parser):
        parser.add_argument("--owner", required=True)
        parser.add_argument("--request-id", required=True)
        parser.add_argument("--confirm-interrupted", action="store_true")

    # Function: Execute safe termination.
    # Inputs: Positional ``args`` and ``options`` containing employee, request, and confirmation.
    # Outputs: Safe request identifier or ``CommandError``.
    # Logic: Find employee then call the single-transaction state transition.
    # Constraints: Does not modify terminal, pending, or another employee's request.
    def handle(self, *args, **options):
        if not options["confirm_interrupted"]:
            raise CommandError("须确认原处理进程已中断，并传入 --confirm-interrupted。")
        owner = (
            get_user_model()
            .objects.filter(username=options["owner"], is_active=True)
            .first()
        )
        if owner is None:
            raise CommandError("员工不存在或已停用。")
        try:
            request = services.interrupt(
                owner, contracts.identifier(options["request_id"])
            )
        except APIException as error:
            raise CommandError(str(error.detail)) from None
        self.stdout.write(f"failed request_id={request.pk}; 可显式创建新尝试。")
