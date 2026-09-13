"""职责：定义邮件理解闭环的持久化实体。
实现：关系字段承担归属与唯一约束，JSON 保存协议原文；邮件分类独立于原文，导入同步批次模型。
关联：ingestion、jobs、results 负责事务写入，selectors 提供授权查询。
目录：
- Mailbox：保存用户拥有的业务邮箱及同步游标。
- Mailbox.Meta：约束同一用户的邮箱地址不重复。
- GmailCredential：保存员工网页 OAuth 授予的 Gmail 只读凭证。
- AgentCredential：保存仅能访问单个用户业务数据的 Agent 服务凭证摘要。
- Company：保存按用户隔离的公司归组及权威 CRM 快照。
- Company.Meta：隔离不同用户的同域公司。
- Contact：保存公司中的联系人身份。
- Contact.Meta：防止同一公司重复建立联系人。
- Email：保存不可变标准邮件。
- Extraction：保存单封邮件的版本化抽取结果。
- Extraction.Meta：限制一个邮件和提示词版本只有一份抽取。
- AnalysisInput：归档 Agent 提交的分析输入与对应后端 revision。
- AnalysisInput.Meta：保证同一输入版本的快照不可重复。
- Analysis：保存 L3 分析的版本化原文。
- Analysis.Meta：保留不同分析提示词版本。
- Score：保存 L4 评分及其规则版本。
- Job：保存公司分析任务及原子领取租约。
变量索引：
- AgentCredential.created_at：数据库记录创建时间
- AgentCredential.digest：高熵 Agent 令牌的 SHA-256 摘要
- AgentCredential.name：实体名称；应用配置中表示模块导入路径
- AgentCredential.owner：已认证业务用户的归属外键，用于数据隔离
- Analysis.Meta.constraints：防止重复实体或重复版本的数据库唯一约束
- Analysis.created_at：数据库记录创建时间
- Analysis.payload：对应协议的完整 JSON 快照
- Analysis.prompt_version：抽取或分析生产者的版本标识
- Analysis.provider：结果来源 rules 或 agent
- Analysis.snapshot：L3 所依据的不可变 L2 快照关系
- AnalysisInput.Meta.constraints：防止重复实体或重复版本的数据库唯一约束
- AnalysisInput.company：所属公司外键，访问时须验证用户归属
- AnalysisInput.created_at：数据库记录创建时间
- AnalysisInput.input_version：Agent 计算并原样提交的输入版本
- AnalysisInput.payload：对应协议的完整 JSON 快照
- AnalysisInput.revision：邮件、事实或业务资料变化时递增的上下文版本
- Company.Meta.constraints：防止重复实体或重复版本的数据库唯一约束
- Company.created_at：数据库记录创建时间
- Company.crm_status：公司 registered 或 unregistered 建档状态
- Company.customer：后端权威 CRM 基础资料快照
- Company.domains：公司组已确认的完整域名列表
- Company.external_version：CRM 外部业务快照版本
- Company.group_key：完整企业域名或公共邮箱联系人地址组成的归组键
- Company.id：实体唯一标识
- Company.name：实体名称；应用配置中表示模块导入路径
- Company.orders：历史订单数组，不能从邮件提及推断
- Company.owner：已认证业务用户的归属外键，用于数据隔离
- Company.quotes：权威报价数组，保留实际外发证据类型
- Company.revision：邮件、事实或业务资料变化时递增的上下文版本
- Company.tickets：权威工单记录数组；当前无编辑入口
- Contact.Meta.constraints：防止重复实体或重复版本的数据库唯一约束
- Contact.company：所属公司外键，访问时须验证用户归属
- Contact.email：联系人邮箱字段；Extraction 中为所属邮件关系
- Contact.name：实体名称；应用配置中表示模块导入路径
- Email.business_classification：business/non_business/needs_review 的当前有效分类。
- Email.classification_source：rule/llm/human 的判断来源。
- Email.classification_reason：可展示的分类依据。
- Email.review_status：人工复核决定，空字符串表示无人工决定。
- Email.reviewed_by：作出人工判断的员工。
- Email.reviewed_at：人工判断时间。
- Email.review_revision：防止并发复核覆盖的版本。
- Email.company：所属公司外键，访问时须验证用户归属
- Email.contact：主要外部联系人外键，messages 反向关系用于往来查询
- Email.dedupe_key：邮箱地址与 Gmail 消息 ID 组成的天然幂等键
- Email.direction：邮件入站 inbound、出站 outbound 或未知 unknown
- Email.mailbox：邮件所属后端业务邮箱
- Email.payload：对应协议的完整 JSON 快照
- Email.received_at：邮件接收时间，用于今日新邮件统计
- Email.sent_at：邮件或业务动作发生的带时区时间
- Extraction.Meta.constraints：防止重复实体或重复版本的数据库唯一约束
- Extraction.created_at：数据库记录创建时间
- Extraction.email：联系人邮箱字段；Extraction 中为所属邮件关系
- Extraction.error：显式失败说明，不作为成功结果展示
- Extraction.facts：可定位的事实结构，失败时按协议为 null
- Extraction.prompt_version：抽取或分析生产者的版本标识
- Extraction.status：当前协议载荷或任务状态，具体允许值见字段声明
- Job.attempt：任务已被领取的次数
- Job.company：所属公司外键，访问时须验证用户归属
- Job.enqueued_at：任务入队时间
- Job.id：实体唯一标识
- Job.lease_token：领取随机凭证，不得写入日志
- Job.lease_until：本次任务租约截止时间
- Job.report：任务最终产出声明及错误信息
- Job.revision：邮件、事实或业务资料变化时递增的上下文版本
- Job.status：当前协议载荷或任务状态，具体允许值见字段声明
- Job.trigger：任务创建的业务事件类型
- Mailbox.Meta.constraints：防止重复实体或重复版本的数据库唯一约束
- Mailbox.address：业务邮箱展示地址，不作为 OAuth 验证证据
- Mailbox.id：实体唯一标识
- Mailbox.owner：已认证业务用户的归属外键，用于数据隔离
- Mailbox.sync_state：不含授权令牌的同步游标状态
- Mailbox.version：同步状态的乐观锁版本
- GmailCredential.authorized_at：首次完成网页授权的时间
- GmailCredential.credentials：Google authorized user JSON，仅供后端与 Agent 路由使用
- GmailCredential.mailbox：与员工业务邮箱的一对一关系
- GmailCredential.updated_at：凭证刷新或重新授权的更新时间
- Score.analysis：评分所属分析外键；序列化器中为四维分析结果
- Score.created_at：数据库记录创建时间
- Score.payload：对应协议的完整 JSON 快照
- Score.score_version：评分规则版本，规则占位与正式 Agent 版本分开
- Score.value：可空的 0–100 跟进优先级
"""
import uuid

