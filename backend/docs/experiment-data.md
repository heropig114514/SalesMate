# KG 实验数据共享

当前算法联调服务器启用[公开实验模式](laboratory-access.md)：所有业务数据（含非 KGSEED）免登录、跨账号可读写，Tool/MCP 无需令牌。下文原鉴权约束仅在关闭实验开关后生效，保留的外部动作与密钥边界见该说明。

所有有效登录账号均可跨账号读取 **KGSEED_20260921_01** 批次的 44 张表（最初 6,000 条虚构记录，维护后数量以目录为准）。现有「客户、商机、报价、订单、工单」等销售列表直接合并显示对应实验行，标注虚构、维护权限和原归属。顶部「实验数据」仍可进入 `/experiments/` 浏览全部 44 表及血缘。无需加入团队，也不需要管理员权限。

网页、内置聊天 Agent、工具 API 与 MCP 共同使用同一份批次清单。原客户工作台和业务写入继续使用既有授权范围；共享不转移 `owner`，仅通过共享维护入口开放下述业务数据的新增、修改和删除，不赋予外部发送或队列执行权限。个人资料表的 100 个禁用测试账号也是虚构数据的一部分，密码等登录字段不会返回。

## 页面使用

1. 用任意正常账号登录工作台，点击顶栏「实验数据」。
2. 左侧选表，可搜索原始字段内容，或按归属用户名 / ID 筛选。
3. 「查看详情」展示完整允许字段、原始主键、归属、批次、可写能力和虚构标记。外键链接可继续查看关联记录。
4. 「快照来源血缘」可追踪分析输入到邮件与抽取；「AI 回答生成记录」「回答引用证据」「工具读取证据」可查看回答证据链。
5. 「下载完整 JSON」返回全部表的记录、字段结构和场景关联清单。二进制文档在 JSON 中保留大小与摘要，实际内容使用详情中的「下载文件」入口取得。

在现有销售列表中，普通业务记录和实验记录按主键去重、统一分页。点击实验记录打开详情，可维护项提供「编辑 / 删除共享记录」链接；选中实验客户时不显示新建业务按钮，写表单的候选客户仍来自原业务权限范围。客户上下文中的私人邮件入口改为实验来源入口。计数卡包含共享实验并单独注明数量；金额沿用原业务权限范围，不加入额外共享的模拟交易。

页面合并读取使用 Session 认证的 `GET /api/v1/sales/browse/{resource}/` 和 `GET /api/v1/sales/browse/overview/`。列表支持 `company`、`status`、`archived`、`page`、`page_size`，默认 20 条、最多 100 条；仅接受 GET/HEAD/OPTIONS。原 `sales/directory/`、`sales/records/`、客户私有详情和写接口保持原授权契约。普通记录先显示，共享实验随后显示，拥有者或团队原已可见的同批记录不会重复；共享详情只展开清单内的联系人、设置和字段，不带出后来关联的普通私有记录。

分析与评分可按明确维护请求修改，向量仍保留原夹具值。它们是模拟记录，不能当作真实模型推理、语义嵌入或模型质量评测结果。

## 只读接口

本节网页接口沿用网站的 Session 认证，只接受 GET、HEAD、OPTIONS。未登录不能读取数据；不把网页会话或 Agent 凭据共享给其他人。Agent 工具认证见下一节。

| 接口 | 内容 |
|---|---|
| `/api/v1/experiments/` | 已批准批次、44 张表、登记数量、字段与外键结构 |
| `/api/v1/experiments/KGSEED_20260921_01/crm.Company/` | 客户表；其他模型名以目录为准 |
| `/api/v1/experiments/KGSEED_20260921_01/export/` | 全部表的 JSON 下载 |
| `/api/v1/experiments/KGSEED_20260921_01/sales.Attachment/{pk}/download/` | 客户附件 |
| `/api/v1/experiments/KGSEED_20260921_01/accounts.SetupDocument/{pk}/download/` | 产品与方案文档 |

表接口支持 `q`、`owner`、`pk`、`page`、`page_size`。默认每页 50 条，最多 200 条；精确主键和外键保持数据库原始身份，不重新编号。导出包中 `scenario_links` 是生成器登记的模拟场景关联，不是模型发现的关系。

