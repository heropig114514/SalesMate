# 销售聊天：后端适配与运行说明

更新：2026-09-20。此文档描述后端主导的 Agent 适配。聊天回报现仅校验 Schema，另保留权限、请求状态和幂等校验。代码已提供会话提问、任务状态、Agent 领取/上下文/回报、证据快照、引用、消费者与网页展示，以及新增的请求绑定只读工具接口。新接口、工具错误、证据存储和发布步骤见[工作空间聊天对接契约](workspace-chat-tools.md)；代码交付不等于生产迁移、服务安装或真实模型验收已经完成。

## 1. 边界和复用

- 复用 `sales.Conversation`、`sales.Message`、消息 `client_key` 和员工绑定的 `AgentAuthentication`。不重建会话，不回填旧消息任务。
- 新应用 `apps.chat` 维护 `AnswerRequest`、`Citation`、`KnowledgeEntry`；初始结构为 `chat.0001_initial`；通用聊天新增 `sales.0005_general_conversation` 与 `chat.0002_general_answer_request`，解除两处 company 非空约束，不修改 L1–L4 的协议、参数或数据。
- 普通 `sales/records/messages/` 仍只保存用户消息；只有受保护的聊天回报服务能创建 assistant。草稿保存不会触发模型。
- 聊天支持无客户绑定的通用私有会话，以及员工自己公司的客户私有会话。团队业务共享不授予邮件和画像访问权；现有业务共享及人工确认发信不变。
- 外部知识关闭；后端支持请求绑定的 customers.search/customers.context 只读调用，模型工具编排由 Agent 接入。聊天不执行发信、日历、CRM/文件写操作，不引入向量库或分布式队列。

## 2. 数据与状态不变量

`AnswerRequest` 保存 owner、company、conversation、user_message、可空且唯一的 assistant_message、状态、时间、冻结历史、上下文快照、结果、提示词版本及 Agent 错误。`retry_of` 关联原失败请求，每个原请求最多一个后继。

```text
pending → processing → completed
                     → failed
pending → failed（领取时确认权限已失效）
failed --用户明确重试--> 新 request_id 的 pending
```

- 一个会话最多一个 pending/processing 请求，数据库条件唯一约束与事务锁共同保证。多个员工或会话可以各有待处理任务。
- 按员工行锁串行化提交、领取、回报和恢复，保持现有业务锁顺序；模型调用不占数据库事务。
- 原问题可对应多次明确尝试，每个请求最多一个助手消息。旧结果不能被新尝试覆盖。
- 相同 client_key、相同正文重传返回原任务；异内容返回 409。显式重试另走 retry，不依赖重新提交。
- 相同终态完整 JSON 结果重复回报返回 duplicate=true；不同结果 409。提示词版本、引用顺序、正文和错误均参与比较。
- completed 可以没有引用；任何结构合法的回报均不要求预先读取上下文快照，后端不按正文含义判断是否必须引用。
- failed 的 assistant_text 必须为空、citations 必须为空，不创建助手消息。
- 管理员确认进程中断后可将 processing 终止为 failed；不自动超时、不把旧请求重置为 pending。迟到回报被拒绝。
- 浏览器对旧问题重新回答时，若会话已有之后的用户问题，返回 409，要求在当前对话末尾重新提问，避免破坏历史顺序。

## 3. 浏览器接口

工作台、业务管理和世界消息页面右下角提供“聊天助手”悬浮按钮，点击后在页面底部展开横向聊天条，使用面板收起按钮 / Esc 收起，无需跳转独立页面或选择客户。支持一般问答、写作、翻译及计划；历史与草稿只属于当前员工。展开浮窗只读取历史，显式提问、保存草稿或新建会话时才写入。手机端保持底部展开及受限高度，采用模态焦点，收起后恢复背景交互；同一页面收起/展开及工作台内导航保留未保存草稿，跨页面或刷新需先保存草稿。

客户详情的 AI 助手仍使用当前客户的授权资料；已有 `/#assistant/<company_id>` 链接可继续恢复该客户会话，旧链接仅在工作台上打开浮窗；`/#assistant` 打开通用会话。客户证据不足不会自动切换为通用模式。

通过 `POST /api/v1/sales/records/conversations/` 创建通用会话时，company 可省略或为 null；客户会话继续传 UUID。列表新增 `conversation_scope=general|customer` 筛选，通用页使用 general；`?company=<uuid>` 继续用于客户会话。绑定创建后不可修改。MCP/CLI 的 `conversations.list` 也暴露此筛选。

