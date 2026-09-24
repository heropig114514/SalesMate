# 业务 schema 与外部信息自动关联建图

本功能将 PostgreSQL 内部业务图、按 schema 提交的不完整记录、邮件和自然语言观察连接起来。结构化业务数据保持权威；外部输入先进入独立观察层，不会因为缺少字段而伪造默认值，也不会自动创建业务订单、确认交易、修改共享权限或发信。

## 已实现的范围

- `GET /api/v1/graph/schema/` 从 Django 元数据返回 48 类业务模型的字段、类型、可空性、外键和枚举。覆盖公司资料、客户、联系人、产品、商机、工单、报价及明细、订单及明细、跟进、会话、邮件、分析、知识条目、业务新闻和活动、工具回执等。身份、凭据、任务队列和同步检查点不属于输入目录；授权/凭据字段不进入模型上下文。
- 原十表图谱扩展为 48 类业务来源，加上 `Episode` 共49张捕获表。原有购买事实和邮件 L1 规则保留；新增 `schema.<模型>.<字段>` 关系/属性及外部观察。映射版本为 `salesmate-kg-v2`。
- 结构化输入只检查实际给出的字段；省略和 null 均保持未知，完整原始 JSON 留存。外键可引用本批次记录或来源 ID；尚未存在的目标形成同用户图中的占位实体，后续来源进入数据库后重新投影即可关联。
- 自然语言由本机 Qwen3-4B-Instruct-2507 Q4_K_M 进行抽取和关联建议。协议 v4 使用 JSON Schema 区分关系与属性，限定局部引用、谓词和空值位置；再校验原文连续引用、实体类型、谓词及目标归属，执行独立实体解析：有效 ID 优先，否则同类型唯一精确名称关联，多候选可用相同已知邻居限定；仍有歧义保留新实体。原始模型结果和程序解析决定分别保存，不把程序关联冒充模型能力。失败明确返回，不修复输出或回退模型。
- 已有实体提供给模型的内容包括标签、少量身份字段和既有观察事实。模型可复用这些实体；新实体使用 `external.<业务模型>` 类型，仅存在图谱中。关联包含模型建议和程序解析判断，不是已核验身份合并。
- 每个观察保留发生时间、接收时间、输入、模型参数和提示摘要。原始来源版本、派生和支持路径可通过现有 lineage 接口查询。不同值保留并标记 `needs_review`；不会用最新陈述擅自覆盖旧值，也不声称完整双时间推理已实现。
- 新邮件可由 `graph_ingest --emails --watch` 自动输入。模型处理失败时命令退出，不自动重试；已有来源包括已撤回来源不重复调用。邮件删除、转为非业务、方向变化或正文变化后，普通 graph_worker 撤销不再有效的旧邮件观察支持。新正文需要新观察。

## 模型与数据库启动

从 `SalesMate` 根目录执行。首次应用迁移会为已有用户排队回填；模型推理与数据库事务分离，图谱继续按用户重算。

```powershell
.venv/Scripts/python.exe backend/manage.py migrate --noinput
.venv/Scripts/python.exe backend/manage.py graph_worker --once

# 在独立终端运行本机模型。模型必须是本项目已核验的 Q4 制品，脚本验证 SHA256。
.venv/Scripts/python.exe backend/tools/run_graph_model.py `
  --executable ../output/crmarena-cpu-q4-20260924/llama-bin/llama-server.exe `
  --model ../output/crmarena-cpu-q4-20260924/Qwen3-4B-Instruct-2507-Q4_K_M.gguf
```

在运行 Web 或输入命令的环境中明确设置：

```powershell
$env:GRAPH_LLM_URL = 'http://127.0.0.1:8088/v1'
$env:GRAPH_LLM_MODEL = 'salesmate-graph'
```

本机模型只绑定回环地址，不经过环境代理或重定向，不发送远程请求。新流程使用纯 CPU、8线程、16384上下文、最大输出1536 tokens、temperature=0、seed=2026、单次HTTP超时300秒；关闭上下文滑动，超长或非正常结束明确失败。它没有更改 CRMArena 冻结题、抽取规则、双T4要求或原CPU实验条件。

权重身份由启动脚本校验；每次响应保留模型别名及参数，不将别名等同于独立远程权重验证。默认仍为官方权重；另有已完成的微调V2实验制品，须显式选择semantic-v2。质量限制与部署见[使用与部署](semantic-graph-deployment.md)。

