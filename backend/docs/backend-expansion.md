# 后端业务扩展：Schema 与实施契约

范围经用户确认：客户与联系人管理、人工归组、产品、工单、商机、报价、订单、跟进、助手会话与草稿、团队权限、审计、文件、任务及外部动作。聊天生成与自主工具选择仍属于后续 Agent 接入；不伪造模型输出或外部动作成功。

## 数据边界

- `crm` 继续持有员工私人邮箱、标准邮件、事实与 L1–L4 版本。现有评分权重、提示词、邮件扫描上限、默认数据库和时区不变。
- 新 `sales` 应用持有结构化销售记录和助手操作。客户共享仅授权业务记录；不自动授予其他员工读取私人 Gmail、邮件事实或 Agent 上下文的权限。
- 交易实体使用 UUID、owner、revision、创建/更新时间和归档状态。写请求必须携带读取时的 If-Match；归档代替不可恢复删除。
- 公司现有 tickets/quotes/orders JSON 是 Agent 契约投影。已有内容完整保留；新增关系记录映射到该投影，业务字段由关系记录管理，不能通过任意 JSON 写入制造“已发送报价”或“历史订单”。
- 报价与订单行保存商品描述、数量、成交单价与折扣快照，目录价格变化不追溯修改历史单据；不跨币种汇总、不自动换汇、不推断税率。
- 审计日志只追加，不保存凭证、文件正文或邮件正文。助手消息、草稿和文件分别存储，权限由后端确认。

## 实体关系

| 实体 | 关键关系与职责 |
|---|---|
| CompanySettings / CompanyAlias | 公司归档、人工主要联系人及明确归组映射 |
| ContactProfile | 关联原 Contact 的人工职位、电话和备注 |
| Team / Membership / CompanyGrant | 团队成员和公司业务访问授权；不共享邮箱 |
| Product | 员工商品目录、明确币种、价格、库存记录 |
| Ticket / Opportunity / FollowUp | 公司工单、商机阶段、负责人和到期跟进 |
| Quote / QuoteLine | 报价头、冻结的行项目与审核状态 |
| SalesOrder / OrderLine | 订单头及行项目；不把草拟订单提供为历史成交 |
| Conversation / Message / Draft | 客户会话、不可变消息与可版本化编辑的草稿 |
| ToolAction | 工具名称、确定参数、确认、执行状态与外部结果 |
| Connection | 员工、提供方、账号联合唯一的加密外部连接 |
| Attachment | 授权文件元数据、内容哈希和私有存储路径 |
| AuditEvent / Notification | 业务操作审计与应用内到期提醒 |

## 状态与失败语义

- 工单：open → in_progress → resolved → closed；允许显式重新打开。
- 商机：new → qualified → proposal → won/lost；变更记录进入审计。
- 报价：draft → approved → sent → accepted/rejected；发送状态仅由真实外发成功写入。
- 订单：draft → confirmed → fulfilled 或 cancelled；已确认订单内容冻结。
- 跟进：open → completed/cancelled；到期提醒使用唯一键去重。
- 外部动作：pending_confirmation → approved → running → succeeded/failed/uncertain，或 cancelled。确认后参数冻结；未知网络结果标为 uncertain，禁止隐式重试。
- 后台命令只处理明确批准或到期的记录；不会自动批准对外动作。取消只在尚未执行时保证生效。
- Gmail 发信需要 gmail.send 权限；当前只读授权保持原样，缺少权限明确报错。日历需要单独服务授权，缺失时明确报告未配置。

## 交付与验证

按模型与迁移、事务服务、权限与 API、前端接入、后台动作顺序实施。测试覆盖权限隔离、状态转换、版本冲突、金额快照、幂等、消息归属、文件安全、失败与未知结果。自动测试使用隔离数据库和外部服务模拟；真实发送需要用户确认具体收件人与内容，不能将模拟测试当成真实外部验证。

所有新增 Python 文件（含迁移和测试）遵循职责、实现、关联、目录、变量索引，以及声明前的功能、输入、输出、逻辑、约束说明。运行项目注释检查器和实际业务检查，不改变检查标准。

## 已实现 API

所有新接口位于 `/api/v1/sales/`，使用原 SessionAuthentication 和 CSRF。业务管理入口为 `/business/`，客户详情右上方仍可打开助手侧栏。

| 路径 | 方法与用途 |
|---|---|
| `catalog/` | GET，实际字段类型、必填/只读属性、关系及状态边 |
| `directory/` | GET 搜索/分页客户与联系人；POST 人工建档 |
| `directory/{company_id}/contacts/` | POST 新建/编辑联系人，If-Match 使用公司 revision |
| `records/{resource}/` | GET 授权分页；POST 严格校验创建 |
| `records/{resource}/{id}/` | GET 单条及 ETag；PATCH 按 If-Match 修改 |
| `records/{resource}/{id}/commands/` | POST 显式 archive / transition / decide / read / interrupted / verify |
| `grouping/move/`、`grouping/merge/` | POST，必须提供源、目标和两个版本；move 另需明确邮件 keys |
| `files/`、`files/{id}/download/` | POST multipart 私有上传 / GET 认证下载 |
| `oauth/` | POST 生成授权地址，GET 校验 OAuth 回调 |
| `calendar/events/`、`calendar/freebusy/` | GET 指定连接、日历及带时区窗口；事件返回分页 token |
| `audit/`、`overview/`、`people/` | GET 审计、按币种统计、协作账号或精确用户名查询 |

