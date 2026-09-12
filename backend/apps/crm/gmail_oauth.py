"""职责：管理员工 Gmail OAuth 与一次性同步请求。
实现：后端交换授权码并保存凭证，浏览器只读安全状态，Agent 通过服务认证领取和回报。
关联：views 暴露浏览器与 Agent 路由，models.GmailCredential 保存授权，Agent 负责读取邮件。
目录：
- _client_config：构造 Google Web application 客户端配置。
- begin_authorization：创建员工授权地址并保存 state 与 PKCE verifier。
- finish_authorization：交换 code、验证账号并请求首次同步。
- mailbox_status：生成不含凭证的浏览器邮箱状态。
- request_mailbox_sync：把已授权邮箱标记为等待同步。
- disconnect_mailbox：移除本地授权并保留历史业务数据。
- claim_mailbox_syncs：为一次性 Agent 领取同步请求。
- report_mailbox_sync：保存 Agent 同步结果与刷新凭证。
变量索引：
- GMAIL_SCOPES：Google Gmail 只读 OAuth scope。
- SESSION_STATE_KEY：当前浏览器会话保存 OAuth state 的键。
- SESSION_CODE_VERIFIER_KEY：当前浏览器会话保存 PKCE code_verifier 的键。
- __all__：本模块公开服务函数。
"""

from __future__ import annotations

import json
import secrets
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from .access import InvalidState, mailbox_for
from .models import GmailCredential, Mailbox

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
SESSION_STATE_KEY = "salesmate_gmail_oauth_state"
SESSION_CODE_VERIFIER_KEY = "salesmate_gmail_oauth_code_verifier"


# 功能：构造 google-auth-oauthlib 所需的 Web application 配置。
# 输入：无显式参数，读取 Django settings。
# 输出：Google OAuth web 客户端字典。
# 逻辑：仅在发起授权时要求客户端 ID 和密钥非空。
# 约束：错误消息不包含密钥值。
def _client_config() -> dict[str, Any]:
    """Build the Google web-client document expected by google-auth-oauthlib."""
    client_id = settings.GOOGLE_OAUTH_CLIENT_ID.strip()
    client_secret = settings.GOOGLE_OAUTH_CLIENT_SECRET.strip()
    if not client_id or not client_secret:
        raise InvalidState("Google OAuth 尚未配置，请先填写根目录 .env。")
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


# 功能：创建当前员工的 Google 授权地址并保存 state。
# 输入：`request` 为已认证浏览器请求。
# 输出：Google authorization URL 字符串。
# 逻辑：请求 offline access、consent 和 gmail.readonly，并保存本次 PKCE verifier。
# 约束：不在响应中返回客户端密钥或 Gmail 令牌。
def begin_authorization(request) -> str:
    """Create the employee-specific Google authorization URL and remember state."""
    flow = Flow.from_client_config(
        _client_config(),
        scopes=GMAIL_SCOPES,
        redirect_uri=settings.GOOGLE_OAUTH_REDIRECT_URI,
    )
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    request.session[SESSION_STATE_KEY] = state
    request.session[SESSION_CODE_VERIFIER_KEY] = flow.code_verifier
    return authorization_url


# 功能：完成 Google 回调并建立当前员工邮箱连接。
# 输入：`request` 含员工会话、code 和 state。
# 输出：已验证地址对应的 Mailbox。
# 逻辑：恢复发起授权时的 PKCE verifier，交换 code、读取 Gmail profile、保存凭证并请求首次同步。
# 约束：state、PKCE verifier 不匹配或地址无效时不建立绑定。
def finish_authorization(request) -> Mailbox:
    """Exchange Google callback code, verify the account, and queue its first sync."""
    expected_state = request.session.pop(SESSION_STATE_KEY, "")
    code_verifier = request.session.pop(SESSION_CODE_VERIFIER_KEY, "")
    returned_state = request.query_params.get("state", "")
    code = request.query_params.get("code", "")
    if not expected_state or not returned_state or not secrets.compare_digest(
        expected_state, returned_state
    ):
        raise InvalidState("Google OAuth state 已失效，请重新发起授权。")
    if not code:
        raise InvalidState("Google 未返回授权码，请重新发起授权。")
    if not code_verifier:
        raise InvalidState("Google OAuth PKCE 校验信息已失效，请重新发起授权。")

    flow = Flow.from_client_config(
        _client_config(),
        scopes=GMAIL_SCOPES,
        state=returned_state,
        redirect_uri=settings.GOOGLE_OAUTH_REDIRECT_URI,
        code_verifier=code_verifier,
        autogenerate_code_verifier=False,
    )
    flow.fetch_token(code=code)
    credentials = flow.credentials
    profile = (
        build("gmail", "v1", credentials=credentials, cache_discovery=False)
        .users()
        .getProfile(userId="me")
        .execute()
    )
    address = str(profile.get("emailAddress", "")).strip().casefold()
    if address.count("@") != 1:
        raise InvalidState("Google 账号未返回有效 Gmail 地址。")

    with transaction.atomic():
        mailbox, _ = Mailbox.objects.get_or_create(
            owner=request.user, address=address
        )
        GmailCredential.objects.update_or_create(
            mailbox=mailbox,
            defaults={"credentials": json.loads(credentials.to_json())},
        )
        request_mailbox_sync(request.user, mailbox.pk)
    return mailbox


