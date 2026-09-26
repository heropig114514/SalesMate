"""Responsibility: Page through Gmail message IDs within the user's frozen day or message-count scope.
Implementation: Use server-side time filtering and newest-first pagination; default to at most 50 emails, requiring approval for an explicit count above 50.
Relationships: Django durable_sync and the one-shot agent share this selector without depending on Django or stored mailbox credentials.
Directory:
- gmail_message_limit: Validate approval and return the allowed email limit for this run.
- scoped_message_pages: Yield pages of message IDs allowed for this run.
Variable index:
- GMAIL_MESSAGE_LIMIT: Per-batch limit of 50 emails without excess-count approval.
"""
from datetime import datetime

GMAIL_MESSAGE_LIMIT = 50


# Function: Determine the maximum email count allowed for this Gmail synchronization or retry.
# Inputs: `options`: request or frozen scope containing optional max_messages and allow_large_sync.
# Outputs: Positive integer limit; invalid counts or unapproved excess requests raise ValueError.
# Logic: Default to 50 when count is absent; approval applies only to an explicit count, never unlimited access.
# Constraints: Do not silently truncate explicitly requested excess counts; approval is stored per batch rather than as a permanent account switch.
def gmail_message_limit(options):
    limit = options.get("max_messages")
    approved = options.get("allow_large_sync", False)
    if type(approved) is not bool:
        raise ValueError("Large-sync approval must be a boolean.")
    if limit is not None and (type(limit) is not int or limit <= 0):
        raise ValueError("Sync message count must be a positive integer.")
    if approved and limit is None:
        raise ValueError("Specify an explicit message count when approving a large sync.")
    if limit is not None and limit > GMAIL_MESSAGE_LIMIT and not approved:
        raise ValueError("A sync defaults to at most 50 messages. Larger batches may occupy workers for a long time; approve the exact count before submitting.")
    return limit if limit is not None else GMAIL_MESSAGE_LIMIT


# Function: Enumerate Gmail IDs within an explicit scope without reading bodies.
# Inputs: `service`: authorized Gmail SDK; `options`: frozen days, count, and UTC window; `page_size`: caller's established page size.
# Outputs: Yield deduplicated ID pages in Gmail newest-first order; invalid scope, responses, or pagination loops raise ValueError/RuntimeError.
# Logic: Include inbox and sent mail; day-only requests still allow at most 50 emails unless approved; apply the limit before caller deduplication.
# Constraints: Use Gmail second-resolution after/before conditions; no expired-History fallback or implicit network retries; retain only seen IDs/page tokens.
def scoped_message_pages(service, options, page_size=20):
    if not isinstance(options, dict):
        raise ValueError("Gmail sync must select the most recent N days or N messages.")
    days, limit = options.get("recent_days"), options.get("max_messages")
    if not (days or limit) or any(value is not None and (type(value) is not int or value <= 0) for value in (days, limit)):
        raise ValueError("Gmail sync scope must include a positive number of days or messages.")
    limit = gmail_message_limit(options)
    until = datetime.fromisoformat(options.get("until") or "")
    since = datetime.fromisoformat(options["since"]) if options.get("since") else None
    if until.tzinfo is None or (days and since is None) or (since and (since.tzinfo is None or since >= until)):
        raise ValueError("Invalid Gmail sync time window.")
    query = f"{{in:inbox in:sent}} before:{int(until.timestamp())}"
    if since:
        query += f" after:{int(since.timestamp())}"
    token, seen_tokens, seen_ids = "", set(), set()
    while True:
        arguments = {"userId": "me", "maxResults": min(page_size, limit - len(seen_ids)), "q": query}
        if token:
            arguments["pageToken"] = token
        response = service.users().messages().list(**arguments).execute()
        if not isinstance(response, dict) or not isinstance(response.get("messages", []), list):
            raise RuntimeError("Invalid Gmail scope list response.")
        ids = [item.get("id") if isinstance(item, dict) else None for item in response.get("messages", [])]
        next_token = response.get("nextPageToken", "")
        if any(not isinstance(value, str) or not value.strip() for value in ids) or not isinstance(next_token, str):
            raise RuntimeError("Invalid Gmail scope pagination.")
        if next_token and (next_token == token or next_token in seen_tokens):
            raise RuntimeError("Gmail scope pagination loop detected.")
        page = [value for value in dict.fromkeys(ids) if value not in seen_ids]
        page = page[:limit - len(seen_ids)]
        seen_ids.update(page)
        yield page
        if not next_token or len(seen_ids) >= limit:
            return
        seen_tokens.add(next_token)
        token = next_token
