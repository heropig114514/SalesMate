"""职责：增加正式 L4 所需的销售方画像和商机产品字段。
实现：创建按 owner 唯一的配置表，为现有商机添加可空产品字段，不补造历史业务数据。
关联：sales.models.SellerProfile 和 Opportunity.product_names 的数据库定义。
目录：
- Migration：正式评分输入的增量迁移。
变量索引：
- Migration.dependencies：账号表与前一 sales 迁移。
- Migration.operations：新增表和产品字段的可逆操作。
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


# 功能：正式评分输入的增量迁移。
# 逻辑：由 Django 创建画像表和可空 JSON 产品字段，原有商机以 null 保持产品未知。
# 约束：不写默认行业、均值或虚构订单；回滚会移除新增配置及产品字段。
class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
        ("sales", "0005_general_conversation"),
    ]

    operations = [
        migrations.CreateModel(
            name="SellerProfile",
            fields=[
                (
                    "owner",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        serialize=False,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ("revision", models.PositiveIntegerField(default=0)),
                ("profile", models.JSONField(default=dict)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.AddField(
            model_name="opportunity",
            name="product_names",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
