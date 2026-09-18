"""职责：将现有业务工作单元包装为 Celery 任务。
实现：消息只包含工作类型和数据库主键；执行前重新校验员工，业务领取仍使用数据库锁与租约。
关联：config.celery 注册任务，common.execution 提交任务，CRM/Sales 保留既有领域逻辑。
目录：
- execute：执行一个同步、分析或已批准销售动作。
- probe：验证真实消息传递及结果回传。
变量索引：
- logger：任务开始和失败日志，不包含邮件内容、凭证或外部响应。
"""
import logging
from django.contrib.auth import get_user_model
from django.db import connections
from agent.config import load_environment
from config.celery import app

logger = logging.getLogger("salesmate.tasks")


# 功能：在 Celery 进程中调用一个现有业务工作单元。
# 输入：`kind` 为 sync/analysis/sales；`key` 为员工主键或销售动作 UUID。
# 输出：业务是否执行的布尔值；异常记录类型后重新抛出。
# 逻辑：重新加载员工有效状态，调用仍受事务保护的领域函数，finally 释放数据库连接。
# 约束：无自动重试；销售仅处理已批准动作；队列仅允许受信服务器访问。
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


# 功能：检查 broker、消费者与结果后端的完整往返。
# 输入：`token` 为调用方生成的非敏感随机探测标识。
# 输出：原样返回 token。
# 逻辑：使用与业务相同的 JSON 消息和结果通道。
# 约束：不读写业务数据，不触发模型或外部邮件调用。
@app.task(name="salesmate.probe", max_retries=0)
def probe(token):
    return token
