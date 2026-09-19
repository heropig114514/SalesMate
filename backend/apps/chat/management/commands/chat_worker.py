"""职责：串行消费所有有效员工的客户与通用聊天请求。
实现：从 pending 队列轮转员工，以单次临时身份通过 HTTP 领取、读取和回报；退出信号在任务边界生效。
关联：Agent process_chat_once、crm.dispatch.scoped_backend 和 common.shutdown；HTTP 权限及原子领取保持不变。
目录：
- next_owner：选择下一位有 pending 聊天请求的有效员工。
- Command：聊天消费者。
- Command.add_arguments：声明单次及轮询参数。
- Command.handle：串行消费和安全失败日志。
变量索引：
- logger：仅记录请求状态及异常类型。
- Command.help：命令用途说明。
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
from common.shutdown import graceful_shutdown

logger = logging.getLogger("salesmate.chat_worker")


# 功能：选择下一位有待回答请求的有效员工。
# 输入：`after` 为上次调度的员工主键，初始为 0。
# 输出：员工对象或 None。
# 逻辑：Exists 仅发现 pending 请求；按主键从游标向后选择，到末尾回绕，避免单员工持续占用。
# 约束：不领取、重置或重试任务；停用员工不参与，真实领取仍由 owner 锁及 HTTP 认证保护。
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


# 功能：运行独立串行聊天消费者。
# 逻辑：按员工轮转并复用独立身份与 Agent HTTP 协议，不直写模型回答。
# 约束：单次凭证只绑定所选员工并在退出时撤销；保持单任务串行，不自动重试失败请求。
class Command(BaseCommand):
    help = "串行处理所有有效员工的聊天请求；须先启动 HTTP 后端。"

    # 功能：定义显式运行选项。
    # 输入：`parser` Django 参数解析器。
    # 输出：无，注册 once/poll。
    # 逻辑：once 处理至多一条，常驻模式空闲按 poll 秒查询，默认 2 秒。
    # 约束：不修改 Agent 模型参数、提示词或任何邮件流程默认值。
    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll", type=float, default=2)

    # 功能：串行执行并记录安全结果。
    # 输入：`args` 位置参数，`options` 含 once/poll。
    # 输出：无；领取或回报失败时非零退出。
    # 逻辑：每次选择有 pending 请求的员工，用临时身份处理至多一条并撤销凭证；空队列才等待，SIGTERM 不中断当前回报。
    # 约束：report_failed 保持待核对现场并停止，不记录模型正文或异常原文。
    def handle(self, *args, **options):
        if not 0 < options["poll"] <= 60:
            raise CommandError("poll 必须在 (0,60]。")
        load_environment()
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
