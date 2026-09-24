"""职责：显式重新提取指定旧新闻的公共销售线索。
实现：显式选择原页面重提取或导入现有 Agent 的 dry-run 结果；默认预览，--apply 才通过版本化服务保存十三个字段。
关联：world_insights 的来源/模型逻辑、WorldNewsSerializer 和 save_record；不改变采集去重，不写 CRM 或展会。
目录：
- refresh_one：提取并校验一条指定新闻，可选择保存。
- read_preview：按来源精确匹配明确指定的 Agent 预览文件。
- Command：受服务器运维权限保护的显式刷新入口。
- Command.add_arguments：定义记录 ID 与应用开关。
- Command.handle：预检全部目标、执行一次提取并报告每条结果。
变量索引：
- SIGNAL_FIELDS：唯一允许回写的十三个公共线索字段。
- logger：记录目标、阶段及错误类型，不打印模型原始响应或凭证。
- Command.help：命令用途及写入约束。
"""

import json
import logging
import uuid
from pathlib import Path
from types import SimpleNamespace

from django.core.management.base import BaseCommand, CommandError

from apps.sales.models import WorldNews
from apps.sales.serializers import WorldNewsSerializer
from apps.sales.services import save_record

SIGNAL_FIELDS = ("company_name", "signal_type", "project_name", "demand_description", "potential_sales_need", "opportunity_reason", "time_window", "evidence", "amount", "currency", "amount_type", "amount_scope", "amount_evidence")
logger = logging.getLogger("salesmate.news_refresh")


# 功能：用现有 Agent 重新提取指定新闻并选择性回写线索。
# 输入：`record` 为未归档 Agent 新闻快照，`apply` 表示是否实际保存，`payload` 为显式导入的 Agent 新闻载荷或 None。
# 输出：仅含公开概要的执行结果；来源、模型、校验或版本冲突异常交调用方报告。
# 逻辑：无外部载荷时读取原页面并提取；有载荷时先确认来源一致；旧摘要不充当原文。十三字段校验后按旧 revision 更新，有变化才写入。
# 约束：不改标题、正文、发布时间或来源，不生成 CRM；原页失败不会自动切换导入模式，不重试、不降级为旧摘要。
def refresh_one(record, apply, payload=None):
    from agent import world_insights

    logger.info("news_refresh_started record_id=%s revision=%s apply=%s", record.pk, record.revision, apply)
    if payload is None:
        excerpt, _, _ = world_insights.fetch_page(record.source_url)
        candidate = world_insights.Candidate("news", record.industry, record.title, record.source_url, "", record.published_at)
        payload = world_insights.summarize_news(candidate, excerpt, world_insights.generate_json)
    elif payload.get("source_url") != record.source_url or payload.get("data_source") != "agent":
        raise CommandError("预览来源必须与目标新闻完全一致且 data_source=agent。")
    if payload is None:
        logger.info("news_refresh_skipped record_id=%s reason=no_relevant_payload", record.pk)
        return {"id": str(record.pk), "status": "not_relevant", "changed_fields": []}
    if not set(SIGNAL_FIELDS).issubset(payload):
        raise CommandError("当前 Agent 载荷不包含完整新闻线索契约。")
    data = {field: payload[field] for field in SIGNAL_FIELDS}
    serializer = WorldNewsSerializer(record, data=data, partial=True, context={"request": SimpleNamespace(user=record.owner)})
    serializer.is_valid(raise_exception=True)
    changed = [name for name, value in serializer.validated_data.items() if value != getattr(record, name)]
    status = "unchanged"
    if changed:
        status = "would_update"
        if apply:
            save_record(serializer, record.owner, record.revision)
            status = "updated"
    logger.info("news_refresh_finished record_id=%s status=%s changed_fields=%s", record.pk, status, ",".join(changed))
    return {"id": str(record.pk), "status": status, "changed_fields": changed, "company_name": data["company_name"], "amount": data["amount"], "currency": data["currency"], "amount_type": data["amount_type"]}


