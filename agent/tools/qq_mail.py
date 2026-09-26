"""Responsibility: Discover and parse incoming/outgoing emails read-only through the fixed QQ IMAP TLS service.
Implementation: Validate the authorization code, EXAMINE folders, locate messages by UIDVALIDITY/UID, and read with BODY.PEEK[].
Relationships: qq_sync persists scan positions and reuses email_parser MIME and body boundaries.
Directory:
- QQMailError: Display-safe connection error without raw server text.
- connect: Establish an authenticated TLS connection.
- disconnect: Release the connection and log sanitized close errors.
- folders: Discover the inbox and unique sent folder.
- select_folder: Select a folder read-only and read UIDVALIDITY.
- message_id: Build a protocol-compatible QQ message identifier.
- split_message_id: Strictly parse a message identifier.
- list_uids: Find UIDs after a cursor with optional coarse date filtering.
- message_dates: Read message internal dates in batches without side effects.
- read_email: Read raw content by persistent message ID.
Variable index:
- IMAP_HOST: Fixed QQ host; clients cannot specify arbitrary targets.
- IMAP_PORT: TLS port 993.
- TIMEOUT: Maximum network wait of 30 seconds per operation.
- logger: Sanitized connection lifecycle logging.
"""
import base64
from datetime import datetime, timedelta, timezone
import imaplib
import logging
import re
import ssl

from .email_parser import parse_raw_email

IMAP_HOST = "imap.qq.com"
IMAP_PORT = 993
TIMEOUT = 30
logger = logging.getLogger("salesmate.qq_imap")


# Function: Describe a safe QQ connection failure.
# Logic: Carry only controlled details.
# Constraints: Exclude authorization codes and raw IMAP exceptions.
class QQMailError(RuntimeError):
    pass


# Function: Connect to QQ and validate the mailbox authorization code.
# Inputs: `address`: complete QQ/foxmail address; `authorization_code`: 16-character client authorization code.
# Outputs: Authenticated IMAP4_SSL; failure raises QQMailError.
# Logic: Fix host, certificate validation, and timeout; close the socket on login failure.
# Constraints: Reject account passwords; do not expose low-level authentication responses or retry automatically.
def connect(address, authorization_code):
    if not re.fullmatch(r"[^\s@]+@(qq|foxmail)\.com", address, re.IGNORECASE):
        raise QQMailError("Enter a complete @qq.com or @foxmail.com email address.")
    if not re.fullmatch(r"[A-Za-z]{16}", authorization_code):
        raise QQMailError("Enter the 16-character QQ Mail authorization code, not the account password.")
    client = None
    try:
        client = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=ssl.create_default_context(), timeout=TIMEOUT)
        status, _ = client.login(address, authorization_code)
        if status != "OK":
            raise QQMailError("QQ Mail login failed. Check IMAP and the authorization code.")
        return client
    except (OSError, imaplib.IMAP4.error, QQMailError) as error:
        logger.warning("qq_connect_failed error_type=%s action=check_imap_network_and_authorization", type(error).__name__)
        if client is not None:
            disconnect(client)
        raise QQMailError("Cannot connect to QQ Mail. Check that IMAP is enabled, the authorization code is valid, and imap.qq.com:993 is reachable.") from None


# Function: Release a read-only IMAP connection.
# Inputs: `client`: established connection.
# Outputs: None; log only safe details on close failure.
# Logic: Close transport after LOGOUT failure to avoid resource leaks.
# Constraints: Cleanup failure does not change completed business results or hide a propagating business exception.
def disconnect(client):
    try:
        client.logout()
    except (OSError, imaplib.IMAP4.error) as error:
        logger.warning("qq_logout_failed error_type=%s", type(error).__name__)
        try:
            client.shutdown()
        except OSError as close_error:
            logger.warning("qq_socket_close_failed error_type=%s", type(close_error).__name__)


