"""职责：运行与 Web 生命周期独立的 Gmail 和公司画像任务进程。
实现：同步与画像通道并行；SIGTERM 后停止调度并等待正在执行单元完成，数据库及租约为权威状态。
关联：worker 执行业务单元，processing 管理批次，common.shutdown 处理部署停止；不消费 sales 外部动作。
目录：
- Command：配置并运行持久队列消费者。
- Command.add_arguments：声明单轮、轮询和画像并发参数。
- Command.handle：轮询并协调邮箱及画像工作单元。
变量索引：
- logger：进程生命周期日志。
- Command.help：管理命令说明。
"""
from concurrent.futures import ThreadPoolExecutor
import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone
from common.shutdown import graceful_shutdown

from apps.crm.models import ExtractionRepair, MailboxSyncRun
from apps.crm.jobs import claimable_jobs
from apps.crm.processing import expire_runs
from apps.crm.worker import run_analysis, run_sync, worker_owner

logger = logging.getLogger("salesmate.crm_worker")


# 功能：独立消费邮箱和画像数据库任务。
# 逻辑：每个进程绑定一个员工，画像互斥由后端领取服务保证。
# 约束：仅 agent 模式运行；不创建 OS 服务，失败任务需明确重试。
class Command(BaseCommand):
    help = "运行 Gmail 与画像 Worker；请先启动 HTTP 后端。"

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
    # 逻辑：填充空闲通道；SIGTERM 后停止新调度并等待 pending 的结果；单轮只处理当前可领取工作。
    # 约束：停止时依然报告任务异常，等待期间需要 Web 可用；不改变并发、租约或业务重试语义。
    def handle(self, *args, **options):
        if settings.ANALYSIS_PROVIDER != "agent":
            raise CommandError("crm_worker 仅用于 ANALYSIS_PROVIDER=agent；规则模式保持显式页面演示。")
        if not 0 < options["poll"] <= 60 or not 1 <= options["analysis_workers"] <= 4:
            raise CommandError("poll 必须在 (0,60]，analysis-workers 必须在 1–4。")
        owner = worker_owner()
        logger.info("crm_worker_started owner_id=%s analysis_workers=%s", owner.pk, options["analysis_workers"])
        pending = {}
        try:
            with graceful_shutdown() as stop, ThreadPoolExecutor(max_workers=options["analysis_workers"] + 1, thread_name_prefix="crm") as pool:
                while not stop["requested"]:
                    close_old_connections()
                    expire_runs(owner)
                    for future in list(pending):
                        if future.done():
                            future.result()
                            del pending[future]
                    repair_waiting = ExtractionRepair.objects.filter(email__mailbox__owner=owner).filter(Q(status="pending") | Q(status="running", lease_until__lte=timezone.now())).exists()
                    sync_waiting = repair_waiting or MailboxSyncRun.objects.filter(mailbox__owner=owner, status="queued").exists()
                    analysis_waiting = claimable_jobs(owner).exists()
                    if sync_waiting and "sync" not in pending.values():
                        pending[pool.submit(run_sync, owner)] = "sync"
                    if analysis_waiting:
                        free = options["analysis_workers"] - list(pending.values()).count("analysis")
                        for _slot in range(free):
                            pending[pool.submit(run_analysis)] = "analysis"
                    if options["once"] and not pending and not sync_waiting and not analysis_waiting:
                        break
                    time.sleep(options["poll"])
                for future in pending:
                    future.result()
                if stop["requested"]:
                    logger.info("crm_worker_stopped reason=deployment_signal pending=finished")
        except KeyboardInterrupt:
            logger.info("crm_worker_stopped reason=keyboard_interrupt")
        logger.info("crm_worker_finished owner_id=%s", owner.pk)
