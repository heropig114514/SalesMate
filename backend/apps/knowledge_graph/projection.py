"""职责：将 SalesMate 权威记录与已有邮件抽取映射为可追溯图谱。
实现：一致快照内重算单个 owner；保留既有业务规则，扩展全业务 schema 映射及外部观察投影。
关联：sync 管理事务及变更确认；models 保存实体、来源、事实与派生；不调用 LLM。
目录：
- canonical：生成稳定 JSON。
- identity：生成有命名空间的确定性 UUID。
- source_key：获取来源身份。
- Projector：单用户图谱映射器。
- Projector.__init__：读取授权归属的白名单来源。
- Projector.snapshot：生成最小可追溯字段快照。
- Projector.version：复用或新增来源版本。
- Projector.node：保存当前实体。
- Projector.emit：建立事实、派生和证据边。
- Projector.structured：映射客户、联系人、产品、商机及订单。
- Projector.emails：映射业务邮件及最新已完成抽取。
- Projector.run：原子替换当前视图并标记待复核候选。
变量索引：
- SOURCE_MODELS：来源模型及 owner 查询路径。
- FIELDS：各来源保留的白名单字段。
- TEXT_FIELDS：允许投影的现有 L1 事实组，不包括购买确认或成交概率。
- NAMESPACE：本应用确定性 UUID 的命名空间。
- MAPPING_VERSION：所有派生记录使用的明确规则版本，v2 增加 schema 与外部观察。
- schema_kind：初始化完整来源目录的模型标签。
- schema_model：初始化时对应的模型声明。
- schema_owner_path：初始化时对应的归属路径。
"""

import hashlib
import json
import uuid

from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Count

from apps.crm.models import Company, Contact, Email, Extraction, Mailbox
from apps.sales.models import CompanySettings, Opportunity, OrderLine, Product, SalesOrder
from .models import Derivation, Entity, Episode, Fact, SourceVersion, Support
from .business_schema import public_fields, source_models

NAMESPACE = uuid.UUID("4e377e76-155b-4b6c-a024-51d155f426ca")
MAPPING_VERSION = "salesmate-kg-v2"
SOURCE_MODELS = {
    "crm.company": (Company, "owner_id"),
    "crm.contact": (Contact, "company__owner_id"),
    "crm.mailbox": (Mailbox, "owner_id"),
    "crm.email": (Email, "mailbox__owner_id"),
    "crm.extraction": (Extraction, "email__mailbox__owner_id"),
    "sales.companysettings": (CompanySettings, "owner_id"),
    "sales.product": (Product, "owner_id"),
    "sales.opportunity": (Opportunity, "owner_id"),
    "sales.salesorder": (SalesOrder, "owner_id"),
    "sales.orderline": (OrderLine, "owner_id"),
}
FIELDS = {
    "crm.company": ("owner_id", "name", "group_key", "domains", "crm_status", "revision"),
    "crm.contact": ("company_id", "name", "email"),
    "crm.mailbox": ("owner_id", "address"),
    "crm.email": ("company_id", "mailbox_id", "contact_id", "business_classification", "review_status", "review_revision", "direction", "sent_at"),
    "crm.extraction": ("email_id", "prompt_version", "repair_generation", "status", "facts", "created_at"),
    "sales.companysettings": ("owner_id", "company_id", "archived", "revision"),
    "sales.product": ("owner_id", "sku", "name", "description", "currency", "unit_price", "stock_quantity", "archived", "revision"),
    "sales.opportunity": ("owner_id", "company_id", "title", "status", "amount", "currency", "expected_close", "product_names", "archived", "revision"),
    "sales.salesorder": ("owner_id", "company_id", "number", "currency", "status", "confirmed_at", "archived", "revision"),
    "sales.orderline": ("owner_id", "order_id", "product_id", "description", "quantity", "unit_price", "discount", "archived", "revision"),
}
TEXT_FIELDS = ("product_need", "quantity", "budget", "delivery_time", "decision_process", "concerns")
for schema_kind, (schema_model, schema_owner_path) in source_models().items():
    SOURCE_MODELS[schema_kind] = (schema_model, schema_owner_path)
    FIELDS[schema_kind] = tuple(dict.fromkeys((*FIELDS.get(schema_kind, ()), *(field.attname for field in public_fields(schema_model)))))
