"""Responsibility: Provide employee-isolated storage and cosine retrieval for explicitly supplied vectors.
Implementation: Validate finite nonzero vectors and run exact similarity queries restricted by employee, namespace, model, and dimensions.
Relationships: Uses ``VectorDocument`` and pgvector; callers select models and create embeddings, and historical email is never read automatically.
Directory:
- validate_vector: Validate and normalize an input vector.
- put_document: Persist a document vector for the same source and model.
- search_documents: Run nearest-neighbor retrieval within an explicit employee and model scope.
Variable index:
- logger: Metadata logger for document writes; it never records text or vectors.
"""
import hashlib
import logging
import math
from django.contrib.auth import get_user_model
from django.db import transaction
from pgvector.django import CosineDistance
from .models import VectorDocument

logger = logging.getLogger("salesmate.vectors")


# Function: Validate that a vector can participate in cosine-distance calculation.
# Inputs: ``vector`` is a numeric sequence.
# Outputs: A list of floats; an empty, non-finite, out-of-float32-range, or zero vector raises ``ValueError``.
# Logic: Convert consistently and check pgvector single-precision bounds and maximum dimensions.
# Constraints: Do not normalize the vector or alter dimensions produced by the embedding model.
def validate_vector(vector):
    values = [float(value) for value in vector]
    if not 1 <= len(values) <= 16000 or not all(math.isfinite(value) and abs(value) <= 3.4028235e38 for value in values) or not any(values):
        raise ValueError("Expected a finite nonzero vector with 1..16000 dimensions")
    return values


# Function: Persist explicitly supplied text and a vector.
# Inputs: ``owner`` is the employee; ``namespace`` is the space; ``source`` is the source; ``model`` is the model version; ``content`` is text; ``embedding`` is the vector.
# Outputs: The persisted ``VectorDocument``; an inactive employee or inconsistent dimensions fails explicitly.
# Logic: Lock the active employee to serialize writes, validate dimensions for the model, then update content and hash by the unique key.
# Constraints: Do not call an external model or vectorize automatically; callers must ensure that the vector represents the text.
@transaction.atomic
def put_document(*, owner, namespace, source, model, content, embedding):
    values = validate_vector(embedding)
    for value, maximum in ((namespace, 100), (source, 255), (model, 150)):
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError("Invalid vector source metadata")
    get_user_model().objects.select_for_update().get(pk=owner.pk, is_active=True)
    scope = VectorDocument.objects.filter(owner=owner, namespace=namespace, model=model)
    if scope.exclude(dimensions=len(values)).exists():
        raise ValueError("Embedding dimensions changed; use an explicit new model version")
    document, _created = VectorDocument.objects.update_or_create(
        owner=owner, namespace=namespace, source=source, model=model,
        defaults={"content": content, "content_hash": hashlib.sha256(content.encode()).hexdigest(), "dimensions": len(values), "embedding": values},
    )
    logger.info("vector_document_saved owner_id=%s document_id=%s dimensions=%s", owner.pk, document.pk, len(values))
    return document


# Function: Search a selected vector namespace for the current employee.
# Inputs: ``owner`` is the employee; ``namespace`` is the space; ``model`` is the model version; ``embedding`` is the query vector; ``limit`` is the return bound.
# Outputs: A list of dictionaries containing source, content, content_hash, and distance, in ascending cosine distance.
# Logic: Validate the active employee and vector, restrict ownership and dimensions before computing distance, and break equal-distance ties by primary key.
# Constraints: ``limit`` is 1..100; exact retrieval has no ANN index, does not mix models, and calls no external service.
def search_documents(*, owner, namespace, model, embedding, limit=10):
    values = validate_vector(embedding)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer in 1..100")
    get_user_model().objects.get(pk=owner.pk, is_active=True)
    return list(VectorDocument.objects.filter(owner=owner, namespace=namespace, model=model, dimensions=len(values))
                .annotate(distance=CosineDistance("embedding", values)).order_by("distance", "pk")
                .values("source", "content", "content_hash", "distance")[:limit])
