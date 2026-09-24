"""职责：在模型抽取与图谱写入之间执行显式、可审计的实体对齐。
实现：已有 ID 优先；未指定 ID 时按同类型精确名称找候选，多候选可由相同已知关系端点进一步限定。
关联：episodes 在原文校验后调用，再复核解析结果；原始模型候选和每项解析决定均保留。
目录：
- resolve_entities：对齐本轮实体并返回解释记录。
变量索引：
- 无
"""
import copy


# 功能：根据同用户图谱执行明确的实体解析阶段。
# 输入：`extraction` 为已通过原文与引用校验的模型候选；`context` 为当前实体、身份字段和观察关系。
# 输出：解析后的副本及逐实体决定；不修改模型原始结果。
# 逻辑：复用模型给出的已校验 ID；否则唯一同类型精确名称匹配；多候选时比较共同已知邻居，仍歧义则保留新实体。
# 约束：名称匹配仍是推断，不代表真实身份已证实；不是失败重试或格式修复，不合并数据库实体或不同类型。
def resolve_entities(extraction, context):
    result = copy.deepcopy(extraction)
    candidates = context["entities"]
    local = {item["key"]: item for item in result["entities"]}
    decisions = []
    for entity in result["entities"]:
        requested = entity["existing_id"]
        method = "model_selected_id" if requested else "new_observation_entity"
        matches = []
        if requested is None:
            name = entity["name"].strip().casefold()
            matches = [candidate for candidate in candidates if candidate["kind"] == entity["kind"] and name in {
                candidate["label"].strip().casefold(), *(str(value).strip().casefold() for value in candidate.get("fields", {}).values())}]
            if len(matches) > 1:
                anchors = set()
                for fact in result["facts"]:
                    neighbour = fact["object"] if fact["subject"] == entity["key"] else fact["subject"] if fact["object"] == entity["key"] else None
                    if neighbour in local and local[neighbour]["existing_id"]:
                        anchors.add(local[neighbour]["existing_id"])
                related = set()
                for fact in context["facts"]:
                    if fact["subject"] in anchors and fact["object"]:
                        related.add(fact["object"])
                    if fact["object"] in anchors:
                        related.add(fact["subject"])
                matches = [candidate for candidate in matches if candidate["id"] in related]
                method = "exact_name_and_known_neighbour" if len(matches) == 1 else "ambiguous_new_entity"
            elif len(matches) == 1:
                method = "unique_exact_name_and_type"
            if len(matches) == 1:
                entity["existing_id"] = matches[0]["id"]
        decisions.append({"key": entity["key"], "model_existing_id": requested, "resolved_existing_id": entity["existing_id"],
                          "method": method, "interpretation": "inferred_association_not_verified_identity"})
    return result, decisions
