"""职责：定义销售业务、团队共享和助手操作的关系 Schema。
实现：可编辑业务采用 UUID、revision 与归档；销售方画像按 owner 保存，商机产品显式录入；任务及外部动作采用专用约束。
关联：sales.services 负责事务与校验，crm 保持私人邮件和 Agent 分析协议。
目录：
- Record：可归档的版本化业务记录基类。
- Record.Meta：声明抽象性或数据库唯一及数值约束。
- CompanyRecord：关联客户和负责人的业务记录基类。
- CompanyRecord.Meta：声明抽象性或数据库唯一及数值约束。
- CompanySettings：客户生命周期和人工主要联系人设置。
- CompanyAlias：人工确认的域名或联系人归组映射。
- CompanyAlias.Meta：声明抽象性或数据库唯一及数值约束。
- ContactProfile：人工联系人补充资料。
- Team：拥有明确管理者的业务团队。
- Membership：团队成员及角色。
- Membership.Meta：声明抽象性或数据库唯一及数值约束。
- CompanyGrant：公司业务记录共享授权。
- CompanyGrant.Meta：声明抽象性或数据库唯一及数值约束。
- Product：商品目录与人工库存记录。
- Product.Meta：声明抽象性或数据库唯一及数值约束。
- Ticket：客户服务工单。
- Opportunity：销售商机与管线。
- SellerProfile：保存 owner 隔离的销售方目标画像。
- Quote：有审核与真实外发证据的报价单。
- Quote.Meta：声明抽象性或数据库唯一及数值约束。
- QuoteLine：报价明细快照。
- QuoteLine.Meta：声明抽象性或数据库唯一及数值约束。
- SalesOrder：客户订单及确认状态。
- SalesOrder.Meta：声明抽象性或数据库唯一及数值约束。
- OrderLine：订单明细快照。
- OrderLine.Meta：声明抽象性或数据库唯一及数值约束。
- FollowUp：客户跟进与到期提醒。
- Conversation：员工自己的通用或客户助手会话。
- Message：不可变会话消息。
- Message.Meta：声明抽象性或数据库唯一及数值约束。
- Draft：私有会话中的可编辑草稿。
- ToolAction：明确确认的外部工具动作与执行状态。
- ToolAction.Meta：声明抽象性或数据库唯一及数值约束。
- Attachment：员工私有文件及客户关联。
- AuditEvent：仅追加的业务操作记录。
- Notification：应用内到期提醒。
- Notification.Meta：声明抽象性或数据库唯一及数值约束。
- Connection：保存单独授权的外部服务加密凭证。
- Connection.Meta：限制员工每个提供方和账号只有一份连接。
变量索引：
- SellerProfile.owner：销售方资料的唯一业务所有者，不能跨 owner 共享统计。
- SellerProfile.revision：销售方资料的乐观锁版本。
- SellerProfile.profile：可缺失的目标行业、规模、地区和 IANA 时区，不存模型猜测。
- SellerProfile.updated_at：最后一次显式更新的时间。
- Opportunity.product_names：显式录入的规范产品名称列表，null 或空数组表示资料缺失。
- Connection.provider：gmail、qq 或 calendar 服务提供方。
- Connection.account：外部账号或日历连接名称。
- Connection.encrypted_credentials：Fernet 加密的 Google 授权 JSON 或 QQ 授权码，浏览器不可读取。
- Connection.Meta.constraints：连接身份联合唯一约束。
- Record.id：实体 UUID。
- Record.owner：权威业务归属，禁止客户端指定。
- Record.revision：乐观锁版本。
- Record.archived：软归档状态。
- Record.created_at：创建时间。
- Record.updated_at：最后修改时间。
- Record.Meta.abstract：不生成基类数据表。
- CompanyRecord.company：关联客户。
- CompanyRecord.assigned_to：已授权的业务负责人。
- CompanyRecord.Meta.abstract：不生成基类数据表。
- CompanySettings.company：对应原客户。
- CompanySettings.primary_contact：人工选定的同公司联系人。
- CompanySettings.notes：人工客户备注。
- CompanyAlias.company：归组目标。
- CompanyAlias.group_key：domain:域名或contact:邮箱的精确归组键。
- CompanyAlias.Meta.constraints：数据库并发下执行唯一性或金额边界校验。
- ContactProfile.contact：原联系人。
- ContactProfile.title：人工确认职位。
- ContactProfile.phone：联系电话。
- ContactProfile.notes：联系人备注。
- Team.name：团队名称。
- Membership.team：所属团队。
- Membership.user：成员账号。
- Membership.role：成员角色。
- Membership.Meta.constraints：数据库并发下执行唯一性或金额边界校验。
- CompanyGrant.company：被授权公司。
- CompanyGrant.team：获授权团队。
- CompanyGrant.role：团队在该公司的权限上限。
- CompanyGrant.Meta.constraints：数据库并发下执行唯一性或金额边界校验。
- Product.sku：员工目录内商品编码。
- Product.name：商品名称。
- Product.description：商品说明。
- Product.currency：明确 ISO 三字母币种。
- Product.unit_price：目录单价。
- Product.stock_quantity：人工记录库存，未知为 null。
- Product.Meta.constraints：数据库并发下执行唯一性或金额边界校验。
- Ticket.title：工单主题。
- Ticket.description：问题描述。
- Ticket.status：open/in_progress/resolved/closed。
- Ticket.priority：人工处理优先级。
- Ticket.due_at：约定截止时间。
- Opportunity.title：商机名称。
- Opportunity.description：商机说明。
- Opportunity.status：new/qualified/proposal/won/lost。
- Opportunity.amount：预估商机额，未知为 null。
- Opportunity.currency：金额币种。
- Opportunity.expected_close：预计关闭日期。
- Quote.number：归属员工范围内报价编号。
- Quote.status：draft/approved/sent/accepted/rejected。
- Quote.currency：整张报价币种。
- Quote.valid_until：报价有效期。
- Quote.notes：报价条款说明。
- Quote.sent_at：实际外发成功时间。
- Quote.external_message_id：真实发送服务返回的邮件标识。
- Quote.Meta.constraints：数据库并发下执行唯一性或金额边界校验。
- QuoteLine.quote：所属报价。
- QuoteLine.product：可选目录商品。
- QuoteLine.description：冻结的商品或服务描述。
- QuoteLine.quantity：报价数量。
- QuoteLine.unit_price：本行明确单价。
- QuoteLine.discount：整行折扣金额。
- QuoteLine.Meta.constraints：数据库并发下执行唯一性或金额边界校验。
- SalesOrder.number：归属员工范围内订单编号。
- SalesOrder.quote：可选来源报价。
- SalesOrder.currency：整张订单币种。
- SalesOrder.status：draft/confirmed/fulfilled/cancelled。
- SalesOrder.notes：订单说明。
- SalesOrder.confirmed_at：人工确认订单时间。
- SalesOrder.Meta.constraints：数据库并发下执行唯一性或金额边界校验。
- OrderLine.order：所属订单。
- OrderLine.product：可选来源商品。
- OrderLine.description：订单商品描述。
- OrderLine.quantity：订单数量。
- OrderLine.unit_price：确认单价。
- OrderLine.discount：整行折扣金额。
- OrderLine.Meta.constraints：数据库并发下执行唯一性或金额边界校验。
- FollowUp.title：跟进事项。
- FollowUp.description：跟进说明。
- FollowUp.due_at：到期时间。
- FollowUp.status：open/completed/cancelled。
- Conversation.company：可空客户；空值表示通用会话。
- Conversation.title：会话显示标题。
- Message.conversation：所属会话。
- Message.role：消息来源角色。
- Message.content：消息正文。
- Message.client_key：客户端幂等键。
- Message.Meta.constraints：数据库并发下执行唯一性或金额边界校验。
- Draft.conversation：所属会话。
- Draft.kind：草稿用途。
- Draft.subject：邮件主题。
- Draft.content：草稿正文。
- Draft.recipients：经接口校验的收件人邮箱数组。
- ToolAction.company：操作客户。
- ToolAction.conversation：可选会话来源。
- ToolAction.tool：允许的外部工具。
- ToolAction.parameters：确认时冻结的完整参数。
- ToolAction.status：待确认/approved/running/succeeded/failed/uncertain/cancelled。
- ToolAction.idempotency_key：员工范围的动作幂等键。
- ToolAction.approved_at：明确确认时间。
- ToolAction.started_at：开始执行时间。
- ToolAction.finished_at：终止时间。
- ToolAction.result：外部服务返回的安全结果。
- ToolAction.error：错误码与可操作诊断。
- ToolAction.Meta.constraints：数据库并发下执行唯一性或金额边界校验。
- Attachment.company：关联客户。
- Attachment.name：上传时文件名，下载使用安全名称。
- Attachment.storage_key：私有相对存储键。
- Attachment.content_type：上传方声明的媒体类型，仅作元数据。
- Attachment.size：实际字节数。
- Attachment.sha256：内容校验摘要。
- AuditEvent.id：审计标识。
- AuditEvent.owner：记录所属业务空间。
- AuditEvent.actor：实际操作者。
- AuditEvent.company：可选业务客户。
- AuditEvent.event：事件类型。
- AuditEvent.object_type：对象模型名。
- AuditEvent.object_id：对象标识。
- AuditEvent.changes：受控的状态或字段名变化，不含正文。
- AuditEvent.created_at：事件发生时间。
- Notification.follow_up：来源跟进任务。
- Notification.source_revision：产生提醒的跟进版本。
- Notification.title：提醒显示文本。
- Notification.read_at：已读时间。
- Notification.Meta.constraints：数据库并发下执行唯一性或金额边界校验。
"""

