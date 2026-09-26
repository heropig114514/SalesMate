"""Responsibility: Generate a server-side correlation ID and completion log for every HTTP request.
Implementation: After calling downstream, writes correlation-ID and laboratory-mode response headers and records path, status, and duration; does not read bodies, query parameters, or authorization headers.
Relationships: First in MIDDLEWARE and provides the same request_id to health checks, the exception handler, and clients.

Directory:
- RequestLoggingMiddleware: Connects one request's server ID, response headers, and completion log.
- RequestLoggingMiddleware.__init__: Stores the downstream response handler supplied by Django.
- RequestLoggingMiddleware.__call__: Executes the request and records server-generated correlation information.

Variable index:
- logger: salesmate.http logger that emits request-completion events.
"""

from common.laboratory import enabled

import json
import logging
from time import perf_counter
from uuid import uuid4

logger = logging.getLogger("salesmate.http")


# Function: Connect a request's server ID, response headers, and completion log.
# Logic: Synchronously calls the downstream response handler and uses a monotonic clock to measure time before returning the response.
# Constraints: Does not read request bodies, query strings, or authorization headers; does not measure subsequent streaming-response transmission.
class RequestLoggingMiddleware:
    """Generate a server-owned correlation ID without logging bodies or credentials."""

    # Function: Store the downstream response handler supplied by Django.
    # Inputs: `get_response` is a synchronous callable accepting a request and returning a response.
    # Outputs: Returns None and stores the handler as self.get_response.
    # Logic: Initialization stores the dependency only and makes no request or database connection.
    # Constraints: Does not wrap invocation errors or cache business responses.
    def __init__(self, get_response):
        self.get_response = get_response

    # Function: Execute a request and record server-generated correlation information.
    # Inputs: `request` is a Django HttpRequest that must expose method and path.
    # Outputs: Returns the downstream response and writes X-Request-ID; laboratory-mode APIs also write X-Lab-Open-Access.
    # Logic: Generates UUID4 into request.request_id; after downstream returns, selects ERROR for 5xx and INFO otherwise, and records a JSON-escaped path and milliseconds elapsed.
    # Constraints: Mutates request and response and writes logs; does not trust supplied request IDs. Exceptions raised directly downstream are not caught, so no subsequent completion log runs.
    def __call__(self, request):
        request.request_id = uuid4().hex
        started = perf_counter()
        response = self.get_response(request)
        response["X-Request-ID"] = request.request_id
        if enabled() and request.path.startswith("/api/"):
            response["X-Lab-Open-Access"] = "true"
        # Log 5xx as a server error; obtain and JSON-escape the path separately without appending the query string.
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
