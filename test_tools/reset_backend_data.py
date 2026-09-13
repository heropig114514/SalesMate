#!/usr/bin/env python
"""清理本地 SalesMate 后端测试数据，并保留账号和授权配置。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="清空本地邮件、公司、联系人、L1-L4 分析和任务数据。"
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="跳过交互确认，直接执行清理。",
    )
    return parser.parse_args()


def prepare_django() -> None:
    if not (BACKEND_DIR / "manage.py").is_file():
        raise RuntimeError(f"找不到后端目录：{BACKEND_DIR}")

    sys.path.insert(0, str(BACKEND_DIR))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

    import django

    django.setup()


def verify_local_sqlite_database() -> Path:
    from django.db import connection

    if connection.vendor != "sqlite":
        raise RuntimeError(
            f"当前数据库类型为 {connection.vendor!r}，脚本只允许清理本地 SQLite 数据库。"
        )

    database_name = connection.settings_dict.get("NAME")
    if not database_name or database_name == ":memory:":
        raise RuntimeError("没有找到可清理的本地 SQLite 数据库文件。")

    database_path = Path(database_name).resolve()
    try:
        database_path.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise RuntimeError(
            f"数据库不在当前项目目录内，已拒绝清理：{database_path}"
        ) from exc

    if not database_path.is_file():
        raise RuntimeError(f"数据库文件不存在：{database_path}")

    return database_path


def get_counts() -> dict[str, int]:
    from apps.crm.models import (
        Analysis,
        AnalysisInput,
        Company,
        Contact,
        Email,
        Extraction,
        Job,
        Score,
    )
    from apps.crm.processing_models import EmailProcessingJob, MailboxSyncRun

    return {
        "邮箱同步批次": MailboxSyncRun.objects.count(),
        "逐封处理任务": EmailProcessingJob.objects.count(),
        "公司": Company.objects.count(),
        "联系人": Contact.objects.count(),
        "邮件": Email.objects.count(),
        "L1 抽取": Extraction.objects.count(),
        "L2 输入": AnalysisInput.objects.count(),
        "L3 分析": Analysis.objects.count(),
        "L4 评分": Score.objects.count(),
        "Agent 任务": Job.objects.count(),
    }


def print_counts(title: str, counts: dict[str, int]) -> None:
    print(title)
    for name, count in counts.items():
        print(f"  {name}: {count}")


def clear_test_data() -> int:
    from django.db import transaction

    from apps.crm.models import (
        Analysis,
        AnalysisInput,
        Company,
        Contact,
        Email,
        Extraction,
        Job,
        Mailbox,
        Score,
    )
    from apps.crm.processing_models import EmailProcessingJob, MailboxSyncRun
    from apps.sales.models import (
        Attachment,
        AuditEvent,
        CompanyAlias,
        CompanyGrant,
        CompanySettings,
        ContactProfile,
        Conversation,
        Draft,
        FollowUp,
        Message,
        Notification,
        Opportunity,
        OrderLine,
        Quote,
        QuoteLine,
        SalesOrder,
        Ticket,
        ToolAction,
    )

    # 先删除依赖公司和联系人的销售测试记录，再删除 Agent 主链路数据。
    models_in_delete_order = [
        EmailProcessingJob,
        MailboxSyncRun,
        Notification,
        Message,
        Draft,
        ToolAction,
        QuoteLine,
        OrderLine,
        SalesOrder,
        Quote,
        Attachment,
        FollowUp,
        Conversation,
        Ticket,
        Opportunity,
        CompanyGrant,
        CompanyAlias,
        CompanySettings,
        ContactProfile,
        AuditEvent,
        Score,
        Analysis,
        AnalysisInput,
        Job,
        Extraction,
        Email,
        Contact,
        Company,
    ]

    with transaction.atomic():
        for model in models_in_delete_order:
            model.objects.all().delete()

        # 保留 Mailbox 与 GmailCredential，只清除 History 和运行状态。
        reset_mailbox_count = Mailbox.objects.update(sync_state={}, version=0)

    return reset_mailbox_count


def main() -> int:
    args = parse_args()
    prepare_django()
    database_path = verify_local_sqlite_database()

    print(f"目标数据库：{database_path}")
    print_counts("清理前：", get_counts())
    print("\n将保留：登录账号、邮箱记录、Google OAuth 授权、Agent 凭据。")

    if not args.yes:
        answer = input("输入 CLEAR 确认清理：").strip()
        if answer != "CLEAR":
            print("已取消，数据库没有变化。")
            return 1

    reset_mailbox_count = clear_test_data()

    print("\n清理完成。")
    print_counts("清理后：", get_counts())
    print(f"  已重置邮箱同步状态: {reset_mailbox_count}")
    print("现在刷新网页并点击 Gmail 同步，即可从头运行 L1-L4 流程。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"清理失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
