"""Responsibility: Map authoritative SalesMate records and existing email extractions into a traceable graph.
Implementation: Recompute one owner within a consistent snapshot, preserving business rules while extending complete schema mapping and external-observation projection.
Relationships: sync manages transactions and change acknowledgement; models persist entities, sources, facts, and derivations; no LLM calls.
Directory:
- canonical: Generate stable JSON.
- identity: Generate a namespaced deterministic UUID.
- source_key: Obtain source identity.
- Projector: Single-user graph projector.
- Projector.__init__: Read allowlisted sources within authorized ownership.
- Projector.snapshot: Generate a minimal traceable field snapshot.
- Projector.version: Reuse or create a source version.
- Projector.node: Persist a current entity.
- Projector.emit: Create fact, derivation, and evidence edges.
- Projector.structured: Map customers, contacts, products, opportunities, and orders.
- Projector.emails: Map business emails and their latest completed extractions.
- Projector.run: Atomically replace the current view and mark candidates requiring review.
Variable index:
- SOURCE_MODELS: Source models and owner query paths.
- FIELDS: Allowlisted fields retained per source.
- TEXT_FIELDS: Existing L1 fact groups eligible for projection, excluding purchase confirmation and deal probability.
- NAMESPACE: Namespace for deterministic UUIDs in this application.
- MAPPING_VERSION: Explicit rule version for all derivations; v2 adds schema and external observations.
- schema_kind: Model labels used to initialize the complete source catalog.
- schema_model: Corresponding model declaration during initialization.
- schema_owner_path: Corresponding ownership path during initialization.
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


# Function: Produce a field-content representation stable across runs.
# Inputs: `value`: data supported by DjangoJSONEncoder.
# Outputs: Unicode JSON string with sorted keys and no redundant whitespace.
# Logic: Use Django's explicit string encoding for UUIDs, Decimals, and times.
# Constraints: Do not execute text or infer numeric/date units.
def canonical(value):
    return json.dumps(value, cls=DjangoJSONEncoder, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# Function: Generate a stable identifier for an entity, fact, or derivation.
# Inputs: `parts`: identity components including purpose and owner.
# Outputs: Deterministic UUID.
# Logic: Apply UUID5 with a fixed namespace to canonical JSON.
# Constraints: Identical names do not merge different source primary keys.
def identity(*parts):
    return uuid.uuid5(NAMESPACE, canonical(parts))


# Function: Obtain an ORM record's source identity.
# Inputs: `row`: loaded allowlisted model instance.
# Outputs: Tuple of model label and primary-key string.
# Logic: Normalize primary-key type to a string while preserving its full value.
# Constraints: Do not infer identity from display names or email domains.
def source_key(row):
    return row._meta.label_lower, str(row.pk)


# Function: Build the current graph within one owner's consistent database snapshot.
# Logic: Changed source versions create new derivations while retaining old records; model AND inputs separately from OR support.
# Constraints: The caller must hold the user graph-maintenance lock and a REPEATABLE READ transaction.
class Projector:
    # Function: Load one user's projection inputs and existing source versions.
    # Inputs: `owner_id`: account ID determined by an event or operator command.
    # Outputs: None; establish rows, versions, previous, nodes, and company_settings instance state.
    # Logic: Query each source type by authoritative ownership and separately verify that business relationship endpoints belong to the same user.
    # Constraints: Do not use experimental cross-account scope or read mailbox credentials.
    def __init__(self, owner_id):
        self.owner_id = owner_id
        self.rows = {kind: {str(row.pk): row for row in model.objects.filter(**{path: owner_id}).order_by("pk")} for kind, (model, path) in SOURCE_MODELS.items()}
        self.previous = {(row.kind, row.source_id): row for row in SourceVersion.objects.filter(owner_id=owner_id, current=True)}
        self.versions = {}
        self.nodes = {}
        self.company_settings = {str(row.company_id): row for row in self.rows["sales.companysettings"].values()}

    # Function: Generate a minimal version snapshot of source fields.
    # Inputs: `row`: allowlisted source record.
    # Outputs: Dictionary suitable for JSONField persistence.
    # Logic: Read FIELDS; represent complete text/JSON payloads by hashes, retain subject/body digests for emails, and validated output/body digests for external observations.
    # Constraints: Do not copy complete email payloads, vectors, or credentials; original quotations remain in observation extractions and corresponding derivations; hashes do not authorize access.
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

    # Function: Provide the exact source version for this pass's input.
    # Inputs: `row`: currently existing source record.
    # Outputs: SourceVersion instance.
    # Logic: Compare allowlisted-field hashes; on change, deactivate the old version and create a new one without overwriting snapshots.
    # Constraints: Even when content returns to an earlier state, create a new version rather than implying continuous historical validity.
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

    # Function: Create a currently valid business entity.
    # Inputs: `row`: source record; `label`: display text.
    # Outputs: Entity instance, also registered in this pass's nodes.
    # Logic: Update label and active state by stable identity; historical facts continue referencing the same entity.
    # Constraints: Callers first validate ownership, archive state, and business endpoints.
    def node(self, row, label):
        key = source_key(row)
        entity, _ = Entity.objects.update_or_create(pk=identity("entity", self.owner_id, *key), defaults={"owner_id": self.owner_id, "kind": key[0], "source_id": key[1], "label": label or key[1], "active": True})
        self.nodes[key] = entity
        return entity

    # Function: Create a traceable fact and one independent support path.
    # Inputs: `subject`: subject entity; `predicate`: relationship name; `dependencies`: source-record/field-path pairs; `object_node`: optional object entity; `value`: attribute value; `origin`: defaults to structured; `quote` and `observed_at`: optional original evidence and business observation time.
    # Outputs: Fact instance; atomically persist Derivation, inputs, and Support.
    # Logic: Assertion content determines fact identity; all dependency versions and evidence locations determine derivation identity, allowing independent sources to support the same fact.
    # Constraints: Do not interpret scores as confidence or force product names without IDs onto catalog entries.
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

    # Function: Map structured business facts from foreign keys and explicit states.
    # Inputs: No external parameters; read current rows, versions, and company_settings.
    # Outputs: None; persist entities and customer, opportunity, order, and product relationships.
    # Logic: Only unarchived confirmed/fulfilled orders produce purchased; lines support independently, while opportunity attributes cite their actual amount/currency or name fields.
    # Constraints: Do not project business data for archived companies or connect orphaned/cross-owner foreign keys; retain product-name arrays as attributes without inferring product identity.
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

    # Function: Project existing L1 extractions as reported facts with original-text evidence.
    # Inputs: No external parameters; read the current user's emails, mailboxes, extractions, and existing company entities.
    # Outputs: None; persist email entities, source relationships, and reported_* attributes.
    # Logic: Select each email's latest extraction by ID and process only business, completed facts with evidence locatable in original text.
    # Constraints: Do not fall back to older results when the latest extraction fails; only inbound emails represent customer statements; retain unmatched products as text and do not invent opportunity links.
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

    # Function: Build one owner's complete current view while retaining auditable history.
    # Inputs: No external parameters; use ORM sources frozen during construction.
    # Outputs: Counts of active entities, facts, and source versions for this pass.
    # Logic: Update source versions, withdraw old support, and rebuild business, complete schema, and external observations; mark multiple budget/quantity/delivery candidates for review.
    # Constraints: Touch only this owner; the outer transaction rolls back all failures; review status does not claim candidates are necessarily semantically contradictory.
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
