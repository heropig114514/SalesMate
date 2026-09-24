"""职责：为公共新闻增加一组销售线索、精确金额及来源证据。
实现：旧文本填空字符串、金额保持 null；数据库约束金额非负并保证元数据全有或全空。
关联：WorldNews 与 WorldNewsSerializer；迁移不解析旧文章、不调用 Agent、不创建 CRM 数据。
目录：
- Migration：增加十三个可选字段及金额一致性约束。
变量索引：
- Migration.dependencies：依赖共享全球资讯迁移及可替换用户模型。
- Migration.operations：新增字段与金额组合检查，不回填推断内容。
"""

from django.conf import settings
from django.db import migrations, models


# 功能：安装新闻公共线索存储契约。
# 逻辑：原子增加字段和约束；旧记录自然满足空值组合。
# 约束：反向迁移会移除新增列；正式执行遵循既有备份发布流程。
class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0009_shared_insights"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="worldnews",
            name="amount",
            field=models.DecimalField(
                blank=True, decimal_places=6, max_digits=30, null=True
            ),
        ),
        migrations.AddField(
            model_name="worldnews",
            name="amount_evidence",
            field=models.CharField(blank=True, default="", max_length=400),
        ),
        migrations.AddField(
            model_name="worldnews",
            name="amount_scope",
            field=models.CharField(
                blank=True,
                choices=[
                    ("whole_project", "whole_project"),
                    ("equipment_procurement", "equipment_procurement"),
                    ("other", "other"),
                ],
                default="",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="worldnews",
            name="amount_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("total_investment", "total_investment"),
                    ("procurement_budget", "procurement_budget"),
                    ("tender_amount", "tender_amount"),
                    ("contract_amount", "contract_amount"),
                    ("other", "other"),
                ],
                default="",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="worldnews",
            name="company_name",
            field=models.CharField(blank=True, default="", max_length=240),
        ),
        migrations.AddField(
            model_name="worldnews",
            name="currency",
            field=models.CharField(
                blank=True,
                choices=[
                    ("CNY", "CNY"),
                    ("USD", "USD"),
                    ("EUR", "EUR"),
                    ("GBP", "GBP"),
                    ("JPY", "JPY"),
                    ("KRW", "KRW"),
                    ("SGD", "SGD"),
                    ("TWD", "TWD"),
                    ("HKD", "HKD"),
                    ("INR", "INR"),
                    ("CAD", "CAD"),
                    ("AUD", "AUD"),
                    ("CHF", "CHF"),
                ],
                default="",
                max_length=3,
            ),
        ),
        migrations.AddField(
            model_name="worldnews",
            name="demand_description",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="worldnews",
            name="evidence",
            field=models.CharField(blank=True, default="", max_length=600),
        ),
        migrations.AddField(
            model_name="worldnews",
            name="opportunity_reason",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="worldnews",
            name="potential_sales_need",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="worldnews",
            name="project_name",
            field=models.CharField(blank=True, default="", max_length=240),
        ),
        migrations.AddField(
            model_name="worldnews",
            name="signal_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("expansion", "expansion"),
                    ("new_factory", "new_factory"),
                    ("tender", "tender"),
                    ("equipment_upgrade", "equipment_upgrade"),
                    ("procurement", "procurement"),
                    ("other", "other"),
                ],
                default="",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="worldnews",
            name="time_window",
            field=models.CharField(blank=True, default="", max_length=240),
        ),
        migrations.AddConstraint(
            model_name="worldnews",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("amount__isnull", True),
                        ("amount_evidence", ""),
                        ("amount_scope", ""),
                        ("amount_type", ""),
                        ("currency", ""),
                    ),
                    models.Q(
                        ("amount__gte", 0),
                        ("amount__isnull", False),
                        (
                            "amount_scope__in",
                            ["whole_project", "equipment_procurement", "other"],
                        ),
                        (
                            "amount_type__in",
                            [
                                "total_investment",
                                "procurement_budget",
                                "tender_amount",
                                "contract_amount",
                                "other",
                            ],
                        ),
                        (
                            "currency__in",
                            [
                                "CNY",
                                "USD",
                                "EUR",
                                "GBP",
                                "JPY",
                                "KRW",
                                "SGD",
                                "TWD",
                                "HKD",
                                "INR",
                                "CAD",
                                "AUD",
                                "CHF",
                            ],
                        ),
                        models.Q(("amount_evidence", ""), _negated=True),
                    ),
                    _connector="OR",
                ),
                name="world_news_amount_consistent",
            ),
        ),
    ]
