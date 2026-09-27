"""Responsibility: Validate frozen order/email proposals independently of model assertions.
Implementation: Closed argument/receipt checks and current-request target evidence;
generic business writes instead use resumable browser approval based on the live catalog.
Relationships: workflows.chat enforces these contracts; backend_api transports calls;
the live catalog declares generic execution modes, never Agent approval authority.
Directory:
- _object: Reject extra or missing object fields.
- _uuid: Validate a string UUID.
- _revision: Validate nonnegative integer versions.
- _text: Require string content, optionally blank.
- _addresses: Validate explicit bounded recipient arrays.
- validate_action_arguments: Validate order/email/status request content.
- require_observed_targets: Require current target and sender reads.
- _expected_changes: Flatten submitted order changes for preview comparison.
- validate_action_receipt: Check exact frozen content and authoritative state.
- action_answer: Render deterministic proposal/status text.
Variable index:
- PREPARE_ORDER: Order proposal tool name.
- PREPARE_EMAIL: Email proposal tool name.
- GET_ACTION: Proposal status tool name.
- PREPARE_TOOLS: Independent unexecuted proposal tools.
- ACTION_TOOLS: Independent proposal and status tools.
- BUSINESS_READ_TOOLS: Private mailbox and authorized order reads.
- CONFIRMATION_CONTRACT: Independent proposal protocol version.
- ORDER_FIELDS: Editable order header fields.
- LINE_FIELDS: Editable existing order line fields.
"""

from datetime import datetime
from decimal import Decimal
import re
import uuid


PREPARE_ORDER = "chat_actions.prepare_order_update"
PREPARE_EMAIL = "chat_actions.prepare_email"
GET_ACTION = "chat_actions.get"
PREPARE_TOOLS = frozenset({PREPARE_ORDER, PREPARE_EMAIL})
ACTION_TOOLS = PREPARE_TOOLS | {GET_ACTION}
BUSINESS_READ_TOOLS = frozenset({"orders.list", "orders.get", "connections.list", "connections.get"})
CONFIRMATION_CONTRACT = "chat-actions-v1"
ORDER_FIELDS = frozenset({"number", "currency", "notes"})
LINE_FIELDS = frozenset({"description", "quantity", "unit_price", "discount"})


# Function: Validate a closed dictionary.
# Inputs: `value` candidate object, `allowed` keys, and `required` keys.
# Outputs: None or ValueError.
# Logic: Check dictionary type, unknown keys and required keys.
# Constraints: Does not coerce values or mutate the input.
def _object(value, allowed, required):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ValueError("Confirmation arguments do not match the contract.")


# Function: Validate a target identifier.
# Inputs: `value` expected UUID string.
# Outputs: None or ValueError from type/UUID validation.
# Logic: Parse only strings through uuid.UUID.
# Constraints: Identifier validity grants no access.
def _uuid(value):
    if not isinstance(value, str):
        raise ValueError("An action target must be a UUID.")
    uuid.UUID(value)


# Function: Validate an observed row version.
# Inputs: `value` expected nonnegative integer.
# Outputs: None or ValueError.
# Logic: Reject booleans and negative/noninteger values.
# Constraints: Does not establish freshness; backend checks current versions.
def _revision(value):
    if type(value) is not int or value < 0:
        raise ValueError("A current nonnegative integer revision is required.")


# Function: Validate action text.
# Inputs: `value` string and keyword `blank` allowing whitespace-only content when true.
# Outputs: None or ValueError.
# Logic: Check type and required stripped content without changing original text.
# Constraints: No normalization or business write occurs.
def _text(value, *, blank=False):
    if not isinstance(value, str) or (not blank and not value.strip()):
        raise ValueError("Action text must be a string with the required content.")


