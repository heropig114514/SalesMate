"""职责：验证限定业务工具权限的独立凭证。
实现：实验模式提供免登录身份；正式模式只接受 Tool，验证撤销、期限和工具范围。
关联：工具入口允许此认证与浏览器 Session；正式模式授权创建和提案确认仅允许 Session。
目录：
- ToolAuthentication：独立工具认证。
- ToolAuthentication.authenticate：校验摘要及授权状态。
- ToolAuthentication.authenticate_header：声明认证方案。
- check_credential：验证凭证当前可用性及具体工具权限。
- ToolAuthenticationSchema：OpenAPI 认证说明。
- ToolAuthenticationSchema.get_security_definition：发布认证头契约。
变量索引：
- ToolAuthenticationSchema.target_class：认证实现路径。
- ToolAuthenticationSchema.name：安全方案名称。
"""

import hashlib
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from .models import ToolCredential
from common.laboratory import enabled, identity


# 功能：核验有效委托。
# 输入：`credential` 授权记录、`name` 可选工具名。
# 输出：无，失效或越权抛认证/权限异常。
# 逻辑：实验模式免授权检查；正式模式逐项检查到期、撤销、停用和白名单。
# 约束：不接受通配符，不从模型输入读取员工身份。
def check_credential(credential, name=None):
    if enabled():
        return
    if (
        credential.revoked_at
        or credential.expires_at <= timezone.now()
        or not credential.owner.is_active
    ):
        raise AuthenticationFailed("工具授权已失效，请由用户重新授权。")
    if name is not None and name not in credential.allowed_tools:
        raise PermissionDenied("该授权不包含此工具。")


# 功能：区分业务委托与既有 Worker 身份。
# 逻辑：实验模式提供公开身份，正式模式仅接受独立 Tool token。
# 约束：令牌不进入日志或查询参数。
class ToolAuthentication(BaseAuthentication):
    # 功能：认证工具请求。
    # 输入：`request` HTTP 请求。
    # 输出：用户与授权记录，或 None。
    # 逻辑：实验模式使用公开身份；正式模式摘要查询后核验当前状态。
    # 约束：正式模式其他 Authorization 方案明确拒绝；实验模式该头仅用于可识别身份归属。
    def authenticate(self, request):
        actor = identity(request)
        if actor is not None:
            return actor, None
        parts = get_authorization_header(request).split()
        if not parts:
            return None
        if len(parts) != 2 or parts[0].lower() != b"tool":
            raise AuthenticationFailed("请使用独立的 Tool 授权。")
        digest = hashlib.sha256(parts[1]).hexdigest()
        credential = (
            ToolCredential.objects.select_related("owner").filter(digest=digest).first()
        )
        if credential is None:
            raise AuthenticationFailed("工具授权无效。")
        check_credential(credential)
        return credential.owner, credential

    # 功能：声明认证方案。
    # 输入：`request`。
    # 输出：Tool。
    # 逻辑：固定响应。
    # 约束：无副作用。
    def authenticate_header(self, request):
        return "Tool"


# 功能：声明工具认证契约。
# 逻辑：独立 Authorization 方案。
# 约束：不执行认证或包含真实 token。
class ToolAuthenticationSchema(OpenApiAuthenticationExtension):
    target_class = "apps.agent_tools.authentication.ToolAuthentication"
    name = "businessToolCredential"

    # 功能：描述认证头。
    # 输入：`auto_schema` 上下文。
    # 输出：OpenAPI 对象。
    # 逻辑：要求 Tool 前缀。
    # 约束：Session-only 授权与确认接口不采用此方案。
    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "header",
            "name": "Authorization",
            "description": "Tool <user-scoped-token>",
        }
