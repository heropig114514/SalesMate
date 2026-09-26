"""Responsibility: Persist QQ authorization and IMAP scanning state independently.
Implementation: Store one encrypted authorization code and folder checkpoint per mailbox without modifying Gmail credentials or History checkpoints.
Relationships: models registers entities, qq_connection validates authorization, and qq_sync transactionally records messages and maximum UID.
Directory:
- QQCredential: Authenticated ciphertext for a QQ client authorization code.
- QQSyncCheckpoint: UIDVALIDITY and most recently recorded UID for each folder.
Variable index:
- QQCredential.mailbox: One-to-one ownership by an employee mailbox.
- QQCredential.encrypted_code: Authorization code encrypted with SALESMATE_VAULT_KEY.
- QQCredential.authorized_at: First verification time.
- QQCredential.updated_at: Re-verification time.
- QQSyncCheckpoint.mailbox: One-to-one mailbox checkpoint.
- QQSyncCheckpoint.folders: Dictionary from wire folder name to uidvalidity and last_uid.
"""
from django.db import models


# Function: Store a QQ authorization code verified through IMAP login.
# Logic: Store ciphertext independently and return only an authorization boolean in mailbox lists.
# Constraints: Do not expose it through Agent HTTP claim interfaces or browsers; decryption happens only in Worker.
class QQCredential(models.Model):
    mailbox = models.OneToOneField("crm.Mailbox", on_delete=models.CASCADE, related_name="qq_credential")
    encrypted_code = models.TextField()
    authorized_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


# Function: Persist QQ folder identity generation and maximum recorded UID.
# Logic: Update last_uid after recording messages; bounded scans do not use it to skip old emails, and failed sources await explicit retry.
# Constraints: Stop synchronization when UIDVALIDITY changes; do not silently reset or reuse an old UID.
class QQSyncCheckpoint(models.Model):
    mailbox = models.OneToOneField("crm.Mailbox", primary_key=True, on_delete=models.CASCADE, related_name="qq_checkpoint")
    folders = models.JSONField(default=dict)