# Function: Validate explicit recipient arrays.
# Inputs: `values` address list and keyword `required` requiring at least one address.
# Outputs: None or ValueError.
# Logic: Enforce the existing 50-address bound, syntax and uniqueness.
# Constraints: No address inference, display names or header newlines; no provider call.
def _addresses(values, *, required=False):
    if not isinstance(values, list) or len(values) > 50 or (required and not values):
        raise ValueError("Email recipients must be an explicit bounded array.")
    for value in values:
        if not isinstance(value, str) or not re.fullmatch(r"[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+", value):
            raise ValueError("Use individual email addresses without display names or header newlines.")
    if len(set(values)) != len(values):
        raise ValueError("Duplicate recipients are not allowed.")


# Function: Validate unexecuted proposal or status inputs.
# Inputs: `name` tool and `args` closed argument dictionary.
# Outputs: None or ValueError for an unsupported or malformed request.
# Logic: Validate UUIDs, versions, field sets, decimal strings and complete email content.
# Constraints: No approval field, target guessing or state mutation; established size limits remain unchanged.
def validate_action_arguments(name, args):
    """Validate proposal inputs without accepting approval or execution fields."""
    if name == GET_ACTION:
        _object(args, {"proposal_id"}, {"proposal_id"})
        _uuid(args["proposal_id"])
    elif name == PREPARE_ORDER:
        _object(args, {"order_id", "revision", "changes", "line_changes"},
                {"order_id", "revision", "changes", "line_changes"})
        _uuid(args["order_id"])
        _revision(args["revision"])
        _object(args["changes"], ORDER_FIELDS, ())
        for field, value in args["changes"].items():
            _text(value, blank=field == "notes")
            if field == "currency" and not re.fullmatch(r"[A-Z]{3}", value):
                raise ValueError("Currency must be a three-letter uppercase code.")
        lines = args["line_changes"]
        if not isinstance(lines, list) or len(lines) > 20 or not (lines or args["changes"]):
            raise ValueError("An order proposal needs changes and at most 20 existing lines.")
        ids = set()
        for line in lines:
            _object(line, {"line_id", "revision", "changes"}, {"line_id", "revision", "changes"})
            _uuid(line["line_id"])
            _revision(line["revision"])
            if line["line_id"] in ids:
                raise ValueError("An order line may appear only once.")
            ids.add(line["line_id"])
            _object(line["changes"], LINE_FIELDS, ())
            if not line["changes"]:
                raise ValueError("Order-line changes cannot be empty.")
            for field, value in line["changes"].items():
                _text(value, blank=field == "description")
                if field != "description" and (
                    len(value) > 40 or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value)
                    or (field == "quantity" and Decimal(value) <= 0)
                ):
                    raise ValueError("Quantity and amounts must be nonnegative decimal strings; quantity must be positive.")
    elif name == PREPARE_EMAIL:
        fields = {"company_id", "connection_id", "to", "cc", "bcc", "subject", "body_text"}
        _object(args, fields, fields)
        for field in ("company_id", "connection_id"):
            _uuid(args[field])
        for field in ("to", "cc", "bcc"):
            _addresses(args[field], required=field == "to")
        _text(args["subject"])
        _text(args["body_text"])
        if any(char in args["subject"] for char in "\r\n"):
            raise ValueError("Email subjects cannot contain header newlines.")
        if len(args["subject"]) > 998 or len(args["body_text"]) > 100_000:
            raise ValueError("Email draft exceeds the supported size.")
    else:
        raise ValueError("Unsupported confirmation tool.")


