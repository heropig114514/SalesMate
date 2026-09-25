# 语义图谱：HTTP、MCP 与小内存服务器部署

后续 Agent 开发先阅读[统一交接入口](semantic-agent-handoff.md)，其中汇总工具选择、写入副作用、失败/幂等处理及当前可用性；本文提供具体配置与部署命令。

本版提供48类业务schema、不完整结构化观察、文本/邮件正文抽取、已有实体关联、来源撤回及证据追溯。图谱由PostgreSQL维护，LLM输出候选，不直接修改订单、业务权限或发送邮件。微调V2 Q4是实验候选，默认启动器仍选择官方Q4；准确率限制见[实验说明](semantic-model-experiments.md)。

## 首版基线验证与优化入口

服务器已成功加载权重；结构化HTTP、来源重放和真实stdio MCP闭环通过。完整schema自然语言请求在301.34秒以502 ReadTimeout失败，模型日志显示2048个输入token耗时256.22秒（约7.99 tokens/s），请求随后取消，未写入该来源。不能将模型health=ok当作自然语言接口可用。模型服务当前已启动但未启用开机自启，实验API已启用自启；8 GiB交换空间持久保留。默认300秒阈值没有为此失败自动放宽。

上述为首版baseline记录。本轮`prefix8`候选的本机完整测试平均62.47→18.31秒，但契约通过数8/12→7/12，未达到启用门槛；默认继续baseline。临时服务器验证、精确限制及恢复状态以[推理优化说明](semantic-inference-optimization.md)为准。

## 输入与结果

调用方提供`source_key`、带时区`observed_at`，以及`text`或`records`之一。`source_key`须在当前用户下唯一；相同键、原文和时间重放返回同一来源，不重复推理。同键不同内容返回409，更正须新建来源并显式撤回旧来源。不要在超时后换键自动重试。

```json
{
  "source_key": "email-20260925-001",
  "observed_at": "2026-09-25T10:00:00+08:00",
  "text": "SandboxAcme needs SandboxEdge. The budget is unknown."
}
```

结构化输入示例：

```json
{
  "source_key": "import-20260925-001",
  "observed_at": "2026-09-25T10:00:00+08:00",
  "records": [
    {"key": "customer", "schema": "crm.company", "fields": {"name": "SandboxAcme"}},
    {"key": "quote", "schema": "sales.quote", "fields": {"number": "Q-DEMO", "company": {"record": "customer"}}}
  ]
}
```

缺失字段保持未知，不补业务默认值；结构化分支不调用LLM。文本分支给模型传入完整紧凑schema、本人候选实体、已知观察和本次原文。返回值包含`id`、`extraction.entities/facts`、`model_audit`、`sync`。`model_audit.raw_model_extraction`保留原始建议，`entity_resolution`记录程序关联决定；只有`sync.current=true`才表示当前图谱就绪。合法原文引用不等于语义已经人工核验。

邮件可传入主题和纯文本正文；现有`graph_ingest --emails`读取数据库中本人的入站业务邮件。附件解析、自动拉取邮箱和发信不属于此功能。

## HTTP调用

直接业务接口为`/api/v1/graph/schema/`、`episodes/`、`episodes/{id}/`、`episodes/{id}/retract/`、`status/`、`entities/`、`facts/`和`facts/{id}/lineage/`，沿用Session登录与CSRF。方法与边界见[详细接口](semantic-graph.md)。

程序和MCP推荐使用现有独立Tool认证。通过已登录用户的`POST /api/v1/agent-tools/credentials/`创建限定授权，例如：

```json
{"name":"语义图谱助手","allowed_tools":["graph.schema","graph.status","graph.entities","graph.facts","graph.lineage","graph.episodes","graph.episode","graph.ingest","graph.retract"],"expires_in_hours":24}
```

令牌只显示一次，不提交Git。随后请求`POST /api/v1/agent-tools/call/`，请求头为`Authorization: Tool <token>`，正文如下：

```json
{
  "name": "graph.ingest",
  "arguments": {
    "source_key": "email-20260925-001",
    "observed_at": "2026-09-25T10:00:00+08:00",
    "text": "SandboxAcme needs SandboxEdge. The budget is unknown."
  }
}
```

