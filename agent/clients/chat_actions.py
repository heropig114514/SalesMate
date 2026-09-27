"""Agent-only contract for employee-confirmed order changes and outgoing email.

The backend must publish these capabilities before they are usable. Preparation
stores a proposal only; no tool in this module approves or executes it. Existing
customer/experiment reads remain available. Experiment writes use the backend's
checkpointed browser-approval flow; they cannot execute directly.
"""

from datetime import datetime
from decimal import Decimal
import re
import uuid

from integrations.salesmate_tools.read_contract import WORKSPACE_READ_TOOLS, EXPERIMENT_WRITE_TOOLS

PREPARE_ORDER = "chat_actions.prepare_order_update"
PREPARE_EMAIL = "chat_actions.prepare_email"
GET_ACTION = "chat_actions.get"
PREPARE_TOOLS = frozenset({PREPARE_ORDER, PREPARE_EMAIL})
ACTION_TOOLS = PREPARE_TOOLS | {GET_ACTION}
BUSINESS_READ_TOOLS = frozenset({"orders.list", "orders.get", "connections.list", "connections.get"})
WORKSPACE_TOOLS = WORKSPACE_READ_TOOLS | BUSINESS_READ_TOOLS | ACTION_TOOLS | EXPERIMENT_WRITE_TOOLS
CONFIRMATION_CONTRACT = "chat-actions-v1"
ORDER_FIELDS = frozenset({"number", "currency", "notes"})
LINE_FIELDS = frozenset({"description", "quantity", "unit_price", "discount"})


def _object(value, allowed, required):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ValueError("Confirmation arguments do not match the contract.")


def _uuid(value):
    if not isinstance(value, str):
        raise ValueError("An action target must be a UUID.")
    uuid.UUID(value)


def _revision(value):
    if type(value) is not int or value < 0:
        raise ValueError("A current nonnegative integer revision is required.")


def _text(value, *, blank=False):
    if not isinstance(value, str) or (not blank and not value.strip()):
        raise ValueError("Action text must be a string with the required content.")


def _addresses(values, *, required=False):
    if not isinstance(values, list) or len(values) > 50 or (required and not values):
        raise ValueError("Email recipients must be an explicit bounded array.")
    for value in values:
        if not isinstance(value, str) or not re.fullmatch(r"[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+", value):
            raise ValueError("Use individual email addresses without display names or header newlines.")
    if len(set(values)) != len(values):
        raise ValueError("Duplicate recipients are not allowed.")


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
            raise ValueError("Read the target customer's context before preparing email.")
        connection = records.get(("connections.get", args["connection_id"]))
        if not connection or connection.get("provider") != "gmail" or connection.get("archived") is not False:
            raise ValueError("Read an active employee Gmail connection before preparing email.")


def _expected_changes(args):
    changes = dict(args["changes"])
    for line in args["line_changes"]:
        changes.update({f"lines.{line['line_id']}.{key}": value for key, value in line["changes"].items()})
    return changes


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
