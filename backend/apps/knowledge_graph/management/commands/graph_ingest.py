"""Responsibility: Import external schema/text files or continuously associate existing inbound business emails.
Implementation: Use the same file envelope as HTTP; email content-digest keys provide idempotency so successful sources do not repeat model calls; exit on failure without automatic retries.
Relationships: episodes is the sole processing path; local llama.cpp runs separately and graph_worker continues maintaining source changes.
Directory:
- email_payload: Build an immutable observation from the current user's email.
- Command: Automatic graph ingestion management command.
- Command.add_arguments: Declare user, input, and continuous-run options.
- Command.handle: Read a file or scan new emails through shared ingestion.
Variable index:
- Command.help: Command purpose.
"""
import hashlib
import json
import time
from pathlib import Path
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import close_old_connections
from common.shutdown import graceful_shutdown
from apps.crm.models import Email
from apps.knowledge_graph.episode_views import parse_input
from apps.knowledge_graph.episodes import ingest, episode_data
from apps.knowledge_graph.models import Episode
from apps.knowledge_graph.business_schema import mail_text


# Function: Pass the original email to semantic graph ingestion as a natural-language source.
# Inputs: `email`: inbound business email already filtered by mailbox ownership.
# Outputs: Input arguments containing source key, observation time, subject, and body.
# Logic: Bind the key to email identity and text digest for same-content idempotency; exclude attachments and mailbox credentials.
# Constraints: Do not guess email entity ownership automatically; the model aligns same-user candidates while original text retains source boundaries.
def email_payload(email):
    text = mail_text(email)
    key = hashlib.sha256((str(email.pk) + "\0" + text).encode()).hexdigest()
    return {"source_key": "mail:" + key, "observed_at": email.sent_at, "text": text, "email_id": str(email.pk)}


# Function: Run file ingestion or automatic email association.
# Logic: Continuous mode processes only new input; model or evidence errors stop immediately and require explicit inspection and restart.
# Constraints: Do not start mailbox synchronization, send email, access other owners, or change model profiles or existing experiments.
class Command(BaseCommand):
    help = "将不完整业务 JSON 或自然语言自动关联到内部图谱；支持本用户业务邮件监听。"

    # Function: Register an explicit input scope.
    # Inputs: `parser`: Django command parser.
    # Outputs: None; declare owner, input/emails, watch, and poll options.
    # Logic: File and email modes are mutually exclusive; default to one pass, with continuous mode checking new input every 10 seconds.
    # Constraints: Polling is not failure retry and applies only to this command, without changing existing worker parameters.
    def add_arguments(self, parser):
        parser.add_argument("--owner", required=True, type=int)
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--input", type=Path)
        group.add_argument("--emails", action="store_true")
        parser.add_argument("--watch", action="store_true")
        parser.add_argument("--poll", type=float, default=10)

    # Function: Execute the automatic association loop.
    # Inputs: `args`: positional arguments; `options`: CLI options.
    # Outputs: File mode returns complete source JSON; email mode prints only source UUIDs and counts; failures exit nonzero.
    # Logic: Only new emails invoke the model; after SIGTERM, finish current inference and exit without claiming another email.
    # Constraints: Do not reimport withdrawn sources automatically; changed original text creates a new observation while retaining the old one without overwrite.
    def handle(self, *args, **options):
        if not 0 < options["poll"] <= 60 or (options["watch"] and not options["emails"]):
            raise CommandError("--watch 仅支持 --emails；--poll 必须在 (0,60]。")
        try:
            owner = get_user_model().objects.get(pk=options["owner"])
            if options["input"]:
                payload = parse_input(json.loads(options["input"].read_text(encoding="utf-8")))
                self.stdout.write(json.dumps(episode_data(ingest(owner.pk, **payload)), ensure_ascii=False, cls=DjangoJSONEncoder))
                return
            with graceful_shutdown() as stop:
                while not stop["requested"]:
                    close_old_connections()
                    for email in Email.objects.filter(mailbox__owner=owner, direction="inbound", business_classification="business").order_by("sent_at", "pk").iterator():
                        if stop["requested"]:
                            break
                        payload = email_payload(email)
                        if Episode.objects.filter(owner=owner, source_key=payload["source_key"]).exists():
                            continue
                        episode = ingest(owner.pk, **payload)
                        self.stdout.write(f"episode={episode.pk} entities={len(episode.extraction['entities'])} facts={len(episode.extraction['facts'])}")
                    if not options["watch"] or stop["requested"]:
                        break
                    time.sleep(options["poll"])
        except Exception as exc:
            raise CommandError(f"自动建图失败（{type(exc).__name__}）；检查服务日志后显式恢复，没有自动重试。") from exc