SOURCE_MODELS["knowledge_graph.episode"] = (Episode, "owner_id")
FIELDS["knowledge_graph.episode"] = ("source_key", "observed_at", "extraction", "model_audit", "retracted")


# 功能：获得跨运行稳定的字段内容表示。
# 输入：`value` 为可由 DjangoJSONEncoder 编码的数据。
# 输出：键排序且无冗余空白的 Unicode JSON 字符串。
# 逻辑：UUID、Decimal 和时间使用 Django 的明确字符串编码。
# 约束：不执行文本、不推断数值或日期单位。
def canonical(value):
    return json.dumps(value, cls=DjangoJSONEncoder, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# 功能：为实体、事实或派生生成稳定标识。
# 输入：`parts` 为包含用途与 owner 的身份组成部分。
# 输出：确定性 UUID。
# 逻辑：对规范 JSON 使用固定命名空间的 UUID5。
# 约束：名称相同不会导致不同源主键合并。
def identity(*parts):
    return uuid.uuid5(NAMESPACE, canonical(parts))


# 功能：获取 ORM 记录的来源身份。
# 输入：`row` 为已加载的白名单模型实例。
# 输出：模型标签和主键字符串组成的元组。
# 逻辑：主键类型统一为字符串，保留其完整值。
# 约束：不使用显示名称或邮箱域名推断身份。
def source_key(row):
    return row._meta.label_lower, str(row.pk)


# 功能：在一个所有者的一致数据库快照内构建当前图谱。
# 逻辑：来源版本变更产生新派生，旧记录保留；AND 输入和 OR 支持分别建模。
# 约束：调用方必须持有用户图谱维护锁与 REPEATABLE READ 事务。
class Projector:
    # 功能：载入单用户的映射输入和现有来源版本。
    # 输入：`owner_id` 为由事件或运维命令确定的账号 ID。
    # 输出：无；建立 rows、versions、previous、nodes、company_settings 实例状态。
    # 逻辑：每类来源按其权威归属查询；业务关系另检查端点是否属于同一用户。
    # 约束：不使用实验模式的跨账号 scope，也不读取邮箱凭证。
    def __init__(self, owner_id):
        self.owner_id = owner_id
        self.rows = {kind: {str(row.pk): row for row in model.objects.filter(**{path: owner_id}).order_by("pk")} for kind, (model, path) in SOURCE_MODELS.items()}
        self.previous = {(row.kind, row.source_id): row for row in SourceVersion.objects.filter(owner_id=owner_id, current=True)}
        self.versions = {}
        self.nodes = {}
        self.company_settings = {str(row.company_id): row for row in self.rows["sales.companysettings"].values()}

    # 功能：生成来源字段的最小版本快照。
    # 输入：`row` 为白名单源记录。
    # 输出：可写入 JSONField 的字典。
    # 逻辑：读取 FIELDS；文本/JSON 来源的完整载荷以哈希表示，邮件保留主题及正文摘要，外部观察保留校验结果及正文摘要。
    # 约束：不复制完整邮件载荷、向量或凭据；原文引句保存在观察抽取及对应派生中，哈希不是授权机制。
    def snapshot(self, row):
        data = {field: getattr(row, field) for field in FIELDS[row._meta.label_lower]}
        for field in ("payload", "raw", "submission", "content", "recent_history", "context_snapshot", "parameters", "arguments", "result", "changes"):
            if field in data:
                data[field] = {"sha256": hashlib.sha256(canonical(data[field]).encode()).hexdigest()}
        if isinstance(row, Email):
            data["subject"] = row.payload.get("subject", "")
            data["body_sha256"] = hashlib.sha256(row.payload.get("body_text", "").encode()).hexdigest()
            data["source"] = row.payload.get("source")
        if isinstance(row, Episode):
            data["text_sha256"] = hashlib.sha256(row.text.encode()).hexdigest()
        return json.loads(canonical(data))

    # 功能：为本轮输入提供精确来源版本。
    # 输入：`row` 为当前存在的源记录。
    # 输出：SourceVersion 实例。
    # 逻辑：比较白名单字段哈希；不同则停用旧版本并新增，不覆盖历史快照。
    # 约束：内容回到更早状态时仍新建版本，不伪造历史时间连续性。
    def version(self, row):
        key = source_key(row)
        snapshot = self.snapshot(row)
        fingerprint = hashlib.sha256(canonical(snapshot).encode()).hexdigest()
        previous = self.previous.get(key)
        if previous is not None and previous.fingerprint == fingerprint:
            version = previous
        else:
            if previous is not None:
                SourceVersion.objects.filter(pk=previous.pk).update(current=False)
            version = SourceVersion.objects.create(owner_id=self.owner_id, kind=key[0], source_id=key[1], fingerprint=fingerprint, snapshot=snapshot)
        self.versions[key] = version
        return version

    # 功能：建立当前有效的业务实体。
    # 输入：`row` 为源记录，`label` 为展示文本。
    # 输出：Entity 实例，同时登记到本轮 nodes。
    # 逻辑：按稳定身份更新显示名称和 active，所有历史事实继续引用同一实体。
    # 约束：调用者先验证归属、归档状态及业务端点。
    def node(self, row, label):
        key = source_key(row)
        entity, _ = Entity.objects.update_or_create(pk=identity("entity", self.owner_id, *key), defaults={"owner_id": self.owner_id, "kind": key[0], "source_id": key[1], "label": label or key[1], "active": True})
        self.nodes[key] = entity
        return entity

    # 功能：创建可追溯事实及一次独立的支持路径。
    # 输入：`subject` 为主语；`predicate` 为关系名；`dependencies` 为来源记录与字段路径对；`object_node` 为可选宾语；`value` 为属性值；`origin` 默认 structured；`quote` 和 `observed_at` 是可选原文证据及业务观察时间。
    # 输出：Fact 实例；原子保存 Derivation、inputs 及 Support。
    # 逻辑：断言内容确定事实身份；全部依赖版本与证据位置确定派生身份，独立来源可以支持同一事实。
    # 约束：不把分数解释为置信度，不把缺少产品 ID 的名称强行映射到目录。
    def emit(self, subject, predicate, dependencies, *, object_node=None, value=None, origin="structured", quote=None, observed_at=None):
        value = json.loads(canonical(value))
        fact_id = identity("fact", self.owner_id, subject.pk, predicate, object_node.pk if object_node else None, value, origin)
        fact, _ = Fact.objects.update_or_create(pk=fact_id, defaults={"owner_id": self.owner_id, "subject": subject, "predicate": predicate, "object": object_node, "value": value, "origin": origin, "status": "active"})
        versions = [self.versions[source_key(row)] for row, _ in dependencies]
        evidence = [{"source_version_id": str(version.pk), "field": path} for version, (_, path) in zip(versions, dependencies)]
        if quote is not None:
            evidence[-1]["quote"] = quote
            evidence[-1]["observed_at"] = json.loads(canonical(observed_at))
        rule = f"{MAPPING_VERSION}:{predicate}"
        derivation, created = Derivation.objects.get_or_create(pk=identity("derivation", self.owner_id, fact_id, rule, evidence), defaults={"owner_id": self.owner_id, "rule": rule, "evidence": evidence, "active": True})
        if created:
            derivation.inputs.add(*versions)
        else:
            Derivation.objects.filter(pk=derivation.pk).update(active=True)
        Support.objects.get_or_create(fact=fact, derivation=derivation)
        return fact

    # 功能：按外键和明确状态映射结构化业务事实。
    # 输入：无外部参数；读取当前 rows、versions 和 company_settings。
    # 输出：无；保存实体及客户、商机、订单、产品关系。
    # 逻辑：仅 confirmed/fulfilled 的非归档订单产生 purchased；明细独立支持；商机属性分别记录其实际金额/币种或名称字段。
    # 约束：归档公司不投影相关业务，孤立或跨 owner 外键不连接；产品名称数组保持属性，不推断产品身份。
    def structured(self):
        company_dependencies = {}
        for key, row in self.rows["crm.company"].items():
            setting = self.company_settings.get(key)
            if setting is not None and setting.archived:
                continue
            self.node(row, row.name or row.group_key)
            company_dependencies[key] = [(row, "id")]
            if setting is not None:
                company_dependencies[key].append((setting, "archived"))
        for row in self.rows["sales.product"].values():
            if not row.archived:
                product = self.node(row, row.name)
                self.emit(product, "catalog_price", [(row, "unit_price,currency,archived")], value={"amount": row.unit_price, "currency": row.currency})
        for row in self.rows["crm.contact"].values():
            company = self.nodes.get(("crm.company", str(row.company_id)))
            if company is not None:
                contact = self.node(row, row.name or row.email)
                self.emit(company, "has_contact", company_dependencies[str(row.company_id)] + [(row, "company_id")], object_node=contact)
        for row in self.rows["sales.opportunity"].values():
            company = self.nodes.get(("crm.company", str(row.company_id)))
            if row.archived or company is None:
                continue
            opportunity = self.node(row, row.title)
            deps = company_dependencies[str(row.company_id)] + [(row, "company_id,status,archived")]
            self.emit(company, "has_opportunity", deps, object_node=opportunity)
            self.emit(opportunity, "stage", deps, value=row.status)
            if row.amount is not None:
                self.emit(opportunity, "opportunity_amount", company_dependencies[str(row.company_id)] + [(row, "amount,currency,archived")], value={"amount": row.amount, "currency": row.currency})
            if row.product_names:
                self.emit(opportunity, "requested_product_names", company_dependencies[str(row.company_id)] + [(row, "product_names,archived")], value=row.product_names)
        for row in self.rows["sales.salesorder"].values():
            company = self.nodes.get(("crm.company", str(row.company_id)))
            if row.archived or company is None:
                continue
            order = self.node(row, row.number)
            deps = company_dependencies[str(row.company_id)] + [(row, "company_id,status")]
            self.emit(company, "has_order", deps, object_node=order)
            self.emit(order, "order_status", deps, value=row.status)
        for row in self.rows["sales.orderline"].values():
            order_row = self.rows["sales.salesorder"].get(str(row.order_id))
            order = self.nodes.get(("sales.salesorder", str(row.order_id)))
            if row.archived or order is None or order_row is None:
                continue
            line = self.node(row, row.description)
            deps = company_dependencies[str(order_row.company_id)] + [(order_row, "status,archived"), (row, "order_id,quantity,product_id,archived")]
            self.emit(order, "has_line", deps, object_node=line)
            self.emit(line, "quantity", deps, value=str(row.quantity))
            product = self.nodes.get(("sales.product", str(row.product_id)))
            if product is not None:
                deps = deps + [(self.rows["sales.product"][str(row.product_id)], "id,archived")]
                self.emit(line, "ordered_product", deps, object_node=product)
                if order_row.status in ("confirmed", "fulfilled"):
                    self.emit(self.nodes[("crm.company", str(order_row.company_id))], "purchased", deps, object_node=product)

    # 功能：将已有 L1 抽取作为带原文证据的报告事实投影。
    # 输入：无外部参数；读取本用户邮件、邮箱、抽取及已创建公司实体。
    # 输出：无；保存邮件实体、来源关系和 reported_* 属性。
    # 逻辑：按抽取 ID 选每封邮件最新版本，仅处理 business、completed 且证据可在原文定位的事实。
    # 约束：最新抽取失败时不回退旧结果；只把入站邮件作为客户陈述；未匹配产品保持文本，未关联商机不擅自挂接。
    def emails(self):
        latest = {}
        for extraction in self.rows["crm.extraction"].values():
            if extraction.email_id not in latest or extraction.pk > latest[extraction.email_id].pk:
                latest[extraction.email_id] = extraction
        for row in self.rows["crm.email"].values():
            company = self.nodes.get(("crm.company", str(row.company_id)))
            if company is None or row.business_classification != "business":
                continue
            email = self.node(row, row.payload.get("subject", ""))
            mailbox = self.rows["crm.mailbox"][str(row.mailbox_id)]
            company_row = self.rows["crm.company"][str(row.company_id)]
            deps = [(company_row, "id"), (mailbox, "owner_id"), (row, "company_id,business_classification,review_revision,direction")]
            setting = self.company_settings.get(str(row.company_id))
            if setting is not None:
                deps.append((setting, "archived"))
            self.emit(company, "has_email", deps, object_node=email)
            extraction = latest.get(row.pk)
            if row.direction != "inbound" or extraction is None or extraction.status != "completed":
                continue
            if not isinstance(extraction.facts, dict):
                raise ValueError("Completed extraction must contain an object")
            for field in TEXT_FIELDS:
                entries = extraction.facts.get(field, [])
                if not isinstance(entries, list):
                    raise ValueError("Extraction fact group must be a list")
                for index, item in enumerate(entries):
                    if not isinstance(item, dict) or not isinstance(item.get("value"), str) or not item["value"].strip():
                        raise ValueError("Extraction fact must have a nonempty string value")
                    quotes = item.get("evidences")
                    if not isinstance(quotes, list) or not quotes:
                        raise ValueError("Extraction fact must have source evidence")
                    for quote_index, quote in enumerate(quotes):
                        if not isinstance(quote, str) or not quote.strip():
                            raise ValueError("Evidence must be a nonempty string")
                        field_path = next((path for path in ("subject", "body_text") if quote in row.payload.get(path, "")), None)
                        if field_path is None:
                            raise ValueError("Extraction evidence cannot be located in the original message")
                        evidence_deps = deps + [(extraction, f"facts.{field}[{index}].evidences[{quote_index}]"), (row, f"payload.{field_path}")]
                        self.emit(company, f"reported_{field}", evidence_deps, value=item["value"], origin="extraction", quote=quote, observed_at=row.sent_at)

    # 功能：构建一个所有者的完整当前视图，保留可审计历史。
    # 输入：无外部参数；使用构造阶段冻结的 ORM 来源。
    # 输出：本轮有效实体、事实和来源版本数量。
    # 逻辑：更新来源版本，撤销旧支持，再重建业务、全 schema 和外部观察；多个预算/数量/交期候选标记待复核。
    # 约束：仅触及本 owner；失败由外层事务整体回滚；待复核不声称候选在语义上必然矛盾。
    def run(self):
        for rows in self.rows.values():
            for row in rows.values():
                self.version(row)
        missing = [version.pk for key, version in self.previous.items() if key not in self.versions]
        SourceVersion.objects.filter(pk__in=missing).update(current=False)
        Entity.objects.filter(owner_id=self.owner_id).update(active=False)
        Fact.objects.filter(owner_id=self.owner_id).update(status="unsupported")
        Derivation.objects.filter(owner_id=self.owner_id).update(active=False)
        self.structured()
        self.emails()
        from .episode_projection import project_schema, project_episodes
        project_schema(self)
        project_episodes(self)
        groups = Fact.objects.filter(owner_id=self.owner_id, status="active", predicate__in=["reported_budget", "reported_quantity", "reported_delivery_time"]).values("subject_id", "predicate").annotate(total=Count("id")).filter(total__gt=1)
        for group in groups:
            Fact.objects.filter(owner_id=self.owner_id, subject_id=group["subject_id"], predicate=group["predicate"], status="active").update(status="needs_review")
        return {"entities": Entity.objects.filter(owner_id=self.owner_id, active=True).count(), "facts": Fact.objects.filter(owner_id=self.owner_id).exclude(status="unsupported").count(), "source_versions": len(self.versions)}
