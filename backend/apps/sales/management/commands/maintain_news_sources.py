"""Responsibility: Explicitly maintain historical news source URLs affected by the known Eurostat migration.
Implementation: Accept only specified IDs and expected revisions; precheck duplicate sources across the batch. Preview by default; --apply updates through the existing versioned save service.
Relationships: Reuse Agent source_url migration rules and sales audits; keep this separate from model lead refreshes, without model or website calls.
Directory:
- target_spec: Parse fixed targets and versions.
- maintain_sources: Preview or atomically maintain sources.
- Command: Management command.
- Command.add_arguments: Declare targets and the write switch.
- Command.handle: Execute and output results.
Variable index:
- MIGRATION_URL: Verified old/new URL scope.
- logger: Maintenance audit logs.
- Command.help: Command description.
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


# Function: Parse fixed targets and expected versions.
# Inputs: `value`: UUID:revision string.
# Outputs: UUID and nonnegative integer; invalid formats raise argparse.ArgumentTypeError.
# Logic: Split at the final colon and convert strictly; reject unversioned full-table updates.
# Constraints: No database reads or writes.
def target_spec(value):
    try:
        identifier, revision = value.rsplit(":", 1)
        if not revision.isascii() or not revision.isdigit() or len(revision) > 18:
            raise ValueError
        return uuid.UUID(identifier), int(revision)
    except (ValueError, TypeError, AttributeError):
        raise argparse.ArgumentTypeError("目标须为 UUID:非负revision。") from None


# Function: Preview or atomically update explicitly selected historical sources.
# Inputs: `targets`: ID/revision pairs; `apply`: explicit write switch.
# Outputs: Public metadata containing targets, before/after URLs, versions, and processing status.
# Logic: Follow service lock order: owners before news. Check versions, migration scope, and global old/new source duplicates, then save all targets together.
# Constraints: Check expected revisions even in experiment mode; any target failure rolls back the batch. Do not archive duplicates, refresh leads, or invoke models.
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


# Function: Provide bounded source maintenance for server administrators.
# Logic: Require explicit ID:revision inputs; previews do not write. Application changes only sources and records existing audits.
# Constraints: URL maintenance is not news-content refresh; no implicit model calls.
class Command(BaseCommand):
    help = "维护已知 Eurostat 来源迁移：UUID:revision；默认预览，--apply 才写入。"

    # Function: Declare targets and the write switch.
    # Inputs: `parser`: Django argument parser.
    # Outputs: None; add targets and apply.
    # Logic: Require at least one versioned UUID; reject implicit full-table scope.
    # Constraints: No arbitrary URL, model parameter, or automatic refresh options.
    def add_arguments(self, parser):
        parser.add_argument("targets", nargs="+", type=target_spec)
        parser.add_argument("--apply", action="store_true")

    # Function: Output preview or application results.
    # Inputs: `args`: positional arguments; `options`: targets/apply and standard options.
    # Outputs: stdout JSON; version or uniqueness conflicts exit nonzero.
    # Logic: Convert only known version/uniqueness errors; do not leak SQL or database parameters.
    # Constraints: No retries; other validation exceptions retain their original failure semantics.
    def handle(self, *args, **options):
        try:
            plans = maintain_sources(options["targets"], options["apply"])
        except Conflict as error:
            raise CommandError(str(error.detail)) from None
        except IntegrityError:
            raise CommandError("来源唯一性冲突，整批未写入；请重新核对重复记录。") from None
        self.stdout.write(json.dumps({"apply": options["apply"], "results": plans}, ensure_ascii=False))