资源名称：`customers`、`aliases`、`contact-profiles`、`teams`、`memberships`、`grants`、`products`、`tickets`、`opportunities`、`quotes`、`quote-lines`、`orders`、`order-lines`、`follow-ups`、`conversations`、`messages`、`drafts`、`actions`、`files`、`notifications`、`connections`。

- 记录列表支持分页（默认 30、上限 100）、适用的 company/conversation/quote/order/team/status 过滤、明确 `archived=true/false/all`，部分文本模型支持 `q`。业务管理页每页 20 条。目录默认隐藏归档公司。
- 修改/命令需要 `If-Match: <revision>`；缺版本或格式错误为 400，过期为 409。浏览器表单在冲突时显示错误，不覆盖或自动重试。
- 原客户基础档案继续使用 `POST /api/v1/companies/{id}/register/`，人数非空必须填写来源；本轮增加同事务审计和归档校验。
- 消息仅接受 user 内容，client_key 保证同一会话的重复提交幂等；不同内容复用同一键返回冲突。消息不可改写。聊天提问走独立 `sales/chat/messages/`，受保护 Agent 回报保存唯一助手消息，见 [聊天适配](chat-integration.md)。
- 团队 manager 可以维护普通成员，但只有团队 owner 可授予、修改或归档 manager。公司共享同时要求团队成员角色与公司 grant 满足权限。
- 产品库存为人工记录；不随订单自动扣减。报价/订单单价、数量、折扣保留 Decimal 精度，每行净额以 ROUND_HALF_UP 保留两位再求和；不推断税费。已有明细的单据不能直接换币种。
- 单据草稿可以编辑；有效交易单据按状态冻结。当前仅草稿、取消订单、拒绝报价允许归档，避免通过归档改变有效交易历史。
- 文件上限 20 MiB，内容保存于被 Git 忽略的 `backend/private_uploads/`，随机存储键不返回客户端。下载采用 attachment 与 `nosniff`，不解析或执行内容。

## Worker 与外部服务

在 `SalesMate/backend/` 使用原 Python 环境：

```powershell
python manage.py migrate
python manage.py sales_worker --once
python manage.py sales_worker --poll 5
```

`--once` 会实际执行已批准动作，仅在明确希望消费队列时运行。常驻进程负责已批准工具动作和应用内到期提醒，默认 5 秒查询；分析仍沿用既有 Agent 调度和开关。进程不为 failed/uncertain/running 隐式重试；空队列不持续写 INFO 日志。生产环境应由进程管理器负责启动与退出，本轮没有安装操作系统服务或创建开机任务。

外部接入步骤：

1. 在根 `.env` 配置新的 `SALESMATE_VAULT_KEY`；用 `Fernet.generate_key()` 生成并妥善备份，丢失密钥会导致无法读取既有新连接。代码不自动生成或回退明文。
2. 使用已有 Google Web OAuth client，在 Google 控制台额外登记对应服务器的 `/api/v1/sales/oauth/` 回调。本机为 `http://127.0.0.1:8000/api/v1/sales/oauth/`。
3. 在“业务管理 → 外部连接”分别授权 Gmail 发信或 Google 日历。Gmail 请求 send + readonly（核对未知结果）；日历请求 events + readonly。原 Gmail 只读同步连接不自动扩权。
4. 创建邮件草稿或会议计划，在“外部动作”审阅完整账号、客户、收件人/参会人、正文/时间及日历通知方式；另行点击确认才进入执行队列。
5. 未知结果可通过确定 Message-ID / 事件 ID 向 Google 只读核对。未查到不等于未执行，不会触发重发。进程中断留下 running，须先核实进程已中断，再标记 uncertain 并核对。

Gmail 采用 MIME 的 Base64URL `raw`，日历使用 UUID 十六进制作为稳定事件 ID，SDK 调用均显式 `num_retries=0`。实现依据：[Gmail 发信指南](https://developers.google.com/workspace/gmail/api/guides/sending)、[Calendar events.insert](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert)。

本轮验证：70 项 Django 测试、123 项既有 Agent 测试通过；Schema 生成及校验、迁移一致性、Python 注释/目录与差分检查、新增 Python 静态检查和 JS 语法检查通过。浏览器检查了业务导航、表单、客户读取和响应式布局。外部授权及发送由模拟覆盖，未发送真实邮件或创建真实会议；生产并发压力、灾难恢复和真实外部授权仍未验证。代码尚未提交 Git，不声称已验证提交原子性。
