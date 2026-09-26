"""Responsibility: Provide read-only Gmail authorization, history scanning, and observable per-email reads.
Implementation: Preserve existing defaults; independent workers persist stages through callbacks while legacy CLI calls remain supported.
Relationships: Software workers use this module; Gmail tools provide raw content and backend HTTP clients persist business data.
Directory:
- GmailHistoryExpiredError: Identify an expired Gmail history cursor.
- create_service: Create a read-only Gmail client with a short-lived token.
- create_service_from_authorization: Create a Gmail client from backend authorization.
- get_profile_address: Query the authorized account address.
- get_profile_history_id: Obtain the incremental history starting point.
- resolve_mailbox_address: Resolve a known business mailbox or authorized account address.
- read_email: Read and parse one raw Gmail email.
- _normalize_mailbox_address: Normalize a complete email address.
- _internal_date_to_utc: Convert Gmail millisecond timestamps.
- list_sync_message_ids: List message IDs for synchronization scanning.
- list_history_message_ids: Enumerate new Gmail messages with pagination.
- read_messages: Read selected messages with optional per-email progress reporting.
- read_sync_emails: Read recent incoming and outgoing emails within the synchronization scope.
Variable index:
- SCOPES: Read-only Gmail authorization scope.
"""

import json
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses
from typing import Mapping

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from agent.tools.email_parser import parse_raw_email

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


# Function: Identify an expired Gmail history cursor.
# Logic: Convert History 404 responses into a dedicated exception.
# Constraints: Indicates invalid synchronization history, not invalid mailbox authorization.
class GmailHistoryExpiredError(RuntimeError):
    """The saved Gmail historyId is no longer usable for incremental reads."""


# Function: Create a read-only Gmail client with a short-lived token.
# Inputs: `access_token`: short-lived access token.
# Outputs: Return a Gmail Service.
# Logic: Validate a nonempty token and construct Credentials/SDK objects.
# Constraints: Convert SDK construction failures to safe RuntimeError without logging tokens.
def create_service(access_token: str):
    """Create a read-only Gmail Service from the frontend's short-lived access token."""
    if not isinstance(access_token, str) or not access_token.strip():
        raise RuntimeError("Gmail access token must not be empty.")
    try:
        credentials = Credentials(token=access_token.strip(), scopes=SCOPES)
        return build("gmail", "v1", credentials=credentials, cache_discovery=False)
    except Exception:
        raise RuntimeError("Cannot connect using the Gmail access token.") from None


# Function: Create a Gmail client from backend authorization.
# Inputs: `authorization`: authorization or mailbox synchronization request object.
# Outputs: Service and persistable refreshed-credential dictionary.
# Logic: Refresh only when expired and a refresh token exists, then construct the SDK.
# Constraints: Fail explicitly on refresh errors; returned credentials are for backend persistence only, never the browser.
def create_service_from_authorization(authorization: Mapping) -> tuple[object, dict]:
    """Create a Gmail Service from backend-stored authorization and return any refreshed credentials."""
    if not isinstance(authorization, Mapping) or not authorization:
        raise RuntimeError("Gmail authorization is empty.")
    try:
        credentials = Credentials.from_authorized_user_info(
            dict(authorization), SCOPES
        )
        if not credentials.valid:
            if credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
            else:
                raise RuntimeError("Gmail authorization has expired; ask the employee to authorize again.")
        service = build(
            "gmail", "v1", credentials=credentials, cache_discovery=False
        )
        return service, json.loads(credentials.to_json())
    except RefreshError:
        raise RuntimeError("Gmail authorization refresh failed; ask the employee to authorize again.") from None
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError("Cannot connect using the Gmail authorization.") from None