from django.conf import settings
from django.db import models


# 功能：保存用户拥有的业务邮箱及同步游标。
# 逻辑：邮箱 ID 由后端创建，用户身份从会话或 Agent 凭证推导。
# 约束：OAuth 凭证保存在独立 GmailCredential 记录中；address 只用于展示。
class Mailbox(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    address = models.EmailField()
    sync_state = models.JSONField(default=dict)
    version = models.PositiveIntegerField(default=0)

    # 功能：约束同一用户的邮箱地址不重复。
    # 逻辑：使用数据库唯一约束处理重复创建。
    # 约束：地址在服务层规范为小写。
    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner", "address"], name="crm_owner_mailbox")]


# 功能：保存员工通过网页 OAuth 授予的 Gmail 只读凭证。
# 逻辑：每个业务邮箱只保留一份当前凭证，Agent 服务通过受保护接口领取同步任务。
# 约束：凭证不会出现在浏览器邮箱列表；当前 MVP 使用数据库 JSON 明文保存。
class GmailCredential(models.Model):
    mailbox = models.OneToOneField(
        Mailbox, related_name="gmail_credential", on_delete=models.CASCADE
    )
    credentials = models.JSONField(default=dict)
    authorized_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


# 功能：保存仅能访问单个用户业务数据的 Agent 服务凭证摘要。
# 逻辑：认证时 SHA-256 比较高熵令牌的摘要，原值仅在创建时交付。
# 约束：不授予浏览器登录或跨用户权限；删除记录即撤销。
class AgentCredential(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    digest = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)


# 功能：保存按用户隔离的公司归组及权威 CRM 快照。
# 逻辑：group_key 使用完整企业域名或公共邮箱联系人地址；revision 标识所有上下文变化。
# 约束：不自动猜测子域或集团多域关系；external_version 仅随 CRM 快照更新。
class Company(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    group_key = models.CharField(max_length=320)
    name = models.CharField(max_length=240, null=True, blank=True)
    domains = models.JSONField(default=list)
    crm_status = models.CharField(max_length=20, default="unregistered")
    customer = models.JSONField(default=dict)
    tickets = models.JSONField(default=list)
    quotes = models.JSONField(default=list)
    orders = models.JSONField(default=list)
    revision = models.PositiveIntegerField(default=0)
    external_version = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    # 功能：隔离不同用户的同域公司。
    # 逻辑：owner 与 group_key 联合唯一。
    # 约束：公司合并需另行设计显式服务，不直接修改 group_key。
    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner", "group_key"], name="crm_owner_group")]


# 功能：保存公司中的联系人身份。
# 逻辑：联系人统计由邮件查询计算，避免独立计数漂移。
# 约束：邮箱不可跨公司重复合并；姓名来自有证据的邮件事实。
class Contact(models.Model):
    company = models.ForeignKey(Company, related_name="contacts", on_delete=models.CASCADE)
    email = models.EmailField()
    name = models.CharField(max_length=240, null=True, blank=True)

    # 功能：防止同一公司重复建立联系人。
    # 逻辑：数据库约束 company 与 email。
    # 约束：修改归组时须同步处理联系人和邮件。
    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "email"], name="crm_company_contact")]


