# 图谱模型与 Agent 工具交接

更新日期：2026-09-25。本文是后续 Agent 开发的入口；运行行为以实际工具目录及源码为准。接口已封装为 HTTP SDK 和 stdio MCP，但尚未接入现有工作空间聊天 Agent 的工具选择流程。不要把文档或 MCP 配置的存在解释为聊天已经可以自动建图。

## 1. 当前交付与可用边界

- 基线代码：`semantic-graph-v0.2.0`，提交 `6b7098ea91455b421c27876e524298a02fb2162d`。代码位于 `feat/semantic-graph-mcp-v2` 分支，[PR #1](https://github.com/heropig114514/SalesMate/pull/1)；不要假设已合并默认分支。后续文档提交不表示服务器同步部署。
- 隔离服务器运行微调 V2 Q4_K_M 权重，推理配置为 `baseline`（16K 上下文、单槽位、CPU 2 线程）。启动器未指定 variant 时仍选择官方模型；权重版本和推理配置是两件事。
- 结构化 `records` 输入、查询、来源重放与撤回，已通过真实 HTTP/MCP 闭环验证。这条路径不调用 LLM。
- 自然语言图谱抽取仍是实验能力：微调 Q4 在 12 条合成测试中，结构/证据合法 8/12、事实完全匹配 6/12；不可视作真实邮件准确率。
- 服务器完整 schema 文本请求曾在 baseline 约 301.34 秒、临时 prefix8 约 300.28 秒返回 502/ReadTimeout。未写入相应失败来源。prefix8 未通过本机质量门槛，已恢复 baseline。
- 2026-09-25 本次交接前在线诊断：两项服务 active、模型 health HTTP 200；13 个输入 token、2 个输出 token 的“Reply with exactly: OK”请求成功返回 OK，但耗时 44.301 秒。这是单次短探测，无完整 schema、无图谱写入，不是生产延迟承诺。
- 同次服务器内存快照：3.7 GiB RAM、约 466 MiB available、约 4 GiB swap 已使用。健康检查成功不代表自然语言建图可在时限内完成。
- Kaggle 单 T4、完整 schema 的 12 条微调 Q4 请求平均 6.045 秒，范围 4.08–8.14 秒，平均输入约 4135 tokens；仅计模型请求，不计加载、业务校验、数据库写入及客户端跨网延迟。Kaggle 是已完成的实验 Notebook，不是已经部署的常驻模型 API。

## 2. Agent 应调用哪一层

| 目的 | 入口 | 副作用与限制 |
|---|---|---|
| 查询实体、事实、来源证据 | `graph.entities/facts/lineage/episodes/episode` | 读取当前身份的图谱；事实 active 不等于人工确认 |
| 从文本或邮件建立观察与关系 | `graph.ingest` 的 `text` 分支 | 调用模型、校验、解析实体、保存来源并同步图谱；是写工具 |
| 导入不完整业务记录 | `graph.ingest` 的 `records` 分支 | 不调用模型；按 schema 建立观察，不直接创建正式订单 |
| 撤回来源支持 | `graph.retract` | 保留历史和其他来源的支持；不是删除全部实体 |
| 只生成候选、不保存 | 尚无公开 graph dry-run 工具 | 不要把 `graph.ingest` 当成无副作用的抽取接口 |

独立模型服务 `127.0.0.1:8088/v1/chat/completions` 只产生文本，不执行图谱授权、证据校验或持久化。业务 Agent 应走后端工具。若以后确需纯抽取，应另行定义、实现和验证独立接口，不伪称已有 dry-run。模型不会随输入在线训练；实时图谱保存在 PostgreSQL。

## 3. 接入配置与认证

完整 SSH 隧道、依赖安装和凭证签发见[部署说明](semantic-graph-deployment.md)。服务器 API 仅监听 `127.0.0.1:8090`；通过 SSH 转发后，本机使用 `http://127.0.0.1:18090`。生产网站地址不是本沙盒的模型工具入口。

在仓库根目录、安装 `integrations/salesmate_tools/requirements.txt` 的 Python 环境中，配置：

```text
SALESMATE_TOOLS_URL=http://127.0.0.1:18090
SALESMATE_TOOLS_TOKEN=<当前用户的专用 Tool 凭证>
SALESMATE_TOOLS_TIMEOUT=360
```

令牌由私有环境或密钥管理注入，不把占位符当实际值，不写进 Git、工具参数或日志。`Tool` 凭证不同于 Gmail/聊天 Worker 的 `Agent` 凭证。隔离图谱沙盒要求鉴权；其他文档里的免登录实验模式不适用于此部署。用户身份由凭证决定，不能传入 owner 冒充其他用户。只读 Agent 只授予查询工具；建图和撤回分别需要对应写工具授权。

SDK 默认超时 30 秒；显式 360 秒只是客户端等待上限，后端模型超时仍为 300 秒。SDK 不加载项目 `.env`，调用进程必须实际得到这些环境变量。

MCP 客户端配置使用已安装依赖的 Python 绝对路径为 command，args 为 `["-m", "integrations.salesmate_tools.mcp_server"]`，cwd 为仓库根目录，并向子进程传入上述环境变量。不支持 cwd 的客户端可设置 PYTHONPATH 为仓库根目录。这是 stdio MCP，无 HTTP MCP URL。读取 `tools/list` 的全部分页，只使用实际授权目录中的工具和 inputSchema；MCP 层和 Agent 层等待时限也需覆盖后端调用。

## 4. 最小调用与返回值

先发现契约并读取 schema，再按用户授权调用。Python SDK 示例从仓库根目录运行；最后一条会写入合成观察，不是只读探测：

```python
from integrations.salesmate_tools.client import ToolClient

client = ToolClient.from_env()
spec = client.describe("graph.ingest")
schema_result = client.call("graph.schema", {})
status_result = client.call("graph.status", {})
result = client.call("graph.ingest", {
    "source_key": "handoff-demo-20260925-001",
    "observed_at": "2026-09-25T10:00:00+08:00",
    "records": [
        {"key": "customer", "schema": "crm.company",
         "fields": {"name": "SandboxAcme"}},
        {"key": "quote", "schema": "sales.quote",
         "fields": {"number": "Q-DEMO", "company": {"record": "customer"}}}
    ]
})
```

自然语言分支使用同一工具，将 `records` 替换为 `text`，二者不能同时存在。以下是调用参数示例，当前小服务器可能超时，不承诺成功：

```json
{
  "source_key": "email-demo-20260925-001",
  "observed_at": "2026-09-25T10:00:00+08:00",
  "text": "Acme needs Edge. Its budget is 800 SGD."
}
```

邮件可将主题、换行、纯文本正文组成 text；本功能不拉取邮箱、不解析附件。source_key 应由外部来源稳定标识生成，observed_at 使用固定的来源观察时间，不要在同一来源重放时重新取当前时间。

HTTP 等价请求为 `POST /api/v1/agent-tools/call/`，头 `Authorization: Tool <token>`，body 为 `{"name":"graph.ingest","arguments":{...}}`。返回外层 `{tool,status,http_status,data}`；来源结果在 data，包含 `id`、`source_key`、`extraction`、`model_audit`、`sync` 等字段。必须检查工具状态和 `data.sync.current`，不能仅凭收到 JSON 或 MCP 传输成功断言建图成功。MCP 协议错误还需检查 `isError`。

模型输出为 entities/facts 候选。`model_audit.raw_model_extraction` 与实体解析决定分开保存，程序匹配收益不能算作模型准确率。输出和事实中的原文引句用于追溯，不证明语义正确。查询事实后用真实 fact_id 调用 `graph.lineage`；不编造实体、来源或事实 ID。详细字段见[图谱接口](semantic-graph.md)和[输入输出说明](semantic-model-experiments.md)。

## 5. 失败、幂等与 Agent 调度

| 状态 | 调用方应如何处理 |
|---|---|
| 400 | 根据实际 inputSchema/schema 修正参数；不要删掉必需证据或伪造缺失信息 |
| 401/403 | 检查凭证到期、撤销及工具范围；不能切换匿名或其他用户身份绕过 |
| 404 | 资源不存在或不可见，不据此猜测其他用户的数据 |
| 409 | 区分来源键内容冲突与推理期间图谱变化；保留原调用，核对状态后由业务决定后续处理 |
| 502 | 模型生成、超时或校验失败；如实报告，不能把候选或空结果当成功 |
| 503 | 图谱未就绪，先核对状态与维护进程，不读取为最新图谱 |
| 连接中断/客户端超时 | 写入结果可能未知；先分页查 `graph.episodes`，再用 episode_id 查详情，不自动换键重试 |

同一用户、相同 source_key、相同输入和观察时间重放复用同一来源，不重新推理；同键内容或时间改变返回 409。更正需要新来源并显式处理旧来源撤回。`graph.ingest`/`graph.retract` 使用来源自身幂等，不传普通工具的 UUID `idempotency_key`。来源分页不是 source_key 搜索接口，应处理全部相关页。

当前后端是同步请求，模型单槽位；没有已实现的异步任务提交、任务轮询或并发吞吐保证。不要直接沿用邮件 L1 的四路并发策略。未来 Agent 接入时需显式设计串行调度/任务等待、用户状态提示及写操作授权；本交接没有更改现有工作流。不得自动重试、切换模型、截断 schema、提高超时或修复模型答案以制造成功。未来实验条件变更仍需按项目变更规范处理。

## 6. 代码定位、验证与后续工作

| 路径 | 职责 |
|---|---|
| `backend/apps/agent_tools/graph.py` | 九项工具目录、输入契约与路由 |
| `integrations/salesmate_tools/client.py`、`mcp_server.py` | 独立 HTTP SDK 与 stdio MCP |
| `backend/apps/knowledge_graph/semantic_contract.py`、`semantic_provider.py` | Prompt、JSON 输出约束与模型请求 |
| `backend/apps/knowledge_graph/episodes.py`、`entity_resolution.py`、`episode_projection.py` | 来源事务、实体关联与投影 |
| `backend/apps/knowledge_graph/business_schema.py` | 业务 schema 目录 |
| `backend/tools/run_graph_model.py`、`backend/apps/knowledge_graph/inference_profiles.py` | 权重校验与推理配置 |
| `backend/tools/smoke_graph_api.py`、`integrations/salesmate_tools/smoke_graph.py` | 真实 HTTP/MCP 沙盒验证，包含写入 |

历史验证：46 项数据库/契约回归测试通过，真实结构化 HTTP/MCP 闭环通过；模型生成质量和服务器文本超时见[优化记录](semantic-inference-optimization.md)。不要将 mock 测试、结构化冒烟或 health 通过解释为真实文本建图已验收。

接手顺序：确认 checkout 和目标环境 → 配置最小 Tool 授权 → 发现工具并读 schema/status → 在专用沙盒验证结构化写入/查询/血缘/撤回 → 单独验证文本质量和延迟 → 再实现聊天 Agent 的显式工具注册与执行流程。图谱工具尚未成为现有聊天的自动工具，不修改现有 L1–L4 链路即可独立联调 SDK/MCP。

复现与权重位置、SHA256、服务启停、私有凭证和 SSH 方式见[部署说明](semantic-graph-deployment.md)。固定评测条件和性能结果见[微调说明](semantic-model-experiments.md)、[推理优化](semantic-inference-optimization.md)。本次交接只补文档，不改变模型、参数、接口、权限或服务器运行配置。
