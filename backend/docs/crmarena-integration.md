# CRMArena 图谱与 BANT 接口

已接入后端和现有 stdio MCP。当前可直接使用公开图谱取证与已完成评测结果回放；实时推理代码已接入，但本机未配置固定权重及双 T4，调用会明确返回 503。

这套接口的对象是 **CRMArena-Pro B2B 公开合成 Lead**，不是 SalesMate 私有客户 UUID，也不是成交概率模型。公开图含 12,905 个节点、23,411 条边；回放与实时推理仅支持原 75 道冻结题。证据查询支持图中有通话关联的 Lead。此版本不接受新问题、私有会话、自定义模型或推理参数。

本轮回归 grounded exact match 为 **8/60（13.3%）**，44/60 因抽取校验失败而弃答。该接口用于研究、展示来源与进一步开发，不用于自动决定线索去留。原 v2 的 25/60（41.7%）仍作为实验对照。数据许可为 CC BY-NC 4.0，限研究使用，来源为 Salesforce CRMArena-Pro；模型身份和运行版本由响应提供。

## HTTP 接口

服务根地址示例：`http://127.0.0.1:8000`。路径均已加入 OpenAPI。

| 方法及路径 | 作用 | execution_type |
|---|---|---|
| GET `/api/v1/graph/crmarena/` | 版本、图统计、评测指标、示例 Lead、运行状态 | 不适用 |
| POST `/api/v1/graph/crmarena/evidence/` | 查询通话、产品、价格、政策和出处 | `evidence_query` |
| POST `/api/v1/graph/crmarena/evaluation/` | 读取已完成 Kaggle 运行的原始结果 | `recorded_evaluation` |
| POST `/api/v1/graph/crmarena/predict/` | 固定模型重新执行该题 | `live_inference` |

三个 POST 接口均只接受以下 JSON，字段必填且必须为字符串：

```json
{
  "dataset_id": "crmarena-pro-b2b-v3",
  "lead_id": "00QWt00000898Y2MAI"
}
```

额外字段、私有数据空间、非字符串或不符合格式的 ID 为 400；公开 Lead 不存在、没有通话或不在推理题集内为 404。当前数据只读，不触发订单、商机状态、评分或业务图写入。

正式模式沿用登录 Session，POST 需要 CSRF。Tool token 应通过 `/api/v1/agent-tools/call/`，不直接用于 graph 路由。当前本地已有实验开放模式可使用下面 PowerShell 示例；本次未更改鉴权开关，也未创建或扩大任何凭证。

```powershell
$base = 'http://127.0.0.1:8000'
$body = Get-Content -Raw backend/docs/examples/crmarena-lead.json

# 模型和图谱信息
Invoke-RestMethod "$base/api/v1/graph/crmarena/"

# 证据查询：无需 GPU
Invoke-RestMethod "$base/api/v1/graph/crmarena/evidence/" `
  -Method Post -ContentType 'application/json' -Body $body

# 已完成评测结果回放：无需 GPU
Invoke-RestMethod "$base/api/v1/graph/crmarena/evaluation/" `
  -Method Post -ContentType 'application/json' -Body $body

# 实时推理：须先满足后面的双 T4 部署条件；未配置时返回 503
Invoke-RestMethod "$base/api/v1/graph/crmarena/predict/" `
  -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 600
```

正式模式下，前端可在已登录且持有 CSRF cookie 的页面调用：

```javascript
const csrf = document.cookie.split('; ')
  .find(item => item.startsWith('csrftoken='))?.split('=').slice(1).join('=');