# Function: Require authoritative reads before proposing writes.
# Inputs: `name` proposal tool, validated `args`, and `records` keyed by tool and target UUID.
# Outputs: None or ValueError naming the missing/stale prerequisite.
# Logic: Compare order/line revisions or require customer context and an active Gmail snapshot.
# Constraints: Reads belong to this request; backend still checks ownership and scopes.
def require_observed_targets(name, args, records):
    """Require current-request reads of the order/version or customer/mailbox."""
    if name == PREPARE_ORDER:
        order = records.get(("orders.get", args["order_id"]))
        if not order or order.get("revision") != args["revision"]:
            raise ValueError("Read the current order and its revision before preparing changes.")
        lines = {line["id"]: line for line in order.get("lines", []) if isinstance(line, dict) and "id" in line}
        for change in args["line_changes"]:
            line = lines.get(change["line_id"])
            if not line or line.get("revision") != change["revision"]:
                raise ValueError("Read the target order line and its current revision first.")
    elif name == PREPARE_EMAIL:
        if ("customers.context", args["company_id"]) not in records:
            raise ValueError("Call customers.context with company_id before preparing email; a search result is not a context read.")
        connection = records.get(("connections.get", args["connection_id"]))
        if not connection or connection.get("provider") != "gmail" or connection.get("archived") is not False:
            raise ValueError("Call connections.get with id set to connection_id and verify an active Gmail account before preparing email. connections.list is discovery only; repeating prepare_email cannot satisfy this requirement.")


# Function: Flatten the proposed order edits.
# Inputs: `args` validated order arguments.
# Outputs: New dictionary mapping header/qualified line fields to proposed values.
# Logic: Copy headers and prefix each existing line field with its UUID.
# Constraints: No mutation of arguments and no total calculation.
def _expected_changes(args):
    changes = dict(args["changes"])
    for line in args["line_changes"]:
        changes.update({f"lines.{line['line_id']}.{key}": value for key, value in line["changes"].items()})
    return changes


# Function: Verify the frozen proposal and execution state.
# Inputs: `data` backend receipt, `name` requested tool, and `args` submitted parameters.
# Outputs: None or ValueError on any contract mismatch.
# Logic: Check identity, revisions, expiry, status/confirmation consistency and exact preview content.
# Constraints: Success is a backend status, never inferred from a model statement or preparation.
def validate_action_receipt(data, name, args):
    """Accept frozen previews and explicit server state, never inferred approval."""
    _object(data, {"id", "kind", "status", "revision", "expires_at", "confirmed_by_employee", "arguments", "preview"},
            {"id", "kind", "status", "revision", "expires_at", "confirmed_by_employee", "arguments", "preview"})
    _uuid(data["id"])
    _revision(data["revision"])
    if data["kind"] not in {"order_update", "email_send"}:
        raise ValueError("Unknown proposal kind.")
    proposal_tool = PREPARE_ORDER if data["kind"] == "order_update" else PREPARE_EMAIL
    validate_action_arguments(proposal_tool, data["arguments"])
    if name in PREPARE_TOOLS:
        if name != proposal_tool or data["arguments"] != args or data["status"] != "pending_confirmation":
            raise ValueError("Preparation must return the exact unexecuted proposal.")
    elif data["id"] != args["proposal_id"]:
        raise ValueError("Proposal receipt refers to another proposal.")
    status = data["status"]
    if status not in {"pending_confirmation", "approved", "running", "succeeded", "failed", "uncertain", "cancelled", "expired", "conflicted"}:
        raise ValueError("Unknown proposal status.")
    approved = data["confirmed_by_employee"]
    if type(approved) is not bool or (status == "pending_confirmation" and approved) or (
        status in {"approved", "running", "succeeded", "uncertain"} and not approved
    ):
        raise ValueError("Proposal status lacks matching employee confirmation.")
    _text(data["expires_at"])
    if datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00")).utcoffset() is None:
        raise ValueError("Proposal expiry must include a time zone.")
    preview = data["preview"]
    frozen = data["arguments"]
    if proposal_tool == PREPARE_ORDER:
        totals = {"currency", "total_before", "total_after"}
        _object(preview, {"company_name", "order_number", "changes"} | totals, {"company_name", "order_number", "changes"})
        _text(preview["company_name"])
        _text(preview["order_number"])
        if totals & set(preview):
            if not totals <= set(preview) or not isinstance(preview["currency"], str) or not re.fullmatch(r"[A-Z]{3}", preview["currency"]):
                raise ValueError("Order totals need their currency and both before/after values.")
            if "currency" in frozen["changes"] and preview["currency"] != frozen["changes"]["currency"]:
                raise ValueError("The proposed total must use the proposed currency.")
            for field in ("total_before", "total_after"):
                value = preview[field]
                if value is not None and (not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value)):
                    raise ValueError("Order totals must be decimal strings or unknown (null).")
        if not isinstance(preview["changes"], list):
            raise ValueError("Order preview must contain a before/after change list.")
        actual = {}
        for change in preview["changes"]:
            _object(change, {"field", "before", "after"}, {"field", "before", "after"})
            _text(change["field"])
            if change["before"] is not None and not isinstance(change["before"], (str, int, float)):
                raise ValueError("A before-value must be a scalar or null.")
            if change["field"] in actual:
                raise ValueError("Order preview contains duplicate changes.")
            actual[change["field"]] = change["after"]
        if actual != _expected_changes(frozen):
            raise ValueError("Order preview does not match the proposed changes.")
    else:
        _object(preview, {"company_name", "from_address"}, {"company_name", "from_address"})
        _text(preview["company_name"])
        _addresses([preview["from_address"]], required=True)