import uuid

from django.conf import settings
from django.db import models


# 功能：可归档的版本化业务记录基类。
# 逻辑：抽象字段统一身份、归属、版本和时间；状态变更由事务服务校验。
# 约束：通过授权事务服务修改；字段值不代表外部动作已经完成。
class Record(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    revision = models.PositiveIntegerField(default=0)
    archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # 功能：声明模型数据库约束。
    # 逻辑：由 Django 迁移生成一致表结构。
    # 约束：不代替服务层权限与跨实体校验。
    class Meta:
        abstract = True


# 功能：关联客户和负责人的业务记录基类。
# 逻辑：所属员工取公司 owner，负责人必须拥有公司业务编辑权限。
# 约束：通过授权事务服务修改；字段值不代表外部动作已经完成。
class CompanyRecord(Record):
    company = models.ForeignKey(
        "crm.Company", on_delete=models.PROTECT, related_name="%(class)s_records"
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    # 功能：声明模型数据库约束。
    # 逻辑：由 Django 迁移生成一致表结构。
    # 约束：不代替服务层权限与跨实体校验。
    class Meta:
        abstract = True


# 功能：客户生命周期和人工主要联系人设置。
# 逻辑：原公司及邮件继续保留，归档不删除证据。
# 约束：通过授权事务服务修改；字段值不代表外部动作已经完成。
class CompanySettings(Record):
    company = models.OneToOneField(
        "crm.Company", on_delete=models.PROTECT, related_name="business_settings"
    )
    primary_contact = models.ForeignKey(
        "crm.Contact",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    notes = models.TextField(blank=True)


# 功能：人工确认的域名或联系人归组映射。
# 逻辑：按原 owner 和 group_key 精确命中；不推断集团关系。
# 约束：通过授权事务服务修改；字段值不代表外部动作已经完成。
class CompanyAlias(Record):
    company = models.ForeignKey(
        "crm.Company", on_delete=models.PROTECT, related_name="group_aliases"
    )
    group_key = models.CharField(max_length=320)

    # 功能：声明模型数据库约束。
    # 逻辑：由 Django 迁移生成一致表结构。
    # 约束：不代替服务层权限与跨实体校验。
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "group_key"], name="sales_owner_alias"
            )
        ]


