"""职责：创建员工隔离向量文档表并启用 pgvector。
实现：先创建 vector 扩展再建表，使用可变维度字段与来源唯一约束。
关联：vectors.models；服务器由管理员预先创建扩展，应用迁移不需要超级用户权限。
目录：
- Migration：向量存储初始迁移。
变量索引：
- Migration.initial：初始迁移标记。
- Migration.dependencies：用户模型依赖。
- Migration.operations：扩展及表创建操作。
"""
import django.db.models.deletion
import pgvector.django.vector
from pgvector.django import VectorExtension
from django.conf import settings
from django.db import migrations, models


# 功能：初始化向量存储结构。
# 逻辑：确保扩展存在后创建向量表。
# 约束：需预装扩展软件；反向迁移会删除向量数据。
class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        VectorExtension(),
        migrations.CreateModel(
            name="VectorDocument",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("namespace", models.CharField(max_length=100)),
                ("source", models.CharField(max_length=255)),
                ("model", models.CharField(max_length=150)),
                ("dimensions", models.PositiveIntegerField()),
                ("content", models.TextField()),
                ("content_hash", models.CharField(max_length=64)),
                ("embedding", pgvector.django.vector.VectorField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("owner", "namespace", "source", "model"),
                        name="vector_owner_source_model_unique",
                    )
                ],
            },
        ),
    ]
