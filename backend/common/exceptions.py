"""职责：统一 DRF 能处理的异常响应格式。
实现：先调用框架处理器，按请求语言翻译已登记的错误文案，再包装数据和请求 ID；未处理异常继续由 Django 处理。
关联：由 REST_FRAMEWORK.EXCEPTION_HANDLER 指定，依赖日志中间件提供 request_id。

目录：
- translate_detail：递归翻译错误值，保留字段键和错误代码。
- api_exception_handler：将 DRF 已处理异常包装为统一错误结构。

变量索引：
- 无
"""

from django.utils.translation import gettext
from rest_framework.exceptions import ErrorDetail
from rest_framework.views import exception_handler


# 功能：递归翻译 DRF 已处理错误的显示文本。
# 输入：`detail` 为错误字符串、列表、字典或标量。
# 输出：相同结构的错误值；ErrorDetail 保留原 code。
# 逻辑：仅错误值调用 gettext，字段键和非字符串不变；未登记文案使用 gettext 原文语义。
# 约束：只在 HTTP 错误边界执行，不翻译业务数据、数据库中的消息或 Agent 协议；不改变状态码。
def translate_detail(detail):
    if isinstance(detail, dict):
        return {key: translate_detail(value) for key, value in detail.items()}
    if isinstance(detail, list):
        return [translate_detail(value) for value in detail]
    if isinstance(detail, ErrorDetail):
        return ErrorDetail(gettext(str(detail)), code=detail.code)
    if isinstance(detail, str):
        return gettext(detail)
    return detail


# 功能：将 DRF 已处理异常包装为统一错误结构。
# 输入：`exc` 为异常对象；`context` 为 DRF 上下文字典，可包含 request。
# 输出：返回包装后的 Response；框架未处理该异常时返回 None。
# 逻辑：保留响应状态和头，翻译已登记的 data 错误值后放入 error.detail；错误代码优先取 default_code，请求 ID 允许缺失。
# 约束：会修改已有响应的 data；不将未知异常伪装为成功，也不打印异常内容或添加重试。
def api_exception_handler(exc, context):
    """Wrap DRF-handled errors; unexpected failures retain Django's failure semantics."""

    response = exception_handler(exc, context)
    if response is not None:
        response.data = {
            "error": {
                "code": getattr(exc, "default_code", "api_error"),
                "detail": translate_detail(response.data),
            },
            "request_id": getattr(context.get("request"), "request_id", None),
        }
    return response
