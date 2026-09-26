"""Responsibility: Perform explicit, auditable entity alignment between model extraction and graph persistence.
Implementation: Prefer existing IDs; without an ID, find exact same-type name candidates and narrow multiple matches using shared known relationship endpoints.
Relationships: episodes calls this after original-text validation and revalidates the resolved result; retain raw model candidates and every resolution decision.
Directory:
- resolve_entities: Align this round's entities and return decision records.
Variable index:
- None
"""
import copy


# Function: Perform an explicit entity-resolution stage against the same user's graph.
# Inputs: `extraction`: model candidates passing text and reference validation; `context`: current entities, identity fields, and observed relations.
# Outputs: Resolved copy and per-entity decisions without mutating original model output.
# Logic: Reuse validated model-supplied IDs; otherwise use unique exact same-type names, compare common known neighbors for multiple matches, and retain new entities if ambiguity remains.
# Constraints: Name matching remains inference, not verified identity; this is neither failure retry nor format repair and does not merge database entities or different types.
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
