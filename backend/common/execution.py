"""职责：提供本地线程与服务器 Celery 的显式工作执行边界。
实现：调度器保留公平轮转、并发上限和排空逻辑；Celery 只执行有界工作单元。
关联：crm_worker 使用 work_executor，sales_worker 使用 execute_sales；TASK_EXECUTION_MODE 显式选型。
目录：
- RemoteFuture：将 Celery 结果适配为调度器使用的 Future 接口。
- RemoteFuture.__init__：保存异步结果。
- RemoteFuture.done：读取完成状态。
- RemoteFuture.result：等待结果并传播失败。
- CeleryExecutor：提供受限任务提交及排空上下文。
- CeleryExecutor.__init__：保存当前提交的 Future。
- CeleryExecutor.__enter__：返回执行器。
- CeleryExecutor.submit：提交 CRM 工作标识。
- CeleryExecutor.__exit__：等待尚未读取的结果。
- work_executor：按显式配置构建执行器。
- execute_sales：在显式选定的执行方式中运行已批准动作。
变量索引：
- 无
"""
from concurrent.futures import ThreadPoolExecutor
from django.conf import settings


# 功能：将远程结果映射到调度器的最小 Future 协议。
# 逻辑：只在读取完成结果后清理 Redis 记录；失败也向调用方传播。
# 约束：无自动重试、超时取消或隐式本地回退。
class RemoteFuture:
    # 功能：保存远程结果对象。
    # 输入：`result` 为 Celery AsyncResult。
    # 输出：初始化实例，无返回值。
    # 逻辑：保存 handle 与 consumed 状态。
    # 约束：不发起消息或查询。
    def __init__(self, result):
        self.handle = result
        self.consumed = False

    # 功能：查询任务是否结束。
    # 输入：实例保存的 handle。
    # 输出：布尔值。
    # 逻辑：通过结果后端查询 ready。
    # 约束：连接错误直接传播。
    def done(self):
        return self.handle.ready()

    # 功能：等待任务结束并返回结果。
    # 输入：实例保存的 handle 和 consumed 状态。
    # 输出：任务结果；业务或基础设施异常传播。
    # 逻辑：成功或已结束失败后清理结果；连接失败时保留记录供排查。
    # 约束：仅调度器调用，部署不能强杀仍有副作用的任务。
    def result(self):
        try:
            return self.handle.get()
        finally:
            if self.handle.ready():
                self.consumed = True
                self.handle.forget()


# 功能：提供有界 CRM 提交适配器。
# 逻辑：只支持现有同步和分析函数，并仅发送员工主键。
# 约束：上限由调用调度器控制，退出等待工作结束。
class CeleryExecutor:
    # 功能：初始化提交记录。
    # 输入：无外部参数。
    # 输出：空 Future 列表。
    # 逻辑：实例对应一次命令生命周期。
    # 约束：不建立 broker 连接。
    def __init__(self):
        self.pending = []

    # 功能：进入执行上下文。
    # 输入：实例状态。
    # 输出：当前实例。
    # 逻辑：由调度器管理生命周期。
    # 约束：不启动独立进程。
    def __enter__(self):
        return self

    # 功能：提交一个 CRM 工作单元。
    # 输入：`function` 为 run_sync/run_analysis；`owner` 为数据库员工。
    # 输出：RemoteFuture；未知函数抛 ValueError。
    # 逻辑：严格映射函数身份到消息类型，只发送 owner.pk；清理已消费的 Future。
    # 约束：消息发送失败直接传播，不切换执行模式或重发。
    def submit(self, function, owner):
        from apps.crm.worker import run_analysis, run_sync
        from common.tasks import execute
        kinds = {run_sync: "sync", run_analysis: "analysis"}
        if function not in kinds:
            raise ValueError("Unsupported work function")
        future = RemoteFuture(execute.apply_async(args=[kinds[function], owner.pk], queue="crm", retry=False))
        self.pending = [item for item in self.pending if not item.consumed]
        self.pending.append(future)
        return future

    # 功能：排空未消费的远程任务。
    # 输入：`exc_type`/`exc`/`traceback` 为上下文异常；读取 pending。
    # 输出：False，保留异常传播。
    # 逻辑：等待每个未消费 Future；记录首个异常，其他任务仍完成排空。
    # 约束：不取消任务或重复提交，原上下文异常优先。
    def __exit__(self, exc_type, exc, traceback):
        failure = None
        for future in self.pending:
            if not future.consumed:
                try:
                    future.result()
                except Exception as error:
                    failure = failure or error
        if failure is not None and exc is None:
            raise failure
        return False


# 功能：按明确配置创建 CRM 执行器。
# 输入：`max_workers`/`thread_name_prefix` 为原线程配置。
# 输出：本地 ThreadPoolExecutor 或 CeleryExecutor。
# 逻辑：local 保持原行为；celery 由服务器独立消费者执行。
# 约束：非法配置明确失败，不按连接可用性回退。
def work_executor(max_workers, thread_name_prefix):
    if settings.TASK_EXECUTION_MODE == "local":
        return ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=thread_name_prefix)
    if settings.TASK_EXECUTION_MODE == "celery":
        return CeleryExecutor()
    raise ValueError("Invalid TASK_EXECUTION_MODE")


# 功能：执行一项已批准销售动作。
# 输入：`key` 为动作主键，`local_execute` 为现有领域函数。
# 输出：本地结果或远程完成标志。
# 逻辑：服务器提交至独立 sales 队列并等待，保留逐项停止边界。
# 约束：不更改批准规则，不自动重发邮件，连接失败向上传播。
def execute_sales(key, local_execute):
    if settings.TASK_EXECUTION_MODE == "local":
        return local_execute(key)
    if settings.TASK_EXECUTION_MODE != "celery":
        raise ValueError("Invalid TASK_EXECUTION_MODE")
    from common.tasks import execute
    return RemoteFuture(execute.apply_async(args=["sales", str(key)], queue="sales", retry=False)).result()
