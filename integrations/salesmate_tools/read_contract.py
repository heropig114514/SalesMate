"""Responsibility: Classify customer and experiment results for specialized evidence.
Implementation: These name sets select result formatting only; the complete live registry determines available tools.
Relationships: Shared by chat evidence/approvals, Agent receipt formatting and synthetic-only SDK test credentials; independent of Django.
Directory:
- None
Variable index:
- EXPERIMENT_TOOLS: Approved synthetic-batch read tools.
- EXPERIMENT_WRITE_TOOLS: Three maintenance tools for shared synthetic records.
- CUSTOMER_WRITE_TOOLS: Private customer creation requiring a resumable browser approval.
"""

EXPERIMENT_TOOLS = frozenset({"experiments.catalog", "experiments.rows", "experiments.file_read"})

EXPERIMENT_WRITE_TOOLS = frozenset({"experiments.create", "experiments.update", "experiments.delete"})
CUSTOMER_WRITE_TOOLS = frozenset({"customers.create"})
