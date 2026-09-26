"""职责：为全业务图投影和不完整外部输入提供同一份受控数据库 schema。
实现：显式声明业务模型归属，从 Django 字段生成字段及外键契约；排除账号身份、凭据和运行队列。
关联：projection 加载业务来源；semantic_contract 校验输入；迁移为本目录的固定表集合安装捕获。
目录：
- catalog：返回业务类型、公开字段和外键目标。
- source_models：解析业务模型与所有者查询路径。
- public_fields：筛选可用于图谱的实际字段。
- label_for：为源记录选取稳定可读标签。
- mail_text：构造邮件语义输入的固定原文。
变量索引：
- SOURCES：业务模型到严格 owner 路径；不复用实验跨账号权限。
- PRIVATE_FIELDS：不得作为外部 schema 输入或模型上下文的运行/权限字段。
- LABEL_FIELDS：按优先级选取实体标签的字段。
"""
from django.apps import apps

SOURCES = {
    "accounts.companyprofile": "owner_id", "accounts.salessetup": "owner_id", "accounts.setupdocument": "owner_id",
    "crm.company": "owner_id", "crm.contact": "company__owner_id", "crm.mailbox": "owner_id",
    "crm.email": "mailbox__owner_id", "crm.storedmessage": "mailbox__owner_id",
    "crm.extraction": "email__mailbox__owner_id", "crm.analysisinput": "company__owner_id",
    "crm.analysis": "snapshot__company__owner_id", "crm.score": "analysis__snapshot__company__owner_id",
    "crm.snapshotsource": "snapshot__company__owner_id", "crm.snapshotinvalidation": "snapshot__company__owner_id",
    "sales.companysettings": "owner_id", "sales.companyalias": "owner_id", "sales.contactprofile": "owner_id",
    "sales.team": "owner_id", "sales.membership": "owner_id", "sales.companygrant": "owner_id",
    "sales.product": "owner_id", "sales.ticket": "owner_id", "sales.opportunity": "owner_id",
    "sales.sellerprofile": "owner_id", "sales.quote": "owner_id", "sales.quoteline": "owner_id",
    "sales.salesorder": "owner_id", "sales.orderline": "owner_id", "sales.followup": "owner_id",
    "sales.conversation": "owner_id", "sales.message": "owner_id", "sales.draft": "owner_id",
    "sales.toolaction": "owner_id", "sales.attachment": "owner_id", "sales.auditevent": "owner_id",
    "sales.notification": "owner_id", "sales.connection": "owner_id", "sales.worldevent": "owner_id",
    "sales.worldnews": "owner_id", "sales.opportunitysignal": "owner_id", "sales.opportunitypriority": "owner_id",
    "vectors.vectordocument": "owner_id", "chat.knowledgeentry": "owner_id", "chat.answerrequest": "owner_id",
    "chat.citation": "request__owner_id", "chat.toolread": "request__owner_id",
    "agent_tools.toolcall": "owner_id", "agent_tools.toolproposal": "owner_id",
}
PRIVATE_FIELDS = {"owner", "actor", "assigned_to", "reviewed_by", "user", "credential", "encrypted_credentials",
                  "storage_key", "embedding", "error", "lease_token", "sync_state"}
LABEL_FIELDS = ("name", "company_name", "title", "title_or_label", "number", "subject", "email", "address", "source_key", "group_key", "event", "tool", "namespace")


# 功能：解析被明确纳入的业务来源。
# 输入：无外部参数；读取 Django 应用注册表。
# 输出：模型标签到模型和 owner 查询路径的字典。
# 逻辑：未注册模型直接失败，避免 schema 静默缺失。
# 约束：不枚举凭据、账户身份、同步检查点或任务队列。
def source_models():
    return {kind: (apps.get_model(kind), path) for kind, path in SOURCES.items()}


# 功能：列出业务 schema 的可建模字段。
# 输入：`model` 为已登记 Django 模型。
# 输出：实际非主键字段列表，关系仅允许指向已登记业务类型。
# 逻辑：排除权限和凭据字段；主键通过外部 source_id 单独表达。
# 约束：不把省略字段填入模型默认值；JSON 字段的嵌套结构保持原数据。
def public_fields(model):
    return [field for field in model._meta.fields if not field.primary_key and field.name not in PRIVATE_FIELDS
            and (not field.is_relation or field.related_model._meta.label_lower in SOURCES)]


# 功能：导出与数据库一致的输入契约。
# 输入：无外部参数；读取受控业务模型元数据。
# 输出：模型到字段类型、可空性、外键目标和枚举值的映射。
# 逻辑：字段名称来自实际声明，所有字段在不完整观察中均可省略。
# 约束：这是图谱观察输入 schema，不是业务写入或权限授权接口。
def catalog():
    return {kind: {field.name: {"type": field.get_internal_type(), "nullable": field.null,
                              **({"target": field.related_model._meta.label_lower} if field.is_relation else {}),
                              **({"choices": [value for value, _ in field.flatchoices]} if field.choices else {})}
                   for field in public_fields(model)} for kind, (model, _) in source_models().items()}


# 功能：取得适合候选关联的业务标签。
# 输入：`row` 为业务来源实例。
# 输出：非空字符串标签，最长 240 字符。
# 逻辑：按已声明的可读字段选择；无标签的记录使用类型及源主键，邮件可使用主题。
# 约束：不从完整正文、令牌或任意 JSON 中猜测实体名称。
def label_for(row):
    if row._meta.label_lower == "crm.email":
        return str(row.payload.get("subject") or row.pk)[:240]
    for field in LABEL_FIELDS:
        value = getattr(row, field, None)
        if isinstance(value, str) and value.strip():
            return value[:240]
    return f"{row._meta.label_lower}/{row.pk}"


# 功能：构造语义建图使用的邮件原文。
# 输入：`email` 为已授权邮件对象。
# 输出：主题、换行及纯文本正文。
# 逻辑：同一表示用于输入、幂等键和后续来源有效性检查。
# 约束：不读取附件、不增加摘要或修改原文。
def mail_text(email):
    return str(email.payload.get("subject", "")) + "\n" + str(email.payload.get("body_text", ""))
