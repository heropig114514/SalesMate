"""职责：定义服务器 Celery 应用与明确的消息失败语义。
实现：从 Django 环境读取 Redis 地址，只传 JSON 标识符，禁止业务自动重试及发送重试。
关联：common.execution 提交工作，common.tasks 执行；本地模式不连接 Redis。
目录：
- 无
变量索引：
- app：Celery 应用，配置序列化、确认、结果和队列行为。
"""
from celery import Celery
from django.conf import settings

app = Celery("salesmate", include=["common.tasks"])
app.conf.update(
    broker_url=settings.CELERY_BROKER_URL,
    result_backend=settings.CELERY_RESULT_BACKEND,
    task_serializer="json", result_serializer="json", accept_content=["json"],
    task_acks_late=False, task_reject_on_worker_lost=False,
    task_publish_retry=False, broker_connection_retry=False,
    broker_connection_retry_on_startup=False, worker_prefetch_multiplier=1,
    result_expires=86400, task_track_started=True,
    broker_transport_options={"socket_connect_timeout": 5, "socket_timeout": 5},
    result_backend_transport_options={"retry_policy": {"max_retries": 0}},
    redis_socket_connect_timeout=5, redis_socket_timeout=5,
    redis_retry_on_timeout=False, broker_connection_timeout=5,
)