if (!csrf) throw new Error('请先通过现有登录流程取得 CSRF cookie');
const response = await fetch('/api/v1/graph/crmarena/evaluation/', {
  method: 'POST',
  credentials: 'same-origin',
  headers: { 'Content-Type': 'application/json', 'X-CSRFToken': decodeURIComponent(csrf) },
  body: JSON.stringify({ dataset_id: 'crmarena-pro-b2b-v3', lead_id: '00QWt00000898Y2MAI' }),
});
const result = await response.json();
if (!response.ok) throw new Error(JSON.stringify(result.error));
console.log(result.answer, result.execution_type);
```

## 输出契约

回放示例的主要字段如下；接口还返回图 SHA、模型 revision、研究限制和原始文本。

```json
{
  "dataset_id": "crmarena-pro-b2b-v3",
  "synthetic": true,
  "usage_scope": "public_synthetic_research_only",
  "execution_type": "recorded_evaluation",
  "lead_id": "00QWt00000898Y2MAI",
  "query_id": "official-51",
  "model_id": "Qwen/Qwen3-4B-Instruct-2507",
  "answer_origin": "llm",
  "kaggle_run_id": 352410191,
  "kaggle_version": 2,
  "answer": {
    "failed_factors": ["Budget"],
    "evidence_ids": ["crmarena-pro-b2b/VoiceCallTranscript__c/a05Wt000003T4uYIAS"],
    "insufficient_evidence": false
  }
}
```

- `failed_factors`：Budget、Authority、Need、Timeline 中不满足的因素；空集合只表示未发现失败因素。
- `insufficient_evidence`：证据不足时为 true，两个数组均为空。
- `answer_origin`：`llm` 或 `extraction_failure_gate`。后者是原实验的抽取失败门控，未执行最终模型回答。
- `execution_type`：必须用于区分历史回放和本次实时执行；服务器不会在实时失败时返回历史答案。
- 证据响应的 `evidence` 包含 `documents`、`products`、`policies`、`mention_paths`、`constraints`；保留来源行摘要、价格外键及政策原文位置。
- 实时响应增加 `generated_at`、抽取原回答与校验、`calculation`、生成长度和耗时。没有官方标签，也没有未来成交概率。

产品“提及”不是购买关系；折扣计算仅是假设场景；引用归属检查不证明模型逐项正确理解政策。

## MCP / Tool HTTP / Python

现有动态目录新增四个只读工具：

- `crmarena.info`：参数 `{}`。
- `crmarena.evidence`：上述 Lead 请求。
- `crmarena.evaluation`：上述 Lead 请求。
- `crmarena.predict`：上述 Lead 请求。

已有凭证不会自动扩大 `allowed_tools`；正式模式需由用户通过现有授权流程明确授予所需工具。推理仍是只读操作，不需要幂等键。宿主需要处理 `tools/list` 分页，新工具可能不在第一页。

Tool HTTP 请求：

```http
POST /api/v1/agent-tools/call/
Authorization: Tool <已授权的用户令牌>
Content-Type: application/json

{
  "name": "crmarena.evaluation",
  "arguments": {
    "dataset_id": "crmarena-pro-b2b-v3",
    "lead_id": "00QWt00000898Y2MAI"
  }
}
```

成功响应为现有 `{tool, status, http_status, revision, data}` 信封，业务结果在 `data`。正式模式令牌只通过私密环境注入；当前已有开放实验模式下无需 Authorization。

从仓库根目录使用 Python SDK：

```python
from integrations.salesmate_tools.client import ToolClient

# 读取 SALESMATE_TOOLS_URL / TOKEN / USER / TIMEOUT。
client = ToolClient.from_env()
request = {
    'dataset_id': 'crmarena-pro-b2b-v3',
    'lead_id': '00QWt00000898Y2MAI',
}
evidence = client.call('crmarena.evidence', request)['data']['evidence']
record = client.call('crmarena.evaluation', request)['data']
print(record['answer'], record['execution_type'])

# GPU 部署完成后可显式调用：
# live = client.call('crmarena.predict', request)['data']
```

CLI 与 MCP 使用同一 SDK：

```powershell
$env:SALESMATE_TOOLS_URL = 'http://127.0.0.1:8000'
# 仅长推理部署需要显式设置。未设置时仍保持原30秒默认值。
$env:SALESMATE_TOOLS_TIMEOUT = '600'
.venv/Scripts/python.exe -m integrations.salesmate_tools.cli list --category crmarena
.venv/Scripts/python.exe -m integrations.salesmate_tools.cli call crmarena.evaluation `
  --arguments-file backend/docs/examples/crmarena-lead.json
.venv/Scripts/python.exe -m integrations.salesmate_tools.mcp_server
```

MCP 宿主使用已安装 SDK 的 Python，可执行参数为 `-m integrations.salesmate_tools.mcp_server`，工作目录为 SalesMate 根目录，环境传入上述专用变量。没有新增公网 MCP 端口。客户端支持显式 0–3600 秒范围内的正超时；503/502 会保留后端结构化错误，并经 MCP 返回 `isError=true`，不自动重试。

## 安装与实时模型运行条件

公开图和结果已经安装在 `backend/model_store/crmarena-pro-b2b-v3/`。Git 跟踪固定 `manifest.json`，大文件及公开样本文件按清单单独安装，不依赖工作区 `output/` 才能服务。

