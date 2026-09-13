"""职责：管理员工 Gmail OAuth 与一次性同步请求。
实现：后端交换授权码并保存凭证；请求写入持久批次；保留旧 Agent 领取及回报接口供版本迁移。
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
        prompt="consent",
    )
    request.session[SESSION_STATE_KEY] = state
    request.session[SESSION_CODE_VERIFIER_KEY] = flow.code_verifier
    return authorization_url


# 功能：交换授权码，并兼容 Google 返回已授权 scope 超集的情况。
# 输入：已恢复 PKCE verifier 的 Flow 与一次性授权码。
# 输出：无；成功后 Flow 持有可供 credentials 属性读取的 token。
# 逻辑：oauthlib 会把 scope 变化抛为 Warning；只在返回权限仍包含 Gmail 只读权限时接受 token。
# 约束：缺少请求权限或 Warning 不含有效 token 时继续抛错。
def _fetch_token(flow, code: str) -> None:
    try:
        flow.fetch_token(code=code)
    except Warning as error:
        token = getattr(error, "token", None)
        returned_scopes = set(getattr(error, "new_scope", None) or [])
        if not token or not set(GMAIL_SCOPES).issubset(returned_scopes):
            raise
        flow.oauth2session.token = token


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
    _fetch_token(flow, code)
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
# 逻辑：复用 processing 创建持久批次，返回旧邮箱表示与新增 run_id/queued 状态。
# 约束：拒绝其他员工邮箱和未授权邮箱。
def request_mailbox_sync(owner, mailbox_id) -> dict[str, Any]:
    """Queue one authorized mailbox for the next one-shot Agent execution."""
    from .processing import request_run, run_data
    run = request_run(owner, mailbox_id)
    mailbox = mailbox_for(owner, mailbox_id)
    return {**mailbox_status(mailbox), **run_data(run)}


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
# 逻辑：复用持久批次原子领取，保留旧响应字段用于 CLI 迁移。
# 约束：只返回 owner 自己的已授权邮箱；精确逐封进度请使用 crm_worker。
def claim_mailbox_syncs(owner, limit: int) -> list[dict[str, Any]]:
    """Claim up to limit employee mailbox requests for one Agent process."""
    from .processing import claim_run
    claimed = []
    for _index in range(limit):
        run = claim_run(owner)
        if run is None:
            break
        claimed.append({"mailbox_id": str(run.mailbox_id), "mailbox_address": run.mailbox.address,
                        "authorization": run.mailbox.gmail_credential.credentials, "max_results": 20})
    return claimed


# 功能：保存 Agent 对员工邮箱同步的最终回报。
# 输入：`owner` 为 Agent 凭证所属员工，`data` 为已校验同步报告。
# 输出：更新后的浏览器安全邮箱状态。
# 逻辑：兼容旧回报载荷，完成当前运行批次并保存安全汇总；新 Worker 直接使用租约身份回报。
# 约束：失败不自动重试，授权已移除时拒绝回报。
def report_mailbox_sync(owner, data: dict[str, Any]) -> dict[str, Any]:
    """Save the final sync state and optionally a refreshed Google credential."""
    from .models import MailboxSyncRun
    from .processing import finish_run
    mailbox = mailbox_for(owner, data["mailbox_id"])
    run = MailboxSyncRun.objects.filter(mailbox=mailbox, status="running").first()
    if run is None:
        raise InvalidState("没有可回报的运行批次。")
    finish_run(run.pk, run.lease_token, {**(data.get("sync_result") or {}), "status": data["status"]}, data.get("authorization"))
    mailbox.refresh_from_db()
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
