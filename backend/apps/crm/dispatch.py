"""Responsibility: Select employees for shared CRM dispatch and manage one work unit's credential lifecycle.
Implementation: Rotate pending work by employee primary key, exclude QQ synchronization when disabled, and hold an account-shared lock for a work unit while cleaning stale connections and revoking its temporary credential after external calls.
Relationships: crm_worker selects employees, worker uses scoped_backend, and AgentAuthentication continues to validate ownership for HTTP.
Directory:
- next_owner: Select the next active employee with executable work for a channel.
- scoped_backend: Create and reclaim an isolated employee client for one work unit.
Variable index:
- logger: Scheduling-credential lifecycle logger without tokens or digests.
"""
from contextlib import contextmanager
import hashlib
import logging
import secrets

from django.contrib.auth import get_user_model
from django.conf import settings
from django.db import close_old_connections
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from agent.clients.backend_api import django_backend_from_environment
from apps.accounts.reset_locks import account_lock, ResetBusy
from apps.accounts.reset_models import AccountReset
from .jobs import claimable_jobs
from .models import AgentCredential, ExtractionRepair, Job, MailboxSyncRun

logger = logging.getLogger("salesmate.crm_dispatch")


# Function: Select active non-disabled employees with work by rotating persistent jobs.
# Inputs: `kind` is sync or analysis; `after` is this channel's most recently scheduled employee primary key, initially 0.
# Outputs: An employee object or None; unknown channels raise ValueError.
# Logic: Rotate by primary key after excluding disabled QQ synchronization; other expired jobs remain schedulable and historical-email repairs can still run.
# Constraints: Only discovers pending work; existing transactions and leases still protect claims, and it adds neither concurrency nor automatic retries.
def next_owner(kind, after=0):
    now = timezone.now()
    owners = get_user_model().objects.filter(is_active=True).order_by("pk")
    if kind == "sync":
        syncs = MailboxSyncRun.objects.filter(mailbox__owner_id=OuterRef("pk")).filter(
            Q(status="queued") | Q(status="running", lease_until__lte=now)
        )
        repairs = ExtractionRepair.objects.filter(email__mailbox__owner_id=OuterRef("pk")).filter(
            # Repairs read cached source text and do not connect to QQ, so they need not pause.
            Q(status="pending") | Q(status="running", lease_until__lte=now)
        )
        if not settings.QQ_MAIL_ENABLED:
            syncs = syncs.filter(mailbox__qq_credential__isnull=True)
        owners = owners.annotate(sync_waiting=Exists(syncs), repair_waiting=Exists(repairs)).filter(
            Q(sync_waiting=True) | Q(repair_waiting=True)
        )
    elif kind == "analysis":
        expired = Job.objects.filter(company__owner_id=OuterRef("pk"), status="running", lease_until__lte=now)
        owners = owners.annotate(
            job_waiting=Exists(claimable_jobs(OuterRef("pk"))), expired_waiting=Exists(expired)
        ).filter(Q(job_waiting=True) | Q(expired_waiting=True))
    else:
        raise ValueError("Unsupported CRM dispatch channel")
    return owners.filter(pk__gt=after).first() or owners.first()


# Function: Provide a single-employee HTTP identity to a trusted server work unit.
# Inputs: `owner` is the employee selected from the database by the scheduler; `mailbox_id` is the synchronization batch mailbox or None.
# Outputs: Yields an isolated client in the context, closes the client and deletes its credential on normal or exceptional exit; disabled employees raise DoesNotExist.
# Logic: The account-shared lock covers identity creation through reclamation; reject execution while cleanup is unfinished; clear connections under the Django lifecycle after external calls and then revoke the credential. The random token is stored only as SHA-256 and does not modify the global environment.
# Constraints: Server-internal calls only; HTTP callers cannot request arbitrary employee identities. Forceful process termination may leave irrecoverable
# digest records, but raw tokens exist only in process memory; does not delete credentials of other Workers or existing CLIs.
@contextmanager
def scoped_backend(owner, mailbox_id=None):
    with account_lock(owner.pk):
        if AccountReset.objects.filter(owner=owner, cleaning=True).exists():
            raise ResetBusy("账户清理未完成，请先继续清空操作。")
        get_user_model().objects.get(pk=owner.pk, is_active=True)
        token = secrets.token_urlsafe(32)
        credential = AgentCredential.objects.create(
            owner=owner, digest=hashlib.sha256(token.encode()).hexdigest(), name="crm-work-unit"
        )
        logger.info("crm_identity_created owner_id=%s credential_id=%s", owner.pk, credential.pk)
        backend = None
        try:
            backend = django_backend_from_environment(mailbox_id=mailbox_id, service_token=token)
            yield backend
        finally:
            try:
                if backend is not None:
                    backend.close()
            finally:
                close_old_connections()
                credential.delete()
                logger.info("crm_identity_revoked owner_id=%s", owner.pk)
