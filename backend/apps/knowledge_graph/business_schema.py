"""Responsibility: Provide one controlled database schema for complete business-graph projection and incomplete external inputs.
Implementation: Explicitly declare business-model ownership and derive field/foreign-key contracts from Django metadata, excluding account identity, credentials, and runtime queues.
Relationships: projection loads business sources; semantic_contract validates input; migrations install capture for this catalog's fixed table set.
Directory:
- catalog: Return business types, public fields, and foreign-key targets.
- source_models: Resolve business models and owner query paths.
- public_fields: Select actual fields eligible for the graph.
- label_for: Choose a stable readable label for a source record.
- mail_text: Construct the fixed original-text representation for email semantic input.
Variable index:
- SOURCES: Business models mapped to strict owner paths; experimental cross-account permissions are not reused.
- PRIVATE_FIELDS: Runtime and permission fields excluded from external schema input and model context.
- LABEL_FIELDS: Fields used to select entity labels in priority order.
"""
from django.apps import apps

SOURCES = {
    "accounts.companyprofile": "owner_id", "accounts.salessetup": "owner_id", "accounts.setupdocument": "owner_id",
    "crm.company": "owner_id", "crm.contact": "company__owner_id", "crm.mailbox": "owner_id",
    "crm.email": "mailbox__owner_id", "crm.storedmessage": "mailbox__owner_id",
    "crm.extraction": "email__mailbox__owner_id", "crm.analysisinput": "company__owner_id",
    "crm.analysis": "snapshot__company__owner_id", "crm.score": "analysis__snapshot__company__owner_id",
    "crm.snapshotsource": "snapshot__company__owner_id", "crm.snapshotinvalidation": "snapshot__company__owner_id",
    "sales.companysettings": "owner_id", "sales.companyalias": "owner_id", "sales.contactprofile": "owner_id",
    "sales.team": "owner_id", "sales.membership": "owner_id", "sales.companygrant": "owner_id",
    "sales.product": "owner_id", "sales.ticket": "owner_id", "sales.opportunity": "owner_id",
    "sales.sellerprofile": "owner_id", "sales.quote": "owner_id", "sales.quoteline": "owner_id",
    "sales.salesorder": "owner_id", "sales.orderline": "owner_id", "sales.followup": "owner_id",
    "sales.conversation": "owner_id", "sales.message": "owner_id", "sales.draft": "owner_id",
    "sales.toolaction": "owner_id", "sales.attachment": "owner_id", "sales.auditevent": "owner_id",
    "sales.notification": "owner_id", "sales.connection": "owner_id", "sales.worldevent": "owner_id",
    "sales.worldnews": "owner_id", "sales.opportunitysignal": "owner_id", "sales.opportunitypriority": "owner_id",
    "vectors.vectordocument": "owner_id", "chat.knowledgeentry": "owner_id", "chat.answerrequest": "owner_id",
    "chat.citation": "request__owner_id", "chat.toolread": "request__owner_id",
    "agent_tools.toolcall": "owner_id", "agent_tools.toolproposal": "owner_id",
}
PRIVATE_FIELDS = {"owner", "actor", "assigned_to", "reviewed_by", "user", "credential", "encrypted_credentials",
                  "storage_key", "embedding", "error", "lease_token", "sync_state"}
LABEL_FIELDS = ("name", "company_name", "title", "title_or_label", "number", "subject", "email", "address", "source_key", "group_key", "event", "tool", "namespace")


# Function: Resolve explicitly included business sources.
# Inputs: No external parameters; read the Django application registry.
# Outputs: Dictionary mapping model labels to models and owner query paths.
# Logic: Fail directly on unregistered models to prevent silent schema omissions.
# Constraints: Do not enumerate credentials, account identities, synchronization checkpoints, or job queues.
def source_models():
    return {kind: (apps.get_model(kind), path) for kind, path in SOURCES.items()}


# Function: List modelable fields in the business schema.
# Inputs: `model`: registered Django model.
# Outputs: Actual non-primary-key fields; relationships may target only registered business types.
# Logic: Exclude permission and credential fields; represent the primary key separately as external source_id.
# Constraints: Do not fill omitted fields with model defaults; preserve nested JSON structures as supplied.
def public_fields(model):
    return [field for field in model._meta.fields if not field.primary_key and field.name not in PRIVATE_FIELDS
            and (not field.is_relation or field.related_model._meta.label_lower in SOURCES)]


# Function: Export an input contract consistent with the database.
# Inputs: No external parameters; read controlled business-model metadata.
# Outputs: Mapping from models to field types, nullability, foreign-key targets, and enum values.
# Logic: Field names come from actual declarations; all fields may be omitted from incomplete observations.
# Constraints: This is a graph-observation input schema, not a business-write or authorization interface.
def catalog():
    return {kind: {field.name: {"type": field.get_internal_type(), "nullable": field.null,
                              **({"target": field.related_model._meta.label_lower} if field.is_relation else {}),
                              **({"choices": [value for value, _ in field.flatchoices]} if field.choices else {})}
                   for field in public_fields(model)} for kind, (model, _) in source_models().items()}


# Function: Obtain a business label suitable for candidate association.
# Inputs: `row`: business source instance.
# Outputs: Nonempty label string, at most 240 characters.
# Logic: Choose declared readable fields; unlabeled records use model type and source key, while emails may use their subject.
# Constraints: Do not infer entity names from complete bodies, tokens, or arbitrary JSON.
def label_for(row):
    if row._meta.label_lower == "crm.email":
        return str(row.payload.get("subject") or row.pk)[:240]
    for field in LABEL_FIELDS:
        value = getattr(row, field, None)
        if isinstance(value, str) and value.strip():
            return value[:240]
    return f"{row._meta.label_lower}/{row.pk}"


# Function: Construct original email text for semantic graph construction.
# Inputs: `email`: authorized email object.
# Outputs: Subject, newline, and plain-text body.
# Logic: Use the same representation for input, idempotency keys, and later source-validity checks.
# Constraints: Do not read attachments, add summaries, or change original text.
def mail_text(email):
    return str(email.payload.get("subject", "")) + "\n" + str(email.payload.get("body_text", ""))
