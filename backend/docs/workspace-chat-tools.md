# 工作空间聊天：Agent 对接契约

更新：2026-09-21。本文描述已经实现的后端接口；生产环境需先应用 `chat.0003_tool_read` 并发布代码。Agent 的工具选择、模型调用循环、分页遍历和提示词由 Agent 侧实现，后端不会自动为模型安装 MCP 或执行模型调用。

## 1. 范围与身份

工作空间聊天不要求预选公司。新建会话时 `company` 可省略或为 null；提交问题和回报回答保持原结构；领取响应只返回 request_id/conversation_id/user_message_id/question/recent_history 五字段，完全省略 company_id。非空 company 不允许创建新会话，旧会话保留历史。

以下接口统一使用 `Authorization: Agent <员工绑定服务令牌>`，不接受普通 Tool token 或浏览器 Session 替代。令牌确定员工；输入不允许 owner_id、employee_id 或其他身份覆盖。Agent 凭证本身标识员工，不区分调用进程是否命名为聊天 Worker。工具目录和工具执行同时检查本人请求处于 processing，且会话仍可访问。

明确开放 `customers.search`、`customers.context`、`experiments.catalog`、`experiments.rows`、`experiments.file_read`，五个读取工具要求实时注册为 read；另开放 `experiments.create/update/delete` 并要求 write。其它工具不自动开放；真实业务写工具、确认工具、授权管理、发信和日历动作不在此入口范围。

客户搜索复用 `visible_company_ids`，客户详情复用原公司 owner 权限。团队共享搜索命中不意味着可以读取私人邮件或画像。共享实验工具复用网页精确批次清单与指纹校验，读取及维护获准虚构记录并保留 owner；不会因此开放普通私有客户详情。后端不改变 L1–L4。实验调用参数及 MCP 接入见 [实验数据共享](experiment-data.md)。

## 2. 接口列表

前缀 `/api/v1/agent/chat/`：

| 方法与相对路径 | 用途 | 必要输入 |
|---|---|---|
| `POST requests/claim/` | 领取待回答请求，原接口 | 空 JSON 对象 |
| `POST context/` | 获取原固定上下文，原接口 | request_id、scope |
| `GET tools/` | 发现本请求可用工具和 JSON Schema | 查询参数 request_id；page/page_size 可选 |
| `POST tool-reads/` | 执行读取或实验维护并登记证据 | request_id、name、arguments |
| `POST answers/` | 保存最终回答，原接口 | 原六字段回报 |
| `GET requests/<request_id>/` | 核对保存状态、消息 ID 和最终引用 | 路径 UUID |

新增接口成功和错误响应均带 `Cache-Control: no-store`。状态查询可以读取本人请求的 pending、processing、completed、failed 状态，但不会重新领取、重跑模型、恢复请求或返回完整工具历史。

## 3. 发现工具与执行

领取成功后可调用：

```http
GET /api/v1/agent/chat/tools/?request_id=<聊天请求UUID>&page=1&page_size=30
Authorization: Agent <token>
```

返回 `contract_version: "chat-tools-v1"`、request_id、tools、count、page、page_size。每个工具包含 name、description、inputSchema、executionMode、category、annotations，与原业务注册表一致，不暴露内部处理器。目录默认每页 30、上限 100，非法或重复查询参数返回 400。

搜索调用：

```json
{
  "request_id": "<聊天请求UUID>",
  "name": "customers.search",
  "arguments": {"q": "盛微", "page": 1, "page_size": 20}
}
```

`arguments` 原样遵循该工具 Schema。搜索可以省略 q，也可以使用原有 company、archived 参数。业务分页默认仍为 30、上限 100，结果原样保留 count/page/page_size/results；后端不自动翻页、不增加排序规则、不将部分页描述为全量。

搜索的 `data.results[].id` 是客户公司 UUID。要读取详情，使用该值作为详情工具的 `arguments.company_id`：

```json
{
  "request_id": "<同一个聊天请求UUID>",
  "name": "customers.context",
  "arguments": {"company_id": "<搜索结果的id>"}
}
```

聊天请求无需 company_id；详情查询仍需定位公司，后端不会猜测要读哪一家。根对象仅接受 request_id/name/arguments，不接受 idempotency_key。调用是显式的一次读取，后端不重试。