# Function: Discover the two folders for this read-only synchronization.
# Inputs: `client`: authenticated connection.
# Outputs: IMAP wire names for INBOX and the sent folder.
# Logic: Parse LIST, preferring the server's Sent special-use flag; otherwise match known QQ sent-folder names.
# Constraints: Fail explicitly on unknown formats or ambiguous/missing sent folders; do not narrow the scope to inbox only.
def folders(client):
    status, rows = client.list()
    if status != "OK":
        raise QQMailError("Failed to read QQ Mail folders.")
    marked, named = [], []
    for row in rows or []:
        if not isinstance(row, bytes):
            raise QQMailError("Unsupported QQ Mail folder-list format.")
        match = re.fullmatch(rb'\(([^)]*)\) (?:"(?:[^"\\]|\\.)*"|NIL) (.+)', row)
        if not match:
            raise QQMailError("Unsupported QQ Mail folder-list format.")
        flags, name = match.groups()
        if b"\\noselect" in flags.lower().split():
            continue
        name = name.decode("ascii")
        if name.startswith('"') and name.endswith('"'):
            name = re.sub(r'\\(.)', r'\1', name[1:-1])
        if b"\\sent" in flags.lower().split():
            marked.append(name)
        if name.casefold() in {"sent", "sent messages", "&xfjt0zab-"}:
            named.append(name)
    candidates = marked if marked else named
    if len(set(candidates)) != 1:
        raise QQMailError("Cannot uniquely identify the QQ Mail Sent folder. Check IMAP folder settings.")
    return ["INBOX", candidates[0]]


# Function: Select one folder read-only.
# Inputs: `client`: connection; `folder`: wire name returned by LIST.
# Outputs: Positive integer UIDVALIDITY.
# Logic: EXAMINE and verify the stable identity generation.
# Constraints: Do not modify read flags; synchronization cannot continue without a generation.
def select_folder(client, folder):
    quoted = '"' + folder.replace('\\', '\\\\').replace('"', '\\"') + '"'
    status, _ = client.select(quoted, readonly=True)
    _, values = client.response("UIDVALIDITY")
    if status != "OK" or not values or not isinstance(values[0], bytes) or not values[0].isdigit() or int(values[0]) <= 0:
        raise QQMailError("QQ Mail folder cannot be opened read-only or lacks UIDVALIDITY.")
    return int(values[0])


# Function: Build a message ID without cross-folder collisions.
# Inputs: `folder`: wire name; `validity`: generation; `uid`: positive integer message UID.
# Outputs: ASCII string prefixed with qq.
# Logic: Combine the Base64URL-encoded folder with generation and UID.
# Constraints: Respect the existing 200-character protocol limit; do not impersonate Gmail server IDs.
def message_id(folder, validity, uid):
    encoded = base64.urlsafe_b64encode(folder.encode("ascii")).decode("ascii").rstrip("=")
    value = f"qq:{encoded}:{validity}:{uid}"
    if len(value) > 200:
        raise QQMailError("QQ Mail folder name exceeds the message ID length limit.")
    return value


# Function: Parse a persistent QQ message identifier.
# Inputs: `value`: protocol message ID.
# Outputs: Folder, UIDVALIDITY, and UID tuple.
# Logic: Strictly validate prefix, numbers, and canonical encoding to prevent command injection.
# Constraints: Reject invalid identifiers immediately rather than interpreting them as another mailbox provider.
def split_message_id(value):
    match = re.fullmatch(r"qq:([A-Za-z0-9_-]+):([1-9][0-9]*):([1-9][0-9]*)", value)
    if not match:
        raise QQMailError("Invalid QQ Mail message ID.")
    encoded, validity, uid = match.groups()
    try:
        folder = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True).decode("ascii")
    except (ValueError, UnicodeError):
        raise QQMailError("Invalid QQ Mail message ID encoding.") from None
    if any(ord(char) < 32 for char in folder) or message_id(folder, int(validity), int(uid)) != value:
        raise QQMailError("Invalid QQ Mail message ID encoding.")
    return folder, int(validity), int(uid)


