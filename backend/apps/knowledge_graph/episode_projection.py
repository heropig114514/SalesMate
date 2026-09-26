"""职责：将完整业务 schema 和外部观察纳入现有可追溯图谱。
实现：业务外键确定性投影；外部陈述复用或建立实体，保留原文与独立支持，不修改权威业务表。
关联：Projector.run 在同一事务调用；Episode 由输入服务创建；普通 graph_worker 同步撤回与源变化。
目录：
- project_schema：补充业务实体、字段和外键关系。
- project_episodes：投影当前未撤回外部观察及证据。
变量索引：
- logger：来源缺失和投影规模日志，不含文本。
"""
import logging
import hashlib

from .business_schema import label_for, public_fields, source_models, mail_text
from .models import Entity, Fact

logger = logging.getLogger("salesmate.graph.episode_projection")


# 功能：发布完整业务目录中的实体、字段和显式外键。
# 输入：`projector` 为处于一致事务内的用户投影器。
# 输出：无；补充实体及 schema.* 谓词的结构化事实。
# 逻辑：归档记录、非业务邮件和已归档公司的直接记录不参与；外键仅连接同用户当前节点。
# 约束：不推断 JSON 内部的 ID 或跨用户关系；大文本仅投影来源摘要，原文仍在源表。
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


# 功能：发布外部观察的实体关联和带来源陈述。
# 输入：`projector` 为已完成业务映射的投影器。
# 输出：无；写 Entity/Fact/Derivation/Support；相异属性候选标记 needs_review。
# 逻辑：复用原业务实体时依赖其当前来源；自动邮件观察还依赖原邮件仍为入站业务邮件且正文未变。
# 约束：external.* 实体仅存在图谱；不自动创建客户、确认订单、授权或撤销别的来源，原文校验不等于语义证明。
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
    # 多来源的相同断言复用同一事实；不同值并存并提示复核，不擅自判断哪一条正确。
    from django.db.models import Count
    groups = Fact.objects.filter(owner_id=projector.owner_id, origin="extraction", object__isnull=True, status="active").values("subject_id", "predicate").annotate(n=Count("id")).filter(n__gt=1)
    for group in groups:
        Fact.objects.filter(owner_id=projector.owner_id, origin="extraction", subject_id=group["subject_id"], predicate=group["predicate"], status="active").update(status="needs_review")
    logger.info("semantic_projection_completed owner_id=%s episodes=%s resolved_mentions=%s", projector.owner_id, len(episodes), len(resolved))
