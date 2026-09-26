"""Responsibility: Explicitly re-extract public sales leads for selected historical news.
Implementation: Explicitly choose original-page extraction or existing Agent dry-run imports. Preview by default; only --apply saves thirteen fields through the versioned service.
Relationships: Use world_insights source/model logic, WorldNewsSerializer, and save_record; preserve collection deduplication and do not write CRM or exhibition records.
Directory:
- refresh_one: Extract and validate one selected news record, optionally saving it.
- read_preview: Match an explicitly selected Agent preview file by exact source.
- Command: Explicit refresh entry point protected by server operations permissions.
- Command.add_arguments: Define record IDs and the application switch.
- Command.handle: Precheck all targets, extract once, and report each result.
Variable index:
- SIGNAL_FIELDS: The only thirteen public lead fields allowed for writeback.
- logger: Log target, stage, and error type without raw model responses or credentials.
- Command.help: Command purpose and write constraints.
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


# Function: Use the existing Agent to re-extract selected news and optionally write back leads.
# Inputs: `record`: unarchived Agent news snapshot; `apply`: whether to save; `payload`: explicitly imported Agent news payload or None.
# Outputs: Execution results containing public summaries only; callers report source, model, validation, or version-conflict exceptions.
# Logic: Without an external payload, read and extract the original page; with a payload, verify source equality first. Never use the old summary as source text. Validate thirteen fields and update against the old revision only if changed.
# Constraints: Preserve title, body, publication time, and source; do not generate CRM. Original-page failures never switch automatically to imports, retry, or fall back to old summaries.
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


# Function: Read an explicitly supplied Agent dry-run result and fix the import targets.
# Inputs: `path`: preview JSON path; `records`: prechecked mapping of UUIDs to news instances.
# Outputs: Mapping from UUIDs to uniquely source-matched news payloads; missing, duplicate, or malformed input raises CommandError.
# Logic: Match only world_news.create URLs identical to selected records; ignore unselected news and exhibitions. Writes still require serializer validation.
# Constraints: Do not parse logs, recollect, or infer relations by title; require complete JSON and reject arbitrary database fields.
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


# Function: Provide maintenance for explicitly authorized targets.
# Logic: Call external sources only after every ID passes prechecks. Report each item; failed items retain old values, while completed items are not rolled back.
# Constraints: Only maintainers with server command execution access may run this. Preview by default; no implicit full-table refresh or synthetic records.
class Command(BaseCommand):
    help = "按明确新闻 ID 重新提取公共线索；默认预览，--apply 才保存。"

    # Function: Declare bounded refresh arguments.
    # Inputs: `parser`: Django command-line parser.
    # Outputs: None; add news_ids, --apply, and --agent-preview.
    # Logic: Require at least one UUID; write and import modes must be explicit.
    # Constraints: Reject arbitrary source URLs, model overrides, or an all switch that expands scope.
    def add_arguments(self, parser):
        parser.add_argument("news_ids", nargs="+", type=uuid.UUID)
        parser.add_argument("--apply", action="store_true", help="保存经过证据校验的十三个线索字段。")
        parser.add_argument("--agent-preview", type=Path, help="使用原 Agent --dry-run 的 JSON 结果，不重新调用模型。")

    # Function: Execute one news refresh within an explicit scope.
    # Inputs: `args`: Django positional arguments; `options`: news_ids, apply, agent_preview, and standard command options.
    # Outputs: stdout JSON; any failed item ultimately raises CommandError and exits nonzero.
    # Logic: Reject duplicate, invalid, non-Agent, archived, or inactive-owner records. Explicitly choose file imports or load the environment for re-extraction; never retry errors automatically.
    # Constraints: Errors disclose only type, with diagnostic reasons allowed for Agent contract errors. Do not output model responses/credentials or change collection configuration.
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
