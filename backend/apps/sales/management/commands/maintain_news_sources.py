"""职责：显式维护已知 Eurostat 迁移的历史新闻来源地址。
实现：仅接受指定 ID 与预期 revision，整批预检重复来源；默认预览，--apply 经原版本化保存服务更新。
关联：复用 Agent source_url 的迁移规则及销售审计；与模型线索刷新分离，不调用模型或网站。
目录：
- target_spec：解析固定目标与版本。
- maintain_sources：预览或原子维护来源。
- Command：管理命令。
- Command.add_arguments：声明目标及写入开关。
- Command.handle：执行并输出结果。
变量索引：
- MIGRATION_URL：已核验旧/新地址范围。
- logger：维护审计日志。
- Command.help：命令说明。
"""
import argparse
import json
import logging
import re
import uuid
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from agent.world_insights import source_url
from apps.crm.access import Conflict
from apps.sales.models import WorldNews
from apps.sales.serializers import WorldNewsSerializer
from apps.sales.services import save_record

MIGRATION_URL = re.compile(r"https://ec\.europa\.eu/eurostat/(?:product\?code=|en/web/products-euro-indicators/w/)(4-[0-9]{8}-ap)\Z")
logger = logging.getLogger("salesmate.news_maintenance")


# 功能：解析固定目标和预期版本。
# 输入：`value` 为 UUID:revision 字符串。
# 输出：UUID 与非负整数；格式不合法抛 argparse.ArgumentTypeError。
# 逻辑：从最后冒号拆分并严格转换，不接受不带版本的全表修改。
# 约束：不读取或修改数据库。
def target_spec(value):
    try:
        identifier, revision = value.rsplit(":", 1)
        if not revision.isascii() or not revision.isdigit() or len(revision) > 18:
            raise ValueError
        return uuid.UUID(identifier), int(revision)
    except (ValueError, TypeError, AttributeError):
        raise argparse.ArgumentTypeError("目标须为 UUID:非负revision。") from None


# 功能：预览或原子更新明确选择的历史来源。
# 输入：`targets` 为 ID/revision 对列表，`apply` 为显式写入开关。
# 输出：包含目标、前后 URL、版本和处理状态的公开元数据列表。
# 逻辑：按服务锁序先锁所有者再锁新闻，核对版本、迁移范围及全局旧/新来源重复，最后统一保存。
# 约束：即使实验模式也检查预期 revision；任何目标失败则整批回滚；不归档重复记录、不刷新线索或触发模型。
@transaction.atomic
def maintain_sources(targets, apply=False):
    expected = dict(targets)
    if not targets or len(expected) != len(targets):
        raise CommandError("必须提供不重复的目标 ID 与 revision。")
    candidates = WorldNews.objects.filter(pk__in=expected, data_source="agent", archived=False, owner__is_active=True)
    owner_ids = list(candidates.values_list("owner_id", flat=True).distinct())
    list(get_user_model().objects.select_for_update().filter(pk__in=owner_ids).order_by("pk"))
    records = {row.pk: row for row in candidates.select_for_update(of=("self",)).select_related("owner").order_by("pk")}
    if set(records) != set(expected):
        raise CommandError("目标必须全部是存在、未归档且 owner 活跃的 Agent 新闻。")
    plans = []
    for identifier, revision in targets:
        record = records[identifier]
        if record.revision != revision:
            raise Conflict("新闻 revision 已变化；请重新预览，不得覆盖并发编辑。")
        match = MIGRATION_URL.fullmatch(record.source_url)
        if match is None:
            raise CommandError("只允许维护已核验的 Eurostat 指标来源迁移。")
        canonical = source_url(record.source_url)
        old = "https://ec.europa.eu/eurostat/product?code=" + match[1]
        if WorldNews.objects.filter(source_url__in=[old, canonical]).exclude(pk=identifier).exists():
            raise CommandError("存在相同旧地址或规范地址记录，必须先明确重复记录处理策略。")
        plans.append({"id": str(identifier), "revision": revision, "source_url": record.source_url, "canonical_url": canonical,
                      "status": "unchanged" if record.source_url == canonical else "would_update"})
    for plan in plans:
        if not apply or plan["status"] == "unchanged":
            continue
        record = records[uuid.UUID(plan["id"])]
        serializer = WorldNewsSerializer(record, data={"source_url": plan["canonical_url"]}, partial=True, context={"request": SimpleNamespace(user=record.owner)})
        serializer.is_valid(raise_exception=True)
        saved = save_record(serializer, record.owner, plan["revision"])
        plan.update(status="updated", revision=saved.revision)
        logger.info("news_source_maintained record_id=%s revision=%s migration=eurostat_indicator", saved.pk, saved.revision)
    return plans


# 功能：提供服务器管理员使用的有界来源维护入口。
# 逻辑：明确输入 ID:revision，预览无写入，应用只改变来源并记录原审计。
# 约束：不把地址维护当作新闻内容刷新，不执行隐式模型调用。
class Command(BaseCommand):
    help = "维护已知 Eurostat 来源迁移：UUID:revision；默认预览，--apply 才写入。"

    # 功能：声明目标与写入开关。
    # 输入：`parser` 为 Django 参数解析器。
    # 输出：无，添加 targets 与 apply。
    # 逻辑：至少一个有版本的 UUID，拒绝隐式全表范围。
    # 约束：不提供任意 URL、模型参数或自动刷新选项。
    def add_arguments(self, parser):
        parser.add_argument("targets", nargs="+", type=target_spec)
        parser.add_argument("--apply", action="store_true")

    # 功能：输出预览或应用结果。
    # 输入：`args` 为位置参数，`options` 含 targets/apply 与标准选项。
    # 输出：stdout JSON；版本或唯一性冲突退出非零。
    # 逻辑：仅转换已知版本/唯一性错误，不泄漏 SQL 或数据库参数。
    # 约束：失败不重试，其他校验异常保持原失败语义。
    def handle(self, *args, **options):
        try:
            plans = maintain_sources(options["targets"], options["apply"])
        except Conflict as error:
            raise CommandError(str(error.detail)) from None
        except IntegrityError:
            raise CommandError("来源唯一性冲突，整批未写入；请重新核对重复记录。") from None
        self.stdout.write(json.dumps({"apply": options["apply"], "results": plans}, ensure_ascii=False))
