"""职责：将业务语义图谱发布为按当前身份隔离的 MCP/工具 HTTP 能力。
实现：固定视图分派；输入与撤回使用来源自身幂等边界，不在推理外包数据库事务。
关联：registry/services 管理授权，knowledge_graph 复用 HTTP 校验和图谱服务。
目录：
- graph_specs：声明图谱工具及来源幂等契约。
- execute_graph_tool：分派到固定图谱视图。
变量索引：
- 无
"""
from .schemas import PAGE, UUID, object_schema


# 功能：声明可发现的图谱读取、输入和撤回工具。
# 输入：`tool` 为现有工具构造函数。
# 输出：九个工具声明，写操作明确来源幂等范围。
# 逻辑：自然语言与 records 二选一，未知字段由实际业务 schema 校验。
# 约束：不接受 owner、模型地址或任意业务写入；模型关系是待核验观察。
def graph_specs(tool):
    record = object_schema({"key": {"type": "string"}, "schema": {"type": "string"},
                            "source_id": {"type": "string"}, "fields": {"type": "object"}},
                           ["key", "schema", "fields"])
    envelope = object_schema({"source_key": {"type": "string", "minLength": 1, "maxLength": 200},
                              "observed_at": {"type": "string", "format": "date-time"},
                              "text": {"type": "string", "minLength": 1, "maxLength": 12000},
                              "records": {"type": "array", "minItems": 1, "maxItems": 30, "items": record}},
                             ["source_key", "observed_at"])
    envelope["oneOf"] = [{"required": ["text"], "not": {"required": ["records"]}},
                         {"required": ["records"], "not": {"required": ["text"]}}]
    entries = [
        tool("graph.schema", "读取48类业务字段目录；缺失字段保持未知。", "graph", object_schema({}), operation="schema"),
        tool("graph.status", "读取当前身份图谱同步状态。", "graph", object_schema({}), operation="status"),
        tool("graph.entities", "查询当前身份的图谱实体。", "graph", object_schema({**PAGE, "kind": {"type": "string"}, "q": {"type": "string"}}), operation="entities"),
        tool("graph.facts", "查询当前身份的候选事实；active不等于人工确认。", "graph", object_schema({**PAGE, "entity": UUID, "predicate": {"type": "string"}}), operation="facts"),
        tool("graph.lineage", "分页回查本人事实的原文证据与历史支持路径。", "graph", object_schema({**PAGE, "fact_id": UUID}, ["fact_id"]), operation="lineage"),
        tool("graph.episodes", "分页读取本人外部观察来源。", "graph", object_schema(PAGE), operation="episodes"),
        tool("graph.episode", "读取本人观察原文、模型候选与同步状态。", "graph", object_schema({"episode_id": UUID}, ["episode_id"]), operation="episode"),
        tool("graph.ingest", "输入文本/邮件正文或不完整records并关联建图；同source_key同内容幂等，不修改订单。模型最长等待300秒，超时结果需按来源查询。", "graph", envelope, mode="write", operation="ingest"),
        tool("graph.retract", "撤回本人观察支持，保留历史；重复撤回同一episode_id幂等。", "graph", object_schema({"episode_id": UUID}, ["episode_id"]), mode="write", operation="retract"),
    ]
    for entry in entries:
        if entry["executionMode"] == "write":
            entry.update(idempotency_required=False, idempotency_scope="source_key" if entry["operation"] == "ingest" else "episode_id")
            entry["annotations"]["idempotentHint"] = True
    return entries


# 功能：调用固定图谱处理器。
# 输入：`actor` 为认证身份；`spec` 为注册表绑定；`args` 为已验证参数。
# 输出：与直接 HTTP 接口相同的 Response，异常保留原状态。
# 逻辑：构造最小请求，UUID 路径参数独立传入；写操作服务自行管理事务和来源幂等。
# 约束：调用前必须逐工具授权；不得在外层开启数据库事务或选择其他用户。
def execute_graph_tool(actor, spec, args):
    from .dispatch import request_context
    from apps.knowledge_graph.episode_views import SchemaView, EpisodeView, EpisodeDetailView, EpisodeRetractView
    from apps.knowledge_graph.views import GraphStatusView, EntityView, FactView, LineageView
    operation = spec["operation"]
    request = request_context(actor, query=args, data=args if operation == "ingest" else {})
    if operation == "ingest":
        return EpisodeView().post(request)
    if operation == "retract":
        return EpisodeRetractView().post(request, args["episode_id"])
    if operation == "episode":
        return EpisodeDetailView().get(request, args["episode_id"])
    if operation == "lineage":
        return LineageView().get(request, args["fact_id"])
    views = {"schema": SchemaView, "status": GraphStatusView, "entities": EntityView, "facts": FactView, "episodes": EpisodeView}
    return views[operation]().get(request)
