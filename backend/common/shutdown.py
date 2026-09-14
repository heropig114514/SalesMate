"""职责：让后台 Worker 在部署停止信号后完成当前工作单元。
实现：临时 SIGTERM 处理器仅设置标志；调用方在领取新任务前检查，退出时恢复旧处理器。
关联：crm_worker 等待线程池完成，sales_worker 等待当前外部动作持久化。
目录：
- graceful_shutdown：提供停止请求状态的上下文。
- graceful_shutdown.request_shutdown：记录 SIGTERM 停止请求。
变量索引：
- 无
"""
from contextlib import contextmanager
import signal


# 功能：将 SIGTERM 转换为可在业务边界处理的停止请求。
# 输入：无参数；读取主线程原 SIGTERM 处理器。
# 输出：包含 requested 布尔值的状态字典。
# 逻辑：处理器只设置状态，既不抛出中断也不获取锁；finally 恢复原处理器。
# 约束：仅用于管理命令主线程；不接管 SIGINT，不自动重试或中止当前外部调用。
@contextmanager
def graceful_shutdown():
    state = {"requested": False}

    # 功能：记录部署停止请求。
    # 输入：`signum` 为 SIGTERM；`frame` 为被打断的 Python 帧。
    # 输出：无，更新闭包状态。
    # 逻辑：只赋值，避免信号处理器重入锁或日志系统。
    # 约束：调用方仍须完成当前单元并主动退出循环。
    def request_shutdown(signum, frame):
        state["requested"] = True

    previous = signal.signal(signal.SIGTERM, request_shutdown)
    try:
        yield state
    finally:
        signal.signal(signal.SIGTERM, previous)
