"""职责：管理销售动作专用 Google OAuth 连接和加密凭证。
实现：凭证使用显式 Fernet 密钥加密；Gmail 发信与日历连接独立于既有只读同步授权。
关联：sales API 提供授权回调与安全连接列表；actions 仅在明确批准后使用连接。
目录：
- vault：构造必需密钥对应的加密器。
- encrypt_credentials：加密 Google 授权 JSON。
- credentials_for：解密并刷新指定连接凭证。
- begin：创建外部服务授权地址。
- finish：处理 OAuth 回调并保存加密连接。
变量索引：
- SCOPES：各提供方明确申请的 Google OAuth 权限。
- SESSION_KEY：保存单次 OAuth state、PKCE 和提供方的会话键。
"""

import json
import secrets

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import transaction
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from rest_framework.exceptions import ValidationError

from apps.crm.access import InvalidState
from .models import Connection
from .services import audit

SCOPES = {
    "gmail": [
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.readonly",
    ],
    "calendar": [
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/calendar.readonly",
    ],
}
SESSION_KEY = "sales_external_oauth"


# 功能：构造必需密钥对应的加密器。
# 输入：无参数；读取 settings.SALESMATE_VAULT_KEY。
# 输出：Fernet；缺失或格式错误抛 InvalidState。
# 逻辑：仅使用专门密钥，不派生自 Django SECRET_KEY。
# 约束：不自动生成、不输出密钥、不回退为明文。
def vault():
    key = settings.SALESMATE_VAULT_KEY
    if not key:
        raise InvalidState("请先配置 SALESMATE_VAULT_KEY 并妥善备份，再连接外部服务。")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeError):
        raise InvalidState("SALESMATE_VAULT_KEY 格式无效。") from None


# 功能：加密 Google 授权 JSON。
# 输入：`document` 为 OAuth 返回的字典。
# 输出：可保存的密文字符串。
# 逻辑：UTF-8 JSON 经 Fernet 认证加密。
# 约束：不将明文写入日志或业务快照。
def encrypt_credentials(document):
    return vault().encrypt(json.dumps(document).encode("utf-8")).decode("ascii")


# 功能：解密并刷新指定连接凭证。
# 输入：`connection` 为已通过 owner 校验的 Connection。
# 输出：Google Credentials。
# 逻辑：验证密文并按当前提供方 scope 构造凭证，过期时显式刷新并重新加密。
# 约束：任何凭证错误在发送业务内容前失败，不将底层异常或令牌暴露给浏览器。
def credentials_for(connection):
    if connection.archived:
        raise InvalidState("外部连接已停用。")
    try:
        document = json.loads(
            vault().decrypt(connection.encrypted_credentials.encode("ascii"))
        )
        if not set(SCOPES[connection.provider]).issubset(document.get("scopes", [])):
            raise InvalidState("外部连接缺少所需权限，请重新授权。")
        credentials = Credentials.from_authorized_user_info(document)
        if not credentials.has_scopes(SCOPES[connection.provider]):
            raise InvalidState("外部连接缺少所需权限，请重新授权。")
        if credentials.expired:
            credentials.refresh(Request())
            connection.encrypted_credentials = encrypt_credentials(
                json.loads(credentials.to_json())
            )
            connection.save(update_fields=["encrypted_credentials", "updated_at"])
        return credentials
    except InvalidState:
        raise
    except (InvalidToken, ValueError, KeyError):
        raise InvalidState("无法解密或解析授权，请核对加密密钥并重新授权。") from None
    except Exception:
        raise InvalidState("外部授权刷新失败，请检查连接状态并重新授权。") from None


# 功能：创建外部服务授权地址。
# 输入：`request` 为员工会话，`provider` 为 gmail/calendar，`redirect_uri` 为服务器固定回调。
# 输出：Google 授权 URL。
# 逻辑：检查加密配置，创建 PKCE 流程并在 Session 保存一次性校验数据。
# 约束：原只读邮箱授权不被修改；回调地址须在 Google 控制台登记。
def begin(request, provider, redirect_uri):
    if provider not in SCOPES:
        raise ValidationError("不支持的外部服务。")
    vault()
    if not settings.GOOGLE_OAUTH_CLIENT_ID or not settings.GOOGLE_OAUTH_CLIENT_SECRET:
        raise InvalidState("Google OAuth 未配置。")
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES[provider],
        redirect_uri=redirect_uri,
        autogenerate_code_verifier=True,
    )
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    request.session[SESSION_KEY] = {
        "provider": provider,
        "state": state,
        "verifier": flow.code_verifier,
        "redirect": redirect_uri,
    }
    return url


# 功能：处理 OAuth 回调并保存加密连接。
# 输入：`request` 含当前员工会话与 Google code/state。
# 输出：Connection。
# 逻辑：消耗单次 state，交换 code，查询真实账号，再加密存储。
# 约束：失败不创建连接；不把 access token 返回浏览器，不自动启动外部动作。
def finish(request):
    saved = request.session.pop(SESSION_KEY, None)
    if not saved or not secrets.compare_digest(
        str(request.query_params.get("state", "")), saved["state"]
    ):
        raise InvalidState("授权状态已失效，请重新连接。")
    if not request.query_params.get("code"):
        raise InvalidState("授权未完成。")
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES[saved["provider"]],
        redirect_uri=saved["redirect"],
        code_verifier=saved["verifier"],
        autogenerate_code_verifier=False,
    )
    try:
        flow.fetch_token(code=request.query_params["code"])
        if saved["provider"] == "gmail":
            account = (
                build(
                    "gmail", "v1", credentials=flow.credentials, cache_discovery=False
                )
                .users()
                .getProfile(userId="me")
                .execute(num_retries=0)["emailAddress"]
            )
        else:
            account = (
                build(
                    "calendar",
                    "v3",
                    credentials=flow.credentials,
                    cache_discovery=False,
                )
                .calendars()
                .get(calendarId="primary")
                .execute(num_retries=0)["id"]
            )
        encrypted = encrypt_credentials(json.loads(flow.credentials.to_json()))
    except InvalidState:
        raise
    except Exception:
        raise InvalidState(
            "Google 授权交换或账号验证失败，请检查权限与回调配置。"
        ) from None
    with transaction.atomic():
        connection, created = Connection.objects.update_or_create(
            owner=request.user,
            provider=saved["provider"],
            account=account,
            defaults={"encrypted_credentials": encrypted, "archived": False},
        )
        if not created:
            connection.revision += 1
            connection.save(update_fields=["revision", "updated_at"])
        audit(request.user, connection, "external_connection_authorized")
    return connection
