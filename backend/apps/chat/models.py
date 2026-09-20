"""职责：持久化回答任务、证据快照和员工维护的内部知识。
实现：复用 sales 会话与消息；独立保存每次工具读取的结果和证据，数据库约束保证会话单任务及唯一回答。
关联：chat.services 维护状态；Agent 仅通过 HTTP 读写本模块。
目录：
- AnswerRequest：一次不可覆盖的回答尝试。
- AnswerRequest.Meta：任务唯一及状态约束。
- Citation：与助手消息对应的有序证据。
- Citation.Meta：引用位置唯一约束。
- ToolRead：请求绑定的不可覆盖工具读取记录。
- KnowledgeEntry：有明确版本的内部知识。
- KnowledgeEntry.Meta：员工知识版本唯一约束。
变量索引：
- AnswerRequest.id：Agent 幂等键。
- AnswerRequest.owner：提交员工。
- AnswerRequest.company：固定的可空客户绑定；空值为通用问答。
- AnswerRequest.conversation：固定私有会话。
- AnswerRequest.user_message：原始问题；多次显式尝试可复用。
- AnswerRequest.assistant_message：每次尝试最多一条不可变回答。
- AnswerRequest.retry_of：显式重试的原失败请求，每个原请求只有一个后继。
- AnswerRequest.status：pending/processing/completed/failed。
- AnswerRequest.created_at：创建时间。
- AnswerRequest.processing_started_at：领取时间。
- AnswerRequest.finished_at：终态时间。
- AnswerRequest.recent_history：领取时冻结的当前问题之前的历史。
- AnswerRequest.context_snapshot：首次读取时冻结的完整 internal 响应。
- AnswerRequest.result：已接收的规范结果，用于精确幂等比较。
- AnswerRequest.chat_prompt_version：实际执行的提示词版本。
- AnswerRequest.error：Agent 提供的错误码和提示，仅检查结构，由生产方负责脱敏。
- AnswerRequest.Meta.constraints：单活动任务和合法状态数据库约束。
- Citation.request：所属回答请求。
- Citation.position：引用数组位置，自 1 开始，不验证正文编号。
- Citation.source_id：Agent 声明的来源标识，不保证已登记到本请求快照。
- Citation.source_type：来源类别。
- Citation.title_or_label：Agent 回报的来源标题。
- Citation.content：匹配本请求上下文或工具记录的证据正文；未匹配时为空。
- ToolRead.id：单次成功读取 UUID，也是来源命名空间。
- ToolRead.request：所属回答请求，员工身份从请求继承。
- ToolRead.tool：实际执行的只读工具名称。
- ToolRead.arguments：实际经过 Schema 校验的参数快照。
- ToolRead.result：原业务工具回执，保留分页、状态、版本与数据。
- ToolRead.evidence_items：本次返回的四字段证据数组，不覆盖旧来源。
- ToolRead.created_at：成功读取记录的创建时间。
- Citation.Meta.constraints：请求内引用位置唯一。
- KnowledgeEntry.id：知识记录标识。
- KnowledgeEntry.owner：可消费本条知识的员工。
- KnowledgeEntry.source_key：人工稳定业务标识。
- KnowledgeEntry.version：显式资料版本，不允许覆盖相同版本正文。
- KnowledgeEntry.title：可引用标题。
- KnowledgeEntry.content：经维护者确认的资料正文。
- KnowledgeEntry.active：是否参与新请求检索。
- KnowledgeEntry.created_at：版本导入时间。
- KnowledgeEntry.Meta.constraints：同员工、标识及版本唯一。
"""

import uuid

from django.conf import settings
from django.db import models


# 功能：记录单次回答尝试与权威绑定。
# 逻辑：空公司标识对应通用会话，非空对应固定客户；活动状态在同一会话内唯一；用户消息可被失败后的新尝试复用。
# 约束：终态不可覆盖，快照和结果仅由受保护服务维护。
class AnswerRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    company = models.ForeignKey(
        "crm.Company", on_delete=models.PROTECT, null=True, blank=True
    )
    conversation = models.ForeignKey("sales.Conversation", on_delete=models.PROTECT)
    user_message = models.ForeignKey(
        "sales.Message", on_delete=models.PROTECT, related_name="chat_requests"
    )
    assistant_message = models.OneToOneField(
        "sales.Message", null=True, on_delete=models.PROTECT, related_name="chat_answer"
    )
    retry_of = models.OneToOneField(
        "self", null=True, on_delete=models.PROTECT, related_name="retry_request"
    )
    status = models.CharField(max_length=20, default="pending", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    processing_started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    recent_history = models.JSONField(default=list)
    context_snapshot = models.JSONField(null=True)
    result = models.JSONField(null=True)
    chat_prompt_version = models.CharField(max_length=100, blank=True)
    error = models.JSONField(null=True)

    # 功能：声明活动请求和枚举约束。
    # 逻辑：数据库阻止并发提交绕过会话串行性。
    # 约束：授权与跨模型绑定仍由服务检查。
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["conversation"],
                condition=models.Q(status__in=["pending", "processing"]),
                name="chat_one_active_conversation",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["pending", "processing", "completed", "failed"]
                ),
                name="chat_request_status",
            ),
        ]


# 功能：保存回答声明的引用及可匹配的证据。
# 逻辑：仅从本请求上下文或工具记录附加正文；未登记来源保留元数据与空正文，不接受 Agent 自报正文。
# 约束：与助手消息和请求终态在同一事务创建。
class Citation(models.Model):
    request = models.ForeignKey(
        AnswerRequest, on_delete=models.PROTECT, related_name="citations"
    )
    position = models.PositiveIntegerField()
    source_id = models.TextField()
    source_type = models.CharField(max_length=80)
    title_or_label = models.TextField()
    content = models.TextField()

    # 功能：保证一个引用编号只对应一条证据。
    # 逻辑：请求和位置联合唯一。
    # 约束：引用必须从 1 连续编号，由回报服务生成。
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["request", "position"], name="chat_citation_position"
            )
        ]


# 功能：保存一次成功工具读取及本次实际返回的来源正文。
# 逻辑：每次读取创建新 UUID，不覆盖已有行；与请求状态锁处于同一事务，失败不登记。
# 约束：只由聊天只读服务创建，浏览器不能直接读取；公司后续修改不会刷新本记录。
class ToolRead(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(
        AnswerRequest, on_delete=models.PROTECT, related_name="tool_reads"
    )
    tool = models.CharField(max_length=120)
    arguments = models.JSONField()
    result = models.JSONField()
    evidence_items = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)


# 功能：保存明确导入的内部知识版本。
# 逻辑：同业务标识的新版本停用旧版本，旧快照仍保留原证据。
# 约束：不预填制度，不调用外部检索或模型。
class KnowledgeEntry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    source_key = models.CharField(max_length=160)
    version = models.CharField(max_length=80)
    title = models.CharField(max_length=240)
    content = models.TextField()
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # 功能：限制知识版本身份。
    # 逻辑：同员工的同标识同版本只保存一次。
    # 约束：服务另行禁止不同正文复用同一版本。
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "source_key", "version"], name="chat_knowledge_version"
            )
        ]