外层结果为`{tool,status,http_status,data}`，`data`是业务响应。来源写入使用`source_key`或`episode_id`幂等，不接受`idempotency_key`，不生成普通`ToolCall`回执；来源及血缘承担持久审计。其他业务工具的UUID幂等规则保持不变。

| 工具 | 参数 | 行为 |
|---|---|---|
| `graph.schema` | `{}` | 完整输入目录 |
| `graph.status` | `{}` | 同步状态 |
| `graph.entities` | 可选kind、q、page、page_size | 本人实体 |
| `graph.facts` | 可选entity、predicate、page、page_size | 本人关系及属性 |
| `graph.lineage` | fact_id，可选page、page_size | 事实证据及历史 |
| `graph.episodes` | 可选page、page_size | 来源摘要 |
| `graph.episode` | episode_id | 原文、候选与审计 |
| `graph.ingest` | 上述输入信封 | 写入观察、关联并同步 |
| `graph.retract` | episode_id | 撤回支持，保留历史 |

401/403表示认证或授权失败，400表示契约问题，404包括他人资源，409表示来源冲突或推理期间上下文变化，502表示模型生成/证据验证失败，503表示图谱未就绪。遇到超时先按`graph.episodes`和`graph.episode`核对结果；不自动重试。

## MCP配置

在仓库根目录建立独立SDK环境：

```powershell
python -m venv .tools-venv
.tools-venv/Scripts/python.exe -m pip install -r integrations/salesmate_tools/requirements.txt
$env:SALESMATE_TOOLS_URL = 'http://127.0.0.1:18090'
$env:SALESMATE_TOOLS_TOKEN = (Get-Content '私有凭证文件.json' -Raw | ConvertFrom-Json).token
$env:SALESMATE_TOOLS_TIMEOUT = '360'
.tools-venv/Scripts/python.exe -m integrations.salesmate_tools.mcp_server
```

这是stdio MCP，不是HTTP MCP。将客户端的command设为SDK环境Python绝对路径，args设为`["-m","integrations.salesmate_tools.mcp_server"]`，工作目录设为仓库根目录，并向该子进程传入上述专用环境变量。若客户端不支持工作目录，可使用`PYTHONPATH`指向仓库根目录。不要将原始token写进公开配置。`SALESMATE_TOOLS_TIMEOUT`默认30秒，模型请求须显式配置更长客户端超时；这不会改变后端模型300秒超时。

只读授权仅列出对应工具，九项图谱授权不会赋予其他业务工具权限。MCP目录支持分页，来源写工具的Schema不会插入传输UUID。SDK的调用等待时限也应覆盖后端等待；本版不自动重试连接或写操作。

## 本机模型启动与制品

