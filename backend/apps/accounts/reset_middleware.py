"""Responsibility: Isolate account clearing from HTTP reads and writes to prevent stale-page writes and caching of business responses.
Implementation: Acquire a shared lock by Session, Agent, or Tool identity before a view runs, spanning the ``SessionMiddleware`` save stage.
Relationships: ``MIDDLEWARE`` registers this before ``SessionMiddleware``; the original DRF authenticators still perform actual authentication.
Directory:
- request_owner: Resolve only the account primary key needed for locking.
- AccountDataMiddleware: Protect the account-request lifecycle.
- AccountDataMiddleware.__init__: Store the downstream handler.
- AccountDataMiddleware.__call__: Release the lock and set cache and version headers.
- AccountDataMiddleware.process_view: Lock the account and reject stale-version writes.
Variable index:
- None
"""
import hashlib

from django.http import JsonResponse

from .reset_locks import account_lock
from .reset_models import AccountReset


# Function: Find the account that a request should lock without replacing authentication or authorization.
# Inputs: ``request`` Authorization and Django session user.
# Outputs: Account primary key or ``None``.
# Logic: Service tokens query the owner only by digest; browsers resolve the owner from an existing authenticated session.
# Constraints: Does not emit tokens or accept a client-supplied owner parameter; DRF subsequently revalidates expiry and permissions.
def request_owner(request):
    header = request.headers.get("Authorization", "").split()
    if header:
        if len(header) != 2:
            return None
        from apps.crm.models import AgentCredential
        from apps.agent_tools.models import ToolCredential
        model = {"agent": AgentCredential, "tool": ToolCredential}.get(header[0].lower())
        if model is None:
            return None
        return model.objects.filter(digest=hashlib.sha256(header[1].encode()).hexdigest()).values_list("owner_id", flat=True).first()
    return request.user.pk if request.user.is_authenticated else None


# Function: Make HTTP requests mutually exclusive with account reset.
# Logic: ``process_view`` has the Django session, while outer ``__call__`` releases the lock after the session is saved.
# Constraints: ``ResetView`` acquires its own exclusive lock; database health probes do not depend on this state table.
class AccountDataMiddleware:
    # Function: Store the processing chain.
    # Inputs: ``get_response`` is the downstream handler.
    # Outputs: None.
    # Logic: Does not connect to the database at startup.
    # Constraints: The instance stores no per-request state, preventing cross-thread reuse.
    def __init__(self, get_response):
        self.get_response = get_response

    # Function: Set the response caching policy and release the request lock.
    # Inputs: ``request``.
    # Outputs: Downstream response; API responses prohibit caching and identified accounts include a data version.
    # Logic: ``finally`` closes the current request's dedicated lock connection, covering rendering and session saving.
    # Constraints: Does not read streaming-file content; reset does not change identity or cookies.
    def __call__(self, request):
        try:
            response = self.get_response(request)
            if request.path.startswith("/api/"):
                response["Cache-Control"] = "no-store"
            if hasattr(request, "account_data_generation"):
                response["X-Account-Data-Version"] = str(request.account_data_generation)
                response["X-Account-ID"] = str(request.account_data_owner)
                response["X-Account-Reset-Status"] = request.account_reset_status
            return response
        finally:
            guard = getattr(request, "account_data_guard", None)
            if guard is not None:
                guard.__exit__(None, None, None)

    # Function: Protect account operations in API and administration views.
    # Inputs: ``request``, ``view_func``, ``view_args``, and ``view_kwargs`` are Django view-dispatch parameters.
    # Outputs: ``None`` to continue, or HTTP 409 for writes during cleanup or from a stale page.
    # Logic: The reset entry point is independently exclusive; read the version under a shared lock, reload the session after cleanup, and permit identity GET requests to recover incomplete cleanup.
    # Constraints: Existing clients without a version header remain compatible; all writes by the new browser carry a version header.
    def process_view(self, request, view_func, view_args, view_kwargs):
        if not request.path.startswith(("/api/", "/admin/")) or request.path.startswith("/api/v1/health/"):
            return None
        if request.resolver_match.view_name == "accounts:reset":
            return None
        owner_id = request_owner(request)
        if owner_id is None:
            return None
        guard = account_lock(owner_id)
        guard.__enter__()
        request.account_data_guard = guard
        state = AccountReset.objects.filter(owner_id=owner_id).first()
        generation = state.generation if state else 0
        request.account_data_generation = generation
        request.account_data_owner = owner_id
        request.account_reset_status = "cleaning" if state and state.cleaning else "completed"
        recovery_read = request.method == "GET" and request.path in {"/api/v1/accounts/me/", "/api/v1/session/"}
        if state and state.cleaning and not recovery_read:
            return JsonResponse({"error": {"code": "account_reset_incomplete", "detail": "账号清理尚未完成，请继续清空操作。"}, "request_id": getattr(request, "request_id", None)}, status=409)
        expected = request.headers.get("X-Account-Data-Version")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and expected is not None and expected != str(generation):
            return JsonResponse({"error": {"code": "account_data_reset", "detail": "账号数据已清空，请刷新页面后重新操作。"}, "request_id": getattr(request, "request_id", None)}, status=409)
        if not request.headers.get("Authorization") and generation > request.session.get("account_data_generation", 0):
            # User resolution may have read a stale session before waiting for the shared lock; reload the redacted persistent session after cleanup.
            request.session = request.session.__class__(session_key=request.session.session_key)
        return None
