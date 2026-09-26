"""Responsibility: Publish the business semantic graph as MCP and tool HTTP capabilities isolated by current identity.
Implementation: Dispatch fixed views; ingest and retract use their own source idempotency boundaries and do not wrap inference in an outer database transaction.
Relationships: ``registry`` and ``services`` manage authorization; ``knowledge_graph`` reuses HTTP validation and graph services.
Directory:
- graph_specs: Declare graph tools and source-idempotency contracts.
- execute_graph_tool: Dispatch to fixed graph views.
Variable index:
- None
"""
from .schemas import PAGE, UUID, object_schema


# Function: Declare discoverable graph read, ingest, and retract tools.
# Inputs: ``tool`` is the existing tool constructor.
# Outputs: Nine tool declarations, with an explicit source-idempotency scope for writes.
# Logic: Natural language and ``records`` are mutually exclusive; actual business schema validates unknown fields.
# Constraints: Does not accept owner, model address, or arbitrary business writes; model relations are observations pending verification.
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


# Function: Invoke a fixed graph handler.
# Inputs: ``actor`` is authenticated identity; ``spec`` is registry binding; ``args`` are validated parameters.
# Outputs: Same ``Response`` as the direct HTTP endpoint; exceptions retain their original status.
# Logic: Construct a minimal request and pass UUID path parameters separately; write services manage their own transactions and source idempotency.
# Constraints: Each tool requires authorization before invocation; outer calls must not open a database transaction or select another user.
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
