"""Responsibility: Declare workspace reads and browser-approved customer/experiment writes.
Implementation: Immutable name sets; backend catalogs always determine actual arguments and authorization.
Relationships: Shared by chat.tool_reads, the Agent HTTP client, and chat workflows; independent of Django.
Directory:
- None
Variable index:
- EXPERIMENT_TOOLS: Approved synthetic-batch read tools.
- EXPERIMENT_WRITE_TOOLS: Three maintenance tools for shared synthetic records.
- CUSTOMER_WRITE_TOOLS: Private customer creation requiring a resumable browser approval.
- WORKSPACE_WRITE_TOOLS: Customer creation and existing synthetic maintenance approvals.
- WORKSPACE_TOOLS: Complete workspace allowlist for reads and checkpointed writes.
- WORKSPACE_READ_TOOLS: Fixed workspace allowlist for customer and experiment reads.
"""

EXPERIMENT_TOOLS = frozenset({"experiments.catalog", "experiments.rows", "experiments.file_read"})
WORKSPACE_READ_TOOLS = frozenset({"customers.search", "customers.context"}) | EXPERIMENT_TOOLS

EXPERIMENT_WRITE_TOOLS = frozenset({"experiments.create", "experiments.update", "experiments.delete"})
CUSTOMER_WRITE_TOOLS = frozenset({"customers.create"})
WORKSPACE_WRITE_TOOLS = EXPERIMENT_WRITE_TOOLS | CUSTOMER_WRITE_TOOLS
WORKSPACE_TOOLS = WORKSPACE_READ_TOOLS | WORKSPACE_WRITE_TOOLS