# Function: Query the authorized account address.
# Inputs: `service`: authorized Gmail SDK client.
# Outputs: Normalized mailbox string.
# Logic: Read the profile and validate its complete email address.
# Constraints: Raise a safe RuntimeError for network or invalid-field failures.
def get_profile_address(service) -> str:
    """Return the current authorized Gmail account's valid complete email address."""
    try:
        profile = service.users().getProfile(userId="me").execute()
    except Exception:
        raise RuntimeError("Failed to read the Gmail profile.") from None

    email_address = profile.get("emailAddress") if isinstance(profile, dict) else None
    normalized = _normalize_mailbox_address(email_address)
    if normalized is None:
        raise RuntimeError("Gmail profile did not return an email address.")
    return normalized


# Function: Obtain the incremental history starting point.
# Inputs: `service`: authorized Gmail SDK client.
# Outputs: Nonempty history ID string.
# Logic: Extract a string or integer historyId from the profile.
# Constraints: Reject booleans; report network or invalid-data failures explicitly.
def get_profile_history_id(service) -> str:
    """Read the current mailbox's Gmail historyId for the next incremental starting point."""
    try:
        profile = service.users().getProfile(userId="me").execute()
    except Exception:
        raise RuntimeError("Failed to read the Gmail profile history cursor.") from None

    history_id = profile.get("historyId") if isinstance(profile, dict) else None
    if isinstance(history_id, bool) or not isinstance(history_id, (str, int)):
        raise RuntimeError("Gmail profile did not return a valid history cursor.")
    normalized = str(history_id).strip()
    if not normalized:
        raise RuntimeError("Gmail profile did not return a valid history cursor.")
    return normalized


# Function: Resolve a known business mailbox or authorized account address.
# Inputs: `service`: authorized Gmail SDK client; `explicit_address`: optional explicit mailbox address.
# Outputs: Valid bare address.
# Logic: Validate the explicit address first and call profile only if invalid.
# Constraints: Preserve existing address selection rules; propagate profile failures.
def resolve_mailbox_address(service, explicit_address: str | None) -> str:
    """Prefer a valid explicit address; otherwise fall back only to the Gmail profile."""
    normalized = _normalize_mailbox_address(explicit_address)
    if normalized is not None:
        return normalized
    return get_profile_address(service)


# Function: Read and parse one raw Gmail email.
# Inputs: `service`: authorized Gmail SDK client; `message_id`: selected Gmail message ID.
# Outputs: Standard email dictionary.
# Logic: Read raw content and ID, normalize internal time, and delegate to the MIME parser.
# Constraints: Sanitize SDK errors as RuntimeError; no sending, deletion, or read-flag changes.
def read_email(service, message_id: str) -> dict:
    """Read the selected message with one raw request and normalize Gmail resource metadata."""
    try:
        message = service.users().messages().get(
            userId="me", id=message_id, format="raw"
        ).execute()
    except Exception:
        raise RuntimeError("Failed to read the Gmail message.") from None

    if not isinstance(message, dict):
        raise RuntimeError("Invalid Gmail message response.")
    raw = message.get("raw")
    returned_id = message.get("id")
    if (
        not isinstance(raw, str)
        or not raw.strip()
        or not isinstance(returned_id, str)
        or not returned_id.strip()
    ):
        raise RuntimeError("Invalid Gmail message response.")

    thread_id = message.get("threadId")
    if not isinstance(thread_id, str) or not thread_id.strip():
        thread_id = None

    return parse_raw_email(
        raw,
        message_id=returned_id,
        thread_id=thread_id,
        received_at=_internal_date_to_utc(message.get("internalDate")),
    )


# Function: Normalize a complete email address.
# Inputs: `value`: original value to validate.
# Outputs: Valid address or None.
# Logic: Require one address with one @, nonempty parts, and no whitespace.
# Constraints: Do not infer company ownership; no network side effects.
def _normalize_mailbox_address(value) -> str | None:
    """Normalize one basically valid complete mailbox to a bare address."""
    if not isinstance(value, str) or not value.strip():
        return None
    addresses = getaddresses([value])
    if len(addresses) != 1:
        return None
    address = addresses[0][1].strip()
    if address.count("@") != 1:
        return None
    local_part, domain = address.rsplit("@", 1)
    if not local_part or not domain or any(character.isspace() for character in address):
        return None
    return address


