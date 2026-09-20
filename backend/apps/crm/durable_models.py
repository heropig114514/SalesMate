"""职责：持久保存 Gmail 原文、Worker 接管标记及历史检查点、抽取修复任务和分析血缘。
实现：消息天然键去重，接管标记与发现记录原子提交；血缘边保留具体抽取与人工判断版本。
关联：durable_sync 消费原文，lineage 维护修复与失效；models 导入以注册模型。
目录：
- StoredMessage：抽取前的持久原文及处理终态。
- StoredMessage.Meta：邮箱内消息唯一约束。
- SyncCheckpoint：Worker 接管标记及旧版本游标审计数据。
- SnapshotSource：分析快照到邮件抽取的版本化来源边。
- SnapshotSource.Meta：快照邮件唯一约束。
- SnapshotInvalidation：保留快照失效原因及时间。
- ExtractionRepair：人工纠错产生的可追溯抽取任务。
- ExtractionRepair.Meta：一封邮件至多一个活动修复任务。
变量索引：
- StoredMessage.mailbox：授权所属邮箱。
- StoredMessage.message_id：Gmail 消息标识。
- StoredMessage.raw：解析后的完整邮件，含可抽取正文和原始头；空对象表示尚未读取。
- StoredMessage.submission：已完成的 L1 提交载荷，后端写入失败后复用以避免重复模型调用。
- StoredMessage.status：pending/completed/failed，不自动重试 failed。
- StoredMessage.prompt_version：最近处理的真实 L1 提示词版本。
- StoredMessage.created_at：发现时间。
- StoredMessage.Meta.constraints：消息天然键唯一约束。
- SyncCheckpoint.mailbox：每邮箱唯一检查点。
- SyncCheckpoint.cursor：旧版本已登记消息的 History 游标，仅保留审计，不驱动有界同步。
- SyncCheckpoint.anchor：旧版本历史扫描前的 History 基准，仅保留审计。
- SyncCheckpoint.page_token：旧版本历史分页位置，仅保留审计。
- SyncCheckpoint.backfill_complete：旧版本历史枚举完成状态，仅保留审计。
- SnapshotSource.snapshot：依赖邮件的 L2 快照。
- SnapshotSource.email：来源邮件。
- SnapshotSource.extraction：当时采用的具体抽取记录。
- SnapshotSource.review_revision：当时的人工判断版本。
- SnapshotSource.Meta.constraints：每快照每邮件仅一条来源边。
- SnapshotInvalidation.snapshot：失效的 L2，关联的 L3/L4 一并不可用。
- SnapshotInvalidation.reason：受控失效原因。
- SnapshotInvalidation.created_at：失效时间。
- ExtractionRepair.email：待补抽取的业务邮件。
- ExtractionRepair.source：被替代的原抽取记录。
- ExtractionRepair.review_revision：入队时人工决定版本。
- ExtractionRepair.status：pending/running/completed/failed/skipped。
- ExtractionRepair.lease_until：执行租约，过期显式失败。
- ExtractionRepair.error：受控错误代码，不存模型原始异常。
- ExtractionRepair.created_at：任务入队时间。
- ExtractionRepair.Meta.constraints：活动修复唯一约束。
"""
from django.db import models


# 功能：在调用模型前保存邮件原文。
# 逻辑：发现即登记，读取后填充 raw，L1 完成先保存 submission；终态与逐封事件一同提交。
# 约束：原文不可覆盖；权限始终经 mailbox.owner 校验，不存授权凭证。
class StoredMessage(models.Model):
    mailbox = models.ForeignKey("crm.Mailbox", on_delete=models.CASCADE, related_name="stored_messages")
    message_id = models.CharField(max_length=200)
    raw = models.JSONField(default=dict)
    submission = models.JSONField(default=dict)
    status = models.CharField(max_length=16, default="pending")
    prompt_version = models.CharField(max_length=100, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    # 功能：保证增量与历史扫描重复发现不会复制邮件。
    # 逻辑：邮箱与消息 ID 联合唯一。
    # 约束：不同邮箱可保存相同 Gmail ID。
    class Meta:
        constraints = [models.UniqueConstraint(fields=["mailbox", "message_id"], name="crm_stored_message")]


# 功能：标识 Worker 接管并保留旧版本同步审计状态。
# 逻辑：有界同步只创建接管标记，ingestion 据此拒绝旧 CLI 游标双写；旧字段不再推进。
# 约束：与消息登记处于同一事务；保留既存历史字段以免升级删除审计数据，不进行全量补采。
class SyncCheckpoint(models.Model):
    mailbox = models.OneToOneField("crm.Mailbox", primary_key=True, on_delete=models.CASCADE, related_name="checkpoint")
    cursor = models.CharField(max_length=200, default="")
    anchor = models.CharField(max_length=200, default="")
    page_token = models.TextField(default="")
    backfill_complete = models.BooleanField(default=False)


# 功能：保存分析输入的精确邮件来源。
# 逻辑：绑定邮件、抽取记录和复核版本，而非仅依赖公司 revision。
# 约束：只在验证 L2 输入的事务中创建；旧边保留供审计。
class SnapshotSource(models.Model):
    snapshot = models.ForeignKey("crm.AnalysisInput", related_name="sources", on_delete=models.CASCADE)
    email = models.ForeignKey("crm.Email", on_delete=models.CASCADE, related_name="snapshot_sources")
    extraction = models.ForeignKey("crm.Extraction", on_delete=models.PROTECT)
    review_revision = models.PositiveIntegerField()

    # 功能：防止同一输入重复登记来源。
    # 逻辑：快照与邮件唯一。
    # 约束：不同快照可绑定同一抽取。
    class Meta:
        constraints = [models.UniqueConstraint(fields=["snapshot", "email"], name="crm_snapshot_source")]


# 功能：使一个快照及其下游分析与评分失效。
# 逻辑：单独追加失效记录，原始结果和来源边不被改写。
# 约束：失效后不可重新命中缓存；重新生成使用新的公司 revision。
class SnapshotInvalidation(models.Model):
    snapshot = models.OneToOneField("crm.AnalysisInput", related_name="invalidation", on_delete=models.CASCADE)
    reason = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)


# 功能：持久保存人工确认触发的补抽取工作。
# 逻辑：运行时固定原抽取与复核版本，回写前重新检查以拒绝过时结果。
# 约束：不覆盖原抽取；失败需员工明确再次确认业务才重排。
class ExtractionRepair(models.Model):
    email = models.ForeignKey("crm.Email", related_name="repairs", on_delete=models.CASCADE)
    source = models.ForeignKey("crm.Extraction", on_delete=models.PROTECT)
    review_revision = models.PositiveIntegerField()
    status = models.CharField(max_length=16, default="pending")
    lease_until = models.DateTimeField(null=True)
    error = models.CharField(max_length=80, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    # 功能：合并重复确认产生的活动任务。
    # 逻辑：只有 pending/running 参与唯一约束。
    # 约束：失败和完成任务保留历史，可显式创建后继。
    class Meta:
        constraints = [models.UniqueConstraint(fields=["email"], condition=models.Q(status__in=["pending", "running"]), name="crm_active_repair")]