前缀 `/api/v1/sales/chat/`，使用既有 SessionAuthentication、CSRF 和统一错误响应。

| 路径 | 方法 | 行为 |
|---|---|---|
| `messages/` | POST | 同一事务保存用户消息与 pending 请求 |
| `requests/?conversation=<uuid>` | GET | 按 created_at/id 排序的请求分页，复用 page/page_size，默认 30、最大 100 |
| `requests/<uuid>/` | GET | 单请求状态、时间、错误、助手消息 ID 及有序引用 |
| `requests/<uuid>/retry/` | POST `{}` | 显式创建失败请求的新尝试，重复操作返回已存在后继 |

提交问题的精确请求体：

```json
{
  "conversation_id": "<uuid>",
  "content": "客户目前最关心什么？",
  "client_key": "<client-generated-uuid>"
}
```

不接收 employee_id/company_id/role。可空公司由既有会话解析，员工从登录身份取得。新建返回 201，同内容重传返回 200。

状态响应包含 `request_id`、`conversation_id`、`user_message_id`、`assistant_message_id`、`status`、`error`、`created_at`、`processing_started_at`、`finished_at`、`chat_prompt_version`、`citations`。浏览器引用包含 position、三元组及后端保存的 content，供展开证据；这些额外字段不发送给 Agent。

消息正文继续从既有 `records/messages/?conversation=...` 读取。现有会话创建和草稿接口保持不变。

## 4. 固定 Agent 接口

前缀 `/api/v1/agent/`，全部使用 `Authorization: Agent <service-token>`。一个 token 只绑定一名员工。浏览器会话不能代替服务认证。

### claim

`POST chat/requests/claim/`，请求 `{}`，无工作返回 `{"request": null}`。有工作返回：

```json
{
  "request": {
    "request_id": "<uuid>",
    "conversation_id": "<uuid>",
    "company_id": "<uuid>",
    "user_message_id": "<uuid>",
    "question": "客户目前最关心什么？",
    "recent_history": []
  }
}
```

后端领取响应继续保持同样六字段，无客户绑定时输出 `company_id: null`，供当前 Agent 选择 `general_chat.answer` 或原客户工作流。浏览器创建通用会话可省略 company，提问和 Agent 回报均无需传 company_id。仓库内当前 Agent 解析器仍要求领取响应显式包含 company_id；这是 Agent 侧契约，本次后端回报校验调整未改变它。

历史只包含同员工同会话、原问题之前的最近 20 条非空 user/assistant 消息，恢复为时间正序；不含当前问题和后来问题。领取时冻结，不改动 Agent 的 6000 字符历史预算。

### context

`POST chat/context/`，请求 `{"request_id":"<uuid>","scope":"internal"}`。

```json
{
  "request_id": "<uuid>",
  "scope": "internal",
  "customer_context": [],
  "context_items": [],
  "customer_context_status": "completed",
  "knowledge_status": "completed",
  "retrieval_gaps": [],
  "external_available": false
}
```

Context Item 严格只有 `source_id`、`source_type`、`title_or_label`、`content` 四个字符串字段，前三项为引用身份。不得混入模型 ID、数据库时间、链接等额外字段；Agent 的解析器会拒绝未知字段。

首次 internal 请求在事务内读取并保存快照；同请求后续读取返回同样内容。后端不接收任意 company 或 query 覆盖。

通用模式只提供当前员工的内部知识，customer_context 为空；没有知识也可调用模型进行一般交流，知识读取失败仍明确失败。通用模式不读取或搜索任何客户的邮件、交易或画像。

客户模式的证据选择策略：最近最多 4 封员工自有邮箱的业务邮件、1 份当前有效模型画像、工单/报价/订单各至多 1 条现有投影记录、最多 4 条内部知识，共不超过原 Agent 12 条预算。邮件按 sent_at 倒序；业务取现有投影末条；知识按问题空白分隔词段的直接包含匹配数优先，再按导入时间倒序。该策略是 Demo 的确定性选择，不声称实现语义检索或完整历史检索。