# 功能：人工联系人补充资料。
# 逻辑：补充信息与邮件抽取事实分别保存，原姓名由显式联系人编辑管理。
# 约束：通过授权事务服务修改；字段值不代表外部动作已经完成。
class ContactProfile(Record):
    contact = models.OneToOneField(
        "crm.Contact", on_delete=models.PROTECT, related_name="business_profile"
    )
    title = models.CharField(max_length=240, blank=True)
    phone = models.CharField(max_length=80, blank=True)
    notes = models.TextField(blank=True)


# 功能：拥有明确管理者的业务团队。
# 逻辑：创建者为 owner，团队不会自动获得任何邮箱权限。
# 约束：通过授权事务服务修改；字段值不代表外部动作已经完成。
class Team(Record):
    name = models.CharField(max_length=160)


# 功能：团队成员及角色。
# 逻辑：manager 管理成员，editor 编辑授权业务，viewer 只读。
# 约束：通过授权事务服务修改；字段值不代表外部动作已经完成。
class Membership(Record):
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sales_memberships",
    )
    role = models.CharField(
        max_length=16,
        choices=[("viewer", "只读"), ("editor", "编辑"), ("manager", "管理")],
    )

    # 功能：声明模型数据库约束。
    # 逻辑：由 Django 迁移生成一致表结构。
    # 约束：不代替服务层权限与跨实体校验。
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["team", "user"], name="sales_team_member")
        ]