成功响应形状：

```json
{
  "request_id": "<聊天请求UUID>",
  "read_id": "<本次读取UUID>",
  "tool": "customers.context",
  "status": "completed",
  "http_status": 200,
  "revision": "<原工具版本头或null>",
  "data": {"company_id": "<公司UUID>", "company_name": "盛微"},
  "evidence_items": [
    {
      "source_id": "chat-tool:<本次读取UUID>:company:<公司UUID>",
      "source_type": "customer_context",
      "title_or_label": "盛微 · 客户资料",
      "content": "<本次实际data的完整JSON文本>"
    }
  ]
}
```

上例 data 为形状示意。真实详情完整保留原接口的摘要、grouping、context、analysis、score_detail 等字段；无画像时保留 null，不生成替代内容。revision 沿用原工具的版本表示。

## 4. 证据规则与稳定性

- 成功读取在独立 ToolRead 表中保存 arguments、原业务回执和本次 evidence_items；失败不登记证据、不结束请求。
- 详情的证据正文是本次返回 data 的完整 JSON 序列化，不二次查询、不摘要、不裁剪。较大客户数据可能很长，Agent 应按自身预算处理；截取内容后不能声称已读取全部资料。原 chat/context 的条数与字符预算保持不变。
- 搜索每个公司生成一条 customer_search 证据，正文为本次目录行；另有一条 customer_search_page 证据，包含原分页计数和本页 returned_company_ids。即使空结果也保留页级证据，能够引用“本次查询未找到”。
- 每次成功读取有新 read_id，来源以读取 UUID 和公司 UUID 区分。再次读取同一公司可能获得新版本，但旧来源和正文不变。搜索各页是各次查询的实际结果，不保证所有页来自同一个全局数据库快照。
- 原 `chat/context/` HTTP 结构及已冻结内容保持不变，工具证据不塞入其响应。读取工具不要求先读取 chat/context。
- 回报回答时，后端从本请求的原上下文及所有成功工具读取记录匹配来源三元组并附加正文。浏览器只收到最终 citations 所列的来源与正文，不收到完整 ToolRead、参数、调用历史或凭证。

**以用户最新要求为准：回答仅校验 Schema，不要求来源已登记。** 未登记、标题不匹配或来自其他请求的引用可作为元数据保存，但 content 为空，后端不会跨请求查找正文，也不声称已验证该来源。该规则替代原需求文档“未登记引用必须拒绝”的验收项。已登记引用的语义是否真正支持回答、是否用错公司，仍由 Agent 保证。

## 5. 错误及恢复

业务工具错误保留原 HTTP 状态，并返回：

```json
{
  "request_id": "<聊天请求UUID>",
  "tool": "customers.context",
  "status": "failed",
  "http_status": 404,
  "error": {"scope": "tool", "code": "not_found", "detail": "公司不存在。"}
}
```

| 情况 | HTTP | 处理原则 |
|---|---|---|
| 搜索无结果 | 200 | completed、count=0、results=[]，不是异常 |
| 工具参数不符合 Schema | 400，scope=tool | 修正显式参数后再决定是否查询 |
| 公司详情不可访问或不存在 | 404，scope=tool | 不能据此判定公司不存在；可回答详情不可用，聊天仍 processing |
| 请求信封或目录参数不合法 | 400，scope=request | 修正调用结构 |
| Agent 认证失败 | 401，scope=request | 核对员工服务凭证，不改用其他身份 |
| 工具不在白名单或已不是 read | 403，scope=request | 不尝试调用写入或确认入口 |
| 聊天请求不存在、归属错误、会话不可访问 | 404，scope=request | 停止使用该请求读取资料 |
| 请求未领取或已结束 | 409，scope=request | 核对请求状态，不复活原请求 |
| 未预期的查询、数据库或服务异常 | 500，scope=request | 明确服务失败，不当成空资料；查询状态并检查日志 |

请求级错误沿用项目统一的 `error.code/error.detail` 信封，增加 error.scope；其顶层 request_id 是 HTTP 日志关联 ID，不能替代聊天请求 UUID。工具成功与工具级失败的顶层 request_id 才是当前聊天请求 UUID。日志关联同时可使用响应头 `X-Request-ID`。

