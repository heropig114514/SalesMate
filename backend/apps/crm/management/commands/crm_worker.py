"""职责：以共享进程消费所有有效员工的邮箱同步和公司画像任务。
实现：同步与画像分别轮转员工，保留全局并发；SIGTERM 后停止领取并排空在途单元。
关联：dispatch 发现员工并管理身份，worker 执行业务，common.execution 显式选择本地线程或 Celery，common.shutdown 处理停止；不消费外部销售动作。
目录：
- Command：配置并运行持久队列消费者。
- Command.add_arguments：声明单轮、轮询和画像并发参数。
- Command.handle：轮询并协调邮箱及画像工作单元。
变量索引：
- logger：进程生命周期日志。
- Command.help：管理命令说明。
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


# 功能：独立消费所有有效员工的邮箱和画像数据库任务。
# 逻辑：每通道保存上次员工游标，按轮转调度，画像互斥由后端领取服务保证。
# 约束：仅 agent 模式运行；不创建 OS 服务，失败任务需明确重试。
class Command(BaseCommand):
    help = "运行所有员工共享的 Gmail/QQ 与画像 Worker；请先启动 HTTP 后端。"

    # 功能：声明 Worker 调度参数。
    # 输入：`parser` 为 Django 参数解析器。
    # 输出：无；增加 once/poll/analysis-workers。
    # 逻辑：默认 2 路画像、1 秒轮询，L1 继续采用 Agent 既定 4 路。
    # 约束：不修改模型、评分、Gmail 扫描上限或任务租约。
    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll", type=float, default=1)
        parser.add_argument("--analysis-workers", type=int, default=2)

    # 功能：调度独立同步和分析通道。
    # 输入：`args` 为位置参数，`options` 含 once/poll/analysis_workers。
    # 输出：无；单轮排空当前可领取工作后退出。
    # 逻辑：pending 保存 Future 到通道及员工的映射，last_owner 分别记录公平轮转游标；
    # 每次填充空闲通道时从持久队列重新选员工；work_executor 显式选择线程或 Celery，
    # 消息提交失败向上传播，SIGTERM 后等待 pending 的结果。
    # 约束：停止时依然报告任务异常，等待期间需要 Web 可用；不改变并发、租约或业务重试语义。
    def handle(self, *args, **options):
        if settings.ANALYSIS_PROVIDER != "agent":
            raise CommandError("crm_worker 仅用于 ANALYSIS_PROVIDER=agent；规则模式保持显式页面演示。")
        if not 0 < options["poll"] <= 60 or not 1 <= options["analysis_workers"] <= 4:
            raise CommandError("poll 必须在 (0,60]，analysis-workers 必须在 1–4。")
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
