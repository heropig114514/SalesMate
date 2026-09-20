"""职责：保存可恢复的邮箱同步批次及逐封处理状态。
实现：一个邮箱至多存在一个活动批次，邮件任务在批次内按提供方消息 ID 唯一；QQ 范围保存在批次内。
关联：processing 管理领取和进度，worker 调用独立 Agent；models 导入以注册 Django 模型。
目录：
- MailboxSyncRun：持久化同步请求、租约和最终汇总。
- MailboxSyncRun.Meta：约束邮箱活动批次唯一。
- EmailProcessingJob：保存逐封阶段和可操作错误。
- EmailProcessingJob.Meta：约束批次内消息唯一。
变量索引：
- MailboxSyncRun.id：同步批次 UUID。
- MailboxSyncRun.mailbox：归属邮箱，权限从 owner 推导。
- MailboxSyncRun.status：queued/running/partial/completed/failed。
- MailboxSyncRun.requested_at：请求时间。
- MailboxSyncRun.started_at：首次领取时间。
- MailboxSyncRun.finished_at：终态时间。
- MailboxSyncRun.lease_until：运行租约截止时间。
- MailboxSyncRun.lease_token：拒绝旧执行者回报的随机凭证。
- MailboxSyncRun.error：批次错误，不包含凭证或原始异常正文。
- MailboxSyncRun.result：Agent 汇总，不包含 Gmail 授权。
- MailboxSyncRun.message_ids：显式重试的消息范围，空数组表示常规扫描。
- MailboxSyncRun.sync_options：Gmail/QQ 批次冻结的时间和封数范围及 Gmail 超量批准；旧批次可能为空对象。
- MailboxSyncRun.Meta.constraints：同邮箱活动批次唯一约束。
- EmailProcessingJob.id：逐封任务 UUID。
- EmailProcessingJob.run：所属同步批次。
- EmailProcessingJob.gmail_message_id：沿用协议字段名，保存 Gmail ID 或 QQ 稳定消息标识。
- EmailProcessingJob.dedupe_key：既定邮箱地址加消息 ID 去重键。
- EmailProcessingJob.stage：discovered/fetching/extracting/persisting/completed/failed。
- EmailProcessingJob.status：pending/running/completed/failed。
- EmailProcessingJob.attempt：本任务开始读取的次数。
- EmailProcessingJob.error：阶段、代码和安全错误说明。
- EmailProcessingJob.created_at：发现时间。
- EmailProcessingJob.started_at：开始处理时间。
- EmailProcessingJob.finished_at：处理终态时间。
- EmailProcessingJob.company：已保存邮件归属公司，用于批次画像进度。
- EmailProcessingJob.Meta.constraints：批次内消息唯一约束。
"""
import uuid

from django.db import models


# 功能：保存一个员工邮箱的同步批次。
# 逻辑：租约凭证标识当前执行者，QQ 范围按批次冻结；活动批次的合并或拒绝由 processing 决定。
# 约束：错误批次不自动重试；活动批次互斥由数据库保证。
class MailboxSyncRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mailbox = models.ForeignKey("crm.Mailbox", related_name="sync_runs", on_delete=models.CASCADE)
    status = models.CharField(max_length=16, default="queued")
    requested_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    lease_until = models.DateTimeField(null=True)
    lease_token = models.UUIDField(null=True)
    error = models.JSONField(null=True)
    result = models.JSONField(default=dict)
    message_ids = models.JSONField(default=list)
    sync_options = models.JSONField(default=dict)

    # 功能：防止重复点击和多进程建立并行邮箱批次。
    # 逻辑：仅 queued/running 参与邮箱唯一约束。
    # 约束：历史终态批次永久保留。
    class Meta:
        constraints = [models.UniqueConstraint(fields=["mailbox"], condition=models.Q(status__in=["queued", "running"]), name="crm_active_mailbox_run")]


# 功能：保存批次内一封邮件的处理进度。
# 逻辑：发现时登记，阶段事件更新同一记录，完成结果关联公司。
# 约束：不保存访问令牌和模型原始异常；邮件本体仍归 Email。
class EmailProcessingJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(MailboxSyncRun, related_name="email_jobs", on_delete=models.CASCADE)
    gmail_message_id = models.CharField(max_length=200)
    dedupe_key = models.CharField(max_length=400)
    stage = models.CharField(max_length=20, default="discovered")
    status = models.CharField(max_length=16, default="pending")
    attempt = models.PositiveIntegerField(default=0)
    error = models.JSONField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    company = models.ForeignKey("crm.Company", null=True, on_delete=models.SET_NULL)

    # 功能：确保重复阶段登记不会增加计数。
    # 逻辑：同步批次和 Gmail 消息 ID 联合唯一。
    # 约束：同消息在后续显式重试批次可产生新的任务。
    class Meta:
        constraints = [models.UniqueConstraint(fields=["run", "gmail_message_id"], name="crm_run_message")]
