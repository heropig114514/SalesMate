"""职责：集中控制实验环境的免登录身份与跨账号业务访问。
实现：显式开关开启时使用会话、已有令牌或公开身份选择头，匿名请求归入独立实验账号。
关联：Session、Agent、Tool 认证和业务 scope 共用；关闭开关恢复各入口原鉴权。
目录：
- enabled：读取实验模式开关。
- identity：解析无需证明身份的实验操作者。
- owner_scope：按当前模式构造业务归属查询。
- LaboratoryAuthentication：在实验模式提供免登录 DRF 身份。
- LaboratoryAuthentication.authenticate：按开关解析身份。
- LaboratoryAuthenticationSchema：声明可选实验身份头。
- LaboratoryAuthenticationSchema.get_security_definition：描述实验身份选择。
变量索引：
- logger：记录实验账号建立，不输出令牌。
- LaboratoryAuthenticationSchema.target_class：认证器路径。
- LaboratoryAuthenticationSchema.name：OpenAPI 身份选择名称。
"""

import hashlib
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import NotFound

logger = logging.getLogger("salesmate.laboratory")


# 功能：读取实验模式开关。
# 输入：无外部参数，读取 Django settings。
# 输出：bool。
# 逻辑：默认关闭，仅显式配置开启。
# 约束：不依赖 DEBUG，不连接数据库。
def enabled():
    return getattr(settings, "LAB_OPEN_ACCESS", False)


# 功能：解析无需证明身份的实验操作者。
# 输入：`request` 为 DRF 或 Django 请求。
# 输出：User 或关闭模式时的 None。
# 逻辑：显式 X-Lab-User 优先，其次现有会话、可识别服务凭据，最后独立实验账号。
# 约束：此模式中的身份仅用于归属和日志，不证明调用者身份；不改变 Django admin 会话或输出密钥。
def identity(request):
    if not enabled():
        return None
    users = get_user_model().objects
    selected = request.headers.get("X-Lab-User")
    if selected:
        actor = users.filter(username=selected).first()
        if actor is None:
            raise NotFound("X-Lab-User 指定的账号不存在。")
        return actor
    raw = getattr(request, "_request", request)
    session_user = getattr(raw, "user", None)
    if session_user is not None and session_user.is_authenticated:
        return session_user
    parts = request.headers.get("Authorization", "").split()
    if len(parts) == 2:
        from apps.crm.models import AgentCredential
        from apps.agent_tools.models import ToolCredential
        model = {"agent": AgentCredential, "tool": ToolCredential}.get(parts[0].lower())
        if model:
            credential = model.objects.select_related("owner").filter(digest=hashlib.sha256(parts[1].encode()).hexdigest()).first()
            if credential:
                return credential.owner
    actor, created = users.get_or_create(username=settings.LAB_DEFAULT_USER,
                                        defaults={"password": "!", "is_active": True})
    if created:
        logger.warning("laboratory_actor_created user_id=%s authentication_disabled=true", actor.pk)
    return actor


# 功能：按当前模式构造业务归属查询。
# 输入：`owner` 为原归属用户，`path` 为 owner 关系路径，默认 owner。
# 输出：Q 条件。
# 逻辑：实验模式不限制归属，正式模式保留原关系过滤。
# 约束：仅用于明确的业务查询，不用于密码、OAuth 或管理员权限判定。
def owner_scope(owner, path="owner"):
    return Q() if enabled() else Q(**{path: owner})


# 功能：在实验模式提供免登录 DRF 身份。
# 逻辑：开启时优先于 Session 认证，不触发其 CSRF 检查。
# 约束：关闭时返回 None，继续既有认证链。
class LaboratoryAuthentication(BaseAuthentication):
    # 功能：按开关解析身份。
    # 输入：`request` HTTP 请求。
    # 输出：身份元组或 None。
    # 逻辑：共享 identity 策略；不签发会话或令牌。
    # 约束：只影响采用此认证器的业务 API。
    def authenticate(self, request):
        actor = identity(request)
        return (actor, None) if actor is not None else None


# 功能：声明可选实验身份头。
# 逻辑：文档明确它不是认证凭据。
# 约束：关闭实验模式时不授予任何权限。
class LaboratoryAuthenticationSchema(OpenApiAuthenticationExtension):
    target_class = "common.laboratory.LaboratoryAuthentication"
    name = "laboratoryIdentity"

    # 功能：描述实验身份选择。
    # 输入：`auto_schema` 为 Schema 上下文。
    # 输出：OpenAPI Header 描述。
    # 逻辑：公开 X-Lab-User 的可选选择语义。
    # 约束：无数据库读取，不提供密码或令牌。
    def get_security_definition(self, auto_schema):
        return {"type": "apiKey", "in": "header", "name": "X-Lab-User",
                "description": "仅 LAB_OPEN_ACCESS=true 时可选的实验归属用户名，无需令牌；省略使用实验默认身份。正式模式不可用。"}
