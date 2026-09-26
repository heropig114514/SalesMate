"""Responsibility: Store and download employees' private customer attachments.
Implementation: Use random storage keys, streaming digests, and explicit size limits. Production downloads require login and owner checks; experiment mode exposes business attachments publicly.
Relationships: views handles multipart uploads; Attachment stores metadata only; no public static route mounts the storage directory.
Directory:
- store_file: Write an authorized attachment and save its audit entry.
- open_file: Open an authorized, intact attachment.
Variable index:
- logger: File-boundary diagnostic logs without file content.
- MAX_BYTES: 20 MiB limit per attachment.
"""

from common.laboratory import enabled

import hashlib
import logging
import uuid
from pathlib import Path

from django.conf import settings
from django.db import transaction
from rest_framework.exceptions import NotFound, ValidationError

from .models import Attachment, CompanySettings
from .permissions import company_access
from .services import audit

logger = logging.getLogger("salesmate.files")
MAX_BYTES = 20 * 1024 * 1024


# Function: Save a private attachment and metadata.
# Inputs: `actor`: user; `company`: company; `upload`: Django upload object.
# Outputs: Attachment; on failure remove the newly generated file and propagate the error.
# Logic: Isolate filenames with random UUID paths, stream size and SHA-256 calculations, and write metadata/audit within a transaction.
# Constraints: 20 MiB maximum; attachments are stored without parsing or execution; exceptions never silently become success.
def store_file(actor, company, upload):
    company_access(actor, company)
    if CompanySettings.objects.filter(company=company, archived=True).exists():
        raise ValidationError("客户已归档。")
    if not upload or upload.size > MAX_BYTES:
        raise ValidationError("请选择不超过 20 MiB 的文件。")
    name = Path(upload.name.replace("\\", "/")).name
    if not name or len(name) > 240:
        raise ValidationError("文件名须为 1–240 字符。")
    key = f"{actor.pk}/{uuid.uuid4().hex}"
    path = settings.BASE_DIR / "private_uploads" / key
    path.parent.mkdir(parents=True, exist_ok=True)
    digest, size = hashlib.sha256(), 0
    try:
        with path.open("xb") as output:
            for chunk in upload.chunks():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValidationError("文件超过 20 MiB。")
                digest.update(chunk)
                output.write(chunk)
        with transaction.atomic():
            record = Attachment.objects.create(
                owner=actor,
                company=company,
                name=name,
                storage_key=key,
                content_type=upload.content_type or "application/octet-stream",
                size=size,
                sha256=digest.hexdigest(),
            )
            audit(actor, record, "file_uploaded", {"size": size})
        return record
    except Exception:
        path.unlink(missing_ok=True)
        logger.warning(
            "file_upload_failed actor_id=%s company_id=%s", actor.pk, company.pk
        )
        raise


# Function: Open a private file for attachment download.
# Inputs: `actor`; `record`: attachment already queried by ID.
# Outputs: Read-only binary file handle; missing files, path escape, or permission failures return 404.
# Logic: Recheck owner in production; all modes check archival state and directory boundaries and record download audits. Browsers receive attachment responses.
# Constraints: No arbitrary path reads; file content never executes as inline HTML.
def open_file(actor, record):
    if (not enabled() and record.owner_id != actor.pk) or record.archived:
        raise NotFound("附件不存在。")
    root = (settings.BASE_DIR / "private_uploads").resolve()
    path = (root / record.storage_key).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        logger.error("file_unavailable file_id=%s", record.pk)
        raise NotFound("附件文件不可用，请检查存储卷和备份。")
    audit(actor, record, "file_downloaded")
    return path.open("rb")
