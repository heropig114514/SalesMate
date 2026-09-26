"""Responsibility: Declare read and experiment-maintenance tool names discoverable by the workspace Agent.
Implementation: Immutable name sets; backend catalogs always determine actual arguments and authorization.
Relationships: Shared by chat.tool_reads, the Agent HTTP client, and chat workflows; independent of Django.
Directory:
- None
Variable index:
- EXPERIMENT_TOOLS: Approved synthetic-batch read tools.
- EXPERIMENT_WRITE_TOOLS: Three maintenance tools for shared synthetic records.
- WORKSPACE_TOOLS: Complete workspace allowlist for reads and experiment maintenance.
- WORKSPACE_READ_TOOLS: Fixed workspace allowlist for customer and experiment reads.
"""

EXPERIMENT_TOOLS = frozenset({"experiments.catalog", "experiments.rows", "experiments.file_read"})
WORKSPACE_READ_TOOLS = frozenset({"customers.search", "customers.context"}) | EXPERIMENT_TOOLS

EXPERIMENT_WRITE_TOOLS = frozenset({"experiments.create", "experiments.update", "experiments.delete"})
WORKSPACE_TOOLS = WORKSPACE_READ_TOOLS | EXPERIMENT_WRITE_TOOLS