在另一个部署目录中，先获取本轮 Kaggle version 2 的 `results/`，再执行：

```powershell
.venv/Scripts/python.exe backend/tools/install_crmarena.py --source /path/to/results
```

安装器验证固定 SHA，拒绝不符版本或覆盖已有部分/损坏安装。完整正确的重复安装仅校验。下载源：[本轮 Kaggle Notebook](https://www.kaggle.com/code/heropig/salesmate-crm-evidence-v3)；不要把日后新版本当作当前清单的同一制品。

实时模型要求保持原批准条件：

- 两张 Tesla T4、CUDA 12.8、torch 2.10.0+cu128。
- transformers 4.56.2、accelerate 1.10.1、huggingface-hub 0.36.2。
- Qwen/Qwen3-4B-Instruct-2507，revision `cdbee75f17c01a7cc42f958dc650907174af0554`，原始 FP16 权重。
- 36 层按 18/18 放置，embedding/lm_head 同在 GPU0，norm 在 GPU1；禁止量化、CPU 卸载或自动设备映射。
- 原输入上限6144、抽取512、最终128 tokens、seed2026、greedy 和严格校验保持不变。

使用独立 GPU 环境，先安装项目依赖和匹配的 CUDA torch，再安装 `backend/requirements/crmarena-gpu.txt`。在部署阶段显式下载固定模型，例如在该环境中执行：

```python
from huggingface_hub import snapshot_download
snapshot_download(
    'Qwen/Qwen3-4B-Instruct-2507',
    revision='cdbee75f17c01a7cc42f958dc650907174af0554',
    local_dir='/srv/models/qwen3-4b-crmarena',
    allow_patterns=['*.json', '*.safetensors', '*.model', '*.txt', '*.jinja'],
)
```

将服务器环境变量 `CRMARENA_MODEL_DIR` 指向该目录，再启动后端。该变量空值时 predict 明确返回503；目录存在也不等于 ready。模型第一次调用时逐文件核验原实验的权重与 tokenizer SHA，然后只用 `local_files_only=True` 加载，不在 HTTP 请求中安装依赖或下载。

一个 GPU 服务进程持有一份模型，线程锁覆盖冷启动和单题推理；同进程忙时立即429。不要配置多个 Web worker 竞争同一双卡，也不要用开发自动重载托管 GPU 模型。正式服务需自行配置身份、数据库和请求超时；这里没有部署新的公网服务。

运行故障会标记 `restart_required=true`，修复后重启；不自动重试。原实验显存峰值约12.09/11.34GiB，仅作为该次测量参考。原输出约束的 `a day` 词表缺口等仍保留，本次接口接入没有顺便调参或改变评测规则。

| 状态码 | 含义 |
|---|---|
| 400 | 未定义字段、类型或数据空间错误 |
| 401/403 | 未登录、未授权或 CSRF 不满足 |
| 404 | 公开 Lead/通话/冻结评测题不存在 |
| 429 | 同一模型正在处理另一请求 |
| 502 | 实时最终 JSON 或来源引用契约错误 |
| 503 | 制品、权重、依赖、GPU 未就绪或运行失败 |

## 实际验证与未验证事项

- 19项后端测试通过：8项新CRMArena测试及11项EASE回归；覆盖隔离制品、鉴权、严格输入、MCP范围、无回退及推理编排。GPU生成在这些测试中明确模拟。
- 6项客户端测试通过，包含真实 MCP stdio 握手、分页、显式超时及503透传。
- 真实本机 HTTP 使用已安装公开包验证：info/evidence/evaluation返回200，predict因未配置返回503。
- 真实 MCP stdio 连接本机后端，发现四个工具，回放正确，实时未配置返回isError。
- 9个迁入算法函数的AST与冻结源码一致；75题的95次已保存生成轨迹通过后端重放，输入消息逐字一致，答案与计算一致。这是编排一致性校验，不是重新进行了GPU评测。
- OpenAPI生成与验证通过；代码目录、模块变量索引、说明同步检查通过，仍人工核对语义。
- 没有在本机验证新的双T4实时推理，没有训练或私有数据微调，没有修改模型质量结论，没有导入私有业务库或提交Git。

本机联调用命令：`.venv/Scripts/python.exe backend/manage.py runserver 127.0.0.1:8000 --noreload`。只启动Web服务，不启动邮箱/销售Worker。
