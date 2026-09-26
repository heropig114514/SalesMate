"""Responsibility: Manage employee Gmail OAuth and one-shot synchronization requests.
Implementation: Backend exchanges Google authorization codes; mailbox status adds QQ flags and legacy Agent claim interfaces process Gmail batches only.
Relationships: views exposes browser and Agent routes, models.GmailCredential stores authorization, and Agent reads messages.
Directory:
- _client_config: Construct Google Web application client configuration.
- begin_authorization: Create an employee authorization URL and save state and PKCE verifier.
- _fetch_token: Exchange an authorization code and accept a scope superset containing Gmail read-only permission.
- finish_authorization: Exchange code, verify account, and await user selection of synchronization scope.
- mailbox_status: Generate browser mailbox status without credentials.
- request_mailbox_sync: Mark an authorized mailbox as awaiting synchronization.
- disconnect_mailbox: Remove local authorization while retaining historical business data.
- claim_mailbox_syncs: Claim synchronization requests for a one-shot Agent.
- report_mailbox_sync: Save Agent synchronization result and refreshed credential.
Variable index:
- GMAIL_SCOPES: Google Gmail read-only OAuth scope.
- SESSION_STATE_KEY: Key saving OAuth state in the current browser session.
- SESSION_CODE_VERIFIER_KEY: Key saving the PKCE code_verifier in the current browser session.
- __all__: Public service functions of this module.
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

from .access import Conflict, InvalidState, mailbox_for
from .models import GmailCredential, Mailbox, QQCredential

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
SESSION_STATE_KEY = "salesmate_gmail_oauth_state"
SESSION_CODE_VERIFIER_KEY = "salesmate_gmail_oauth_code_verifier"


# Function: Construct the Web application configuration required by google-auth-oauthlib.
# Inputs: No explicit parameters; reads Django settings.
# Outputs: Google OAuth web client dictionary.
# Logic: Require nonempty client ID and secret only when authorization begins.
# Constraints: Error messages contain no secret values.
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


# Function: Create the current employee's Google authorization URL and save state.
# Inputs: `request` is an authenticated browser request.
# Outputs: Google authorization URL string.
# Logic: Request offline access, consent, and gmail.readonly, and save this PKCE verifier.
# Constraints: Does not return client secret or Gmail token in the response.
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


# Function: Exchange an authorization code while supporting Google returning an authorized scope superset.
# Inputs: `flow` is Flow with restored PKCE verifier; `code` is a one-time authorization code.
# Outputs: None; on success Flow holds a token readable through credentials.
# Logic: oauthlib raises scope change as Warning; accept the token only when returned permissions still include Gmail read-only.
# Constraints: Continue raising when requested permission is missing or Warning has no valid token.
def _fetch_token(flow, code: str) -> None:
    try:
        flow.fetch_token(code=code)
    except Warning as error:
        token = getattr(error, "token", None)
        returned_scopes = set(getattr(error, "new_scope", None) or [])
        if not token or not set(GMAIL_SCOPES).issubset(returned_scopes):
            raise
        flow.oauth2session.token = token


# Function: Complete the Google callback and establish the current employee mailbox connection.
# Inputs: `request` contains employee session, code, and state.
# Outputs: Mailbox for the verified address.
# Logic: Restore PKCE verifier, exchange code, read Gmail profile, and save credentials; the user requests initial synchronization after selecting scope.
# Constraints: Does not establish a binding for mismatched state or PKCE verifier or invalid address, and does not overwrite an existing QQ connection.
def finish_authorization(request) -> Mailbox:
    """Exchange Google callback code and verify the account without queueing mail reads."""
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
        mailbox = mailbox_for(request.user, mailbox.pk, lock=True)
        if QQCredential.objects.filter(mailbox=mailbox).exists():
            raise Conflict("该邮箱已有 QQ 连接，不能覆盖为 Gmail 授权。")
        GmailCredential.objects.update_or_create(
            mailbox=mailbox,
            defaults={"credentials": json.loads(credentials.to_json())},
        )
        if not mailbox.sync_state or mailbox.sync_state.get("status") == "authorization_required":
            mailbox.sync_state = {"status": "connected"}
            mailbox.version += 1
            mailbox.save(update_fields=["sync_state", "version"])
    return mailbox


# Function: Generate browser-safe status for one employee mailbox.
# Inputs: `mailbox` is a Mailbox accessible to the current employee.
# Outputs: Mailbox identifier, address, authorization booleans, and sync_state.
# Logic: Display Gmail and QQ connection flags independently without changing existing Gmail field semantics.
# Constraints: Output contains no Google tokens, QQ authorization codes, or ciphertext.
def mailbox_status(mailbox: Mailbox) -> dict[str, Any]:
    """Return one browser-safe mailbox row for the owning employee."""
    return {
        "mailbox_id": str(mailbox.pk),
        "address": mailbox.address,
        "gmail_authorized": hasattr(mailbox, "gmail_credential"),
        "qq_authorized": hasattr(mailbox, "qq_credential"),
        "sync_state": mailbox.sync_state or {"status": "authorization_required"},
    }


# Function: Request one employee Gmail or QQ synchronization.
# Inputs: `owner` is the current employee, `mailbox_id` is its mailbox UUID, and `sync_options` explicitly limits this run.
# Outputs: Updated browser-safe mailbox status.
# Logic: Reuse processing to create a durable batch and return prior mailbox representation with new run_id and queued state.
# Constraints: Rejects other employees' mailboxes and unauthorized mailboxes.
def request_mailbox_sync(owner, mailbox_id, sync_options=None) -> dict[str, Any]:
    """Queue one authorized mailbox for the next one-shot Agent execution."""
    from .processing import request_run, run_data
    run = request_run(owner, mailbox_id, sync_options=sync_options)
    mailbox = mailbox_for(owner, mailbox_id)
    return {**mailbox_status(mailbox), **run_data(run)}


# Function: Remove an employee's local Gmail authorization.
# Inputs: `owner` is the current employee and `mailbox_id` is its mailbox UUID.
# Outputs: Browser status authorization_required.
# Logic: Delete the one-to-one Google credential and increment mailbox version; reject operations on QQ connections.
# Constraints: Retains historical messages, companies, and analysis records.
def disconnect_mailbox(owner, mailbox_id) -> dict[str, Any]:
    """Delete the local Gmail grant and mark the mailbox disconnected."""
    with transaction.atomic():
        mailbox = mailbox_for(owner, mailbox_id, lock=True)
        if QQCredential.objects.filter(mailbox=mailbox).exists():
            raise InvalidState("QQ 邮箱请使用 QQ 连接移除接口。")
        GmailCredential.objects.filter(mailbox=mailbox).delete()
        mailbox.sync_state = {"status": "authorization_required"}
        mailbox.version += 1
        mailbox.save(update_fields=["sync_state", "version"])
    return mailbox_status(mailbox)


# Function: Claim employee mailbox synchronization requests for a one-shot Agent.
# Inputs: `owner` is the employee belonging to the Agent credential and `limit` is this claim maximum.
# Outputs: Synchronization request array containing mailbox address, authorization, frozen scope, and explicit retry IDs.
# Logic: Reuse durable batch atomic claim and restrict it to Gmail so legacy CLI cannot claim QQ work; retain page size and forward sync_options and message_ids, which legacy clients must not ignore.
# Constraints: Returns only the owner's authorized mailboxes; use crm_worker for exact per-message progress.
def claim_mailbox_syncs(owner, limit: int) -> list[dict[str, Any]]:
    """Claim up to limit employee mailbox requests for one Agent process."""
    from .processing import claim_run
    claimed = []
    for _index in range(limit):
        run = claim_run(owner, gmail_only=True)
        if run is None:
            break
        claimed.append({"mailbox_id": str(run.mailbox_id), "mailbox_address": run.mailbox.address,
                        "authorization": run.mailbox.gmail_credential.credentials, "max_results": 20,
                        "sync_options": run.sync_options, "message_ids": run.message_ids})
    return claimed


# Function: Save an Agent's terminal report for employee mailbox synchronization.
# Inputs: `owner` is the employee belonging to the Agent credential and `data` is a validated synchronization report.
# Outputs: Updated browser-safe mailbox status.
# Logic: Supports legacy report payloads, completes the current running batch, and saves safe summary; new Worker reports directly through lease identity.
# Constraints: Failure does not retry automatically and reports are rejected after authorization removal.
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