## 输入示例

保存以下内容为 UTF-8 JSON 文件，例如 `partial-input.json`。观察仅含部分报价字段，客户可以在同批次不完整地给出：

```json
{
  "source_key": "demo-quote-001",
  "observed_at": "2026-09-24T20:00:00+08:00",
  "records": [
    {"key": "customer", "schema": "crm.company", "fields": {"name": "Acme"}},
    {"key": "quote", "schema": "sales.quote", "fields": {"number": "Q-001", "company": {"record": "customer"}}}
  ]
}
```

`source_id` 可放在单条 record 上，与该 schema 的原始记录 ID 对齐。也可以直接在外键字段给出目标 ID 字符串。未知目标只建立图谱占位，不会跨用户取数。省略 `source_id` 时，结构化输入按提供的 sku、number、email、group_key、source_key、company_name、name、title 与同类型候选作唯一精确匹配；存在歧义则保留为独立观察实体。这些匹配也不能视为经过人工核验的身份。

自然语言输入使用同一信封，但 `text` 和 `records` 必须二选一：

```json
{
  "source_key": "meeting-001",
  "observed_at": "2026-09-24T20:10:00+08:00",
  "text": "Mira 是 Acme 的联系人。Acme 需要 Edge，预算为 800 SGD。"
}
```

```powershell
.venv/Scripts/python.exe backend/manage.py graph_ingest --owner 1 --input partial-input.json

# 仅处理该账号已存的入站业务邮件；不会拉取邮箱或发送邮件。
.venv/Scripts/python.exe backend/manage.py graph_ingest --owner 1 --emails --watch
```

同来源键和相同内容重放不调用模型；同键不同内容返回409。更正内容应使用新键；明确撤回旧来源再加入新来源可避免把更正误作并存候选，撤回不会删除历史。

## HTTP 接口

沿用项目身份和 Session CSRF；owner 来自当前登录用户，不接受请求指定 owner。开发实验模式仍沿用项目既有开关，没有为该功能扩大权限。

| 接口 | 用途 |
|---|---|
| `GET /api/v1/graph/schema/` | 输入 schema 目录 |
| `POST /api/v1/graph/episodes/` | 接收上述 JSON 并自动关联、同步 |
| `GET /api/v1/graph/episodes/` | 本人来源分页列表 |
| `GET /api/v1/graph/episodes/{id}/` | 原文、抽取、模型审计及图谱同步状态 |
| `POST /api/v1/graph/episodes/{id}/retract/` | 空对象请求撤回该来源 |
| `GET /api/v1/graph/entities/` | 查询当前实体，包括 external.* |
| `GET /api/v1/graph/facts/?entity={id}` | 查看实体关系及观察属性 |
| `GET /api/v1/graph/facts/{id}/lineage/` | 回查来源版本与原文证据 |

图谱未就绪或捕获缺失返回503；上下文改变/来源键冲突返回409；模型或证据失败返回502，含阶段和安全错误原因。结果中的 `sync.current` 才表示当前图谱就绪，保存观察与图谱发布不是同一个状态。

## 边界及验证

首版不是自动业务决策服务。原文引用存在不证明语义正确；模型可能漏提实体、错误对齐或误读否定。纯名称相同也可能误关联，部署前应建立针对业务的实体对齐与关系标注集。

真实合成样例中，Qwen 将“是 Acme 的联系人”映射为 `works_for`。这超出了严格的雇佣语义，虽然原文引用校验通过，仍只能作为模型提出的关系候选。当前没有独立语义判定模型或人工审核界面，不能据此声称所有入图关系已经核验。

自然语言最多12000字符，结构化记录最多30条/60000字符；模型输出最多30个实体和60个事实。整个关联上下文最多80000字符，超过明确拒绝，不静默丢弃候选；本机模型还受 token 上限限制。大图尚需分层候选检索和性能评测。图谱仍按用户全量重算，未证明大规模性能。PDF/网页解析、主动爬取、通用聊天自动调用及图形浏览页面不在本轮实现范围。

数据库测试使用真实 PostgreSQL，模型边界有明确 mock；真实模型冒烟使用独立 `salesmate_semantic_smoke_` 数据库与合成信息。实际运行记录见本轮交付报告，不能将模拟测试解释为真实模型准确率。本次发布的检查与Git提交见[使用与部署](semantic-graph-deployment.md)；历史冒烟与当前发布验证分别记录。
