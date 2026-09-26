"""Responsibility: Provide explicit execution boundaries for local threads and server-side Celery.
Implementation: Schedulers retain fair rotation, concurrency limits, and draining logic; Celery executes bounded work units only.
Relationships: crm_worker uses work_executor and sales_worker uses execute_sales; TASK_EXECUTION_MODE makes the selection explicit.
Directory:
- RemoteFuture: Adapts a Celery result to the Future interface used by schedulers.
- RemoteFuture.__init__: Stores the asynchronous result.
- RemoteFuture.done: Reads completion state.
- RemoteFuture.result: Waits for the result and propagates failure.
- CeleryExecutor: Provides constrained task submission and a draining context.
- CeleryExecutor.__init__: Stores submitted Futures.
- CeleryExecutor.__enter__: Returns the executor.
- CeleryExecutor.submit: Submits a CRM work identifier.
- CeleryExecutor.__exit__: Waits for unread results.
- work_executor: Builds an executor from explicit configuration.
- execute_sales: Runs an approved action in the explicitly selected execution mode.
Variable index:
- None
"""
from concurrent.futures import ThreadPoolExecutor
from django.conf import settings


# Function: Map a remote result to the minimal Future protocol used by schedulers.
# Logic: Clears Redis records only after their completed result is read; failures also propagate to the caller.
# Constraints: No automatic retry, timeout cancellation, or implicit local fallback.
class RemoteFuture:
    # Function: Store the remote result object.
    # Inputs: `result` is a Celery AsyncResult.
    # Outputs: Initializes the instance and returns no value.
    # Logic: Stores handle and consumed state.
    # Constraints: Does not send messages or issue queries.
    def __init__(self, result):
        self.handle = result
        self.consumed = False

    # Function: Check whether the task has completed.
    # Inputs: The handle stored on the instance.
    # Outputs: Boolean.
    # Logic: Queries ready through the result backend.
    # Constraints: Connection errors propagate directly.
    def done(self):
        return self.handle.ready()

    # Function: Wait for task completion and return its result.
    # Inputs: The instance's handle and consumed state.
    # Outputs: Task result; business or infrastructure exceptions propagate.
    # Logic: Clears the result after success or completed failure; retains the record for diagnosis if the connection fails.
    # Constraints: Called only by schedulers; deployment must not forcibly kill tasks that may still have side effects.
    def result(self):
        try:
            return self.handle.get()
        finally:
            if self.handle.ready():
                self.consumed = True
                self.handle.forget()


# Function: Provide a bounded CRM-submission adapter.
# Logic: Supports existing sync and analysis functions only and sends only employee primary keys.
# Constraints: The calling scheduler controls limits; exit waits for work completion.
class CeleryExecutor:
    # Function: Initialize submission tracking.
    # Inputs: No external parameters.
    # Outputs: Empty Future list.
    # Logic: The instance represents one command lifecycle.
    # Constraints: Does not establish a broker connection.
    def __init__(self):
        self.pending = []

    # Function: Enter the execution context.
    # Inputs: Instance state.
    # Outputs: The current instance.
    # Logic: The scheduler manages the lifecycle.
    # Constraints: Does not start a separate process.
    def __enter__(self):
        return self

    # Function: Submit one CRM work unit.
    # Inputs: `function` is run_sync or run_analysis; `owner` is the database employee.
    # Outputs: RemoteFuture; raises ValueError for an unknown function.
    # Logic: Strictly maps function identity to message type, sends only owner.pk, and removes consumed Futures.
    # Constraints: Message-send failures propagate directly without switching execution mode or resending.
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

    # Function: Drain unconsumed remote tasks.
    # Inputs: `exc_type`, `exc`, and `traceback` are context exceptions; reads pending.
    # Outputs: False, preserving exception propagation.
    # Logic: Waits for every unconsumed Future, records the first failure, and still drains remaining tasks.
    # Constraints: Does not cancel tasks or submit them again; the original context exception takes precedence.
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


# Function: Create a CRM executor from explicit configuration.
# Inputs: `max_workers` and `thread_name_prefix` are the existing thread configuration.
# Outputs: Local ThreadPoolExecutor or CeleryExecutor.
# Logic: local preserves existing behavior; an independent server consumer executes celery work.
# Constraints: Invalid configuration fails explicitly and never falls back based on connection availability.
def work_executor(max_workers, thread_name_prefix):
    if settings.TASK_EXECUTION_MODE == "local":
        return ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=thread_name_prefix)
    if settings.TASK_EXECUTION_MODE == "celery":
        return CeleryExecutor()
    raise ValueError("Invalid TASK_EXECUTION_MODE")


# Function: Execute one approved sales action.
# Inputs: `key` is the action primary key; `local_execute` is the existing domain function.
# Outputs: Local result or remote-completion indicator.
# Logic: Server mode submits to the separate sales queue and waits, preserving the per-item stop boundary.
# Constraints: Does not change approval rules or automatically resend mail; connection failures propagate.
def execute_sales(key, local_execute):
    if settings.TASK_EXECUTION_MODE == "local":
        return local_execute(key)
    if settings.TASK_EXECUTION_MODE != "celery":
        raise ValueError("Invalid TASK_EXECUTION_MODE")
    from common.tasks import execute
    return RemoteFuture(execute.apply_async(args=["sales", str(key)], queue="sales", retry=False)).result()
