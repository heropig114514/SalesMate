"""职责：增加聊天只读查询的独立结果与证据存储。
实现：仅创建 ToolRead 表及请求外键索引，不改写已有聊天或业务记录。
关联：chat.models.ToolRead；部署新接口前须应用本迁移。
目录：
- Migration：工具读取记录的新增表迁移。
变量索引：
- Migration.dependencies：要求通用聊天请求的可空公司迁移已应用。
- Migration.operations：创建带 UUID、请求外键、工具参数、结果和证据的新表。
"""

import uuid
from typing import ClassVar

import django.db.models.deletion
from django.db import migrations, models


# 功能：保存独立工具结果，不向原上下文 HTTP 契约添加内部字段。
# 逻辑：通过 CreateModel 建立新表；请求外键采用 PROTECT，保留引用的历史依赖。
# 约束：不搬迁旧证据、不修改旧版本回答；回滚会移除新表，部署方须先评估已产生的数据。
class Migration(migrations.Migration):
    dependencies: ClassVar[list] = [
        ("chat", "0002_general_answer_request"),
    ]

    operations: ClassVar[list] = [
        migrations.CreateModel(
            name="ToolRead",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("tool", models.CharField(max_length=120)),
                ("arguments", models.JSONField()),
                ("result", models.JSONField()),
                ("evidence_items", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "request",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="tool_reads",
                        to="chat.answerrequest",
                    ),
                ),
            ],
        ),
    ]
