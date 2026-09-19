"""职责：显式终止已确认中断的聊天请求。
实现：指定员工与 request_id，强制要求确认进程中断。
关联：chat.services.interrupt；后续重试由浏览器另行提交。
目录：
- Command：人工恢复入口。
- Command.add_arguments：声明必需标识和确认。
- Command.handle：核验并关闭请求。
变量索引：
- Command.help：操作用途。
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from rest_framework.exceptions import APIException
from apps.chat import contracts, services


# 功能：关闭明确指定的中断请求。
# 逻辑：不根据猜测的超时批量重置。
# 约束：命令行操作者必须已核对执行进程。
class Command(BaseCommand):
    help = "确认进程中断后，将指定 processing 请求终止为 failed。"

    # 功能：声明恢复参数。
    # 输入：`parser` 参数解析器。
    # 输出：无，注册 owner/request-id/confirm-interrupted。
    # 逻辑：禁止省略身份或任务，确认是独立开关。
    # 约束：不设置自动超时或批量恢复默认值。
    def add_arguments(self, parser):
        parser.add_argument("--owner", required=True)
        parser.add_argument("--request-id", required=True)
        parser.add_argument("--confirm-interrupted", action="store_true")

    # 功能：执行安全终止。
    # 输入：`args` 位置参数，`options` 员工、任务与确认。
    # 输出：安全任务标识或 CommandError。
    # 逻辑：查找员工后调用单事务状态转换。
    # 约束：终态、pending 或其他员工请求不会被修改。
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
