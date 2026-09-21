"""职责：为 Agent 查询和批量响应声明可消费的 OpenAPI 结构。
实现：独立响应序列化器描述真实数组与对象；CompanyContext 描述独立评分上下文，邮箱领取响应包含必需冻结范围和重试 ID。
关联：views 用作 Schema 声明，服务的实际字段由契约测试核验。
目录：
- SubmissionResultSerializer：描述单封邮件的提交结果。
- JobResponseSerializer：描述已领取 Job 与并发扩展字段。
- CachedAnalysisResponseSerializer：描述分析缓存元数据。
- GroupingResponseSerializer：描述后端公司归组结果。
- CompanyContextResponseSerializer：描述公司邮件与业务快照。
- MailboxResponseSerializer：描述浏览器邮箱列表行。
- MailboxSyncClaimResponseSerializer：描述 Agent 领取的员工邮箱同步请求。
变量索引：
- CompanyContextResponseSerializer.company_enrichment：后端匹配的实验资料、状态和来源版本，无需额外 Tool 授权。
- CompanyContextResponseSerializer.priority_context：公司级 customer、deal、seller 正式评分资料；未知字段保持缺失。
- SubmissionResultSerializer.dedupe_key：本次提交的邮件天然键。
- SubmissionResultSerializer.company_id：后端归组分配的公司 UUID。
- SubmissionResultSerializer.status：created、updated 或 duplicate 提交结果。
- JobResponseSerializer.job_id：后端任务 UUID。
- JobResponseSerializer.company_id：待分析公司的后端 UUID。
- JobResponseSerializer.trigger：创建任务的业务事件。
- JobResponseSerializer.enqueued_at：任务入队时间。
- JobResponseSerializer.attempt：已领取次数。
- JobResponseSerializer.lease_until：领取租约截止时间。
- JobResponseSerializer.lease_token：领取者写入时须提交的随机凭证。
- JobResponseSerializer.expected_version：任务固定的后端上下文 revision。
- CachedAnalysisResponseSerializer.company_id：当前查询的公司 UUID。
- CachedAnalysisResponseSerializer.input_version：命中或旧分析的输入版本。
- CachedAnalysisResponseSerializer.analysis_prompt_version：缓存分析的提示词版本，无缓存为 null。
- CachedAnalysisResponseSerializer.generated_at：分析生成时间，无缓存为 null。
- CachedAnalysisResponseSerializer.status：缓存元数据中的处理状态。
- CachedAnalysisResponseSerializer.hit：是否匹配当前 revision、输入和指定提示词。
- CachedAnalysisResponseSerializer.analysis：命中时返回的完整 L3 Analysis，未命中为 null。
- GroupingResponseSerializer.company_id：后端公司 UUID。
- GroupingResponseSerializer.company_name：可空公司展示名。
- GroupingResponseSerializer.crm_status：公司建档状态。
- GroupingResponseSerializer.domains：已确认的完整域名数组。
- GroupingResponseSerializer.contacts：联系人邮箱、姓名、往来次数和主要联系人标记。
- GroupingResponseSerializer.member_dedupe_keys：公司成员邮件的完整天然键集合。
- CompanyContextResponseSerializer.company_id：后端公司 UUID。
- CompanyContextResponseSerializer.external_snapshot_version：CRM 外部业务快照版本。
- CompanyContextResponseSerializer.emails：当前标准邮件及抽取事实数组。
- CompanyContextResponseSerializer.customer：权威 CRM 基础字段快照。
- CompanyContextResponseSerializer.tickets：权威工单数组。
- CompanyContextResponseSerializer.quotes：权威报价数组。
- CompanyContextResponseSerializer.orders：权威订单数组。
- MailboxResponseSerializer.mailbox_id：后端业务邮箱 UUID。
- MailboxResponseSerializer.address：业务邮箱展示地址。
- MailboxResponseSerializer.gmail_authorized：当前邮箱是否已经完成 Google OAuth。
- MailboxResponseSerializer.qq_authorized：当前邮箱是否已验证 QQ IMAP 授权码。
- MailboxResponseSerializer.sync_state：业务同步游标及状态。
- MailboxSyncClaimResponseSerializer.authorization：仅向 Agent 返回的 Google authorized user JSON。
- MailboxSyncClaimResponseSerializer.mailbox_address：已经由 Gmail profile 验证的邮箱地址。
- MailboxSyncClaimResponseSerializer.mailbox_id：后端员工业务邮箱 UUID。
- MailboxSyncClaimResponseSerializer.sync_options：用户选择、服务器冻结的时间窗口及本次超量批准。
- MailboxSyncClaimResponseSerializer.message_ids：明确失败重试 ID，普通范围同步为空数组。
- MailboxSyncClaimResponseSerializer.max_results：Gmail 单页读取上限，不是本批总封数。
"""
from rest_framework import serializers as s

