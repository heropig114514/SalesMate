"""Responsibility: Persist the minimum coordination state for account reset without business content.
Implementation: Versions isolate stale pages; a file list supports explicit recovery of attachment cleanup after a database commit.
Relationships: ``reset``, ``reset_middleware``, and background-work locks; imported and registered by ``accounts.models``.
Directory:
- AccountReset: Account data version and attachment-cleanup progress.
Variable index:
- AccountReset.owner: Retained login identity.
- AccountReset.generation: Data version incremented for every database cleanup.
- AccountReset.key: Idempotency key of the most recently successful submission.
- AccountReset.keys: Submitted-operation key list that prevents an earlier request replay from deleting new data again.
- AccountReset.pending_files: Private-storage keys still requiring processing, excluding file content.
- AccountReset.cleaning: Database is cleared but files or session still require cleanup.
"""
from django.conf import settings
from django.db import models


# Function: Coordinate account-data cleanup and stale-request isolation.
# Logic: One metadata record per account; clear the list after successful file cleanup without changing ``User`` or passwords.
# Constraints: State is not identity authorization and is modified only under the account exclusive lock.
class AccountReset(models.Model):
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, primary_key=True, on_delete=models.CASCADE)
    generation = models.PositiveBigIntegerField(default=0)
    key = models.UUIDField(null=True)
    keys = models.JSONField(default=list)
    pending_files = models.JSONField(default=list)
    cleaning = models.BooleanField(default=False)
