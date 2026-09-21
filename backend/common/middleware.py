"""职责：为每个 HTTP 请求生成服务端关联 ID 并记录完成日志。
实现：调用下游后写入关联 ID 与实验模式标记响应头，记录路径、状态和耗时；不读取正文、查询参数或授权头。
关联：位于 MIDDLEWARE 首位，向健康检查、异常处理器和客户端提供同一 request_id。

目录：
- RequestLoggingMiddleware：串联一次请求的服务端 ID、响应头与完成日志。
- RequestLoggingMiddleware.__init__：保存 Django 提供的下游响应处理器。
- RequestLoggingMiddleware.__call__：执行请求并记录服务端生成的关联信息。

变量索引：
- logger：salesmate.http 日志记录器，输出请求完成事件。
"""

from common.laboratory import enabled

import json
import logging
from time import perf_counter
from uuid import uuid4

logger = logging.getLogger("salesmate.http")


# 功能：串联一次请求的服务端 ID、响应头与完成日志。
# 逻辑：同步调用下游响应处理器，使用单调时钟度量返回响应前的耗时。
# 约束：不读取请求正文、查询字符串和授权头；不测量流式响应后续传输时长。
class RequestLoggingMiddleware:
    """Generate a server-owned correlation ID without logging bodies or credentials."""

    # 功能：保存 Django 提供的下游响应处理器。
    # 输入：`get_response` 为接收请求并返回响应的同步可调用对象。
    # 输出：返回 None，将处理器保存为 self.get_response。
    # 逻辑：初始化阶段仅保存依赖，不发起请求或连接数据库。
    # 约束：不包装调用异常，也不缓存业务响应。
    def __init__(self, get_response):
        self.get_response = get_response

    # 功能：执行请求并记录服务端生成的关联信息。
    # 输入：`request` 为 Django HttpRequest，需要具有 method 与 path 属性。
    # 输出：返回下游响应，写入 X-Request-ID；实验模式 API 同时写入 X-Lab-Open-Access。
    # 逻辑：生成 UUID4 写入 request.request_id；下游返回后按 5xx/其他状态选择 ERROR/INFO，记录 JSON 转义路径及毫秒耗时。
    # 约束：会修改请求与响应并写日志；不采信传入的请求 ID。下游直接抛出的异常不在此捕获，此时不执行后续完成日志。
    def __call__(self, request):
        request.request_id = uuid4().hex
        started = perf_counter()
        response = self.get_response(request)
        response["X-Request-ID"] = request.request_id
        if enabled() and request.path.startswith("/api/"):
            response["X-Lab-Open-Access"] = "true"
        # 5xx 作为服务端错误记录；路径单独取值并 JSON 转义，不拼接查询字符串。
        level = logging.ERROR if response.status_code >= 500 else logging.INFO
        logger.log(
            level,
            "request_completed request_id=%s method=%s path=%s status=%s duration_ms=%.2f",
            request.request_id,
            request.method,
            json.dumps(request.path, ensure_ascii=False),
            response.status_code,
            (perf_counter() - started) * 1000,
        )
        return response