# 功能：公司业务记录共享授权。
# 逻辑：公司所有者显式授权给团队，权限不覆盖私人邮件或 Agent 接口。
# 约束：通过授权事务服务修改；字段值不代表外部动作已经完成。
class CompanyGrant(Record):
    company = models.ForeignKey(
        "crm.Company", on_delete=models.PROTECT, related_name="business_grants"
    )
    team = models.ForeignKey(
        Team, on_delete=models.PROTECT, related_name="company_grants"
    )
    role = models.CharField(
        max_length=16, choices=[("viewer", "只读"), ("editor", "编辑")]
    )

    # 功能：声明模型数据库约束。
    # 逻辑：由 Django 迁移生成一致表结构。
    # 约束：不代替服务层权限与跨实体校验。
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "team"], name="sales_company_team"
            )
        ]


# 功能：商品目录与人工库存记录。
# 逻辑：价格明确币种，历史单据保存独立快照；库存不自动代表预留或履约。
# 约束：通过授权事务服务修改；字段值不代表外部动作已经完成。
class Product(Record):
    sku = models.CharField(max_length=100)
    name = models.CharField(max_length=240)
    description = models.TextField(blank=True)
    currency = models.CharField(max_length=3)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)
    stock_quantity = models.DecimalField(
        max_digits=18, decimal_places=3, null=True, blank=True
    )

    # 功能：声明模型数据库约束。
    # 逻辑：由 Django 迁移生成一致表结构。
    # 约束：不代替服务层权限与跨实体校验。
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["owner", "sku"], name="sales_owner_sku"),
            models.CheckConstraint(
                condition=models.Q(unit_price__gte=0), name="sales_product_price"
            ),
        ]


# 功能：客户服务工单。
# 逻辑：状态由显式转换接口推进，不从邮件提及推断已处理。
# 约束：通过授权事务服务修改；字段值不代表外部动作已经完成。
class Ticket(CompanyRecord):
    title = models.CharField(max_length=240)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, default="open", db_index=True)
    priority = models.CharField(
        max_length=16,
        choices=[("low", "低"), ("normal", "普通"), ("high", "高")],
        default="normal",
    )
    due_at = models.DateTimeField(null=True, blank=True)


# 功能：销售商机与管线。
# 逻辑：只汇总明确金额，产品名称由用户显式录入，won/lost 为用户显式声明。
# 约束：通过授权事务服务修改；字段值不代表外部动作已经完成。
class Opportunity(CompanyRecord):
    title = models.CharField(max_length=240)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, default="new", db_index=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3)
    expected_close = models.DateField(null=True, blank=True)
    product_names = models.JSONField(null=True, blank=True)


# 功能：保存 owner 隔离的销售方目标画像。
# 逻辑：一名业务所有者对应一份带版本的配置；JSON 仅接收专门接口验证后的目标条件。
# 约束：无默认业务画像；均值、产品目录和相似赢单由权威数据计算，不由此表人工填充。
class SellerProfile(models.Model):
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, primary_key=True, on_delete=models.CASCADE)
    revision = models.PositiveIntegerField(default=0)
    profile = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)


# 功能：有审核与真实外发证据的报价单。
# 逻辑：draft 可编辑，审核后内容冻结；sent 只能由真实发送结果写入。
# 约束：通过授权事务服务修改；字段值不代表外部动作已经完成。
class Quote(CompanyRecord):
    number = models.CharField(max_length=100)
    status = models.CharField(max_length=20, default="draft", db_index=True)
    currency = models.CharField(max_length=3)
    valid_until = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    external_message_id = models.CharField(max_length=200, blank=True)

    # 功能：声明模型数据库约束。
    # 逻辑：由 Django 迁移生成一致表结构。
    # 约束：不代替服务层权限与跨实体校验。
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "number"], name="sales_quote_number"
            )
        ]