- 邮件正文提供明确节选，包含时间、方向和主题，排除非业务/隐藏邮件；来源 ID 包含数据库邮件身份与复核版本，兼容 Gmail/QQ。
- 模型画像必须 `provider=agent`、成功、当前 company revision、快照未失效。不会把页面可展示的 stale 画像或 rules 占位当作当前模型结论。
- 工单、报价、订单沿用后端既有业务投影：保留状态、币种和真实外发语义，不把草拟报价冒充已发报价。
- 结构化记录用带字段名的可读投影，保留事实与判断区别；不调用额外模型生成摘要。每条最多 2000 字符，节选明确标记。
- 客户没有有效画像属于资料缺失，通过 retrieval_gaps 表达，仍可使用其他客户证据。查询成功但资料为空仍是 completed，Agent 可返回资料不足。
- 内部知识只使用维护者导入的真实资料，没有默认制度。当前没有独立远程知识源；数据库读取异常正常失败，不隐式变为空知识。
- `external_available=false`；请求 external 返回 409，其他非法 scope 为 400。未来启用 external 需补充版本契约及测试，本次不预置备用实现。
- 引用回报只检查三元组结构。匹配本请求的原上下文或成功 ToolRead 证据时复制正文；未匹配时只保存 Agent 声明的三元组，content 为空，不根据 source_id 查询其他请求或业务记录，不接受 Agent 自报正文。空正文表示后端没有附加可验证证据，不能当作已经验证的来源。工具记录独立保存，原 chat/context 响应不增加字段。

### report

`POST chat/answers/`，成功示例：

```json
{
  "request_id": "<uuid>",
  "chat_prompt_version": "chat-v2",
  "assistant_text": "现有资料不足，无法回答该问题。",
  "citations": [],
  "status": "completed",
  "error": null
}
```

`chat_prompt_version` 是非空字符串，最长 100 字符；后端不设版本白名单，也不与 company 绑定。例如 workspace-chat-v1 可直接回报。版本接受不表示新工具编排已经实现，Agent 仍需自行完成工作流适配。后端不判定回答语义、正文引用编号、引用是否重复或是否属于本请求快照；引用准确性与事实支持由 Agent 负责。

失败示例：

```json
{
  "request_id": "<uuid>",
  "chat_prompt_version": "chat-v2",
  "assistant_text": "",
  "citations": [],
  "status": "failed",
  "error": {"code":"model_unavailable","message":"回答模型暂时不可用，请稍后重试。"}
}
```

error 在 failed 时必须是恰含 code/message 的对象，两个值均为非空字符串；不再限制具体错误码和固定文案。Agent 负责输出可对用户展示且已脱敏的错误，后端原样保存。当前 Worker 的 report_failed 仍代表本地“未确认保存”，不会自动通过该失败请求再次回报。

保存响应固定含 `request_id`、`saved=true`、布尔 `duplicate`、`assistant_message_id`；completed 必须有消息 ID，failed 为 null。每条引用恰含 source_id/source_type/title_or_label 三个非空字符串，source_type 最长 80 字符；引用数组按原顺序保存，不去重、不校验正文中的 `[1]` 等编号。顶层仍要求 request_id/chat_prompt_version/assistant_text/citations/status/error 六字段；request_id 为 UUID。completed 要求非空 assistant_text 和 error=null，failed 要求 assistant_text=""、citations=[] 及错误对象。未知字段、错误类型和非法状态继续返回 400。

401 表示服务认证缺失/无效；越权与不存在统一 404；格式错误 400；状态、版本结果冲突 409。沿用既有 `error.code/error.detail` 和 HTTP request_id 包装，不另建错误体系。

## 5. 运行、恢复和知识维护

在仓库根目录，使用项目 Python 环境及现有 `.env`：

```powershell
python backend/manage.py migrate
python -m uvicorn --app-dir backend config.asgi:application --host 127.0.0.1 --port 8000
# 另一个终端；共享进程轮转所有有效员工的 pending 请求
python backend/manage.py chat_worker
# 只处理至多一个请求
python backend/manage.py chat_worker --once
```

共享 chat_worker 沿用 `SALESMATE_BACKEND_AGENT_URL` 及 Agent 模型配置，每个工作单元通过服务器内部 `scoped_backend` 生成临时员工令牌，退出即撤销；不再由环境中的固定 `SALESMATE_AGENT_SERVICE_TOKEN` 限定消费用户。独立 Agent CLI 仍使用其显式配置的固定身份。chat_worker 独立于 ANALYSIS_PROVIDER 和 crm_worker；不会改变 L1/L3 模型参数、邮箱读取范围或画像并发。聊天仍需要可用模型配置，不存在规则聊天降级。

常驻消费者按员工主键轮转待办，排除停用员工，仅发现 pending；每次以该员工的独立 HTTP 身份串行处理一条，空队列默认每 2 秒查询；`--poll` 可显式指定 (0,60] 秒。SIGTERM 等待在途任务完成后停止领取。普通已保存失败保留失败记录；领取异常或 report_failed 停止消费者并非零退出，不隐式重试。日志只记录任务标识、员工、状态、错误码和异常类型。

