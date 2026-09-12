"""职责：一次性创建本地开发用户和独立 Agent 凭证。
实现：创建用户、业务邮箱和服务凭证，并把本地运行所需 ID 与令牌写入根目录 .env。
关联：SessionView 使用用户密码，AgentAuthentication 使用服务凭证。
目录：
- Command：创建无管理员权限的本地联调身份。
- Command.add_arguments：注册本地账号参数。
- Command.handle：执行显式本地账号创建。
变量索引：
- Command.help：本地初始化命令帮助文本
"""
import hashlib
import json
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.crm.models import AgentCredential, Mailbox


# 功能：创建无管理员权限的本地联调身份。
# 逻辑：用户名与凭证文件均必须不存在，避免覆盖已有账号或凭证。
# 约束：仅 DEBUG 环境允许；输出文件含秘密，不得提交或分享。
class Command(BaseCommand):
    help = "Create local user and Agent credential; write secrets to ignored .local-access.json."

    # 功能：注册本地账号参数。
    # 输入：`parser` 为 Django 命令解析器。
    # 输出：无；注册必需 username 和 mailbox-address。
    # 逻辑：凭证输出位置固定在 backend，避免误写可公开目录。
    # 约束：不在命令行接收密码。
    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--mailbox-address", required=True)

    # 功能：执行显式本地账号创建。
    # 输入：`args` 为未使用的位置参数；`options` 含 username 和 mailbox_address。
    # 输出：仅打印凭证文件路径，不打印秘密。
    # 逻辑：检查配置和文件，事务创建用户与服务凭证，排他创建本地 JSON。
    # 约束：文件写入失败回滚数据库；不重设已有用户密码。
    @transaction.atomic
    def handle(self, *args, **options):
        path = settings.BASE_DIR / ".local-access.json"
        if not settings.DEBUG or path.exists() or get_user_model().objects.filter(username=options["username"]).exists():
            raise CommandError("仅允许 DEBUG 环境中新建账号，且 .local-access.json 与用户名不得存在。")
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