# 功能：保存不可变标准邮件及独立的可变分类、人工复核元数据。
# 逻辑：payload 保留邮件本体；时间和方向用于查询，分类控制可见性，review_revision 保护并发决定。
# 约束：dedupe_key 全局唯一且匹配已授权邮箱和消息 ID；复核不得改写原文，事实另表版本化。
class Email(models.Model):
    business_classification = models.CharField(max_length=24, default="business")
    classification_source = models.CharField(max_length=12, default="rule")
    classification_reason = models.TextField(blank=True, default="")
    review_status = models.CharField(max_length=32, blank=True, default="")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="reviewed_customer_emails")
    reviewed_at = models.DateTimeField(null=True)
    review_revision = models.PositiveIntegerField(default=0)
    dedupe_key = models.CharField(max_length=400, primary_key=True)
    mailbox = models.ForeignKey(Mailbox, related_name="emails", on_delete=models.CASCADE)
    company = models.ForeignKey(Company, related_name="emails", on_delete=models.CASCADE)
    contact = models.ForeignKey(Contact, related_name="messages", on_delete=models.PROTECT, null=True)
    payload = models.JSONField()
    sent_at = models.DateTimeField()
    received_at = models.DateTimeField()
    direction = models.CharField(max_length=12)


# 功能：保存单封邮件的版本化抽取结果。
# 逻辑：相同提示词版本唯一；仅允许 failed 到 completed 的一次补交。
# 约束：完成记录不可覆盖；新版本保留历史并通过创建时间选择当前抽取。
class Extraction(models.Model):
    email = models.ForeignKey(Email, related_name="extractions", on_delete=models.CASCADE)
    prompt_version = models.CharField(max_length=100)
    status = models.CharField(max_length=30)
    facts = models.JSONField(null=True)
    error = models.TextField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # 功能：限制一个邮件和提示词版本只有一份抽取。
    # 逻辑：以联合唯一约束保证事务并发安全。
    # 约束：不以版本字符串字典序判断新旧。
    class Meta:
        constraints = [models.UniqueConstraint(fields=["email", "prompt_version"], name="crm_extract_version")]


# 功能：归档 Agent 提交的分析输入与对应后端 revision。
# 逻辑：原样保存载荷，revision 用于拒绝并发变化后的旧结果。
# 约束：input_version 由 Agent 计算，后端不重定义其哈希算法。
class AnalysisInput(models.Model):
    company = models.ForeignKey(Company, related_name="inputs", on_delete=models.CASCADE)
    input_version = models.CharField(max_length=160)
    revision = models.PositiveIntegerField()
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    # 功能：保证同一输入版本的快照不可重复。
    # 逻辑：公司与输入版本联合唯一。
    # 约束：相同键不同载荷返回冲突。
    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "input_version"], name="crm_input_version")]


# 功能：保存 L3 分析的版本化原文。
# 逻辑：通过 snapshot 绑定来源，provider 显式区分规则占位与 Agent。
# 约束：列表与详情均投影自同一 payload；不把占位声明成模型结论。
class Analysis(models.Model):
    snapshot = models.ForeignKey(AnalysisInput, related_name="analyses", on_delete=models.CASCADE)
    prompt_version = models.CharField(max_length=100)
    payload = models.JSONField()
    provider = models.CharField(max_length=16)
    created_at = models.DateTimeField(auto_now_add=True)

    # 功能：保留不同分析提示词版本。
    # 逻辑：快照与提示词版本联合唯一。
    # 约束：既有成功结果不可被失败结果覆盖。
    class Meta:
        constraints = [models.UniqueConstraint(fields=["snapshot", "prompt_version"], name="crm_analysis_version")]


# 功能：保存 L4 评分及其规则版本。
# 逻辑：绑定具体分析，确保更换分析提示词后不会混用旧评分。
# 约束：允许分数为 null；贡献和校验在序列化器中执行。
class Score(models.Model):
    analysis = models.ForeignKey(Analysis, related_name="scores", on_delete=models.CASCADE)
    payload = models.JSONField()
    score_version = models.CharField(max_length=100)
    value = models.PositiveSmallIntegerField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)


# 功能：保存公司分析任务及原子领取租约。
# 逻辑：公司 revision 变化可建立后继任务；运行任务绑定领取凭证与输入 revision。
# 约束：租约过期显式失败，不隐式重试；用户可重新请求分析。
class Job(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, related_name="jobs", on_delete=models.CASCADE)
    trigger = models.CharField(max_length=40)
    revision = models.PositiveIntegerField()
    status = models.CharField(max_length=16, default="pending")
    enqueued_at = models.DateTimeField(auto_now_add=True)
    attempt = models.PositiveIntegerField(default=0)
    lease_until = models.DateTimeField(null=True)
    lease_token = models.UUIDField(null=True)
    report = models.JSONField(null=True)


from .processing_models import EmailProcessingJob, MailboxSyncRun  # noqa: E402,F401
