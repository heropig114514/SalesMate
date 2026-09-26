"""Responsibility: Backfill the current non-business classification for historical emails while retaining original records.
Implementation: Preview by default; --apply transactionally applies review rules to emails without a purchasing stage and propagates lineage invalidation. Human results are never overwritten.
Relationships: classification supplies the same mapping, and selectors hide non-business companies according to classification.
Directory:
- Command: Historical-classification backfill entry point.
- Command.add_arguments: Declare the apply switch.
- Command.handle: Preview or execute classification and version updates per company.
Variable index:
- Command.help: Command description.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.crm.classification import apply_classification, automatic_classification
from apps.crm.models import Company


# Function: Backfill historical email classification.
# Logic: Calculate from the latest extraction; non-business records are only hidden, never deleting emails or customers.
# Constraints: Read-only by default; human judgment takes precedence.
class Command(BaseCommand):
    help = "Preview historical email-classification changes; use --apply to apply them explicitly."

    # Function: Declare the explicit apply switch.
    # Inputs: `parser` is the command parser.
    # Outputs: Registers --apply.
    # Logic: Preview by default to avoid inadvertently reclassifying existing data.
    # Constraints: Makes no external service calls.
    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    # Function: Atomically backfill classification by company.
    # Inputs: `args` are positional arguments and `options` includes apply.
    # Outputs: Writes the difference count; application updates classification and context version.
    # Logic: Update only actual changes. A classification change invalidates source snapshots and automatically queues recomputation when business sources remain.
    # Constraints: Creates persistent jobs without executing models; does not delete history or alter human decisions or confirmed transactions.
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
        self.stdout.write(f"{'Applied' if options['apply'] else 'Preview'} classification changes: {changed} emails; original messages were not deleted.")
