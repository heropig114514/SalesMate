"""职责：显式执行图谱存量回填、重算或失败事件恢复。
实现：用户范围必须选择 --owner 或 --all；失败重排须显式 --retry-failed。
关联：knowledge_graph.sync 负责事件及原子投影；不调用外部服务。
目录：
- Command：显式图谱同步命令。
- Command.add_arguments：声明范围和恢复参数。
- Command.handle：排队并执行所选用户。
变量索引：
- Command.help：命令用途。
"""
import json
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from apps.knowledge_graph.sync import request_sync, sync_owner


# 功能：在明确范围内构建源库图谱。
# 逻辑：每位用户一个独立事务，失败会阻止该次命令继续。
# 约束：不会修改业务记录、自动调用 LLM 或重试失败事件。
class Command(BaseCommand):
    help = "回填本人或全部用户图谱；--retry-failed 明确恢复失败事件。"

    # 功能：定义必须明确选择的同步范围。
    # 输入：`parser` 为 Django 参数解析器。
    # 输出：无；注册 owner/all 和 retry-failed。
    # 逻辑：owner 与 all 互斥，避免无意全库处理。
    # 约束：不提供隐含默认 owner。
    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--owner", type=int)
        group.add_argument("--all", action="store_true")
        parser.add_argument("--retry-failed", action="store_true")

    # 功能：排队并执行明确要求的同步。
    # 输入：`args` 为位置参数；`options` 包含 owner、all、retry_failed。
    # 输出：逐用户 JSON 统计；失败抛 CommandError。
    # 逻辑：命令只调用图谱服务，错误详情保留受控类型。
    # 约束：锁忙时事件保持 pending，明确显示 queued，不伪报已完成。
    def handle(self, *args, **options):
        owners = list(get_user_model().objects.order_by("pk").values_list("pk", flat=True)) if options["all"] else [options["owner"]]
        try:
            for owner_id in owners:
                request_sync(owner_id, retry_failed=options["retry_failed"])
                result = sync_owner(owner_id)
                self.stdout.write(json.dumps({"owner_id": owner_id, "status": "completed" if result else "queued", "result": result}))
        except Exception as exc:
            raise CommandError(f"Graph sync failed ({type(exc).__name__}); inspect graph events and logs before explicit recovery.") from exc
