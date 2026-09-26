"""Responsibility: Define the server Celery application and explicit message-failure semantics.
Implementation: Reads Redis addresses from the Django environment, sends JSON identifiers only, and disables business and publish retries.
Relationships: common.execution submits work and common.tasks executes it; local mode does not connect to Redis.
Directory:
- None
Variable index:
- app: Celery application configuring serialization, acknowledgements, results, and queue behavior.
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
