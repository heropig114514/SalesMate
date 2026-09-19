"""职责：串行触发现有 Agent 一次性聊天工作流。
实现：核验固定服务令牌的员工归属后，通过 HTTP 领取、读取和回报；退出信号在任务边界生效。
关联：Agent process_chat_once、服务令牌和 common.shutdown；不修改 crm_worker 调度。
目录：
- worker_owner：核验聊天服务令牌绑定的有效员工。
- Command：聊天消费者。
- Command.add_arguments：声明单次及轮询参数。
- Command.handle：串行消费和安全失败日志。
变量索引：
- logger：仅记录请求状态及异常类型。
- Command.help：命令用途说明。
"""

import hashlib
import logging
import os
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from agent.clients.backend_api import django_backend_from_environment
from agent.config import load_environment
from agent.workflows.chat import process_chat_once
from apps.crm.models import AgentCredential
from common.shutdown import graceful_shutdown

logger = logging.getLogger("salesmate.chat_worker")


# 功能：解析当前独立聊天 Worker 的员工身份。
# 输入：无外部参数；读取现有 Agent 环境配置和数据库凭证摘要。
# 输出：启用的员工对象；连接配置无效时沿用客户端配置异常，凭证不匹配抛 CommandError。
# 逻辑：保留原固定令牌校验，按摘要查询 owner，不创建或输出令牌。
# 约束：一个聊天 Worker 绑定一个员工；新版 CRM 已独立调度，故此校验由聊天模块维护。
def worker_owner():
    load_environment()
    django_backend_from_environment()
    digest = hashlib.sha256(os.environ.get("SALESMATE_AGENT_SERVICE_TOKEN", "").encode()).hexdigest()
    credential = AgentCredential.objects.select_related("owner").filter(digest=digest, owner__is_active=True).first()
    if credential is None:
        raise CommandError("Worker 服务令牌未绑定有效员工，请检查 Agent 配置。")
    return credential.owner


# 功能：运行独立串行聊天消费者。
# 逻辑：复用 Agent 函数及 HTTP 协议，不直写模型回答。
# 约束：一个服务凭证绑定一个员工，不自动重试失败请求。
class Command(BaseCommand):
    help = "串行处理只读聊天请求；须先启动 HTTP 后端。"

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
    # 逻辑：处理一条后立即尝试下一条，空队列才等待；SIGTERM 不中断当前回报。
    # 约束：report_failed 保持待核对现场并停止，不记录模型正文或异常原文。
    def handle(self, *args, **options):
        if not 0 < options["poll"] <= 60:
            raise CommandError("poll 必须在 (0,60]。")
        owner = worker_owner()
        logger.info("chat_worker_started owner_id=%s", owner.pk)
        try:
            with graceful_shutdown() as stop:
                while not stop["requested"]:
                    try:
                        result = process_chat_once(
                            backend=django_backend_from_environment()
                        )
                    except Exception as error:
                        logger.error(
                            "chat_worker_failed error_type=%s action=inspect_processing_requests",
                            type(error).__name__,
                        )
                        raise CommandError(
                            "聊天执行失败，请核对 processing 请求及服务日志。"
                        ) from None
                    finally:
                        connections.close_all()
                    if result:
                        logger.info(
                            "chat_worker_result request_id=%s status=%s code=%s",
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
            logger.info("chat_worker_stopped owner_id=%s", owner.pk)
