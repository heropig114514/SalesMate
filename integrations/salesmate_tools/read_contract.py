"""职责：集中声明工作空间 Agent 可以请求发现的读取及实验维护工具名称。
实现：使用不可变名称集合；实际参数和授权始终由后端目录决定。
关联：chat.tool_reads、Agent HTTP 客户端及聊天工作流共同使用；不依赖 Django。
目录：
- 无
变量索引：
- EXPERIMENT_TOOLS：经过批准的合成批次读取工具。
- EXPERIMENT_WRITE_TOOLS：共享虚构记录的三种维护工具。
- WORKSPACE_TOOLS：读取与实验维护的完整工作空间白名单。
- WORKSPACE_READ_TOOLS：客户读取与实验读取的固定工作空间白名单。
"""

EXPERIMENT_TOOLS = frozenset({"experiments.catalog", "experiments.rows", "experiments.file_read"})
WORKSPACE_READ_TOOLS = frozenset({"customers.search", "customers.context"}) | EXPERIMENT_TOOLS

EXPERIMENT_WRITE_TOOLS = frozenset({"experiments.create", "experiments.update", "experiments.delete"})
WORKSPACE_TOOLS = WORKSPACE_READ_TOOLS | EXPERIMENT_WRITE_TOOLS
