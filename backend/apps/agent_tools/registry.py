"""职责：维护可发现的业务工具白名单及输入契约。
实现：从既有销售序列化器派生记录工具，显式登记客户、邮件、日历与证据能力。
关联：dispatch 仅解释固定 kind；services 控制授权、幂等和提案；MCP 不自行扩展白名单。
目录：
- tool：建立工具声明。
- build_registry：构造完整工具目录。
变量索引：
- RESOURCES：业务资源与稳定工具前缀。
- READ_ONLY：禁止常规创建更新的资源。
- HUMAN_WRITES：涉及授权或归组的写入必须由用户确认。
"""

from apps.sales.serializers import SERIALIZERS
from apps.sales.services import TRANSITIONS
from apps.sales.views import LABELS
from apps.crm.serializers import RegisterSerializer
from .schemas import UUID, REVISION, PAGE, object_schema, record_schema

RESOURCES = {
    "customers": "customer_settings",
    "contact-profiles": "contact_profiles",
    "aliases": "aliases",
    "teams": "teams",
    "memberships": "memberships",
    "grants": "grants",
    "products": "products",
    "tickets": "tickets",
    "opportunities": "opportunities",
    "quotes": "quotes",
    "quote-lines": "quote_lines",
    "orders": "orders",
    "order-lines": "order_lines",
    "follow-ups": "follow_ups",
    "conversations": "conversations",
    "drafts": "drafts",
    "messages": "messages",
    "actions": "actions",
    "files": "files",
    "notifications": "notifications",
    "connections": "connections",
}
READ_ONLY = {"messages", "actions", "files", "notifications", "connections"}
HUMAN_WRITES = {"aliases", "teams", "memberships", "grants"}


# 功能：生成工具描述。
# 输入：`name`、`description`、`kind`、`schema`、`mode` 执行模式及 `binding` 固定路由信息。
# 输出：内部工具字典。
# 逻辑：公开结构与内部绑定分离。
# 约束：确认模式不表示已经执行业务操作。
def tool(name, description, kind, schema, mode="read", **binding):
    return {
        "name": name,
        "description": description,
        "inputSchema": schema,
        "executionMode": mode,
        "category": name.split(".")[0],
        "annotations": {
            "readOnlyHint": mode == "read",
            "destructiveHint": mode == "confirm",
            "idempotentHint": True,
            "openWorldHint": kind in {"calendar", "sync"},
        },
        "kind": kind,
        **binding,
    }