# Function: Convert Gmail millisecond timestamps.
# Inputs: `value`: original value to validate.
# Outputs: UTC ISO string or None.
# Logic: Check integer and ASCII-digit representations, then add milliseconds to the Unix epoch.
# Constraints: Return None for booleans, malformed values, or overflow; do not infer unknown times.
def _internal_date_to_utc(value) -> str | None:
    """Independently convert representable Gmail epoch milliseconds to UTC ISO8601."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        milliseconds = value
    elif isinstance(value, str):
        candidate = value.strip()
        digits = candidate[1:] if candidate[:1] in {"+", "-"} else candidate
        if not digits or not digits.isascii() or not digits.isdigit():
            return None
        try:
            milliseconds = int(candidate)
        except ValueError:
            return None
    else:
        return None

    try:
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        return (epoch + timedelta(milliseconds=milliseconds)).isoformat()
    except (OverflowError, ValueError):
        return None


# Function: List message IDs for synchronization scanning.
# Inputs: `service`: authorized Gmail SDK client; `limit`: explicit caller-specified count limit.
# Outputs: At most twenty string IDs.
# Logic: Use the fixed inbox/sent query and validate response structure.
# Constraints: Reject invalid counts and responses; do not read bodies.
def list_sync_message_ids(service, limit: int = 20) -> list[str]:
    """List recent incoming and outgoing Gmail message IDs without reading bodies."""
    if type(limit) is not int:
        raise RuntimeError("Gmail sync count must be an integer.")
    max_results = max(1, min(limit, 20))
    try:
        response = service.users().messages().list(
            userId="me",
            maxResults=max_results,
            q="{in:inbox in:sent}",
        ).execute()
    except Exception:
        raise RuntimeError("Failed to read the Gmail sync message list.") from None

    if not isinstance(response, dict):
        raise RuntimeError("Invalid Gmail sync message-list response.")
    items = response.get("messages", [])
    if not isinstance(items, list):
        raise RuntimeError("Invalid Gmail sync message-list response.")

    message_ids: list[str] = []
    for item in items[:max_results]:
        message_id = item.get("id") if isinstance(item, dict) else None
        if not isinstance(message_id, str) or not message_id.strip():
            raise RuntimeError("Invalid Gmail sync message-list response.")
        message_ids.append(message_id)
    return message_ids


# Function: Enumerate new Gmail messages with pagination.
# Inputs: `service`: authorized Gmail SDK client; `start_history_id`: saved Gmail history cursor.
# Outputs: New ID list and next history cursor.
# Logic: Traverse messageAdded history, filtering inbox/sent labels and deduplicating.
# Constraints: Raise the dedicated expired-cursor exception on 404; reject pagination loops and structural errors.
def list_history_message_ids(
    service, start_history_id: str
) -> tuple[list[str], str]:
    """List incoming and outgoing IDs added after historyId and return the latest cursor."""
    if not isinstance(start_history_id, str) or not start_history_id.strip():
        raise RuntimeError("Gmail history cursor must not be empty.")

    message_ids: list[str] = []
    seen_message_ids: set[str] = set()
    seen_page_tokens: set[str] = set()
    page_token: str | None = None
    latest_history_id = start_history_id.strip()
    while True:
        arguments = {
            "userId": "me",
            "startHistoryId": start_history_id.strip(),
            "historyTypes": ["messageAdded"],
            "maxResults": 100,
        }
        if page_token is not None:
            arguments["pageToken"] = page_token
        try:
            response = service.users().history().list(**arguments).execute()
        except Exception as error:
            status = getattr(getattr(error, "resp", None), "status", None)
            if status == 404:
                raise GmailHistoryExpiredError(
                    "Gmail history cursor expired; rescan recent messages."
                ) from None
            raise RuntimeError("Failed to read Gmail incremental history.") from None

        if not isinstance(response, dict):
            raise RuntimeError("Invalid Gmail incremental history response.")
        returned_history_id = response.get("historyId")
        if isinstance(returned_history_id, (str, int)) and not isinstance(
            returned_history_id, bool
        ):
            candidate = str(returned_history_id).strip()
            if candidate:
                latest_history_id = candidate

        history = response.get("history", [])
        if not isinstance(history, list):
            raise RuntimeError("Invalid Gmail incremental history response.")
        for record in history:
            additions = record.get("messagesAdded", []) if isinstance(record, dict) else []
            if not isinstance(additions, list):
                raise RuntimeError("Invalid Gmail incremental history response.")
            for addition in additions:
                message = addition.get("message") if isinstance(addition, dict) else None
                if not isinstance(message, dict):
                    raise RuntimeError("Invalid Gmail incremental history response.")
                message_id = message.get("id")
                if not isinstance(message_id, str) or not message_id.strip():
                    raise RuntimeError("Invalid Gmail incremental history response.")
                labels = message.get("labelIds")
                if isinstance(labels, list) and labels and not {
                    "INBOX",
                    "SENT",
                }.intersection(labels):
                    continue
                if message_id not in seen_message_ids:
                    seen_message_ids.add(message_id)
                    message_ids.append(message_id)

        next_page_token = response.get("nextPageToken")
        if next_page_token is None:
            break
        if (
            not isinstance(next_page_token, str)
            or not next_page_token
            or next_page_token in seen_page_tokens
        ):
            raise RuntimeError("Invalid Gmail incremental history pagination.")
        seen_page_tokens.add(next_page_token)
        page_token = next_page_token

    return message_ids, latest_history_id


# Function: Read selected messages with optional per-email progress reporting.
# Inputs: `service`: authorized Gmail SDK client; `message_ids`: selected Gmail IDs, or None for the existing scan scope; `progress`: optional stage callback.
# Outputs: Array of successfully parsed emails.
# Logic: With a callback, register all IDs first, then report individual reads and safe errors while continuing other emails.
# Constraints: Without a callback, preserve legacy exception propagation; propagate callback persistence failures rather than inventing durable progress.
def read_messages(service, message_ids: list[str], progress=None) -> list[dict]:
    """Read raw Gmail emails in the supplied order."""
    if not isinstance(message_ids, list) or any(
        not isinstance(message_id, str) or not message_id.strip()
        for message_id in message_ids
    ):
        raise RuntimeError("Invalid Gmail message ID list.")
    if progress is None:
        return [read_email(service, message_id) for message_id in message_ids]
    progress("discovered", {"message_ids": message_ids})
    emails = []
    for message_id in message_ids:
        progress("fetching", {"gmail_message_id": message_id})
        try:
            email = read_email(service, message_id)
        except Exception:
            progress("failed", {"gmail_message_id": message_id, "stage": "fetching", "code": "gmail_read_failed"})
            continue
        emails.append(email)
    return emails


# Function: Read recent incoming and outgoing emails within the synchronization scope.
# Inputs: `service`: authorized Gmail SDK client; `limit`: explicit caller-specified count limit; `progress`: optional stage callback.
# Outputs: Array of successful emails.
# Logic: Reuse ID listing and individual reads, forwarding the optional progress callback.
# Constraints: Preserve the twenty-email limit and original call contract when no callback is supplied.
def read_sync_emails(service, limit: int = 20, progress=None) -> list[dict]:
    """Read recent incoming and outgoing emails for this synchronization, up to twenty."""
    return read_messages(service, list_sync_message_ids(service, limit), progress=progress) if progress else read_messages(service, list_sync_message_ids(service, limit))
