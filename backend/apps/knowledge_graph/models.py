"""Responsibility: Store business entities, versioned sources, facts, derivation evidence, and transactional change events.
Implementation: Source records remain authoritative; multiple independent derivations may support a fact, with every derivation depending on all its input versions.
Relationships: projection creates versions and source edges; sync consumes PostgreSQL trigger events; views query by owner.
Directory:
- Entity: Stable business entity.
- Entity.Meta: Unique source-identity constraint.
- SourceVersion: Immutable snapshot of source fields.
- SourceVersion.Meta: Only one current version per source.
- Fact: Entity relationship or attribute assertion.
- Derivation: One deterministic rule application and its inputs.
- Support: Many-to-many support relationship between facts and derivations.
- Support.Meta: Prevent duplicate support edges.
- Change: Change event written within a transaction.
- ProjectionState: Graph construction state for each owner.
- Episode: External text and one validated model association result.
- Episode.Meta: Unique source-key constraint per user.
Variable index:
- Entity.id: Stable UUID derived from owner, model, and source primary key.
- Entity.owner: Business ownership; account deletion cascades through the graph.
- Entity.kind: Source model identifier.
- Entity.source_id: String representation of the source primary key.
- Entity.label: Current display label, not identity.
- Entity.active: Whether the source record participates in the current graph.
- Entity.Meta.constraints: Unique owner and source identity.
- SourceVersion.id: Immutable source-version UUID.
- SourceVersion.owner: Source ownership.
- SourceVersion.kind: Source model identifier.
- SourceVersion.source_id: Source primary key.
- SourceVersion.fingerprint: SHA-256 of canonical JSON for selected fields.
- SourceVersion.snapshot: Allowlisted field snapshot without mailbox credentials or complete email bodies.
- SourceVersion.current: Whether this version corresponds to a currently existing source record.
- SourceVersion.recorded_at: Time the graph recorded this version, not claimed business-valid time.
- SourceVersion.Meta.constraints: Conditional uniqueness constraint for current source versions.
- Fact.id: Stable UUID derived from owner and assertion content.
- Fact.owner: Fact ownership.
- Fact.subject: Subject entity.
- Fact.predicate: Rule-defined relationship or attribute type.
- Fact.object: Optional object entity.
- Fact.value: Original or structured attribute value; null for relationship facts.
- Fact.origin: structured or extraction, without implying human confirmation.
- Fact.status: active, unsupported, or needs_review.
- Derivation.id: Stable UUID derived from rule, assertion, evidence location, and input versions.
- Derivation.owner: Derivation ownership.
- Derivation.rule: Explicitly versioned mapping rule.
- Derivation.inputs: Source versions that must jointly hold for this derivation.
- Derivation.evidence: Field path, version ID, optional original quotation, and business observation time.
- Derivation.active: Whether the current inputs remain applicable.
- Derivation.created_at: Time this derivation was first recorded.
- Support.fact: Supported fact.
- Support.derivation: Independent support path.
- Support.Meta.constraints: Unique fact/derivation pair.
- Change.owner_id: Owner ID requiring update, without a foreign key to support account deletion cascades.
- Change.kind: Source model triggering the change, or explicit backfill.
- Change.source_id: Source primary key for controlled auditing only.
- Change.operation: INSERT, UPDATE, DELETE, TRUNCATE, or BACKFILL.
- Change.status: pending, completed, or failed; failed events are not implicitly retried.
- Change.error_code: Failure type without raw exceptions or business text.
- Change.created_at: Event creation time; auto-increment IDs do not determine transaction commit order.
- ProjectionState.owner: Unique state record per owner.
- ProjectionState.ready: Whether at least one build has completed.
- ProjectionState.generation: Successful build count.
- ProjectionState.synced_at: Most recent successful build time.
- Episode.id: Stable external-input UUID.
- Episode.owner: Owner of the input and extraction result.
- Episode.source_key: Caller-supplied immutable source idempotency key.
- Episode.text: Original input stored under business-data permissions.
- Episode.observed_at: Source occurrence time explicitly supplied by the caller.
- Episode.extraction: Validated entity, relationship, and attribute candidates.
- Episode.model_audit: Actual model, prompt version, and generation parameters, excluding credentials.
- Episode.created_at: System reception time.
- Episode.retracted: Explicit withdrawal flag without overwriting historical evidence.
- Episode.Meta.constraints: Unique source key per user to prevent duplicate persistence.
"""

import uuid
from django.conf import settings
from django.db import models