# 功能：构造业务工具集合。
# 输入：无参数，读取固定映射与实际字段。
# 输出：按名称索引的工具字典。
# 逻辑：记录、关系和状态沿用现有模型，特殊能力独立列举。
# 约束：不注册外部动作批准/执行、任意 SQL、凭证读取、数据库运维或虚构新闻查询。
def build_registry():
    entries = []
    for resource, prefix in RESOURCES.items():
        serializer = SERIALIZERS[resource]
        names = {field.name for field in serializer.Meta.model._meta.fields}
        filters = {**PAGE, "archived": {"enum": ["true", "false", "all"]}}
        for key in ("company", "conversation", "quote", "order", "team", "status"):
            if key in names:
                filters[key] = {"type": "string"} if key == "status" else UUID
        if names.intersection(
            {"name", "title", "number", "sku", "subject", "description", "content"}
        ):
            filters["q"] = {"type": "string", "maxLength": 500}
        entries.append(
            tool(
                prefix + ".list",
                f"分页查询授权范围内的{LABELS[resource]}；不自动遍历所有页。",
                "record_list",
                object_schema(filters),
                resource=resource,
            )
        )
        entries.append(
            tool(
                prefix + ".get",
                f"读取指定{LABELS[resource]}及 revision。",
                "record_get",
                object_schema({"id": UUID}, ["id"]),
                resource=resource,
            )
        )
        if resource not in READ_ONLY:
            mode = "confirm" if resource in HUMAN_WRITES else "write"
            entries.append(
                tool(
                    prefix + ".create",
                    f"创建{LABELS[resource]}，不发送邮件或执行外部动作。",
                    "record_create",
                    object_schema({"data": record_schema(serializer)}, ["data"]),
                    mode,
                    resource=resource,
                )
            )
            entries.append(
                tool(
                    prefix + ".update",
                    f"按读取到的 revision 修改{LABELS[resource]}，不允许覆盖并发更新。",
                    "record_update",
                    object_schema(
                        {
                            "id": UUID,
                            "revision": REVISION,
                            "data": record_schema(serializer, True),
                        },
                        ["id", "revision", "data"],
                    ),
                    mode,
                    resource=resource,
                )
            )
        if resource not in {"messages", "actions", "notifications", "connections"}:
            entries.append(
                tool(
                    prefix + ".archive",
                    f"提出归档或恢复{LABELS[resource]}的计划；须用户确认。",
                    "record_command",
                    object_schema(
                        {
                            "id": UUID,
                            "revision": REVISION,
                            "archived": {"type": "boolean"},
                        },
                        ["id", "revision", "archived"],
                    ),
                    "confirm",
                    resource=resource,
                    command="archive",
                )
            )
        transitions = TRANSITIONS.get(serializer.Meta.model)
        if transitions:
            targets = sorted(
                {target for values in transitions.values() for target in values}
            )
            entries.append(
                tool(
                    prefix + ".transition",
                    f"提出{LABELS[resource]}状态变更，须用户确认；禁止越过原状态机。",
                    "record_command",
                    object_schema(
                        {"id": UUID, "revision": REVISION, "status": {"enum": targets}},
                        ["id", "revision", "status"],
                    ),
                    "confirm",
                    resource=resource,
                    command="transition",
                )
            )
    entries.extend(
        [
            tool(
                "customers.search",
                "按名称、域名或联系人查询授权业务客户，不返回私人邮件。",
                "customers",
                object_schema(
                    {
                        **PAGE,
                        "q": {"type": "string", "maxLength": 500},
                        "company": UUID,
                        "archived": {"enum": ["false", "all"]},
                    }
                ),
            ),
            tool(
                "customers.create",
                "新建独立人工客户，不猜测域名或自动合并。",
                "customer_create",
                object_schema(
                    {"name": {"type": "string", "minLength": 1, "maxLength": 240}},
                    ["name"],
                ),
                "write",
            ),
            tool(
                "customers.context",
                "读取自有客户的邮件、画像与评分；业务共享不授予私人邮件访问。",
                "customer_context",
                object_schema({"company_id": UUID}, ["company_id"]),
            ),
            tool(
                "customers.analyze",
                "明确请求自有客户重新分析；按现有 rules/agent 配置同步处理或入队，返回实际任务状态。",
                "analyze",
                object_schema({"company_id": UUID}, ["company_id"]),
                "write",
            ),
            tool(
                "customers.register",
                "登记自有客户 CRM 资料并请求更新画像；人数必须附来源，不自动推断。",
                "register",
                object_schema(
                    {
                        "company_id": UUID,
                        "revision": REVISION,
                        "data": record_schema(RegisterSerializer),
                    },
                    ["company_id", "revision", "data"],
                ),
                "write",
            ),
            tool(
                "contacts.save",
                "保存客户联系人邮箱及姓名，revision 为公司版本；电话职位另用 contact_profiles。",
                "contact",
                object_schema(
                    {
                        "company_id": UUID,
                        "revision": REVISION,
                        "data": object_schema(
                            {
                                "id": {"type": "integer", "minimum": 1},
                                "email": {"type": "string", "format": "email"},
                                "name": {"type": "string"},
                            },
                            ["email"],
                        ),
                    },
                    ["company_id", "revision", "data"],
                ),
                "write",
            ),
            tool(
                "sales.overview",
                "读取销售概况与待办，金额按币种分别汇总。",
                "overview",
                object_schema({}),
            ),
            tool(
                "sales.audit",
                "查询授权业务的操作历史，仅返回脱敏审计元数据。",
                "audit",
                object_schema({**PAGE, "company": UUID}),
            ),
            tool(
                "people.find",
                "按完整用户名查找有效协作账号，不枚举用户目录。",
                "people",
                object_schema(
                    {"username": {"type": "string", "minLength": 1}}, ["username"]
                ),
            ),
            tool(
                "mailboxes.list",
                "列出自己的邮箱及同步状态，不返回连接密钥。",
                "mailboxes",
                object_schema({}),
            ),
            tool(
                "mailboxes.sync",
                "提出同步指定邮箱的请求；QQ 必须明确 recent_days 或 max_messages，Gmail 不接受 QQ 范围；须用户确认。",
                "sync",
                object_schema(
                    {
                        "mailbox_id": UUID,
                        "sync_options": object_schema(
                            {
                                "recent_days": {"type": "integer", "minimum": 1},
                                "max_messages": {"type": "integer", "minimum": 1},
                            }
                        ),
                    },
                    ["mailbox_id"],
                ),
                "confirm",
            ),
            tool(
                "mailboxes.sync_status",
                "读取自己的邮箱同步批次进度。",
                "sync_status",
                object_schema({"run_id": UUID}, ["run_id"]),
            ),
            tool(
                "mailboxes.retry",
                "提出重新处理失败同步邮件的请求，须用户确认；不自动重试。",
                "sync_retry",
                object_schema({"run_id": UUID}, ["run_id"]),
                "confirm",
            ),
            tool(
                "emails.list",
                "分页读取自己的已保存邮件与复核证据，支持邮箱和复核状态。",
                "emails",
                object_schema(
                    {
                        "mailbox_id": UUID,
                        "page": PAGE["page"],
                        "status": {"enum": ["pending", "non_business", "all", "saved"]},
                    }
                ),
            ),
            tool(
                "emails.review",
                "提出将邮件确认为业务或非业务的人工复核决定；可能改变分析输入，须用户确认。",
                "email_review",
                object_schema(
                    {
                        "email_id": {"type": "string", "minLength": 1},
                        "revision": REVISION,
                        "review_status": {
                            "enum": ["confirmed_business", "confirmed_non_business"]
                        },
                    },
                    ["email_id", "revision", "review_status"],
                ),
                "confirm",
            ),
            tool(
                "knowledge.search",
                "分页检索自己的已导入知识，返回来源与版本；不是向量语义搜索。",
                "knowledge_search",
                object_schema({**PAGE, "q": {"type": "string", "maxLength": 500}}),
            ),
            tool(
                "knowledge.get",
                "读取自己的一条有效知识及引用来源。",
                "knowledge_get",
                object_schema({"id": UUID}, ["id"]),
            ),
            tool(
                "notifications.read",
                "将自己的提醒标记已读。",
                "record_command",
                object_schema({"id": UUID, "revision": REVISION}, ["id", "revision"]),
                "write",
                resource="notifications",
                command="read",
            ),
            tool(
                "files.download_link",
                "返回已授权附件的网页登录下载链接；不解析内容，不暴露存储路径。",
                "file_link",
                object_schema({"id": UUID}, ["id"]),
            ),
            tool(
                "proposals.get",
                "读取自己的冻结提案和确认结果；不批准、不取消，也不执行提案。",
                "proposal_get",
                object_schema({"id": UUID}, ["id"]),
            ),
        ]
    )
    for operation in ("events", "freebusy"):
        props = {
            "connection_id": UUID,
            "calendar_id": {"type": "string"},
            "start": {"type": "string", "format": "date-time"},
            "end": {"type": "string", "format": "date-time"},
            "page_token": {"type": "string"},
        }
        entries.append(
            tool(
                "calendar." + operation,
                "读取已授权日历的事件或忙闲；须明确时区及时间窗口，不自动翻页。",
                "calendar",
                object_schema(props, ["connection_id", "calendar_id", "start", "end"]),
                operation=operation,
            )
        )
    for operation in ("merge", "move"):
        props = {
            "source_id": UUID,
            "target_id": UUID,
            "source_revision": REVISION,
            "target_revision": REVISION,
        }
        if operation == "move":
            props["keys"] = {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
            }
        entries.append(
            tool(
                "customers." + operation,
                "提出客户合并或指定邮件归组计划；双方必须为自有客户，须用户确认。",
                "grouping",
                object_schema(props, list(props)),
                "confirm",
                operation=operation,
            )
        )
    for provider in ("gmail", "qq", "calendar"):
        props = {"connection_id": UUID}
        if provider != "calendar":
            props.update({"draft_id": UUID, "quote_id": UUID})
            required = ["connection_id", "draft_id"]
        else:
            props.update(
                {
                    "calendar_id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "start": {"type": "string", "format": "date-time"},
                    "end": {"type": "string", "format": "date-time"},
                    "attendees": {
                        "type": "array",
                        "items": {"type": "string", "format": "email"},
                    },
                    "send_updates": {"enum": ["all", "none", "externalOnly"]},
                }
            )
            required = list(props)
        entries.append(
            tool(
                "actions.prepare_" + provider,
                "冻结发信或会议内容为待确认动作；不会发送，用户必须通过现有业务页面批准。",
                "prepare_action",
                object_schema(
                    {
                        "company": UUID,
                        "conversation": UUID,
                        "parameters": object_schema(props, required),
                    },
                    ["company", "parameters"],
                ),
                "write",
                action_tool=provider
                + (".create" if provider == "calendar" else ".send"),
            )
        )
    return {entry["name"]: entry for entry in entries}