恢复步骤：先查询 request 状态，尤其回报响应丢失时，数据库可能已经 completed。确认原进程中断且请求仍 processing 后执行：

```powershell
python backend/manage.py chat_interrupt --owner <username> --request-id <uuid> --confirm-interrupted
```

这将原请求标记为 worker_interrupted/failed。随后由用户点击“重新回答”，创建新 ID。没有自动超时阈值或隐式重新入队。

内部知识文件为 UTF-8 JSON 数组，每条精确包含 source_key、version、title、content、active；四个文本非空，active 为布尔值。只能写入已确认的资料；不要把需求文档示例制度当作真实数据。

```powershell
python backend/manage.py chat_knowledge --owner <username> --file <knowledge.json>
```

同 key/version 不允许改标题或正文；修改内容须明确新 version。同 key 的新 active 版本会停用旧版本。用相同内容及 active=false 可显式停用。整批失败全部回滚，历史请求证据保持不变。

## 6. 网页行为

“发送问题”创建回答任务，“保存草稿”保持原行为。活动请求禁止当前会话再次提问。页面每 2 秒观察一次状态，每轮最多 120 次，失败或达到上限后暂停并提供“继续查询”；恢复查询不会重新生成回答。

完成后自动读取助手消息和引用，证据可展开；未保存输入、焦点和历史区阅读位置保留。失败展示安全提示并提供明确重试。关闭侧栏或切换客户/会话取消观察资格和在途状态请求；旧响应不能覆盖新上下文。所有文本转义，来源内容不作为 HTML 或脚本执行。

## 7. Lightsail 部署

新增 `backend/deploy/lightsail/salesmate-chat.service`，普通 salesmate 用户运行，使用共享的 `/opt/salesmate/shared/runtime.env`，不自动重启失败进程。蓝绿部署先排空聊天、CRM/销售调度器及 Celery 消费者，再备份和迁移；旧 Web 保持服务，候选 Web 健康且切流成功后再启动聊天，最后排空并退役旧 Web。聊天共享调度复用已有 scoped_backend 临时身份机制；每个 HTTP 客户端仍只绑定一名员工，原权限检查不变。

仓库 `deploy-from-git.sh` 已补充聊天服务预检、停止、启动和健康检查。服务器该脚本是 root 保护的独立副本，**不会因普通代码拉取自动升级**；首次上线需按既有运维方式先审阅安装聊天 service（暂不启动）、更新受保护部署脚本，再发布本次代码。脚本在停机前检查 service 已安装，不自动从普通代码替换 systemd 配置。仅合并代码不代表聊天消费者已经启动，应以部署结果和 systemd 实际状态为准。

首次发布前已确认目标生产数据库尚未应用聊天迁移，将 `chat.0001_initial` 的四项约束放入对应 `CreateModel.options.constraints`。迁移只创建三个新表，不修改既有业务表；活动会话唯一性、请求状态、引用位置及知识版本约束保持相同。在线迁移门禁不变，仍拒绝单独的 `AddConstraint`。发布时按既有流程先备份再迁移；此后不得改写已应用的迁移。

手工部署时完成迁移后，安装服务文件到 `/etc/systemd/system/`，执行 daemon-reload，再明确启用/启动 salesmate-chat；此前核对模型与后端地址配置及待处理队列。日志使用 `journalctl -u salesmate-chat`。单服务覆盖全部有效员工，无需为新注册员工配置永久服务令牌或独立进程。

## 8. 验证与交付边界

```powershell
python backend/manage.py test tests --noinput
python -m unittest discover -s agent/tests
python backend/manage.py makemigrations --check --dry-run
python backend/manage.py spectacular --file backend/contracts/openapi.yaml --validate
# 配置现有 Playwright/浏览器环境后，另跑联合网页验收
python backend/manage.py test tools.chat_browser_e2e --noinput
# 从 backend/ 执行
python tools/check_docs.py
python tools/check_doc_changes.py
```

浏览器沿用项目显式 Playwright/Chrome 配置，执行 `node backend/tools/browser_chat.cjs`；已加入部署 CI。后端测试覆盖真实 PostgreSQL 并发提交/领取/回报、事务回滚、隔离、权限撤销、画像版本、证据快照、引用、知识版本、失败新尝试和恢复。真实 Django 临时 HTTP 服务对接原 Agent 客户端及工作流，模型边界使用模拟输出。

