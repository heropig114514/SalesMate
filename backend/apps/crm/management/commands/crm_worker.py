"""职责：运行与 Web 生命周期独立的 Gmail 和公司画像任务进程。
实现：一个邮箱同步通道与少量公司分析通道并行，数据库任务和租约作为权威状态。
关联：worker 执行业务单元，processing 管理过期批次；不消费 sales 外部动作队列。
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

from apps.crm.models import Job, MailboxSyncRun
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
    # 逻辑：持续检查已完成 future，再填充空闲通道；进程重启继续读取 queued/pending。
    # 约束：中断等待正在执行单元结束；硬中断由租约过期显式失败，不重发外部动作。
    def handle(self, *args, **options):
        if settings.ANALYSIS_PROVIDER != "agent":
            raise CommandError("crm_worker 仅用于 ANALYSIS_PROVIDER=agent；规则模式保持显式页面演示。")
        if not 0 < options["poll"] <= 60 or not 1 <= options["analysis_workers"] <= 4:
            raise CommandError("poll 必须在 (0,60]，analysis-workers 必须在 1–4。")
        owner = worker_owner()
        logger.info("crm_worker_started owner_id=%s analysis_workers=%s", owner.pk, options["analysis_workers"])
        pending = {}
        try:
            with ThreadPoolExecutor(max_workers=options["analysis_workers"] + 1, thread_name_prefix="crm") as pool:
                while True:
                    close_old_connections()
                    expire_runs(owner)
                    for future in list(pending):
                        if future.done():
                            future.result()
                            del pending[future]
                    sync_waiting = MailboxSyncRun.objects.filter(mailbox__owner=owner, status="queued").exists()
                    analysis_waiting = Job.objects.filter(company__owner=owner, status="pending").exists()
                    if sync_waiting and "sync" not in pending.values():
                        pending[pool.submit(run_sync, owner)] = "sync"
                    if analysis_waiting:
                        free = options["analysis_workers"] - list(pending.values()).count("analysis")
                        for _slot in range(free):
                            pending[pool.submit(run_analysis)] = "analysis"
                    if options["once"] and not pending and not sync_waiting and not analysis_waiting:
                        break
                    time.sleep(options["poll"])
        except KeyboardInterrupt:
            logger.info("crm_worker_stopped reason=keyboard_interrupt")
        logger.info("crm_worker_finished owner_id=%s", owner.pk)