网络超时没有可靠业务结果时，调用方不得虚构成功。只读调用可由 Agent 明确决定重新读取，但不会复用同一 read_id；后端没有自动重试。回答回报响应丢失时先 `GET requests/<request_id>/` 核对终态，必要时原样重传同一结果，沿用原 answers 的幂等保护。

员工锁和请求行锁覆盖工具查询与登记，与最终回答保存使用相同锁顺序。在途查询与回报串行化：先取得锁的操作先完成，终态提交后不能再发起成功的业务读取。

## 6. 回答与 Agent 对接责任

回报仍为 request_id/chat_prompt_version/assistant_text/citations/status/error 六字段。版本为非空且不超过 100 字符的字符串，可使用 workspace-chat-v1；后端不限制具体版本或与公司绑定。

citations 项恰含 source_id/source_type/title_or_label，均为非空字符串，source_type 不超过 80 字符。后端不检查正文编号、引用重复或语义。成功要求非空正文且 error=null；失败要求空正文、空引用及非空 code/message 错误对象。错误脱敏由 Agent 负责。权限、请求状态、终态不可覆盖和幂等检查始终保留。

Agent 开发侧需完成：

1. 领取请求，根据问题决定是否查工具；普通问候可直接回答。
2. 通过工具目录取得 Schema，将选定工具调用映射到 tool-reads；无需普通 Tool token，也无需改写原 MCP 的认证方式。
3. 多公司问题逐个读取并保留不同来源；按分页字段判断覆盖范围。
4. 区分 error.scope，详情 404 不直接将整次回答判失败。
5. 使用实际返回的 evidence_items 组织引用，并按既有 answers 回报。

仓库当前通用聊天工作流没有自动启用上述工具循环；本次交付的是可供其调用的后端接口，不改变其模型调用次数、提示词或实验预算。

## 7. 迁移与验证

在正常发布流程中先应用迁移，再启用新版接口：

```powershell
python backend/manage.py migrate chat
python backend/manage.py check
python backend/manage.py makemigrations --check --dry-run
```

迁移只新增 chat_toolread 表；不需要回填旧请求、重算画像或修改员工权限。部署旧应用回滚时可保留新表，不应未经评估直接反向迁移删除读取证据。

回归命令：

```powershell
python backend/manage.py test tests.contracts.test_chat tests.contracts.test_schema tests.integration.test_chat_tools tests.integration.test_chat tests.integration.test_general_chat tests.integration.test_shared_chat_worker tests.integration.test_agent_tools --noinput
python backend/manage.py spectacular --file backend/contracts/openapi.yaml --validate --fail-on-warn
```

注释与目录检查在 backend/ 执行 `python tools/check_docs.py`。数据库测试采用真实 PostgreSQL、隔离数据库和合成记录，旧聊天 HTTP/Worker 测试模拟模型输出，不证明真实模型工具编排或生产部署已完成。

## 工作空间契约升级

本次无需新增数据库迁移。停止旧聊天 Worker 后启动新版本；新 Worker 启动时自动将旧公司绑定的 pending/processing 明确结束为 failed，错误码 workspace_chat_required，原消息、已完成结果和证据保留。领取也会处理该员工遗留旧任务，不能重新提交或重试旧公司会话；用户需在工作空间明确重新提问。普通工作空间 processing 不会被重置或重派。

前端移除客户专属聊天入口，旧客户聊天链接只打开工作空间。邮件草稿可以来自本人工作空间；发信动作仍必须明确选择客户并另行审阅批准，其他员工或另一客户历史草稿不可使用。

CI 保留并更新工作空间契约测试：直接解析五字段领取、action:tool → 真实 HTTP 查询 → action:answer、共享 Worker 多员工隔离，以及网页实际提问和引用回读。后端不验证模型内容含义、提示词版本枚举或引用真实性；继续验证 Schema、身份、权限、请求状态与幂等。

实验写入复用请求绑定接口，使用当前请求 UUID 与完整参数派生幂等键，数据变更和回执证据在同一事务提交。更新/删除要求最近读取的 fingerprint，错误不会伪装为成功。详细字段、边界与清理见 [实验数据共享](experiment-data.md)。
