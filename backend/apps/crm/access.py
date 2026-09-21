"""职责：统一业务授权、冲突错误和 JSON 规范化。
实现：正式模式核验令牌和归属；实验模式免登录，开放跨账号查询并跳过 If-Match。
关联：浏览器 Session、AgentCredential 与 common.laboratory 的显式实验开关共用。
目录：
- Conflict：表示版本、幂等或租约冲突。
- InvalidState：表示操作与当前状态不兼容。
- AgentAuthentication：认证仅用于后端业务接口的高熵 Agent 服务令牌。
- AgentAuthentication.authenticate：校验 Authorization: Agent 令牌。
- AgentAuthentication.authenticate_header：声明 Agent 认证方案。
- plain：转换已经校验的数据为 JSON 可存储值。
- company_for：解析属于当前用户的公司。
- mailbox_for：解析属于当前用户的业务邮箱。
- check_version：要求调用方提供与数据库相同的乐观锁版本。
变量索引：
- Conflict.default_code：协议错误码，供客户端分支处理。
- Conflict.default_detail：默认可操作的错误说明。
- Conflict.status_code：该异常对应的 HTTP 状态码。
- InvalidState.default_code：协议错误码，供客户端分支处理。
- InvalidState.default_detail：默认可操作的错误说明。
- InvalidState.status_code：该异常对应的 HTTP 状态码。
- logger：当前模块的脱敏诊断日志记录器。
"""
import hashlib
import json
import logging
import uuid

from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import APIException, AuthenticationFailed, NotFound, ValidationError
from rest_framework.renderers import JSONRenderer

from .models import AgentCredential, Company, Mailbox
from common.laboratory import enabled, identity, owner_scope

logger = logging.getLogger("salesmate.business")


# 功能：表示版本、幂等或租约冲突。
# 逻辑：统一使用 HTTP 409 与协议 conflict 错误码。
# 约束：不进行隐式重试。
class Conflict(APIException):
    status_code = 409
    default_code = "conflict"
    default_detail = "数据已变化，请刷新后重试。"


# 功能：表示操作与当前状态不兼容。
# 逻辑：返回可机器处理的 invalid_state。
# 约束：不掩盖失败为成功。
class InvalidState(APIException):
    status_code = 409
    default_code = "invalid_state"
    default_detail = "当前状态不允许此操作。"


# 功能：认证仅用于后端业务接口的高熵 Agent 服务令牌。
# 逻辑：实验模式使用公开身份；正式模式及输出 OAuth 凭据的领取端点仍校验 Agent 摘要。
# 约束：公开模式身份仅标记归属；OAuth 凭据传输保留机器认证，部署使用 HTTPS。
class AgentAuthentication(BaseAuthentication):
    # 功能：校验 Authorization: Agent 令牌。
    # 输入：`request` 为 DRF 请求，读取 Authorization 头。
    # 输出：已验证用户与凭证元组；无头返回 None，无效头抛 AuthenticationFailed。
    # 逻辑：实验模式使用公开身份；正式模式查找 SHA-256 摘要并要求 owner 仍启用。
    # 约束：仅记录失败类型，不记录头或令牌内容。
    def authenticate(self, request):
        actor = None if request.path.endswith("/mailbox-syncs/claim/") else identity(request)
        if actor is not None:
            return actor, None
        header = get_authorization_header(request).split()
        if not header:
            return None
        if len(header) != 2 or header[0] != b"Agent":
            raise AuthenticationFailed("需要 Agent 服务凭证。")
        digest = hashlib.sha256(header[1]).hexdigest()
        credential = AgentCredential.objects.select_related("owner").filter(digest=digest, owner__is_active=True).first()
        if credential is None:
            logger.warning("agent_authentication_failed action=check_or_rotate_service_credential")
            raise AuthenticationFailed("Agent 服务凭证无效。")
        return credential.owner, credential

    # 功能：声明 Agent 认证方案。
    # 输入：`request` 为未认证请求，不读取正文。
    # 输出：WWW-Authenticate 使用的方案名。
    # 逻辑：返回固定 Agent 字符串。
    # 约束：无副作用。
    def authenticate_header(self, request):
        return "Agent"


# 功能：转换已经校验的数据为 JSON 可存储值。
# 输入：`value` 为含 UUID、日期或 Decimal 的 DRF 数据。
# 输出：普通 JSON 数据结构。
# 逻辑：使用 DRF 渲染器保持 HTTP 表示与数据库快照一致。
# 约束：不执行网络或数据库操作。
def plain(value):
    return json.loads(JSONRenderer().render(value))


# 功能：解析属于当前用户的公司。
# 输入：`owner` 为已认证用户；`company_id` 为公司 UUID；`lock` 控制是否取得事务行锁。
# 输出：Company；UUID 格式错误返回 400，不存在或越权返回相同 404。
# 逻辑：正式模式按 owner 过滤；实验模式开放所有公司。
# 约束：lock=True 时调用方必须处于事务中。
def company_for(owner, company_id, lock=False):
    try:
        company_id = uuid.UUID(str(company_id))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError("company_id 必须为有效 UUID。") from None
    query = Company.objects.filter(owner_scope(owner))
    if lock:
        query = query.select_for_update()
    try:
        return query.get(pk=company_id)
    except (Company.DoesNotExist, ValueError):
        raise NotFound("公司不存在。") from None


# 功能：解析属于当前用户的业务邮箱。
# 输入：`owner` 为认证用户；`mailbox_id` 为后端邮箱 UUID；`lock` 控制行锁。
# 输出：Mailbox；UUID 格式错误返回 400，不存在或越权返回 404。
# 逻辑：正式模式使用 owner 与 ID 联合查询；实验模式按 ID 查询所有邮箱。
# 约束：地址存在不代表 Gmail OAuth 已验证。
def mailbox_for(owner, mailbox_id, lock=False):
    try:
        mailbox_id = uuid.UUID(str(mailbox_id))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError("mailbox_id 必须为有效 UUID。") from None
    query = Mailbox.objects.filter(owner_scope(owner))
    if lock:
        query = query.select_for_update()
    try:
        return query.get(pk=mailbox_id)
    except (Mailbox.DoesNotExist, ValueError):
        raise NotFound("邮箱不存在。") from None


# 功能：要求调用方提供与数据库相同的乐观锁版本。
# 输入：`expected` 为 HTTP 版本整数；`actual` 为实体当前版本。
# 输出：无；缺失或格式错误抛 ValidationError，过期抛 Conflict。
# 逻辑：实验模式直接通过；正式模式只接受非负整数字符串或整数。
# 约束：校验时调用方须持有相关行锁。
def check_version(expected, actual):
    if enabled():
        return
    if expected is None or not str(expected).isdigit():
        raise ValidationError("必须使用 If-Match 传入读取时的非负版本。")
    if int(expected) != actual:
        logger.warning("version_conflict expected=%s actual=%s action=reload_context", expected, actual)
        raise Conflict()
