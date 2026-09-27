"""Responsibility: Manage encrypted Google OAuth and QQ SMTP credentials dedicated to sales actions.
Implementation: Encrypt with an explicit Fernet key; return Google credentials or QQ authorization codes by provider. Sending connections remain separate from read-only synchronization.
Relationships: Sales API provides authorization callbacks and safe connection lists; actions uses connections only after explicit approval.
Directory:
- vault: Construct an encryptor from the required key.
- encrypt_credentials: Encrypt Google or QQ authorization JSON.
- credentials_for: Decrypt QQ authorization codes or refresh Google credentials.
- begin: Create an external-service authorization URL.
- finish: Handle OAuth callbacks and save encrypted connections.
Variable index:
- SCOPES: Google OAuth scopes explicitly requested for each provider.
- logger: Logs OAuth owner mismatches using internal user IDs only.
- SESSION_KEY: Session key holding one-time OAuth state, PKCE, and provider.
"""

import json
import logging
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
logger = logging.getLogger("salesmate.oauth")


# Function: Construct an encryptor from the required key.
# Inputs: No parameters; read settings.SALESMATE_VAULT_KEY.
# Outputs: Fernet; missing or malformed keys raise InvalidState.
# Logic: Use only the dedicated key; never derive it from Django SECRET_KEY.
# Constraints: Do not generate or log keys automatically or fall back to plaintext.
def vault():
    key = settings.SALESMATE_VAULT_KEY
    if not key:
        raise InvalidState("请先配置 SALESMATE_VAULT_KEY 并妥善备份，再连接外部服务。")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeError):
        raise InvalidState("SALESMATE_VAULT_KEY 格式无效。") from None


# Function: Encrypt Google or QQ authorization JSON.
# Inputs: `document`: Google OAuth or QQ authorization-code dictionary.
# Outputs: Persistable ciphertext string.
# Logic: Apply Fernet authenticated encryption to UTF-8 JSON.
# Constraints: Never write plaintext to logs or business snapshots.
def encrypt_credentials(document):
    return vault().encrypt(json.dumps(document).encode("utf-8")).decode("ascii")


# Function: Decrypt a connection and return provider-specific execution credentials.
# Inputs: `connection`: Connection that has passed owner checks.
# Outputs: Google Credentials or a QQ dictionary containing authorization_code.
# Logic: Validate and return QQ authorization-code format directly; validate Google scopes and refresh/re-encrypt expired credentials.
# Constraints: Credential errors fail before business content is sent; do not expose underlying exceptions or tokens to the browser.
def credentials_for(connection):
    if connection.archived:
        raise InvalidState("外部连接已停用。")
    try:
        document = json.loads(
            vault().decrypt(connection.encrypted_credentials.encode("ascii"))
        )
        if connection.provider == "qq":
            from .qq_smtp import validate_credentials
            validate_credentials(connection.account, document.get("authorization_code"))
            return {"authorization_code": document["authorization_code"]}
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


# Function: Create an external-service authorization URL.
# Inputs: `request`: employee session; `provider`: gmail/calendar; `redirect_uri`: fixed server callback.
# Outputs: Google authorization URL.
# Logic: Check encryption configuration, create a PKCE flow, and save one-time validation data and the initiating employee ID in Session.
# Constraints: Do not modify existing read-only mailbox authorization; register the callback URL in Google Console.
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
        "owner_id": request.user.pk,
        "provider": provider,
        "state": state,
        "verifier": flow.code_verifier,
        "redirect": redirect_uri,
    }
    return url


# Function: Handle OAuth callbacks and save encrypted connections.
# Inputs: `request`: current employee session and Google code/state.
# Outputs: Connection.
# Logic: Consume one-time state, verify the initiating employee, exchange the code, query the real account, and store encrypted credentials.
# Constraints: Create no connection on failure; never return access tokens to the browser or start external actions automatically.
def finish(request):
    saved = request.session.pop(SESSION_KEY, None)
    if not saved or not secrets.compare_digest(
        str(request.query_params.get("state", "")), saved["state"]
    ):
        raise InvalidState("授权状态已失效，请重新连接。")
    if saved.get("owner_id") != request.user.pk or not request.user.is_authenticated or not request.user.is_active:
        logger.warning("oauth_owner_mismatch provider=%s initiator_id=%s actor_id=%s action=restart_authorization",
                       saved.get("provider"), saved.get("owner_id"), request.user.pk)
        raise InvalidState("登录账号已变化或授权归属已失效，请用当前账号重新连接。")
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
