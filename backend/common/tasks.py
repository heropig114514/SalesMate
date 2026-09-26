"""Responsibility: Wrap existing business work units as Celery tasks.
Implementation: Messages contain only work type and database key; execution revalidates the employee, while business claims continue to use database locks and leases.
Relationships: config.celery registers tasks, common.execution submits them, and CRM/Sales retain existing domain logic.
Directory:
- execute: Executes a sync, analysis, or approved sales action.
- probe: Verifies real message delivery and result return.
Variable index:
- logger: Logs task start and failure without mail content, credentials, or external responses.
"""
import logging
from django.contrib.auth import get_user_model
from django.db import connections
from agent.config import load_environment
from config.celery import app

logger = logging.getLogger("salesmate.tasks")


# Function: Invoke an existing business work unit in a Celery process.
# Inputs: `kind` is sync, analysis, or sales; `key` is an employee primary key or sales-action UUID.
# Outputs: Boolean indicating whether business work ran; after recording its type, exceptions are re-raised.
# Logic: Reloads employee validity, calls the domain function still protected by transactions, and releases database connections in finally.
# Constraints: No automatic retries; sales handles approved actions only; queues are accessible only to trusted servers.
@app.task(name="salesmate.execute", max_retries=0)
def execute(kind, key):
    logger.info("task_started kind=%s key=%s", kind, key)
    try:
        load_environment()
        if kind == "sales":
            from apps.sales.actions import run_action
            run_action(key)
            return True
        if kind not in {"sync", "analysis"}:
            raise ValueError("Unknown work kind")
        from apps.crm.worker import run_analysis, run_sync
        owner = get_user_model().objects.get(pk=key, is_active=True)
        return (run_sync if kind == "sync" else run_analysis)(owner)
    except Exception as error:
        logger.error("task_failed kind=%s key=%s error_type=%s action=inspect_business_state_no_automatic_retry", kind, key, type(error).__name__)
        raise
    finally:
        connections.close_all()


# Function: Check the complete round trip through the broker, consumer, and result backend.
# Inputs: `token` is a caller-generated, non-sensitive random probe identifier.
# Outputs: Returns token unchanged.
# Logic: Uses the same JSON message and result channels as business work.
# Constraints: Does not read or write business data or trigger model or external-mail calls.
@app.task(name="salesmate.probe", max_retries=0)
def probe(token):
    return token
