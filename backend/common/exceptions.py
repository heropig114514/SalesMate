"""Responsibility: Normalize response structures for exceptions handled by DRF.
Implementation: Calls the framework handler first, translates registered error text for the request language, then wraps data and the request ID; unhandled exceptions continue to Django.
Relationships: Configured as REST_FRAMEWORK.EXCEPTION_HANDLER and depends on request_id provided by logging middleware.

Directory:
- translate_detail: Recursively translates error values while preserving field keys and error codes.
- api_exception_handler: Wraps DRF-handled exceptions in the standard error structure.

Variable index:
- None
"""

from django.utils.translation import gettext
from rest_framework.exceptions import ErrorDetail
from rest_framework.views import exception_handler


# Function: Recursively translate display text for DRF-handled errors.
# Inputs: `detail` is an error string, list, dictionary, or scalar.
# Outputs: Error values with the same structure; ErrorDetail retains its original code.
# Logic: Calls gettext only for error values, leaving field keys and non-strings unchanged; unregistered text follows gettext's source-text semantics.
# Constraints: Runs only at the HTTP error boundary, never translates business data, database messages, or Agent protocols, and never changes the status code.
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


# Function: Wrap a DRF-handled exception in the standard error structure.
# Inputs: `exc` is the exception object; `context` is the DRF context dictionary and may contain request.
# Outputs: The wrapped Response; returns None when the framework does not handle the exception.
# Logic: Preserves response status and headers, translates registered data error values into error.detail, prefers default_code for the error code, and permits a missing request ID.
# Constraints: Mutates data on an existing response; never presents unknown exceptions as success, prints exception contents, or adds retries.
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