# 功能：报价明细快照。
# 逻辑：金额按数量乘单价减整行折扣计算，不推断税费或汇率。
# 约束：通过授权事务服务修改；字段值不代表外部动作已经完成。
class QuoteLine(Record):
    quote = models.ForeignKey(Quote, on_delete=models.PROTECT, related_name="lines")
    product = models.ForeignKey(
        Product, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    description = models.CharField(max_length=500)
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)
    discount = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    # 功能：声明模型数据库约束。
    # 逻辑：由 Django 迁移生成一致表结构。
    # 约束：不代替服务层权限与跨实体校验。
    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0)
                & models.Q(unit_price__gte=0)
                & models.Q(discount__gte=0),
                name="sales_quote_line_values",
            )
        ]


# 功能：客户订单及确认状态。
# 逻辑：draft 不进入 Agent 历史订单投影，confirmed 后冻结交易内容。
# 约束：通过授权事务服务修改；字段值不代表外部动作已经完成。
class SalesOrder(CompanyRecord):
    number = models.CharField(max_length=100)
    quote = models.ForeignKey(
        Quote, null=True, blank=True, on_delete=models.PROTECT, related_name="orders"
    )
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=20, default="draft", db_index=True)
    notes = models.TextField(blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    # 功能：声明模型数据库约束。
    # 逻辑：由 Django 迁移生成一致表结构。
    # 约束：不代替服务层权限与跨实体校验。
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "number"], name="sales_order_number"
            )
        ]


# 功能：订单明细快照。
# 逻辑：确认后不可编辑，目录变化不追溯修改单据。
# 约束：通过授权事务服务修改；字段值不代表外部动作已经完成。
class OrderLine(Record):
    order = models.ForeignKey(
        SalesOrder, on_delete=models.PROTECT, related_name="lines"
    )
    product = models.ForeignKey(
        Product, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    description = models.CharField(max_length=500)
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)
    discount = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    # 功能：声明模型数据库约束。
    # 逻辑：由 Django 迁移生成一致表结构。
    # 约束：不代替服务层权限与跨实体校验。
    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0)
                & models.Q(unit_price__gte=0)
                & models.Q(discount__gte=0),
                name="sales_order_line_values",
            )
        ]


# 功能：客户跟进与到期提醒。
# 逻辑：后台只创建应用内提醒，不自动发送对外消息。
# 约束：通过授权事务服务修改；字段值不代表外部动作已经完成。
class FollowUp(CompanyRecord):
    title = models.CharField(max_length=240)
    description = models.TextField(blank=True)
    due_at = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=20, default="open", db_index=True)


# 功能：员工自己的通用或客户助手会话。
# 逻辑：空公司表示通用聊天；客户会话不随业务共享，消息不可伪造为 Agent 输出。
# 约束：通过授权事务服务修改；字段值不代表外部动作已经完成。
class Conversation(Record):
    company = models.ForeignKey(
        "crm.Company",
        on_delete=models.PROTECT,
        related_name="conversations",
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=240, default="新对话")


# 功能：不可变会话消息。
# 逻辑：当前浏览器只允许提交用户消息；模型回复由未来受保护集成提供。
# 约束：通过授权事务服务修改；字段值不代表外部动作已经完成。
class Message(Record):
    conversation = models.ForeignKey(
        Conversation, on_delete=models.PROTECT, related_name="messages"
    )
    role = models.CharField(max_length=20, default="user")
    content = models.TextField()
    client_key = models.UUIDField()

    # 功能：声明模型数据库约束。
    # 逻辑：由 Django 迁移生成一致表结构。
    # 约束：不代替服务层权限与跨实体校验。
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "client_key"], name="sales_message_key"
            )
        ]