## 公司分析补充资料

公司分析 Worker 已可通过原 Agent 上下文接口获得跨账号的匹配实验资料，无需额外 Tool 令牌；L2 独立保存来源，L3 可使用补充人数，来源变更后旧缓存失效。契约与集成方法见 [公司资料补充](company-enrichment.md)。

## Agent、工具 API 和 MCP

普通账号可在网页助手提问：“请查看 KGSEED 实验数据目录，读取两条客户记录，注明原归属和来源。”内置 Agent 自动使用请求绑定的数据工具接口，无需用户手动配置 Tool token。提示词版本为 `workspace-chat-v3`，查询次数、分页预算、上下文和模型参数保持原值。维护回执与文件优先展示，其后是目录/分页、行记录，保证文件证据不会被前一页记录挤出上下文；不会把未展示来源用于引用。

外部算法程序先由各自账号登录网站，向 `POST /api/v1/agent-tools/credentials/` 提交以下授权（携带 Session 和 CSRF）：

```json
{"name":"KG实验只读","expires_in_hours":24,"allowed_tools":["experiments.catalog","experiments.rows","experiments.file_read"]}
```

返回的 token 仅显示一次。程序使用 `Authorization: Tool <token>` 访问 `GET /api/v1/agent-tools/catalog/?category=experiments` 和 `POST /api/v1/agent-tools/call/`。已有令牌的名称清单在签发时冻结，部署不会自动扩权；需要签发包含新工具的新令牌。新签发的 `read_only` 模板也包含这三个工具。

| 工具 | 参数及返回 |
|---|---|
| `experiments.catalog` | `{}`；返回批次、原归属、表计数、字段及外键 |
| `experiments.rows` | 必填 `batch`、`model`；可选 `q`、`owner`、`pk`、`page`、`page_size`；返回分页和保留主外键的原记录 |
| `experiments.file_read` | 必填 `batch`、`model`、`pk`、`format`、`offset`、`limit`；仅附件/方案文档；返回归属、摘要、内容块及 `next_offset` |

例如向工具调用入口发送：

```json
{"name":"experiments.rows","arguments":{"batch":"KGSEED_20260921_01","model":"crm.Company","page":1,"page_size":20}}
```

工具分页默认 50、最多 100 条，网页 Agent 每页最多 20 条。按 `count` 和页码逐页读取全部数据，不能把一页称为全量。文件 `text` 支持 UTF-8 TXT 或已核验为 `text/plain` 的实验记录，每次最多 16,000 字符；`base64` 每次最多 256 KiB 原始字节。两者都必须显式给出偏移和长度，二进制文档不会自动提取文本。

MCP 使用已有 stdio 桥接，无需新增公网端口。安装 `integrations/salesmate_tools/requirements.txt`，宿主以仓库根目录为工作目录，启动 `python -m integrations.salesmate_tools.mcp_server`，私密环境设置 `SALESMATE_TOOLS_URL=https://milkdragon.dev` 及各自的 `SALESMATE_TOOLS_TOKEN`。MCP 动态发布该令牌获准的目录，不维护第二套实验权限。完整配置见 [业务工具接入](agent-business-tools.md)。

网页 Agent 的成功读取会生成属于提问者请求的 `ToolRead`，回答引用保留实际读取证据，证据里的原 `owner` 不变。读取不会修改业务记录；维护会原子更新当前清理清单，并保存原清单与操作者审计。证据快照与夹具分离。

## 新增、修改和删除

所有有效登录账号（包括新注册账号）均可维护 28 类业务与知识数据：公司资料、引导资料、销售画像；客户、联系人、邮件、持久邮件、抽取事实、分析快照、分析结果、评分、快照来源与失效记录；客户设置、别名、联系人补充资料、产品、工单、商机、报价及明细、订单及明细、跟进、会话、消息、草稿；知识条目。三个单账号资料模型仅允许修改和删除，避免新增时覆盖批次拥有者的真实资料。

身份账号、团队与授权、附件/文档、向量、通知、执行请求/回执及审计证据等 16 类表保留只读。目录 `tables[].write` 与分页响应的 `write` 是当前能力和字段的权威说明。

