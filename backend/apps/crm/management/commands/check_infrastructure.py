"""职责：提供部署期 Redis、pgvector 和 Celery 消费能力检查。
实现：检查真实连接；可选向两个队列发送无业务副作用的随机标识并验证回传。
关联：蓝绿部署在切换前检查基础设施、启动消费者后检查完整消息链。
目录：
- Command：基础设施检查命令。
- Command.add_arguments：定义消费者探测选项。
- Command.handle：执行脱敏检查并明确报告失败。
变量索引：
- Command.help：命令用途。
"""
import secrets
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from redis import Redis


# 功能：验证实际基础设施可用。
# 逻辑：使用配置地址，不读取或打印凭证明文。
# 约束：不把网络探测当作真实邮件发送验收。
class Command(BaseCommand):
    help = "检查 Redis、pgvector，按需检查 Celery 两个队列的消息往返。"

    # 功能：声明检查选项。
    # 输入：`parser` 为命令解析器。
    # 输出：注册 workers 开关。
    # 逻辑：默认只检查基础连接，部署消费者启动后才传 workers。
    # 约束：不会启动消费者或自动修复失败。
    def add_arguments(self, parser):
        parser.add_argument("--workers", action="store_true")

    # 功能：检查数据库扩展、broker 与结果存储。
    # 输入：`args` 位置参数；`options` 包含 workers，隐式读取 Django 配置。
    # 输出：成功写 stdout；失败抛脱敏 CommandError。
    # 逻辑：执行 vector 运算和 Redis PING；可选各队列任务结果需在 20 秒内匹配。
    # 约束：不修改业务表；随机探测结果读取后删除，异常不自动重试。
    def handle(self, *args, **options):
        try:
            if settings.TASK_EXECUTION_MODE != "celery":
                raise ValueError("This check requires explicit celery mode")
            with connection.cursor() as cursor:
                cursor.execute("SELECT '[1,0]'::vector <=> '[1,0]'::vector")
                if cursor.fetchone()[0] != 0:
                    raise ValueError("Vector distance mismatch")
            for url in (settings.CELERY_BROKER_URL, settings.CELERY_RESULT_BACKEND):
                with Redis.from_url(url, socket_connect_timeout=5, socket_timeout=5) as client:
                    if not client.ping():
                        raise ValueError("Redis ping rejected")
            if options["workers"]:
                from common.tasks import probe
                for queue in ("crm", "sales"):
                    token = secrets.token_hex(16)
                    result = probe.apply_async(args=[token], queue=queue, retry=False, expires=30)
                    if result.get(timeout=20) != token:
                        raise ValueError("Worker probe mismatch")
                    result.forget()
            self.stdout.write("Redis, pgvector and requested worker probes: OK")
        except Exception as error:
            raise CommandError(f"Infrastructure check failed ({type(error).__name__}); inspect Redis, vector extension and Celery services. Credentials omitted.") from None
