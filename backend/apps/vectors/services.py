"""职责：提供显式输入向量的员工隔离存储和余弦检索。
实现：验证有限非零向量，限定员工、空间、模型和维度后执行精确相似度查询。
关联：VectorDocument/pgvector；调用方负责模型选择与嵌入生成，不自动读取历史邮件。
目录：
- validate_vector：验证并标准化输入向量。
- put_document：写入同来源同模型的文档向量。
- search_documents：在明确的员工和模型范围内执行近邻检索。
变量索引：
- logger：文档写入的元数据日志，不记录文本和向量。
"""
import hashlib
import logging
import math
from django.contrib.auth import get_user_model
from django.db import transaction
from pgvector.django import CosineDistance
from .models import VectorDocument

logger = logging.getLogger("salesmate.vectors")


# 功能：验证向量可以参与余弦距离计算。
# 输入：`vector` 为数值序列。
# 输出：float 列表；空、非有限、超出 float32 或零向量抛 ValueError。
# 逻辑：统一转换并检查 pgvector 单精度范围和最大维度。
# 约束：不归一化或更改嵌入模型输出的维度。
def validate_vector(vector):
    values = [float(value) for value in vector]
    if not 1 <= len(values) <= 16000 or not all(math.isfinite(value) and abs(value) <= 3.4028235e38 for value in values) or not any(values):
        raise ValueError("Expected a finite nonzero vector with 1..16000 dimensions")
    return values


# 功能：写入显式提供的文本及向量。
# 输入：`owner` 员工、`namespace` 空间、`source` 来源、`model` 模型版本、`content` 文本、`embedding` 向量。
# 输出：保存后的 VectorDocument；停用员工或维度不一致明确失败。
# 逻辑：锁定有效员工序列化写入，校验同模型维度一致，再按唯一键更新内容及摘要。
# 约束：不调用外部模型、不自动向量化；调用方必须确保向量对应文本。
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


# 功能：在当前员工的指定向量空间搜索文档。
# 输入：`owner` 员工、`namespace` 空间、`model` 模型版本、`embedding` 查询向量、`limit` 返回上限。
# 输出：含 source/content/content_hash/distance 的字典列表，按余弦距离升序。
# 逻辑：验证员工有效和向量，先限定归属及维度，再计算距离；主键打破相同距离平局。
# 约束：limit 为 1..100；精确检索无 ANN 索引，不混用模型，不调用外部服务。
def search_documents(*, owner, namespace, model, embedding, limit=10):
    values = validate_vector(embedding)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer in 1..100")
    get_user_model().objects.get(pk=owner.pk, is_active=True)
    return list(VectorDocument.objects.filter(owner=owner, namespace=namespace, model=model, dimensions=len(values))
                .annotate(distance=CosineDistance("embedding", values)).order_by("distance", "pk")
                .values("source", "content", "content_hash", "distance")[:limit])
