"""职责：隔离账户清空与 HTTP 读写，防止旧页面写回和业务响应缓存。
实现：视图执行前按 Session/Agent/Tool 身份取得共享锁，覆盖 SessionMiddleware 保存阶段。
关联：MIDDLEWARE 在 SessionMiddleware 之前注册；真正认证仍由原 DRF 认证器执行。
目录：
- request_owner：只解析锁所需的账号主键。
- AccountDataMiddleware：账号请求生命周期保护。
- AccountDataMiddleware.__init__：保存下游处理器。
- AccountDataMiddleware.__call__：释放锁并设置缓存及版本头。
- AccountDataMiddleware.process_view：锁定账号并拒绝旧版本写入。
变量索引：
- 无
"""
import hashlib

from django.http import JsonResponse

from .reset_locks import account_lock
from .reset_models import AccountReset


# 功能：找到请求应锁定的账号，不代替认证授权。
# 输入：`request` 的 Authorization 和 Django session user。
# 输出：账号主键或 None。
# 逻辑：服务令牌只按摘要查询 owner；浏览器从现有认证会话解析。
# 约束：不输出令牌，不接受客户端 owner 参数；DRF 随后重新核验失效及权限。
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


# 功能：让 HTTP 请求和账号重置互斥。
# 逻辑：process_view 已有 Django 会话，外层 __call__ 等会话保存后释放锁。
# 约束：ResetView 自行取得独占锁；数据库健康探针不依赖此状态表。
class AccountDataMiddleware:
    # 功能：保存处理链。
    # 输入：`get_response` 下游处理器。
    # 输出：无。
    # 逻辑：不在启动时连接数据库。
    # 约束：实例不存储单次请求状态，避免线程混用。
    def __init__(self, get_response):
        self.get_response = get_response

    # 功能：设置响应缓存策略并释放请求锁。
    # 输入：`request`。
    # 输出：下游响应，API 禁止缓存；已识别账号附带数据版本。
    # 逻辑：finally 关闭当前请求独立锁连接，覆盖渲染和会话保存。
    # 约束：不读取流式文件内容；重置不改变身份或 cookie。
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

    # 功能：保护 API 与后台管理视图中的账号操作。
    # 输入：`request`、`view_func`、`view_args`、`view_kwargs` 为 Django 视图调度参数。
    # 输出：继续执行的 None，或清理期间/旧页面写入的 409。
    # 逻辑：重置入口自行独占；共享锁下读取版本，清理后重新加载 session；未完成时允许身份 GET 以恢复清理。
    # 约束：缺少版本头的既有客户端保持兼容；新版浏览器所有写操作带版本头。
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
            # user 解析可能在等待共享锁之前读取了旧 session；清理后重新加载已脱敏的持久会话。
            request.session = request.session.__class__(session_key=request.session.session_key)
        return None