# 功能：读取运维明确提供的 Agent dry-run 结果并固定导入目标。
# 输入：`path` 为预览 JSON 文件路径，`records` 为已预检的 UUID 到新闻实例映射。
# 输出：UUID 到唯一来源匹配的新闻载荷映射；缺失、重复或格式错误抛 CommandError。
# 逻辑：只匹配 world_news.create 中与指定记录完全相同的 URL；忽略未选新闻与展会，写入仍需 serializer 校验。
# 约束：不解析日志、不重采、不按标题猜测关系；必须提供完整 JSON，不接收任意数据库字段。
def read_preview(path, records):
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CommandError(f"无法读取 Agent 预览 JSON：{type(error).__name__}") from None
    if not isinstance(document, dict) or not isinstance(document.get("preview"), list):
        raise CommandError("预览文件必须包含 Agent 的 preview 列表。")
    selected = {}
    for identifier, record in records.items():
        matches = [item["data"] for item in document["preview"] if isinstance(item, dict) and item.get("tool") == "world_news.create" and isinstance(item.get("data"), dict) and item["data"].get("source_url") == record.source_url]
        if len(matches) != 1:
            raise CommandError(f"新闻 {identifier} 必须在预览中有且只有一条完全相同来源的结果。")
        selected[identifier] = matches[0]
    return selected


# 功能：提供明确授权目标的维护入口。
# 逻辑：全部 ID 预检通过后才调用外部来源；逐条报告，失败保留该条旧值，已完成条目不回滚。
# 约束：仅有服务器命令执行权的维护者可运行；默认预览，不能隐式刷新全表或使用合成记录。
class Command(BaseCommand):
    help = "按明确新闻 ID 重新提取公共线索；默认预览，--apply 才保存。"

    # 功能：声明有界刷新参数。
    # 输入：`parser` 为 Django 命令行解析器。
    # 输出：无；增加 news_ids、--apply 和 --agent-preview。
    # 逻辑：至少一个 UUID，写入及导入模式必须显式提供。
    # 约束：不接受任意来源 URL、模型覆盖或扩大范围的 all 开关。
    def add_arguments(self, parser):
        parser.add_argument("news_ids", nargs="+", type=uuid.UUID)
        parser.add_argument("--apply", action="store_true", help="保存经过证据校验的十三个线索字段。")
        parser.add_argument("--agent-preview", type=Path, help="使用原 Agent --dry-run 的 JSON 结果，不重新调用模型。")

    # 功能：执行一次明确范围的新闻刷新。
    # 输入：`args` 为 Django 位置参数，`options` 包含 news_ids、apply、agent_preview 和标准命令选项。
    # 输出：stdout JSON；任何条目失败最终抛 CommandError，退出非零。
    # 逻辑：拒绝重复/失效/非 Agent/归档/非活跃 owner；明确选择文件导入或加载环境后重提取，错误不自动重试。
    # 约束：错误只披露类型，Agent 自定义契约错误可附诊断原因；不输出模型响应或凭证，不修改采集配置。
    def handle(self, *args, **options):
        from agent import world_insights

        identifiers = options["news_ids"]
        if len(set(identifiers)) != len(identifiers):
            raise CommandError("新闻 ID 不得重复。")
        records = {row.pk: row for row in WorldNews.objects.filter(pk__in=identifiers, data_source="agent", archived=False, owner__is_active=True).select_related("owner")}
        if set(records) != set(identifiers):
            raise CommandError("目标必须全部是存在、未归档且 owner 活跃的 Agent 新闻。")
        previews = read_preview(options["agent_preview"], records) if options["agent_preview"] else {}
        if not previews:
            world_insights.load_environment()
        results = []
        for identifier in identifiers:
            try:
                results.append(refresh_one(records[identifier], options["apply"], previews.get(identifier)))
            except Exception as error:
                reason = str(error) if isinstance(error, (world_insights.InsightError, CommandError)) else type(error).__name__
                logger.error("news_refresh_failed record_id=%s error_type=%s reason=%s", identifier, type(error).__name__, reason)
                results.append({"id": str(identifier), "status": "failed", "error_type": type(error).__name__, "reason": reason})
        report = {"apply": options["apply"], "results": results, "failed": sum(row["status"] == "failed" for row in results)}
        self.stdout.write(json.dumps(report, ensure_ascii=False))
        if report["failed"]:
            raise CommandError(f'{report["failed"]} 条新闻刷新失败；请核对逐条结果，未自动重试。')
