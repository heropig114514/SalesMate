"""职责：保存和下载员工私有客户附件。
实现：随机存储键、流式摘要、显式大小限制；正式模式下载经过登录及 owner 校验，实验模式公开业务附件。
关联：views 处理 multipart，Attachment 只保存元数据；目录不挂载公共静态路由。
目录：
- store_file：写入已授权附件并保存审计。
- open_file：打开已授权且完整的附件。
变量索引：
- logger：文件边界诊断日志，不记录文件内容。
- MAX_BYTES：单附件 20 MiB 上限。
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


# 功能：保存私有附件与元数据。
# 输入：`actor` 为用户，`company` 为公司，`upload` 为 Django 上传对象。
# 输出：Attachment；失败时清理本次生成的文件并传播错误。
# 逻辑：随机 UUID 路径隔离文件名，流式统计大小和 SHA-256，事务写入元数据及审计。
# 约束：上限 20 MiB；附件仅存储，不解析或执行；异常不会静默转成成功。
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


# 功能：打开私有文件供附件下载。
# 输入：`actor`、`record` 为已经按 ID 查询的附件。
# 输出：只读二进制文件句柄；缺失、越界或权限错误返回 404。
# 逻辑：正式模式复核 owner；所有模式检查归档和目录边界，记录下载审计；浏览器以 attachment 方式接收。
# 约束：不提供任意路径读取；文件内容不会以内联 HTML 方式执行。
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
