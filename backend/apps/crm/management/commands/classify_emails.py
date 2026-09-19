"""职责：为历史邮件回填当前非业务分类并保留原始记录。
实现：默认预览，--apply 按公司事务应用无采购阶段邮件的复核规则并传播血缘失效；人工结果不覆盖。
关联：classification 提供同一映射，selectors 依据分类隐藏非业务公司。
目录：
- Command：历史分类回填入口。
- Command.add_arguments：声明应用开关。
- Command.handle：预览或逐公司执行分类及版本更新。
变量索引：
- Command.help：命令说明。
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.crm.classification import apply_classification, automatic_classification
from apps.crm.models import Company


# 功能：回填历史邮件分类。
# 逻辑：以最新抽取计算，非业务仅隐藏，不删除邮件或客户。
# 约束：默认只读；人工判断优先。
class Command(BaseCommand):
    help = "预览历史邮件分类变化；使用 --apply 明确应用。"

    # 功能：声明显式应用开关。
    # 输入：`parser` 为命令解析器。
    # 输出：注册 --apply。
    # 逻辑：默认预览，避免无意重分类现有数据。
    # 约束：无外部服务调用。
    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    # 功能：按公司原子回填分类。
    # 输入：`args` 为位置参数，`options` 包含 apply。
    # 输出：控制台差异数量；应用时更新分类和上下文版本。
    # 逻辑：仅实际差异才更新；分类变化使来源快照失效，有剩余业务来源时自动排队重算。
    # 约束：只创建持久任务而不执行模型；不删除历史，不改人工决定或已确认交易。
    def handle(self, *args, **options):
        from apps.crm.lineage import invalidate_email, schedule_analysis
        changed = 0
        for company_id in Company.objects.values_list("pk", flat=True).iterator():
            with transaction.atomic():
                company = Company.objects.select_for_update().get(pk=company_id)
                company_changed = False
                for email in company.emails.exclude(classification_source="human").iterator():
                    extraction = email.extractions.order_by("-pk").first()
                    if extraction is None:
                        continue
                    target = automatic_classification(extraction.status, extraction.facts, email.payload)
                    if (email.business_classification, email.classification_source, email.classification_reason) == target:
                        continue
                    changed += 1
                    if options["apply"]:
                        company_changed |= email.business_classification != target[0]
                        if email.business_classification != target[0]:
                            invalidate_email(email, "classification_backfill")
                        apply_classification(email, extraction)
                if company_changed:
                    company.revision += 1
                    company.save(update_fields=["revision"])
                    schedule_analysis(company)
        self.stdout.write(f"{'已应用' if options['apply'] else '预览'}分类变化：{changed} 封；未删除原始邮件。")
