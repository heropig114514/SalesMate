"""Responsibility: Execute mailbox synchronization and company analysis in an isolated process.
Implementation: Perform synchronization and profiling for an explicit employee with an account-shared lock and isolated HTTP identity. Clean stale connections after external calls; preserve typed Gmail reauthorization failures and safe stage/location diagnostics.
Relationships: crm_worker shares scheduling and dispatch manages temporary credentials; Gmail/QQ and L1–L4 continue reading and writing through HTTP protocol.
Directory:
- error_location: Extract safe code locations from an exception chain.
- run_sync: Claim and execute one durable mailbox batch.
- run_analysis: Claim and execute one company job.
Variable index:
- logger: Work-unit lifecycle and safe-error logger.
"""
import logging
from pathlib import Path

from django.db import close_old_connections, connections
from apps.accounts.reset_locks import account_work

from agent.tools.gmail import GmailReauthorizationRequired, create_service_from_authorization
from agent.tools import qq_mail
from agent.workflows.orchestration import process_jobs_once

from .models import GmailCredential, QQCredential
from .dispatch import scoped_backend
from .qq_connection import authorization_code
from .qq_sync import sync_persisted as sync_qq
from .processing import claim_run, expire_runs, finish_run
from .durable_sync import sync_persisted
from .lineage import run_repair

logger = logging.getLogger("salesmate.crm_worker")


# Function: Extract exception types and frames needed for diagnosis without outputting exception bodies or locals.
# Inputs: `error` is a captured exception instance.
# Outputs: String containing each exception-chain level's type, filename, function, and line number.
# Logic: Traverse explicit cause or unsuppressed context and use a seen set to prevent exception-chain loops.
# Constraints: Does not read source lines or serialize exception arguments, credentials, or HTTP bodies; full paths are not logged.
def error_location(error):
    parts, seen = [], set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        frames, frame = [], error.__traceback__
        while frame is not None:
            code = frame.tb_frame.f_code
            frames.append(f"{Path(code.co_filename).name}:{code.co_name}:{frame.tb_lineno}")
            frame = frame.tb_next
        parts.append(f"{type(error).__name__}[{' > '.join(frames)}]")
        error = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
    return " <- ".join(parts)


# Function: Execute one employee mailbox's durable synchronization batch.
# Inputs: `owner` is the employee selected by shared scheduling.
# Outputs: Whether work was claimed; final state is stored in the database.
# Logic: Lock the unit and report failures with their execution stage; only typed Gmail authorization rejection maps to reauthorization guidance. Prioritize human repairs, dispatch Gmail/QQ through temporary identity, and clear stale connections before terminal writes.
# Constraints: Read Gmail/QQ only, never execute sales email or calendar actions, and do not retry failures automatically; error bodies are not logged and QQ connections are released in finally.
@account_work
def run_sync(owner):
    run = None
    qq_client = None
    qq_credential = None
    stage = "expire_runs"
    try:
        expire_runs(owner)
        stage = "repair"
        if run_repair(owner):
            return True
        stage = "claim_run"
        run = claim_run(owner)
        if run is None:
            return False
        stage = "backend_identity"
        with scoped_backend(owner, str(run.mailbox_id)) as backend:
            stage = "load_credentials"
            qq_credential = QQCredential.objects.filter(mailbox_id=run.mailbox_id).first()
            if qq_credential:
                stage = "qq_connect"
                qq_client = qq_mail.connect(run.mailbox.address, authorization_code(qq_credential))
                stage = "qq_sync"
                result, refreshed = sync_qq(run, qq_client, backend), None
            else:
                credentials = GmailCredential.objects.get(mailbox_id=run.mailbox_id).credentials
                stage = "gmail_authorize"
                service, refreshed = create_service_from_authorization(credentials)
                stage = "gmail_sync"
                result = sync_persisted(run, service, backend)
            stage = "finish_run"
            close_old_connections()
            finish_run(run.pk, run.lease_token, result, refreshed)
            stage = "revoke_identity"
        return True
    except Exception as error:
        logger.error("sync_worker_failed owner_id=%s run_id=%s stage=%s error_type=%s location=%s action=inspect_run_and_retry_explicitly", owner.pk, run.pk if run else None, stage, type(error).__name__, error_location(error))
        if run:
            try:
                close_old_connections()
                code = "gmail_reauthorization_required" if isinstance(error, GmailReauthorizationRequired) else "worker_sync_failed"
                finish_run(run.pk, run.lease_token, {"status": "failed", "error": {"code": code}})
            except Exception as report_error:
                logger.error("sync_failure_report_rejected run_id=%s stage=report_failure error_type=%s location=%s action=inspect_lease", run.pk, type(report_error).__name__, error_location(report_error))
        return run is not None
    finally:
        if qq_client is not None:
            qq_mail.disconnect(qq_client)
        connections.close_all()


# Function: Execute one company profiling job for the current employee.
# Inputs: `owner` is the employee selected by shared scheduling.
# Outputs: Whether a job was claimed.
# Logic: The shared lock covers the unit; create an isolated temporary identity and HTTP client, then claim one job for that employee.
# Constraints: On exception, record only type and code location and end this unit without bodies or retry; units for other companies continue.
@account_work
def run_analysis(owner):
    try:
        with scoped_backend(owner) as backend:
            return bool(process_jobs_once(backend=backend, limit=1))
    except Exception as error:
        logger.error("analysis_worker_failed owner_id=%s error_type=%s location=%s action=inspect_job_and_lease", owner.pk, type(error).__name__, error_location(error))
        return False
    finally:
        connections.close_all()
