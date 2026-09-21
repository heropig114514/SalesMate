# KG 实验数据共享

所有有效登录账号均可跨账号读取 **KGSEED_20260921_01** 批次的 44 张表、6,000 条虚构记录。现有「客户、商机、报价、订单、工单」等销售列表直接合并显示对应实验行，标注“虚构实验 · 只读 · 归属”。顶部「实验数据」仍可进入 `/experiments/` 浏览全部 44 表及血缘。无需加入团队，也不需要管理员权限。

网页、内置聊天 Agent、工具 API 与 MCP 共同使用同一份批次清单。原客户工作台和业务写入继续使用既有授权范围；共享不转移 `owner`，不赋予模型重分析、发送、审批、修改或删除权限。个人资料表的 100 个禁用测试账号也是虚构数据的一部分，密码等登录字段不会返回。

## 页面使用

1. 用任意正常账号登录工作台，点击顶栏「实验数据」。
2. 左侧选表，可搜索原始字段内容，或按归属用户名 / ID 筛选。
3. 「查看详情」展示完整允许字段、原始主键、归属、批次、只读和虚构标记。外键链接可继续查看关联记录。
4. 「快照来源血缘」可追踪分析输入到邮件与抽取；「AI 回答生成记录」「回答引用证据」「工具读取证据」可查看回答证据链。
5. 「下载完整 JSON」返回全部表的记录、字段结构和场景关联清单。二进制文档在 JSON 中保留大小与摘要，实际内容使用详情中的「下载文件」入口取得。

在现有销售列表中，普通业务记录和实验记录按主键去重、统一分页。点击实验记录打开只读详情，不显示编辑、审批或发送操作；选中实验客户时不显示新建业务按钮，写表单的候选客户仍来自原业务权限范围。客户上下文中的私人邮件入口改为实验来源入口。计数卡包含共享实验并单独注明数量；金额沿用原业务权限范围，不加入额外共享的模拟交易。

页面合并读取使用 Session 认证的 `GET /api/v1/sales/browse/{resource}/` 和 `GET /api/v1/sales/browse/overview/`。列表支持 `company`、`status`、`archived`、`page`、`page_size`，默认 20 条、最多 100 条；仅接受 GET/HEAD/OPTIONS。原 `sales/directory/`、`sales/records/`、客户私有详情和写接口保持原授权契约。普通记录先显示，共享实验随后显示，拥有者或团队原已可见的同批记录不会重复；共享详情只展开清单内的联系人、设置和字段，不带出后来关联的普通私有记录。

分析、评分和向量沿用原夹具值。它们是模拟记录，不能当作真实模型推理、语义嵌入或模型质量评测结果。

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

## Agent、工具 API 和 MCP

普通账号可在网页助手提问：“请查看 KGSEED 实验数据目录，读取两条客户记录，注明原归属和来源。”内置 Agent 自动使用请求绑定的只读接口，无需用户手动配置 Tool token。提示词版本为 `workspace-chat-v2`，查询次数、分页预算、上下文和模型参数保持原值。新实验来源的展示顺序是文件、目录/分页、行记录，保证文件证据不会被前一页记录挤出上下文；不会把未展示来源用于引用。

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

网页 Agent 的成功读取会生成属于提问者请求的 `ToolRead`，回答引用保留实际读取证据，证据里的原 `owner` 不变。工具 API / MCP 查询不写业务记录；历史读取快照与原夹具分离，不会改写原清理清单。

## 共享边界与清理

- 只开放代码 `APPROVED_BATCHES` 中明确列出的这一批次，不通过 `KGSEED` 名称前缀判断授权，也不自动开放未来批次。
- 只读取 `AuditEvent` 原始导入清单内的精确主键，并核对保存时的行指纹。清单外记录不返回，包括后来关联到同一虚构客户的普通数据。
- 仅允许 44 个模型；凭据、连接、框架权限和任务调度表没有实验读取入口。账号密码、内部附件路径和工具授权关联字段被排除。
- 任一表出现缺失或修改的夹具行，该表返回 409；全量导出同样失败，不默默跳过损坏数据。恢复共享需维护者核验，不能为了通过检查直接刷新原清理清单的指纹。
- 清单处于文件清理状态时停止读取；清单删除后原批次路径返回 404。现有 `seed_kg_lab --action delete` 仍使用原清单清理，不必反向撤销权限记录。
- 读取日志仅记录访问账号、批次、模型与下载类型，不记录邮件正文或凭据。原夹具始终只读；聊天证据快照保存 JSON，不给原夹具添加外键依赖。

## 验证

在隔离 PostgreSQL 数据库运行：

```sh
python manage.py test tests.integration.test_experiments tests.integration.test_sales tests.integration.test_crm --noinput
python manage.py test tests.integration.test_experiment_tools tests.integration.test_chat_tools --noinput
python manage.py test tests.integration.test_business_browse --noinput
python manage.py test tests.contracts.test_schema --noinput
python tools/check_docs.py
```

实验测试覆盖全部表跨账号读取、归属、精确批次、伪造前缀、匿名拒绝、写入拒绝、旧业务权限不升级、修改和缺失时停止读取、分页、血缘外键、完整导出、附件摘要及清单撤销。Agent 测试使用真实 HTTP 和数据库，模型决策为确定性模拟；MCP 用真实 SDK 和 stdio 子进程，未安装 SDK 时该项明确跳过。不得连接实验站点实际数据库运行这些测试。