网页：在「实验数据」选表后点击「新增共享虚构记录」；详情中点击「修改记录」或「删除记录」。普通业务页详情也有维护链接。外键需填同批次主键，归属不能修改；持久邮件与跟进任务只能保存终态（completed/failed 或 completed/cancelled），不启动后台处理或提醒。每次操作保存操作者、时间、变更字段名和前后指纹。删除只允许没有反向引用的单行，不隐式级联。

工具 API：仍用 `POST /api/v1/agent-tools/call/`，Session 需要 CSRF，外部调用需要包含对应工具名称的 Tool token。写入信封必须提供 UUID `idempotency_key`；结果未知时保持原参数和同一键，不自动重新生成逻辑操作。

| 工具 | arguments |
|---|---|
| `experiments.create` | `batch`, `model`, `data`（只含 `write.fields` 中的字段） |
| `experiments.update` | `batch`, `model`, `pk`, `expected`, `data` |
| `experiments.delete` | `batch`, `model`, `pk`, `expected` |

`expected` 必须等于最近读取的行 `fingerprint`；版本过期返回 409。保存与当前清单指纹原子更新，非法字段/关系返回 400，未开放模型返回 403，非清单主键返回 404。新行自动登记批次，归属固定为批次拥有者。首次维护保留 `original_rows`，后续变更追加 `mutations`。生成器的 `scenario_links` 保留原始值，发生维护后 `scenario_links_status=original_before_edits`，不能把它当作更新后的真值。

内置网页 Agent 可按明确请求维护虚构数据，例如“请把 KGSEED 中已确定主键的客户名称改为……”。它先读取目录与最新指纹，再执行维护并引用真实回执。请求绑定接口保留 `tool-reads/` 路径；写工具按 `executionMode=write` 执行，幂等键由请求 ID、工具名和完整参数派生。真实业务写入和外部动作仍不在工作空间聊天范围内。

MCP 动态发布三个新增写工具，参数除上表之外须显式带 `idempotency_key`。旧只读 token 不自动扩权；可重新签发明确包含六个实验工具的 token，或使用会包含其它业务维护权限的 `data_management` 预设。

## 共享边界与清理

- 只开放代码 `APPROVED_BATCHES` 中明确列出的这一批次，不通过 `KGSEED` 名称前缀判断授权，也不自动开放未来批次。
- 只读取 `AuditEvent` 当前批次清单内的精确主键，并核对保存时的行指纹。清单外记录不返回，包括后来关联到同一虚构客户的普通数据。
- 仅允许 44 个模型；凭据、连接、框架权限和任务调度表没有实验读取入口。账号密码、内部附件路径和工具授权关联字段被排除。
- 任一表出现未通过维护入口登记的缺失或修改，该表返回 409；全量导出同样失败，不默默跳过损坏数据。恢复共享需维护者核验，不能绕过维护事务手动刷新指纹。
- 清单处于文件清理状态时停止读取；清单删除后原批次路径返回 404。现有 `seed_kg_lab --action delete` 使用当前清单与当前外键依赖顺序清理，不必反向撤销权限记录。
- 读取日志仅记录访问账号、批次、模型与下载类型，不记录邮件正文或凭据。维护日志只含定位元数据；聊天证据快照保存 JSON，不给原夹具添加外键依赖。

## 验证

在隔离 PostgreSQL 数据库运行：

```sh
python manage.py test tests.integration.test_experiments tests.integration.test_sales tests.integration.test_crm --noinput
python manage.py test tests.integration.test_experiment_tools tests.integration.test_chat_tools --noinput
python manage.py test tests.integration.test_business_browse tests.integration.test_experiment_writes --noinput
python manage.py test tests.contracts.test_schema --noinput
python tools/check_docs.py
```

实验测试覆盖全部表跨账号读取、归属、精确批次、伪造前缀、匿名拒绝、受保护写入拒绝、跨账号 CRUD、幂等及旧指纹冲突、旧业务权限不升级、修改和缺失时停止读取、分页、血缘外键、完整导出、附件摘要及清单撤销。Agent 测试使用真实 HTTP 和数据库，模型决策为确定性模拟；MCP 用真实 SDK 和 stdio 子进程，未安装 SDK 时该项明确跳过。不得连接实验站点实际数据库运行这些测试。
