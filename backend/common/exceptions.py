"""职责：统一 DRF 能处理的异常响应格式。
实现：先调用框架处理器，再包装既有响应数据和请求 ID；未处理异常继续由 Django 处理。
关联：由 REST_FRAMEWORK.EXCEPTION_HANDLER 指定，依赖日志中间件提供 request_id。

目录：
- api_exception_handler：将 DRF 已处理异常包装为统一错误结构。

变量索引：
- 无
"""

from rest_framework.views import exception_handler


# 功能：将 DRF 已处理异常包装为统一错误结构。
# 输入：`exc` 为异常对象；`context` 为 DRF 上下文字典，可包含 request。
# 输出：返回包装后的 Response；框架未处理该异常时返回 None。
# 逻辑：保留原响应状态和头，将原 data 放入 error.detail；错误代码优先取 default_code，请求 ID 允许缺失。
# 约束：会修改已有响应的 data；不将未知异常伪装为成功，也不打印异常内容或添加重试。
def api_exception_handler(exc, context):
    """Wrap DRF-handled errors; unexpected failures retain Django's failure semantics."""

    response = exception_handler(exc, context)
    if response is not None:
        response.data = {
            "error": {
                "code": getattr(exc, "default_code", "api_error"),
                "detail": response.data,
            },
            "request_id": getattr(context.get("request"), "request_id", None),
        }
    return response
