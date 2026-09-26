#!/usr/bin/env python
"""Responsibility: Clear local SalesMate backend test data while preserving account and authorization configuration.
Implementation: Initialize Django with local settings, refuse every database except a project-local SQLite file, show record counts, require interactive confirmation unless --yes is supplied, delete dependent CRM and sales records in a transaction, and reset mailbox synchronization state.
Relationships: Imports backend Django models and local settings; it is a standalone maintenance script and must never be used against a shared or non-SQLite database.
Directory:
- parse_args: Parse the non-interactive confirmation option.
- prepare_django: Validate the backend location and initialize Django local settings.
- verify_local_sqlite_database: Refuse unsafe database targets and return the local database path.
- get_counts: Count the CRM records that the script reports before and after cleanup.
- print_counts: Print a labelled record-count summary.
- clear_test_data: Delete dependent test data atomically and reset retained mailbox state.
- main: Coordinate validation, confirmation, cleanup, and status reporting.
Variable index:
- PROJECT_ROOT: Repository root resolved from this script location.
- BACKEND_DIR: Backend directory used for Django initialization.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"


# Function: Parse command-line options for this maintenance script.
# Inputs: Process command-line arguments.
# Outputs: argparse namespace containing the yes confirmation flag.
# Logic: Declare only the --yes switch, which bypasses interactive confirmation.
# Constraints: Does not initialize Django or modify data.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clear local mail, company, contact, L1-L4 analysis, and task data."
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip interactive confirmation and clear data immediately.",
    )
    return parser.parse_args()


# Function: Initialize Django using the repository's local backend settings.
# Inputs: Module-level backend directory path and process environment.
# Outputs: None; Django is configured or RuntimeError is raised.
# Logic: Verify manage.py, prepend the backend directory to sys.path, set a default settings module, then call django.setup.
# Constraints: Does not connect to a remote service or clear data.
def prepare_django() -> None:
    if not (BACKEND_DIR / "manage.py").is_file():
        raise RuntimeError(f"Backend directory not found: {BACKEND_DIR}")

    sys.path.insert(0, str(BACKEND_DIR))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

    import django

    django.setup()


# Function: Verify that cleanup targets an existing project-local SQLite file.
# Inputs: Configured Django database connection and module-level project root.
# Outputs: Resolved SQLite database path.
# Logic: Reject non-SQLite, in-memory, missing, or out-of-project database targets before returning the path.
# Constraints: Refusal occurs before data deletion and prevents use against shared databases.
def verify_local_sqlite_database() -> Path:
    from django.db import connection

    if connection.vendor != "sqlite":
        raise RuntimeError(
            f"Current database type is {connection.vendor!r}; this script may clear local SQLite databases only."
        )

    database_name = connection.settings_dict.get("NAME")
    if not database_name or database_name == ":memory:":
        raise RuntimeError("No local SQLite database file is available to clear.")

    database_path = Path(database_name).resolve()
    try:
        database_path.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise RuntimeError(
            f"Database is outside the current project directory; clearing was refused: {database_path}"
        ) from exc

    if not database_path.is_file():
        raise RuntimeError(f"Database file does not exist: {database_path}")

    return database_path


# Function: Count the CRM records reported by the cleanup command.
# Inputs: Configured Django ORM state.
# Outputs: Mapping from display label to record count.
# Logic: Query each mail, CRM-analysis, and Agent-job model independently.
# Constraints: Performs reads only and excludes preserved account and authorization records.
def get_counts() -> dict[str, int]:
    from apps.crm.models import (
        Analysis,
        AnalysisInput,
        Company,
        Contact,
        Email,
        Extraction,
        Job,
        Score,
    )
    from apps.crm.processing_models import EmailProcessingJob, MailboxSyncRun

    return {
        "mail synchronization runs": MailboxSyncRun.objects.count(),
        "per-mail processing jobs": EmailProcessingJob.objects.count(),
        "companies": Company.objects.count(),
        "contacts": Contact.objects.count(),
        "mail": Email.objects.count(),
        "L1 extractions": Extraction.objects.count(),
        "L2 inputs": AnalysisInput.objects.count(),
        "L3 analyses": Analysis.objects.count(),
        "L4 scores": Score.objects.count(),
        "Agent jobs": Job.objects.count(),
    }


# Function: Print a labelled summary of record counts.
# Inputs: ``title`` is the summary heading; ``counts`` maps display labels to counts.
# Outputs: None; writes formatted lines to standard output.
# Logic: Print the heading then every mapping entry in insertion order.
# Constraints: Does not alter counts or database state.
def print_counts(title: str, counts: dict[str, int]) -> None:
    print(title)
    for name, count in counts.items():
        print(f"  {name}: {count}")


# Function: Atomically remove backend test data and reset retained mailbox synchronization state.
# Inputs: Configured Django ORM and transaction manager.
# Outputs: Number of retained mailboxes whose synchronization state was reset.
# Logic: Delete dependent sales and CRM models in foreign-key-safe order inside one transaction, then clear mailbox sync state and reset its version.
# Constraints: Retains account, mailbox, Gmail credential, and authorization records; a database error rolls back the deletion transaction.
def clear_test_data() -> int:
    from django.db import transaction

    from apps.crm.models import (
        Analysis,
        AnalysisInput,
        Company,
        Contact,
        Email,
        Extraction,
        Job,
        Mailbox,
        Score,
    )
    from apps.crm.processing_models import EmailProcessingJob, MailboxSyncRun
    from apps.sales.models import (
        Attachment,
        AuditEvent,
        CompanyAlias,
        CompanyGrant,
        CompanySettings,
        ContactProfile,
        Conversation,
        Draft,
        FollowUp,
        Message,
        Notification,
        Opportunity,
        OrderLine,
        Quote,
        QuoteLine,
        SalesOrder,
        Ticket,
        ToolAction,
    )

    # Delete sales test records that depend on companies and contacts before deleting Agent primary-path data.
    models_in_delete_order = [
        EmailProcessingJob,
        MailboxSyncRun,
        Notification,
        Message,
        Draft,
        ToolAction,
        QuoteLine,
        OrderLine,
        SalesOrder,
        Quote,
        Attachment,
        FollowUp,
        Conversation,
        Ticket,
        Opportunity,
        CompanyGrant,
        CompanyAlias,
        CompanySettings,
        ContactProfile,
        AuditEvent,
        Score,
        Analysis,
        AnalysisInput,
        Job,
        Extraction,
        Email,
        Contact,
        Company,
    ]

    with transaction.atomic():
        for model in models_in_delete_order:
            model.objects.all().delete()

        # Retain Mailbox and GmailCredential; clear only History and run state.
        reset_mailbox_count = Mailbox.objects.update(sync_state={}, version=0)

    return reset_mailbox_count


# Function: Run the guarded local-data cleanup workflow.
# Inputs: Parsed command-line options and interactive standard input when --yes is absent.
# Outputs: Zero after successful cleanup or one when the user cancels.
# Logic: Initialize Django, validate the SQLite target, report counts, obtain confirmation, clear data, then report final counts.
# Constraints: Does not bypass the database safety checks; exceptions propagate to the module entry point for error reporting.
def main() -> int:
    args = parse_args()
    prepare_django()
    database_path = verify_local_sqlite_database()

    print(f"Target database: {database_path}")
    print_counts("Before clearing:", get_counts())
    print("\nThe script retains: login accounts, mailbox records, Google OAuth authorization, and Agent credentials.")

    if not args.yes:
        answer = input("Type CLEAR to confirm clearing: ").strip()
        if answer != "CLEAR":
            print("Cancelled; the database was not changed.")
            return 1

    reset_mailbox_count = clear_test_data()

    print("\nClearing completed.")
    print_counts("After clearing:", get_counts())
    print(f"  mailbox synchronization state reset: {reset_mailbox_count}")
    print("Refresh the page and select Gmail synchronization to run the L1-L4 flow again from the beginning.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Clearing failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