# Function: Render the verified proposal or status for the employee.
# Inputs: `data` validated backend proposal receipt.
# Outputs: Plain text containing status and a complete pending preview when relevant.
# Logic: Select exact state wording; show sender/recipients/body or before/after order values.
# Constraints: Provider acceptance is not recipient delivery; rendering never executes the action.
def action_answer(data):
    """Render an exact backend proposal/status without an LLM success claim."""
    status, kind = data["status"], data["kind"]
    pending = status == "pending_confirmation"
    label = "Order update" if kind == "order_update" else "Email"
    messages = {
        "pending_confirmation": f"{label} prepared for your review. Nothing has been changed or sent. Please explicitly confirm the displayed proposal before execution.",
        "approved": f"{label} confirmed by you and awaiting execution; it is not complete yet.",
        "running": f"{label} is being processed; completion has not been confirmed.",
        "succeeded": "The order was updated successfully." if kind == "order_update" else "The email provider accepted the message for sending; recipient delivery is not confirmed.",
        "failed": f"{label} failed. Review the action status before trying again.",
        "uncertain": f"The outcome of this {label.lower()} is uncertain. Do not repeat the operation until the backend reconciles its status.",
        "cancelled": f"{label} was cancelled and was not executed.",
        "expired": f"{label} proposal expired; prepare a new proposal for confirmation.",
        "conflicted": f"{label} was not executed because the record changed. Read it again and confirm a new proposal.",
    }
    lines = [messages[status], f"Proposal: {data['id']}"]
    if pending:
        preview, args = data["preview"], data["arguments"]
        lines.append(f"Customer: {preview['company_name']}")
        if kind == "order_update":
            lines.append(f"Order: {preview['order_number']}")
            for change in preview["changes"]:
                field = change["field"]
                if field.startswith("lines."):
                    _, line_id, field = field.split(".", 2)
                    field = f"Line {line_id} / {field.replace('_', ' ')}"
                lines.append(f"- {field}: {change['before']} → {change['after']}")
            if "total_after" in preview:
                before = preview["total_before"] if preview["total_before"] is not None else "unknown"
                after = preview["total_after"] if preview["total_after"] is not None else "unknown"
                old_currency = next((change["before"] for change in preview["changes"] if change["field"] == "currency"), preview["currency"])
                lines.append(f"Total: {before} {old_currency} → {after} {preview['currency']}")
        else:
            lines.extend([f"From: {preview['from_address']}", f"To: {', '.join(args['to'])}",
                          f"Cc: {', '.join(args['cc']) or '(none)'}", f"Bcc: {', '.join(args['bcc']) or '(none)'}",
                          f"Subject: {args['subject']}", "", args["body_text"]])
        lines.append(f"\nConfirmation expires at: {data['expires_at']}")
    return "\n".join(lines)