浏览器交互测试使用真实页面/JS、模拟 API，覆盖提交、完成、引用转义、失败重试、编辑保留、查询失败、观察上限及切换关闭。另有 `tools.chat_browser_e2e` 使用真实浏览器、Session/CSRF、临时 Django HTTP 服务和隔离 PostgreSQL，网页创建任务后由原 Agent 工作流领取、读取证据、保存答案，网页自动显示正文与引用；仅替换应用启动脚本以单独挂载真实侧栏，所有业务 API 均真实执行，模型输出在 Python 调用边界模拟。两类浏览器检查均已加入 CI。

这些检查不冒充真实百炼或已部署网页验收。生产迁移、服务安装、真实模型质量和完整外部授权仍需实际环境验证。

实现、注释、目录和迁移在当前工作区同步维护；未提交 Git 前不声称已验证同一次提交的原子性。

### 2026-09-18 本地验证记录

- 独立 PostgreSQL 16 测试库：全后端 155 项通过，其中新增聊天 24 项；未连接业务数据库。
- Agent 原离线套件 186 项、邮箱测试工具 9 项通过；Agent 可执行代码未修改。
- 浏览器真实 HTTP 联合验收 1 项通过，模型输出模拟；聊天、工作空间、邮件处理、QQ 发信四个浏览器脚本通过。
- 迁移一致性无未生成变更，OpenAPI 生成/校验及版本契约测试通过。
- Python 文档结构覆盖 128 文件；差分检查 0 错误、0 待复核，另人工核对本次实现说明及目录；新增 Python 的 Ruff F 检查、变更 JS 语法、部署脚本 Bash 语法、依赖一致性及 Git 空白检查通过。
- 测试临时 PostgreSQL 已停止。未应用生产迁移、未安装线上服务、未调用真实模型或发信，未提交或推送 Git。

### 2026-09-19 发布前复核

- 合并基础设施版本后，独立 PostgreSQL 16/pgvector 实例上的完整后端 171 项通过，包含聊天并发、权限及约束验证；未使用生产业务库运行测试。
- Agent 186 项、邮箱工具 9 项通过；五组模拟 API 浏览器检查通过，包括新增聊天一级入口、世界地图、邮件处理和 QQ 发信。
- 真实浏览器、HTTP、Session/CSRF、隔离 PostgreSQL 和原 Agent 工作流的联合验收 1 项通过，模型输出仍在调用边界模拟。
- 新建数据库迁移、`makemigrations --check --dry-run` 和 OpenAPI 生成校验通过；针对目标生产库执行的只读在线迁移门禁与聊天令牌归属预检通过。
- Python 文档结构与变更检查覆盖 141 个文件，0 错误、0 待复核；检查器测试 12 + 9 项通过。另人工复核前端、部署文件和第三方资源说明。
- 以上记录为发布前验证，不等同真实模型质量或生产部署成功；最终以对应提交的 GitHub Actions 结果和服务器版本、健康检查为准。

## 通用模式发布顺序

先应用新增的两条数据库迁移，再启动同时支持两种模式的 Web 与 chat_worker。旧 Worker 不接受空 company_id，不能与通用聊天混用。回滚为非空字段前须先处理通用会话及回答记录，迁移不会自动删除历史。保守在线迁移门禁会要求对 AlterField 单独审核；本次未修改门禁策略。

这两条 AlterField 已按结构审阅：仅将 `sales_conversation.company_id` 与 `chat_answerrequest.company_id` 改为可空，保留列类型、外键、索引和 PROTECT 语义，不删除或改写历史记录。生产发布先在部署锁内备份数据库，使用待发布提交的迁移检查实际 SQL 与计划，再显式应用这两条迁移；保守自动迁移门禁保持不变。旧 Web 仍要求客户字段，因此过渡期间不会从旧界面创建通用任务。随后按既有蓝绿流程排空旧聊天 Worker、切换 Web 并启动新 Worker，防止旧消费者领取通用任务。

## 多用户排队故障回归

旧聊天服务只以环境固定凭证轮询一位员工，其他员工的请求会长期 pending，即使 systemd 显示 active。共享调度修复通过真实 HTTP 验证两位无服务凭证用户的 ping 均能落库回答、临时凭证撤销、跨用户读取拒绝；保留单任务串行、默认两秒轮询、失败不重试及 SIGTERM 完成在途回报后退出的语义。已有 pending 会自然被领取，不重建消息或重置 processing。