# 功能：生成一条浏览器安全的员工邮箱状态。
# 输入：`mailbox` 为当前员工可访问的 Mailbox。
# 输出：邮箱标识、地址、授权布尔值和 sync_state。
# 逻辑：根据一对一 GmailCredential 是否存在判断授权。
# 约束：输出不包含 authorization 或任何 Google 令牌。
def mailbox_status(mailbox: Mailbox) -> dict[str, Any]:
    """Return one browser-safe mailbox row for the owning employee."""
    return {
        "mailbox_id": str(mailbox.pk),
        "address": mailbox.address,
        "gmail_authorized": hasattr(mailbox, "gmail_credential"),
        "sync_state": mailbox.sync_state or {"status": "authorization_required"},
    }


# 功能：请求一次员工 Gmail 同步。
# 输入：`owner` 为当前员工，`mailbox_id` 为其邮箱 UUID。
# 输出：更新后的浏览器安全邮箱状态。
# 逻辑：锁定邮箱并把状态改为 sync_requested。
# 约束：拒绝其他员工邮箱和未授权邮箱。
def request_mailbox_sync(owner, mailbox_id) -> dict[str, Any]:
    """Queue one authorized mailbox for the next one-shot Agent execution."""
    with transaction.atomic():
        mailbox = mailbox_for(owner, mailbox_id, lock=True)
        if not GmailCredential.objects.filter(mailbox=mailbox).exists():
            raise InvalidState("该邮箱尚未完成 Google 授权。")
        current = dict(mailbox.sync_state or {})
        current.update(
            {
                "status": "sync_requested",
                "requested_at": timezone.now().isoformat(),
                "error": None,
            }
        )
        mailbox.sync_state = current
        mailbox.version += 1
        mailbox.save(update_fields=["sync_state", "version"])
    return mailbox_status(mailbox)


# 功能：移除员工 Gmail 的本地授权。
# 输入：`owner` 为当前员工，`mailbox_id` 为其邮箱 UUID。
# 输出：authorization_required 浏览器状态。
# 逻辑：删除一对一凭证并递增邮箱版本。
# 约束：保留历史邮件、公司和分析记录。
def disconnect_mailbox(owner, mailbox_id) -> dict[str, Any]:
    """Delete the local Gmail grant and mark the mailbox disconnected."""
    with transaction.atomic():
        mailbox = mailbox_for(owner, mailbox_id, lock=True)
        GmailCredential.objects.filter(mailbox=mailbox).delete()
        mailbox.sync_state = {"status": "authorization_required"}
        mailbox.version += 1
        mailbox.save(update_fields=["sync_state", "version"])
    return mailbox_status(mailbox)


# 功能：为一次性 Agent 领取员工邮箱同步请求。
# 输入：`owner` 为 Agent 凭证所属员工，`limit` 为本次领取上限。
# 输出：含邮箱地址、授权和读取上限的同步请求数组。
# 逻辑：锁定 sync_requested 邮箱并改为 sync_running。
# 约束：只返回 owner 自己的已授权邮箱，不实现租约。
def claim_mailbox_syncs(owner, limit: int) -> list[dict[str, Any]]:
    """Claim up to limit employee mailbox requests for one Agent process."""
    claimed: list[dict[str, Any]] = []
    with transaction.atomic():
        mailboxes = list(
            Mailbox.objects.select_for_update()
            .select_related("gmail_credential")
            .filter(
                owner=owner,
                gmail_credential__isnull=False,
                sync_state__status="sync_requested",
            )
            .order_by("address")[:limit]
        )
        for mailbox in mailboxes:
            mailbox.sync_state = {
                **(mailbox.sync_state or {}),
                "status": "sync_running",
                "started_at": timezone.now().isoformat(),
                "error": None,
            }
            mailbox.version += 1
            mailbox.save(update_fields=["sync_state", "version"])
            claimed.append(
                {
                    "mailbox_id": str(mailbox.pk),
                    "mailbox_address": mailbox.address,
                    "authorization": mailbox.gmail_credential.credentials,
                    "max_results": 20,
                }
            )
    return claimed


# 功能：保存 Agent 对员工邮箱同步的最终回报。
# 输入：`owner` 为 Agent 凭证所属员工，`data` 为已校验同步报告。
# 输出：更新后的浏览器安全邮箱状态。
# 逻辑：保存汇总、完成时间和可选刷新凭证。
# 约束：失败不自动重试，授权已移除时拒绝回报。
def report_mailbox_sync(owner, data: dict[str, Any]) -> dict[str, Any]:
    """Save the final sync state and optionally a refreshed Google credential."""
    with transaction.atomic():
        mailbox = mailbox_for(owner, data["mailbox_id"], lock=True)
        credential = GmailCredential.objects.filter(mailbox=mailbox).first()
        if credential is None:
            raise InvalidState("该邮箱授权已被员工移除。")
        refreshed = data.get("authorization")
        if isinstance(refreshed, dict) and refreshed:
            credential.credentials = refreshed
            credential.save(update_fields=["credentials", "updated_at"])

        status = data["status"]
        sync_result = data.get("sync_result") or {}
        current = dict(mailbox.sync_state or {})
        current.update(
            {
                "status": status,
                "finished_at": timezone.now().isoformat(),
                "error": data.get("error") if status == "failed" else None,
                "last_result": sync_result,
            }
        )
        if status == "completed":
            current["last_synced_at"] = timezone.now().isoformat()
        mailbox.sync_state = current
        mailbox.version += 1
        mailbox.save(update_fields=["sync_state", "version"])
    return mailbox_status(mailbox)


__all__ = [
    "GMAIL_SCOPES",
    "begin_authorization",
    "claim_mailbox_syncs",
    "disconnect_mailbox",
    "finish_authorization",
    "mailbox_status",
    "report_mailbox_sync",
    "request_mailbox_sync",
]