# Function: Represent the stable identity of a source business record.
# Logic: Labels may change without changing identity; deactivate when the source is archived.
# Constraints: Same-named records from different owners or sources are not automatically merged.
class Entity(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    kind = models.CharField(max_length=80)
    source_id = models.CharField(max_length=400)
    label = models.TextField()
    active = models.BooleanField(default=True)

    # Function: Enforce unique source identity.
    # Logic: Only one entity per owner, kind, and source_id.
    # Constraints: Do not deduplicate by label.
    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner", "kind", "source_id"], name="kg_entity_source")]


# Function: Store the exact source version used for projection.
# Logic: Create a new version when field content changes; only update current on older versions.
# Constraints: Never overwrite snapshots; after business deletion only the owner may audit history, while account deletion cascades.
class SourceVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    kind = models.CharField(max_length=80)
    source_id = models.CharField(max_length=400)
    fingerprint = models.CharField(max_length=64)
    snapshot = models.JSONField()
    current = models.BooleanField(default=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    # Function: Prevent two current versions for one source.
    # Logic: The conditional unique index applies only to current=True.
    # Constraints: Allow historical versions with identical content but different occurrence times.
    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner", "kind", "source_id"], condition=models.Q(current=True), name="kg_current_source")]


# Function: Store relationships or attributes supported by multiple sources.
# Logic: Reuse identical assertions and mark unsupported when all current support is lost.
# Constraints: needs_review indicates multiple distinct candidate values found by rules, not a model judgment of truth.
class Fact(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    subject = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="outgoing")
    predicate = models.CharField(max_length=100)
    object = models.ForeignKey(Entity, null=True, on_delete=models.CASCADE, related_name="incoming")
    value = models.JSONField(null=True)
    origin = models.CharField(max_length=24)
    status = models.CharField(max_length=24, default="active", db_index=True)


# Function: Represent one rule application jointly supported by all input versions.
# Logic: One fact may have multiple independent derivations; any valid path can continue supporting it.
# Constraints: This version performs deterministic mapping only, without model calls or changes to original extraction confidence.
class Derivation(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    rule = models.CharField(max_length=120)
    inputs = models.ManyToManyField(SourceVersion, related_name="derivations")
    evidence = models.JSONField(default=list)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)


# Function: Connect a fact to one independent support path.
# Logic: Retain historical support edges; derivation active state determines current validity.
# Constraints: Canceling one order does not delete support from other orders.
class Support(models.Model):
    fact = models.ForeignKey(Fact, on_delete=models.CASCADE, related_name="supports")
    derivation = models.ForeignKey(Derivation, on_delete=models.CASCADE, related_name="supports")

    # Function: Ensure support-edge idempotency.
    # Logic: Constrain the fact/derivation pair in the database.
    # Constraints: Preserve distinct derivations across versions.
    class Meta:
        constraints = [models.UniqueConstraint(fields=["fact", "derivation"], name="kg_fact_support")]


# Function: Persist graph maintenance requests in the same transaction as business writes.
# Logic: Workers claim pending events rather than skipping uncommitted events by maximum sequence number.
# Constraints: Retain failures explicitly; only explicit operator actions requeue them; do not store complete source rows.
class Change(models.Model):
    owner_id = models.BigIntegerField(db_index=True)
    kind = models.CharField(max_length=80)
    source_id = models.CharField(max_length=400)
    operation = models.CharField(max_length=16)
    status = models.CharField(max_length=16, default="pending", db_index=True)
    error_code = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)


# Function: Describe whether one user's graph read model has been built.
# Logic: Advance generation and timestamp only when a complete build commits successfully.
# Constraints: ready does not guarantee current data; queries must also check unfinished changes.
class ProjectionState(models.Model):
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, primary_key=True, on_delete=models.CASCADE)
    ready = models.BooleanField(default=False)
    generation = models.PositiveBigIntegerField(default=0)
    synced_at = models.DateTimeField(null=True)


# Function: Store an immutable source record containing external text and one model-processing result.
# Logic: The service validates entity references against the current graph; database capture events drive normal graph-worker projection.
# Constraints: Model results are evidenced statements, not CRM transaction updates; bodies stay out of logs and withdrawal preserves audit history.
class Episode(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    source_key = models.CharField(max_length=200)
    text = models.TextField()
    observed_at = models.DateTimeField()
    extraction = models.JSONField()
    model_audit = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    retracted = models.BooleanField(default=False)

    # Function: Accept each external source only once per user.
    # Logic: Database uniqueness and service content comparison jointly provide idempotency; changed content requires a new source key.
    # Constraints: Different users may reuse a source key without sharing data.
    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner", "source_key"], name="kg_episode_source")]
