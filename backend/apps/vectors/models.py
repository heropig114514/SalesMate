"""Responsibility: Persist vector documents isolated by employee and model version.
Implementation: A variable-length vector permits explicit embedding dimensions; a unique constraint prevents duplicate records for the same source and model.
Relationships: ``services`` validates and reads or writes vectors; this module neither invokes models automatically nor replaces authoritative CRM data.
Directory:
- VectorDocument: Document content, source, and vector.
- VectorDocument.Meta: Source uniqueness constraint.
Variable index:
- VectorDocument.owner: Employee that owns the document.
- VectorDocument.namespace: Knowledge namespace explicitly selected by the caller.
- VectorDocument.source: Source identifier within the namespace.
- VectorDocument.model: Embedding model and version identifier.
- VectorDocument.dimensions: Vector length, used to isolate differing dimensions.
- VectorDocument.content: Text represented by the vector.
- VectorDocument.content_hash: SHA-256 of the text, used for source-consistency checks.
- VectorDocument.embedding: Variable-length pgvector vector.
- VectorDocument.updated_at: Time of the most recent write.
- VectorDocument.Meta.constraints: Unique across employee, namespace, source, and model.
"""
from django.conf import settings
from django.db import models
from pgvector.django import VectorField


# Function: Persist standalone vector-retrieval material.
# Logic: Record an explicit model version and dimension without mixing vector spaces.
# Constraints: All application access must pass an employee through ``services``; this table grants no cross-employee read permission.
class VectorDocument(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    namespace = models.CharField(max_length=100)
    source = models.CharField(max_length=255)
    model = models.CharField(max_length=150)
    dimensions = models.PositiveIntegerField()
    content = models.TextField()
    content_hash = models.CharField(max_length=64)
    embedding = VectorField()
    updated_at = models.DateTimeField(auto_now=True)

    # Function: Constrain vector records for duplicate sources.
    # Logic: The same document may coexist under different model versions.
    # Constraints: A changed dimension requires a new model-version identifier.
    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner", "namespace", "source", "model"], name="vector_owner_source_model_unique")]
