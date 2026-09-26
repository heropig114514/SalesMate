"""Responsibility: Explicitly import an employee's confirmed internal-knowledge versions.
Implementation: Read UTF-8 JSON array and ensure same-version content cannot be overwritten under an employee transaction lock.
Relationships: ``KnowledgeEntry`` supports ``chat.context`` queries; no built-in example policy exists.
Directory:
- Command: Knowledge-maintenance command.
- Command.add_arguments: Declare employee and file.
- Command.handle: Atomically import after validating every entry.
Variable index:
- Command.help: Import purpose.
"""

import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from rest_framework.exceptions import APIException

from apps.chat import contracts, services
from apps.chat.models import KnowledgeEntry


# Function: Import knowledge with explicit versions.
# Logic: A new version for the same identifier deactivates the old version; ``active=false`` can explicitly deactivate the current version.
# Constraints: Same version with different content errors and rolls back the whole batch.
class Command(BaseCommand):
    help = "导入已确认的内部知识 JSON，不预置或生成业务制度。"

    # Function: Declare knowledge-import input.
    # Inputs: Argument parser ``parser``.
    # Outputs: None; registers owner and file.
    # Logic: Both arguments are required.
    # Constraints: File path is never sent to an external service.
    def add_arguments(self, parser):
        parser.add_argument("--owner", required=True)
        parser.add_argument("--file", required=True)

    # Function: Validate and import a complete knowledge batch.
    # Inputs: Positional ``args`` and ``options`` containing employee and local UTF-8 JSON path.
    # Outputs: Entry count; failure raises ``CommandError``.
    # Logic: Validate fields, lengths, and identity first, then write and update active flags under employee lock.
    # Constraints: Does not output content; JSON, file, and contract errors report explicitly and database exceptions are not swallowed.
    def handle(self, *args, **options):
        owner = (
            get_user_model()
            .objects.filter(username=options["owner"], is_active=True)
            .first()
        )
        if owner is None:
            raise CommandError("员工不存在或已停用。")
        try:
            rows = json.loads(Path(options["file"]).read_text(encoding="utf-8"))
            if not isinstance(rows, list):
                raise CommandError("知识文件必须为 JSON 数组。")
            seen = set()
            for row in rows:
                contracts.fields(
                    row, {"source_key", "version", "title", "content", "active"}
                )
                for key, limit in (
                    ("source_key", 160),
                    ("version", 80),
                    ("title", 240),
                    ("content", None),
                ):
                    value = contracts.text(row[key], key)
                    if limit and len(value) > limit:
                        raise CommandError(f"{key} 超出长度限制。")
                if type(row["active"]) is not bool or row["source_key"] in seen:
                    raise CommandError(
                        "active 必须为布尔值，批次内 source_key 不得重复。"
                    )
                seen.add(row["source_key"])
        except (OSError, UnicodeError, json.JSONDecodeError, APIException):
            raise CommandError(
                "知识文件无法读取或字段不符合契约，请检查 UTF-8 JSON。"
            ) from None
        with transaction.atomic():
            services.lock_owner(owner)
            for row in rows:
                entry, created = KnowledgeEntry.objects.get_or_create(
                    owner=owner,
                    source_key=row["source_key"],
                    version=row["version"],
                    defaults={key: row[key] for key in ("title", "content", "active")},
                )
                if not created and (
                    entry.title != row["title"] or entry.content != row["content"]
                ):
                    raise CommandError("同一知识版本已存在不同内容，请明确使用新版本。")
                if row["active"]:
                    KnowledgeEntry.objects.filter(
                        owner=owner, source_key=row["source_key"]
                    ).exclude(pk=entry.pk).update(active=False)
                entry.active = row["active"]
                entry.save(update_fields=["active"])
        self.stdout.write(f"已导入 {len(rows)} 条知识；owner_id={owner.pk}。")
