"""职责：为隔离图谱部署创建专用合成身份及仅graph工具的短期凭证。
实现：检查数据库命名前缀和输出文件不存在；原始令牌只写权限0600文件，不打印。
关联：需先运行Django迁移；正式业务用户仍通过Session授权接口管理凭证。
目录：
- main：解析显式参数并创建沙盒授权。
变量索引：
- 无
"""
import argparse
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys


# 功能：初始化一次专用沙盒身份。
# 输入：无函数参数；CLI username、credential-file必填，expires-hours默认24且最多720；数据库由环境配置。
# 输出：用户及凭证元数据写入新文件，stdout只报告ID和期限；失败非零退出。
# 逻辑：只接受salesmate_graph_sandbox前缀数据库，创建无密码用户和九项graph授权。
# 约束：拒绝覆盖文件或复用用户；不迁移、不授予其他工具、不打印令牌；文件失败回滚数据库。
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