# 功能：私有会话中的可编辑草稿。
# 逻辑：浏览器输入与邮件草稿分开，只有显式确认动作可执行外发。
# 约束：通过授权事务服务修改；字段值不代表外部动作已经完成。
class Draft(Record):
    conversation = models.ForeignKey(
        Conversation, on_delete=models.PROTECT, related_name="drafts"
    )
    kind = models.CharField(
        max_length=16, choices=[("chat", "聊天输入"), ("email", "邮件草稿")]
    )
    subject = models.CharField(max_length=1000, blank=True)
    content = models.TextField(blank=True)
    recipients = models.JSONField(default=list, blank=True)


# 功能：明确确认的外部工具动作与执行状态。
# 逻辑：支持 Gmail/QQ 发信及日历创建；准备时冻结参数，后台仅执行显式批准记录，未知结果不自动重试。
# 约束：通过授权事务服务修改；字段值不代表外部动作已经完成。
class ToolAction(Record):
    company = models.ForeignKey(
        "crm.Company", on_delete=models.PROTECT, related_name="tool_actions"
    )
    conversation = models.ForeignKey(
        Conversation,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="actions",
    )
    tool = models.CharField(
        max_length=40,
        choices=[
            ("gmail.send", "Gmail 发信"),
            ("qq.send", "QQ 发信"),
            ("calendar.create", "创建日历会议"),
        ],
    )
    parameters = models.JSONField()
    status = models.CharField(
        max_length=32, default="pending_confirmation", db_index=True
    )
    idempotency_key = models.UUIDField()
    approved_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    result = models.JSONField(null=True, blank=True)
    error = models.JSONField(null=True, blank=True)

    # 功能：声明模型数据库约束。
    # 逻辑：由 Django 迁移生成一致表结构。
    # 约束：不代替服务层权限与跨实体校验。
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "idempotency_key"], name="sales_action_key"
            )
        ]


# 功能：员工私有文件及客户关联。
# 逻辑：文件经认证接口下载，不暴露本地路径，不执行上传内容。
# 约束：通过授权事务服务修改；字段值不代表外部动作已经完成。
class Attachment(Record):
    company = models.ForeignKey(
        "crm.Company", on_delete=models.PROTECT, related_name="attachments"
    )
    name = models.CharField(max_length=255)
    storage_key = models.CharField(max_length=500)
    content_type = models.CharField(max_length=150)
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)


# 功能：仅追加的业务操作记录。
# 逻辑：记录操作者、对象标识和状态变化，不复制秘密或正文。
# 约束：仅经事务追加；API 不提供修改或删除，数据库管理员仍须遵循审计保留策略。
class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    company = models.ForeignKey(
        "crm.Company", null=True, on_delete=models.PROTECT, related_name="audit_events"
    )
    event = models.CharField(max_length=80)
    object_type = models.CharField(max_length=80)
    object_id = models.CharField(max_length=400)
    changes = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


# 功能：应用内到期提醒。
# 逻辑：每个负责人和跟进版本只创建一条提醒，后台轮询不重复创建。
# 约束：通过授权事务服务修改；字段值不代表外部动作已经完成。
class Notification(Record):
    follow_up = models.ForeignKey(
        FollowUp, on_delete=models.PROTECT, related_name="notifications"
    )
    source_revision = models.PositiveIntegerField()
    title = models.CharField(max_length=240)
    read_at = models.DateTimeField(null=True, blank=True)

    # 功能：声明模型数据库约束。
    # 逻辑：由 Django 迁移生成一致表结构。
    # 约束：不代替服务层权限与跨实体校验。
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "follow_up", "source_revision"],
                name="sales_reminder_unique",
            )
        ]


# 功能：保存单独授权的外部服务加密凭证。
# 逻辑：用 Fernet 加密 Google OAuth 或 QQ 授权码载荷，原只读同步连接不自动扩权。
# 约束：浏览器只可查看连接状态，缺少密钥时明确失败，不回退为明文。
class Connection(Record):
    provider = models.CharField(
        max_length=16,
        choices=[
            ("gmail", "Gmail 发信"),
            ("qq", "QQ 发信"),
            ("calendar", "Google 日历"),
        ],
    )
    account = models.CharField(max_length=320)
    encrypted_credentials = models.TextField()

    # 功能：声明外部连接身份唯一性。
    # 逻辑：员工、提供方、账号联合唯一。
    # 约束：更新授权替换同身份连接，不产生额外访问主体。
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "provider", "account"],
                name="sales_connection_identity",
            )
        ]
