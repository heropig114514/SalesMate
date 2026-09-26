"""Responsibility: Create a local development user and independent Agent credential once.
Implementation: Create a user, business mailbox, and service credential, then write IDs and tokens required for local execution to the root .env.
Relationships: SessionView uses the user password and AgentAuthentication uses the service credential.
Directory:
- Command: Create a local integration identity without administrator rights.
- Command.add_arguments: Register local-account arguments.
- Command.handle: Perform explicit local-account creation.
Variable index:
- Command.help: Help text for the local-initialization command.
"""
import hashlib
import json
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.crm.models import AgentCredential, Mailbox


# Function: Create a local integration identity without administrator rights.
# Logic: Require both the username and credential file to be absent to avoid overwriting an existing account or credentials.
# Constraints: Allowed only in DEBUG environments; the output file contains secrets and must not be committed or shared.
class Command(BaseCommand):
    help = "Create local user and Agent credential; write secrets to ignored .local-access.json."

    # Function: Register local-account arguments.
    # Inputs: `parser` is the Django command parser.
    # Outputs: None; registers required username and mailbox-address arguments.
    # Logic: Fixes credential output under backend to avoid accidentally writing to a public directory.
    # Constraints: Does not accept a password on the command line.
    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--mailbox-address", required=True)

    # Function: Perform explicit local-account creation.
    # Inputs: `args` are unused positional arguments; `options` contains username and mailbox_address.
    # Outputs: Prints only the credential-file path, never secrets.
    # Logic: Check configuration and files, transactionally create a user and service credential, then exclusively create local JSON.
    # Constraints: Roll back the database if file writing fails; does not reset an existing user's password.
    @transaction.atomic
    def handle(self, *args, **options):
        path = settings.BASE_DIR / ".local-access.json"
        if not settings.DEBUG or path.exists() or get_user_model().objects.filter(username=options["username"]).exists():
            raise CommandError("New accounts are allowed only in DEBUG, and both .local-access.json and the username must be absent.")
        password, token = secrets.token_urlsafe(20), secrets.token_urlsafe(40)
        user = get_user_model().objects.create_user(username=options["username"], password=password)
        AgentCredential.objects.create(owner=user, digest=hashlib.sha256(token.encode()).hexdigest(), name="local-development")
        mailbox = Mailbox.objects.create(owner=user, address=options["mailbox_address"].casefold())
        with path.open("x", encoding="utf-8") as handle:
            json.dump({"username": user.username, "password": password, "agent_token": token,
                       "mailbox_id": str(mailbox.pk), "mailbox_address": mailbox.address}, handle, indent=2)
        env_path = settings.PROJECT_DIR / ".env"
        lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
        replacements = {"SALESMATE_AGENT_SERVICE_TOKEN": token, "SALESMATE_MAILBOX_ID": str(mailbox.pk),
                        "LOCAL_DEBUG_USER": user.username}
        written = set()
        updated = []
        for line in lines:
            key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
            if key in replacements:
                updated.append(f"{key}={replacements[key]}")
                written.add(key)
            else:
                updated.append(line)
        updated.extend(f"{key}={value}" for key, value in replacements.items() if key not in written)
        env_path.write_text("\n".join(updated) + "\n", encoding="utf-8")
        self.stdout.write(f"Local credentials saved to {path}; Agent IDs were written to {env_path}. Keep both files private.")
