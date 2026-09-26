"""Responsibility: Include the complete business schema and external observations in the existing traceable graph.
Implementation: Project business foreign keys deterministically; external statements reuse or create entities while retaining original text and independent support, without modifying authoritative business tables.
Relationships: Projector.run calls this in the same transaction; the input service creates Episode records and graph_worker handles withdrawals and source changes.
Directory:
- project_schema: Add business entities, fields, and foreign-key relationships.
- project_episodes: Project current unwithdrawn external observations and evidence.
Variable index:
- logger: Log missing sources and projection size without text content.
"""
import logging
import hashlib

from .business_schema import label_for, public_fields, source_models, mail_text
from .models import Entity, Fact

logger = logging.getLogger("salesmate.graph.episode_projection")


# Function: Publish entities, fields, and explicit foreign keys from the complete business catalog.
# Inputs: `projector`: user projector operating within a consistent transaction.
# Outputs: None; add entities and structured facts with schema.* predicates.
# Logic: Exclude archived records, non-business emails, and direct records of archived companies; foreign keys connect only current nodes belonging to the same user.
# Constraints: Do not infer IDs inside JSON or cross-user relationships; project only source summaries for large text, retaining originals in source tables.
def project_schema(projector):
    for kind in source_models():
        for row in projector.rows[kind].values():
            if getattr(row, "archived", False) or (kind == "chat.knowledgeentry" and not row.active):
                continue
            if kind == "crm.company" and (kind, str(row.pk)) not in projector.nodes:
                continue
            company_id = getattr(row, "company_id", None)
            if company_id is not None and ("crm.company", str(company_id)) not in projector.nodes:
                continue
            if kind == "crm.email" and row.business_classification != "business":
                continue
            projector.node(row, label_for(row))
    for (kind, key), node in list(projector.nodes.items()):
        row = projector.rows[kind][key]
        snapshot = projector.versions[(kind, key)].snapshot
        for field in public_fields(type(row)):
            value = snapshot.get(field.attname)
            if value is None:
                continue
            dependencies = [(row, field.name)]
            if field.is_relation:
                target_kind = field.related_model._meta.label_lower
                target = projector.nodes.get((target_kind, str(value)))
                if target is not None:
                    dependencies.append((projector.rows[target_kind][str(value)], "id"))
                    projector.emit(node, f"schema.{kind}.{field.name}", dependencies, object_node=target)
            else:
                projector.emit(node, f"schema.{kind}.{field.name}", dependencies, value=value)


# Function: Publish external observations' entity links and sourced statements.
# Inputs: `projector`: projector with completed business mappings.
# Outputs: None; write Entity/Fact/Derivation/Support and mark conflicting attribute candidates needs_review.
# Logic: Reused business entities depend on their current sources; automatic email observations also require unchanged bodies and continued inbound business-email status.
# Constraints: external.* entities exist only in the graph; do not create customers, confirm orders, authorize actions, or withdraw other sources automatically; text validation is not semantic proof.
def project_episodes(projector):
    from .projection import identity
    episodes, email_sources = [], {}
    for episode in projector.rows["knowledge_graph.episode"].values():
        if episode.retracted:
            continue
        reference = episode.model_audit.get("source_email")
        if reference:
            email = projector.rows["crm.email"].get(reference["id"])
            if email is None or email.direction != "inbound" or email.business_classification != "business" or ("crm.email", str(email.pk)) not in projector.nodes:
                continue
            if hashlib.sha256(mail_text(email).encode()).hexdigest() != reference["text_sha256"]:
                continue
            email_sources[episode.pk] = email
        episodes.append(episode)
    existing = {str(node.pk): (node, projector.rows[kind][key]) for (kind, key), node in projector.nodes.items()}
    previous_external = {str(node.pk): node for node in Entity.objects.filter(owner_id=projector.owner_id, kind__startswith="external.")}
    resolved = {}
    for episode in episodes:
        source = projector.node(episode, episode.source_key)
        for item in episode.extraction["entities"]:
            reference = item["existing_id"]
            source_key = item.get("source_id")
            authoritative = projector.nodes.get((item["kind"], source_key)) if source_key else None
            if authoritative is not None:
                reference = str(authoritative.pk)
            if reference:
                if reference in existing:
                    resolved[(episode.pk, item["key"])] = existing[reference]
                elif reference in previous_external:
                    node = previous_external[reference]
                    node.active = True
                    node.save(update_fields=["active"])
                    resolved[(episode.pk, item["key"])] = (node, None)
                else:
                    logger.warning("semantic_target_unavailable owner_id=%s episode=%s action=inspect_source_or_reingest", projector.owner_id, episode.pk)
                    continue
            else:
                key = f"{episode.pk}/{item['key']}"
                node_id = identity("external-source", projector.owner_id, item["kind"], source_key) if source_key else identity("external", projector.owner_id, str(episode.pk), item["key"])
                node, _ = Entity.objects.update_or_create(pk=node_id,
                    defaults={"owner_id": projector.owner_id, "kind": "external." + item["kind"], "source_id": key, "label": item["name"], "active": True})
                resolved[(episode.pk, item["key"])] = (node, None)
            node, target_row = resolved[(episode.pk, item["key"])]
            dependencies = ([(target_row, "id")] if target_row else []) + [(episode, "extraction.entities." + item["key"])]
            if episode.pk in email_sources:
                dependencies.insert(0, (email_sources[episode.pk], "payload.subject,payload.body_text,direction,business_classification"))
            projector.emit(source, "observed_entity", dependencies, object_node=node, origin="extraction", quote=item["quote"], observed_at=episode.observed_at)
    for episode in episodes:
        for index, fact in enumerate(episode.extraction["facts"]):
            source_pair = resolved.get((episode.pk, fact["subject"]))
            target_pair = resolved.get((episode.pk, fact["object"])) if fact["object"] else None
            if source_pair is None or (fact["object"] and target_pair is None):
                continue
            dependencies = [(pair[1], "id") for pair in (source_pair, target_pair) if pair and pair[1] is not None]
            if episode.pk in email_sources:
                dependencies.append((email_sources[episode.pk], "payload.subject,payload.body_text,direction,business_classification"))
            dependencies.append((episode, f"extraction.facts[{index}]"))
            projector.emit(source_pair[0], fact["predicate"], dependencies, object_node=target_pair[0] if target_pair else None,
                           value=fact["value"], origin="extraction", quote=fact["quote"], observed_at=episode.observed_at)
    # Identical assertions from multiple sources share one fact; different values coexist and request review without arbitrarily choosing a winner.
    from django.db.models import Count
    groups = Fact.objects.filter(owner_id=projector.owner_id, origin="extraction", object__isnull=True, status="active").values("subject_id", "predicate").annotate(n=Count("id")).filter(n__gt=1)
    for group in groups:
        Fact.objects.filter(owner_id=projector.owner_id, origin="extraction", subject_id=group["subject_id"], predicate=group["predicate"], status="active").update(status="needs_review")
    logger.info("semantic_projection_completed owner_id=%s episodes=%s resolved_mentions=%s", projector.owner_id, len(episodes), len(resolved))