# Function: Discover new message UIDs in the current folder.
# Inputs: `client`: connection with a selected folder; `after`: UID lower bound; `since`: optional timezone-aware lower time bound.
# Outputs: Sorted, deduplicated list of new UIDs.
# Logic: UID SEARCH may include SINCE; move the date back one day for server timezone boundaries, leaving precise filtering to the caller.
# Constraints: Return an empty list for an empty mailbox; do not treat error responses as successful synchronization; do not read bodies.
def list_uids(client, after, since=None):
    criteria = ["UID", f"{after + 1}:*"]
    if since:
        day = since.date() - timedelta(days=1) if since.date() > datetime.min.date() else since.date()
        month = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()[day.month - 1]
        criteria.extend(["SINCE", f"{day.day:02d}-{month}-{day.year:04d}"])
    status, rows = client.uid("search", None, *criteria)
    if status != "OK" or not rows or not isinstance(rows[0], bytes):
        raise QQMailError("Failed to read QQ Mail UID list.")
    items = rows[0].split()
    if any(not item.isdigit() or int(item) <= 0 for item in items):
        raise QQMailError("Invalid QQ Mail UID-list format.")
    return sorted({int(item) for item in items if int(item) > after})


# Function: Read internal dates for one page of UIDs without reading bodies.
# Inputs: `client`: connection with the target folder selected; `uids`: positive integer UID array.
# Outputs: Mapping from UID to UTC-aware datetime.
# Logic: UID FETCH requests only UID and INTERNALDATE; strictly verify the response set and duplicates.
# Constraints: Fail explicitly on missing entries caused by moved or deleted emails; do not change read flags or invoke a model.
def message_dates(client, uids):
    if not uids:
        return {}
    if any(type(uid) is not int or uid <= 0 for uid in uids):
        raise QQMailError("Invalid QQ Mail date-query UID.")
    status, rows = client.uid("fetch", ",".join(map(str, uids)), "(UID INTERNALDATE)")
    dates = {}
    if status != "OK":
        raise QQMailError("QQ Mail date query failed.")
    for row in rows or []:
        if not isinstance(row, bytes):
            raise QQMailError("Invalid QQ Mail date response.")
        uid_match = re.search(rb"\bUID (\d+)\b", row)
        date_match = re.search(rb'INTERNALDATE "([^"]+)"', row)
        if not uid_match or not date_match or int(uid_match[1]) in dates:
            raise QQMailError("QQ Mail date response is missing a unique UID or date.")
        try:
            dates[int(uid_match[1])] = datetime.strptime(date_match[1].decode("ascii"), "%d-%b-%Y %H:%M:%S %z").astimezone(timezone.utc)
        except (ValueError, UnicodeError):
            raise QQMailError("Invalid QQ Mail internal date.") from None
    if set(dates) != set(uids):
        raise QQMailError("QQ Mail date-query result is incomplete; the message may have moved or been deleted.")
    return dates


# Function: Read and normalize raw content of a specified QQ email without side effects.
# Inputs: `client`: authenticated connection; `value`: persistent message identifier.
# Outputs: Email dictionary required by existing L1, with thread_id set to None.
# Logic: Recheck generation, then UID FETCH BODY.PEEK[]; preserve reception time from INTERNALDATE and reuse the MIME parser.
# Constraints: Fail on missing messages, generation changes, or mismatched responses; do not mark read, delete, or infer thread relationships.
def read_email(client, value):
    folder, validity, uid = split_message_id(value)
    if select_folder(client, folder) != validity:
        raise QQMailError("QQ Mail folder UIDVALIDITY changed; check sync state before continuing.")
    status, rows = client.uid("fetch", str(uid), "(UID INTERNALDATE BODY.PEEK[])")
    parts = [item for item in rows or [] if isinstance(item, tuple)]
    if status != "OK" or len(parts) != 1:
        raise QQMailError("Failed to read QQ Mail message; it may have moved or been deleted.")
    meta, raw = parts[0]
    uid_match = re.search(rb"\bUID (\d+)\b", meta)
    date_match = re.search(rb'INTERNALDATE "([^"]+)"', meta)
    if not uid_match or int(uid_match[1]) != uid or not date_match or not isinstance(raw, bytes):
        raise QQMailError("QQ Mail message response is missing UID or received time.")
    try:
        received = datetime.strptime(date_match[1].decode("ascii"), "%d-%b-%Y %H:%M:%S %z").astimezone(timezone.utc).isoformat()
    except (ValueError, UnicodeError):
        raise QQMailError("Invalid QQ Mail received time.") from None
    return parse_raw_email(base64.urlsafe_b64encode(raw).decode("ascii"), message_id=value, thread_id=None, received_at=received)