from .serializers import EmailSubmissionSerializer, SyncStateSerializer


# 功能：描述单封邮件的提交结果。
# 逻辑：同一响应数组的每项携带天然键、公司 ID 和创建状态。
# 约束：此声明不执行入库，实际去重由 ingestion 负责。
class SubmissionResultSerializer(s.Serializer):
    dedupe_key = s.CharField()
    company_id = s.UUIDField()
    status = s.ChoiceField(choices=["created", "updated", "duplicate"])


# 功能：描述已领取 Job 与并发扩展字段。
# 逻辑：保留 README 原字段并加入 lease_token 和 expected_version。
# 约束：只向成功认证的领取者返回凭证。
class JobResponseSerializer(s.Serializer):
    job_id = s.UUIDField()
    trigger = s.CharField()
    company_id = s.UUIDField()
    enqueued_at = s.DateTimeField()
    attempt = s.IntegerField(min_value=1)
    lease_until = s.DateTimeField()
    lease_token = s.UUIDField()
    expected_version = s.IntegerField(min_value=0)


# 功能：描述分析缓存元数据。
# 逻辑：保留命中状态和可空旧分析信息。
# 约束：hit 的实际判定由 results 服务完成。
class CachedAnalysisResponseSerializer(s.Serializer):
    company_id = s.UUIDField()
    input_version = s.CharField()
    analysis_prompt_version = s.CharField(allow_null=True)
    generated_at = s.DateTimeField(allow_null=True)
    status = s.CharField()
    hit = s.BooleanField()
    analysis = s.DictField(allow_null=True)


# 功能：描述后端公司归组结果。
# 逻辑：联系人和邮件成员范围与 CompanyContext 使用同一 ETag。
# 约束：不让 Agent 根据该结构重新决定归组。
class GroupingResponseSerializer(s.Serializer):
    company_id = s.UUIDField()
    company_name = s.CharField(allow_null=True)
    crm_status = s.ChoiceField(choices=["registered", "unregistered"])
    domains = s.ListField(child=s.CharField())
    contacts = s.ListField(child=s.DictField())
    member_dedupe_keys = s.ListField(child=s.CharField())


# 功能：描述公司邮件与业务快照。
# 逻辑：邮件回显 L1 标准载荷，priority_context 提供权威公司商机与销售方资料，company_enrichment 独立标记实验来源。
# 约束：评分上下文不重复邮件、不混入已保存的 L2 载荷，不凭空补未知数据。
class CompanyContextResponseSerializer(s.Serializer):
    company_id = s.UUIDField()
    external_snapshot_version = s.CharField()
    emails = EmailSubmissionSerializer(many=True)
    customer = s.DictField()
    tickets = s.ListField(child=s.DictField())
    quotes = s.ListField(child=s.DictField())
    orders = s.ListField(child=s.DictField())
    priority_context = s.DictField()
    company_enrichment = s.DictField(required=False)


# 功能：描述浏览器邮箱列表行。
# 逻辑：显示业务标识、Gmail/QQ 独立授权标志与不含凭证的同步状态。
# 约束：返回地址不代表已完成 Gmail OAuth。
class MailboxResponseSerializer(s.Serializer):
    mailbox_id = s.UUIDField()
    address = s.EmailField()
    gmail_authorized = s.BooleanField()
    qq_authorized = s.BooleanField()
    sync_state = s.DictField()


# 功能：描述 Agent 领取到的员工邮箱同步请求。
# 逻辑：完整授权凭证只在 AgentAuthentication 保护的响应中出现；消费者必须执行 sync_options 或明确 message_ids。
# 约束：浏览器邮箱接口不得使用该结构。
class MailboxSyncClaimResponseSerializer(s.Serializer):
    mailbox_id = s.UUIDField()
    mailbox_address = s.EmailField()
    authorization = s.DictField()
    max_results = s.IntegerField(min_value=1, max_value=20)
    sync_options = s.DictField()
    message_ids = s.ListField(child=s.CharField(max_length=200))