依赖官方llama.cpp **b11146 / 7fe450e19**，Linux CPU包为[官方发布](https://github.com/ggml-org/llama.cpp/releases/tag/b11146)。Ubuntu需要`libgomp1`。Python后端需安装`backend/requirements/base.txt`和`agent/requirements.txt`；MCP依赖单独安装，避免与Web依赖混用。

微调权重文件为`Qwen3-4B-SalesMate-Semantic-v2-Q4_K_M.gguf`，大小2,497,278,784字节，SHA256：

```text
cadbc0a850ad31c712b4441a39775a8969ecdf9c0a762730c2a9a1c4344efd09
```

GGUF和LoRA不进入Git。权重保留在[私有Kaggle实验输出](https://www.kaggle.com/code/heropig/salesmate-semantic-qlora-v2)、本机实验目录以及此次服务器独立模型目录。下载后须按上述摘要核验。

```bash
python backend/tools/run_graph_model.py \
  --executable /absolute/path/llama-server \
  --model /absolute/path/Qwen3-4B-SalesMate-Semantic-v2-Q4_K_M.gguf \
  --variant semantic-v2 --threads 2 --port 8088
```

不传variant时仍为official，并要求官方制品哈希。默认线程8，服务器部署显式设为2以匹配2 vCPU；这是一项新的部署验证，不与此前8线程桌面微基准直接比较。默认`--profile baseline`使用上下文16384、单槽位、F16 KV并关闭prompt缓存。所有配置保持输出预算1536、temperature=0、seed=2026，并关闭自动fit及上下文滑动；失败不换模型或精度。

后端环境设置`GRAPH_LLM_URL=http://127.0.0.1:8088/v1`、`GRAPH_LLM_MODEL=salesmate-graph`。模型端口仅监听回环，不暴露公网；HTTP工具认证由后端执行。

优化配置通过模型启动器`--profile`和API环境`GRAPH_LLM_PROFILE`同步显式选择，默认均为baseline。配置、质量门槛、冷/热请求结果和当前部署验证见[推理优化说明](semantic-inference-optimization.md)。推理配置与模型权重版本是两个独立选项，不能把KV缓存量化误认为重新量化了权重。

## 本次服务器布局与连接

服务器为2 vCPU、3.7 GiB RAM，原有网站继续运行。实验使用`/opt/salesmate-semantic/`、独立系统用户`salesmate-graph`、独立PostgreSQL数据库/角色`salesmate_graph_sandbox`；没有将图谱迁移应用到线上业务库，没有替换线上默认模型。

- `app/`：本次Git版本源码；`venv/`：独立后端环境。
- `models/`：已核验GGUF；`llama-b11146/`：官方Linux CPU二进制。
- `runtime.env`：root及专用组可读的运行配置，不进Git。
- `private/client.json`：0600沙盒凭证，只授予九项graph工具，期限720小时。正式业务账号仍用Session授权接口。
- `salesmate-graph-model.service`：模型127.0.0.1:8088。
- `salesmate-graph-api.service`：实验后端127.0.0.1:8090。

使用`server_info.txt`所列SSH密钥建立隧道，在该终端持续运行：

```powershell
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 `
  -i 'LightsailDefaultKey-ap-southeast-1.pem' `
  -L 18090:127.0.0.1:8090 ubuntu@47.131.232.143
```

HTTP和MCP客户端连接`http://127.0.0.1:18090`。凭证到期会明确拒绝；通过服务器管理员显式创建新的沙盒用户与授权，或在正式系统中使用用户授权接口，不自动续期。

服务器已建立`/swapfile-salesmate-graph`，8 GiB、0600，加入`/etc/fstab`持久启用。交换空间能降低内存不足风险，但不能代替RAM，也不保证接口时限。检查：

```bash
free -h
swapon --show
sudo systemctl status salesmate-graph-model salesmate-graph-api
sudo systemctl show salesmate-graph-model -p MemoryCurrent -p MemorySwapCurrent
sudo journalctl -u salesmate-graph-model -u salesmate-graph-api --since '10 minutes ago'
curl -fsS http://127.0.0.1:8088/health
```

两项服务模板位于`deploy/lightsail/`，均`Restart=no`，低优先级运行，故障后需检查再显式启动。释放模型占用可执行`sudo systemctl stop salesmate-graph-model`；结构化图谱API仍可使用，文本输入将明确失败。停止实验后端用`sudo systemctl stop salesmate-graph-api`。停止进程后先核对可用内存再考虑swapoff，不在内存不足时强行关闭交换空间。

## 可复现验证

接口冒烟只允许`graph-sandbox-`专用身份，创建合成数据，保留结果，不自动删除或重试。输出目录必须不存在：

```bash
python backend/tools/smoke_graph_api.py --url http://127.0.0.1:8090 \
  --credential-file /opt/salesmate-semantic/private/client.json \
  --output /opt/salesmate-semantic/private/http-smoke-new
```

真实MCP闭环在SDK环境运行，验证发现、写入、重放、查询与撤回；它不重复调用模型：

```powershell
.tools-venv/Scripts/python.exe -m integrations.salesmate_tools.smoke_graph `
  --url http://127.0.0.1:18090 --credential-file '私有凭证文件.json' `
  --output 'mcp-verification-new.json'
```

数据库测试需真实PostgreSQL/pgvector，测试账号创建数据库但通常无权创建扩展，应由管理员预先在专用测试库创建vector，再使用`--keepdb`。正式授权测试须`LAB_OPEN_ACCESS=False`、`LOCAL_DEBUG_AUTO_LOGIN=False`，不能继承本机开放实验模式。模型边界mock测试和真实服务器推理结果分别报告。

实际服务器速度、成功/失败边界与最终检查记录见同目录`semantic-release-verification.json`。单个合成成功样例也不能推翻微调Q4在否定、联系人/雇佣和方向判断上的已知质量限制。
