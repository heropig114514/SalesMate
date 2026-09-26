"""职责：共享 CRM 调度的员工选择与单次工作凭证生命周期。
实现：按员工主键轮转待办，禁用时排除 QQ 同步；工作单元持有账号共享锁，外部调用后清理失效连接再回收临时凭证。
关联：crm_worker 选择员工，worker 使用 scoped_backend；HTTP 仍由 AgentAuthentication 校验归属。
目录：
- next_owner：选择某通道下一位有可执行工作的有效员工。
- scoped_backend：为一个工作单元创建并回收独立员工客户端。
变量索引：
- logger：调度凭证生命周期日志，不记录令牌或摘要。
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


# 功能：从持久任务中轮转选择有工作且未停用的员工。
# 输入：`kind` 为 sync/analysis；`after` 为本通道上次调度的员工主键，初始为 0。
# 输出：员工对象或 None；未知通道抛 ValueError。
# 逻辑：排除禁用 QQ 同步后按主键轮转；其他过期任务仍被调度，历史邮件修复仍可执行。
# 约束：此处只发现待办，领取仍由原事务与租约保护；不增加并发或自动重试。
def next_owner(kind, after=0):
    now = timezone.now()
    owners = get_user_model().objects.filter(is_active=True).order_by("pk")
    if kind == "sync":
        syncs = MailboxSyncRun.objects.filter(mailbox__owner_id=OuterRef("pk")).filter(
            Q(status="queued") | Q(status="running", lease_until__lte=now)
        )
        repairs = ExtractionRepair.objects.filter(email__mailbox__owner_id=OuterRef("pk")).filter(
            # 修复读取已缓存原文，不连接 QQ，因此无需暂停。
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


# 功能：为受信服务器工作单元提供单员工 HTTP 身份。
# 输入：`owner` 为调度器从数据库选出的员工；`mailbox_id` 为同步批次的邮箱或 None。
# 输出：上下文中产出独立客户端，正常/异常退出均关闭客户端并删除凭证；停用员工抛 DoesNotExist。
# 逻辑：账号共享锁覆盖身份创建到回收；清理未完成时拒绝执行；外部调用后按 Django 连接生命周期清理再撤销凭证；随机令牌只保存 SHA-256，不修改全局环境。
# 约束：只有服务器内部调用；HTTP 调用方不能申请任意员工身份。进程强杀可能留下不可恢复
# 的摘要记录，但原始令牌仅存进程内存；不删除其他 Worker 或原有 CLI 的凭证。
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
