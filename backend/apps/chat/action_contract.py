"""Responsibility: Define the chat-actions-v1 backend tool boundary.
Implementation: Closed schemas publish preparation separately from employee decisions; no laboratory mode rewrites execution modes.
Relationships: action_services implements these contracts; tool_reads publishes them to the existing Agent client.
Directory:
- catalog: Construct the three independent proposal capabilities.
Variable index:
- PREPARE_ORDER: Order proposal tool name.
- PREPARE_EMAIL: Email proposal tool name.
- GET_ACTION: Proposal status tool name.
- ACTION_TOOLS: Proposal capability names.
- BUSINESS_READ_TOOLS: Existing business snapshot names used only to classify evidence, never filter the catalog.
- DECIMAL: Exact nonnegative decimal string syntax.
- ORDER_CHANGES: Permitted order header fields.
- LINE_CHANGES: Permitted existing line fields.
- EMAIL_FIELDS: Frozen plaintext message schema.
"""

from apps.agent_tools.schemas import UUID, REVISION, object_schema

PREPARE_ORDER = "chat_actions.prepare_order_update"
PREPARE_EMAIL = "chat_actions.prepare_email"
GET_ACTION = "chat_actions.get"
ACTION_TOOLS = frozenset({PREPARE_ORDER, PREPARE_EMAIL, GET_ACTION})
BUSINESS_READ_TOOLS = frozenset({"orders.list", "orders.get", "connections.list", "connections.get"})
DECIMAL = {"type": "string", "pattern": r"^[0-9]+(?:\.[0-9]+)?$", "maxLength": 40}
ORDER_CHANGES = object_schema({"number": {"type": "string", "minLength": 1},
    "currency": {"type": "string", "pattern": "^[A-Z]{3}$"}, "notes": {"type": "string"}})
LINE_CHANGES = object_schema({"description": {"type": "string"},
    "quantity": DECIMAL, "unit_price": DECIMAL, "discount": DECIMAL}) | {"minProperties": 1}
EMAIL_FIELDS = {"company_id": UUID, "connection_id": UUID,
    "to": {"type": "array", "minItems": 1, "maxItems": 50, "uniqueItems": True, "items": {"type": "string", "format": "email"}},
    "cc": {"type": "array", "maxItems": 50, "uniqueItems": True, "items": {"type": "string", "format": "email"}},
    "bcc": {"type": "array", "maxItems": 50, "uniqueItems": True, "items": {"type": "string", "format": "email"}},
    "subject": {"type": "string", "minLength": 1, "maxLength": 998, "pattern": r"^[^\r\n]+$"},
    "body_text": {"type": "string", "minLength": 1, "maxLength": 100000}}


# Function: Construct independent, immutable-in-meaning chat tool contracts.
# Inputs: None; module order, email and UUID schemas.
# Outputs: Dictionary of tool names to public descriptions and closed input schemas.
# Logic: Publish only three independent proposal operations; generic business reads come from the MCP registry.
# Constraints: Never publish approval or execution to the model or change modes in laboratory deployments.
def catalog():
    schemas = {
        PREPARE_ORDER: object_schema({"order_id": UUID, "revision": REVISION, "changes": ORDER_CHANGES,
            "line_changes": {"type": "array", "maxItems": 20, "items": object_schema(
                {"line_id": UUID, "revision": REVISION, "changes": LINE_CHANGES}, ["line_id", "revision", "changes"])}}
            , ["order_id", "revision", "changes", "line_changes"]),
        PREPARE_EMAIL: object_schema(EMAIL_FIELDS, EMAIL_FIELDS),
        GET_ACTION: object_schema({"proposal_id": UUID}, ["proposal_id"]),
    }
    descriptions = {PREPARE_ORDER: "Prepare an existing draft order update for employee confirmation; no order is changed.",
        PREPARE_EMAIL: "Prepare frozen plaintext Gmail content for employee confirmation; no business draft or send is created.",
        GET_ACTION: "Read a proposal in this employee's current conversation and its actual execution status."}

    return {name: {"name": name, "description": descriptions[name], "inputSchema": schema,
        "executionMode": "confirm" if name in {PREPARE_ORDER, PREPARE_EMAIL} else "read",
        **({"confirmationContract": "chat-actions-v1"} if name in ACTION_TOOLS else {})}
        for name, schema in schemas.items()}
