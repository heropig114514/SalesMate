"""Responsibility: Create a dedicated synthetic identity and short-lived, graph-tool-only credential for an isolated graph deployment.
Implementation: Checks the database-name prefix and that the output file does not exist; writes the raw token only to a mode-0600 file and never prints it.
Relationships: Requires Django migrations to run first; production business users continue to manage credentials through the Session-authorized interface.
Directory:
- main: Parse explicit arguments and create sandbox authorization.
Variable index:
- None
"""
import argparse
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys


# Function: Initialize one dedicated sandbox identity.
# Inputs: No function parameters; CLI username and credential-file are required, expires-hours defaults to 24 and is at most 720, and the environment configures the database.
# Outputs: Writes user and credential metadata to a new file; stdout reports only ID and expiry, and failures exit nonzero.
# Logic: Accepts only databases with the salesmate_graph_sandbox prefix, creates a passwordless user, and grants nine graph permissions.
# Constraints: Refuses to overwrite files or reuse users; does not migrate, grant other tools, or print tokens; file failure rolls back the database.
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--credential-file", required=True, type=Path)
    parser.add_argument("--expires-hours", type=int, default=24)
    args = parser.parse_args()
    if not 1 <= args.expires_hours <= 720:
        parser.error("expires-hours must be between 1 and 720")
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2])]
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.base")
    import django
    django.setup()
    from django.conf import settings
    from django.contrib.auth import get_user_model
    from django.db import transaction
    from django.utils import timezone
    from apps.agent_tools.models import ToolCredential
    from apps.agent_tools.registry import build_registry
    if not str(settings.DATABASES["default"]["NAME"]).startswith("salesmate_graph_sandbox"):
        raise ValueError("Only an explicitly named graph sandbox database is allowed")
    token = secrets.token_urlsafe(32)
    expires = timezone.now() + timedelta(hours=args.expires_hours)
    descriptor = os.open(args.credential_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output, transaction.atomic():
            owner = get_user_model().objects.create_user(username=args.username)
            names = sorted(name for name in build_registry() if name.startswith("graph."))
            credential = ToolCredential.objects.create(owner=owner, name="isolated-graph-sandbox",
                digest=hashlib.sha256(token.encode()).hexdigest(), allowed_tools=names, expires_at=expires)
            json.dump({"token": token, "username": owner.username, "owner_id": owner.pk,
                       "credential_id": str(credential.pk), "expires_at": expires.isoformat(), "allowed_tools": names}, output)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        args.credential_file.unlink()
        raise
    print(f"Created sandbox owner={owner.pk} credential={credential.pk} expires_at={expires.isoformat()}; token stored only in requested file")


if __name__ == "__main__":
    main()
