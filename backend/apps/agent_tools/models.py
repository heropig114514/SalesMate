"""职责：保存独立工具授权、幂等回执和待人工确认提案。
实现：凭证仅存摘要；用户与幂等键唯一；提案冻结工具输入且有期限。
关联：authentication 认证工具 token；services 在事务内维护回执和确认状态。
目录：
- ToolCredential：用户委托的工具权限。
- ToolCall：一次逻辑写入回执。
- ToolCall.Meta：员工内幂等键约束。
- ToolProposal：需要真人确认的冻结变更。
变量索引：
- ToolCredential.id：授权标识。
- ToolCredential.owner：授权用户。
- ToolCredential.name：用户可读名称。
- ToolCredential.digest：令牌 SHA-256 摘要。
- ToolCredential.allowed_tools：明确工具名称白名单。
- ToolCredential.expires_at：授权到期时间。
- ToolCredential.revoked_at：撤销时间。
- ToolCredential.created_at：创建时间。
- ToolCall.id：回执标识。
- ToolCall.owner：调用用户。
- ToolCall.key：调用者生成的幂等 UUID。
- ToolCall.tool：固定工具名称。
- ToolCall.input_hash：输入内容摘要。
- ToolCall.result：冻结执行或提案回执。
- ToolCall.created_at：完成时间。
- ToolCall.Meta.constraints：员工和 key 联合唯一。
- ToolProposal.id：提案标识。
- ToolProposal.owner：必须确认的用户。
- ToolProposal.credential：发起授权；撤销或到期后不能批准。
- ToolProposal.tool：拟执行工具。
- ToolProposal.arguments：冻结输入，包含业务旧版本。
- ToolProposal.status：pending/approved/cancelled。
- ToolProposal.result：确认后的执行结果。
- ToolProposal.expires_at：提案到期时间。
- ToolProposal.created_at：提案创建时间。
"""

import uuid
from django.conf import settings
from django.db import models


# 功能：隔离 Agent 任务凭证与业务操作授权。
# 逻辑：每份授权限定用户、工具名单和有效期。
# 约束：原始 token 只在创建响应返回一次，不存数据库。
class ToolCredential(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    digest = models.CharField(max_length=64, unique=True)
    allowed_tools = models.JSONField()
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)


# 功能：保存逻辑写入的可核对回执。
# 逻辑：同用户相同幂等键只接受相同工具和输入。
# 约束：回执是历史执行快照，不代表关联实体当前状态。
class ToolCall(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    key = models.UUIDField()
    tool = models.CharField(max_length=120)
    input_hash = models.CharField(max_length=64)
    result = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    # 功能：保护员工内幂等身份。
    # 逻辑：数据库保证最终唯一。
    # 约束：服务另行比较输入摘要。
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "key"], name="agent_tools_owner_call_key"
            )
        ]


# 功能：保存需要真人确认的工具调用。
# 逻辑：输入不可经确认请求修改，旧版本冲突时要求重新提出。
# 约束：工具凭证不能批准，取消和到期不会执行业务。
class ToolProposal(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    credential = models.ForeignKey(ToolCredential, null=True, on_delete=models.PROTECT)
    tool = models.CharField(max_length=120)
    arguments = models.JSONField()
    status = models.CharField(max_length=16, default="pending")
    result = models.JSONField(null=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
