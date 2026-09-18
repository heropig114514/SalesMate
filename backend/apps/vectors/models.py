"""职责：保存按员工和模型版本隔离的向量文档。
实现：变长 vector 允许显式选定不同嵌入维度；唯一约束防止同来源同模型重复记录。
关联：services 验证向量并读写；不自动调用模型或替代 CRM 权威数据。
目录：
- VectorDocument：文档内容、来源和向量。
- VectorDocument.Meta：来源唯一约束。
变量索引：
- VectorDocument.owner：归属员工。
- VectorDocument.namespace：调用方显式指定的知识空间。
- VectorDocument.source：空间内的来源标识。
- VectorDocument.model：嵌入模型及版本标识。
- VectorDocument.dimensions：向量长度，用于隔离不同维度。
- VectorDocument.content：与向量对应的文本。
- VectorDocument.content_hash：文本 SHA-256，供来源一致性检查。
- VectorDocument.embedding：pgvector 变长向量。
- VectorDocument.updated_at：最后写入时间。
- VectorDocument.Meta.constraints：同员工、空间、来源、模型唯一。
"""
from django.conf import settings
from django.db import models
from pgvector.django import VectorField


# 功能：保存独立向量检索资料。
# 逻辑：记录显式模型版本和维度，不混用不同向量空间。
# 约束：所有应用访问必须经 services 传入员工；本表不授予跨员工读取权限。
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

    # 功能：约束重复来源的向量记录。
    # 逻辑：同一文档允许不同模型版本并存。
    # 约束：维度变化须对应新的模型版本标识。
    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner", "namespace", "source", "model"], name="vector_owner_source_model_unique")]
