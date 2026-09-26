# SalesMate Agent MVP

## 全球洞察采集

`python -m agent.world_insights` 是独立于 Gmail、L1–L4 和聊天的单次采集任务。它从 `world_insights_sources.json` 配置的公开 GDELT 搜索、NIST/Eurostat 订阅源以及半导体活动日历发现半导体设备、精密量测等行业资讯及活动，不需要新闻搜索 API Key。新闻必须有来源页面或订阅源提供的带时区发布日期；模型只整理来源片段，不能臆测国家。地图活动只从来源页的结构化 Event 数据读取明确的活动名称、日期、城市和国家，经 Nominatim 校验城市坐标后才写入。若来源只给活动日期、不提供钟点，记录会明确标记时间是系统占位值。缺地点的条目会跳过，不生成猜测位置。每轮最多写入 4 条资讯、2 条活动，已存在的来源 URL 不重复写入；单条失败不影响其他条目。

Eurostat 的已知指标旧地址 `/eurostat/product?code=4-<八位数字>-ap` 会转换为官方同主机的 `/eurostat/en/web/products-euro-indicators/w/…`，抓取和去重均使用规范地址，新记录也保存规范地址。已有数据库记录不会自动改写，需后端显式维护。抓取仍验证公网 DNS、HTTPS、响应类型和大小，不跟随其他重定向。`world_item_failed` 会记录阶段、异常类型及安全错误原因。

资讯模型还会从同一来源片段提取公司、事件类型、项目、明确需求、潜在需求及理由、时间窗口与原文证据；金额必须带币种、金额类型、范围和原文证据，且数值能与原文核对。没有可核对的公司级事件时，这些字段留空。Agent 已将这组字段直接附加到 `world_news.create`，因此**必须先让后端扩展 `world_news` 写入契约，再启用正式采集服务**；当前后端会拒绝新增字段并返回 400。`--dry-run` 可先查看完整的待写载荷，不会写入后端。已按旧契约存储的资讯不会因来源 URL 去重而自动补齐新字段，须另行安排回填。

先在项目根目录安装 `agent/requirements.txt`，配置百炼模型现有环境变量，以及独立的 `SALESMATE_TOOLS_URL`、`SALESMATE_TOOLS_TOKEN`。Tool 凭证至少授权 `world_news.list/create` 和 `world_events.list/create`；不能使用 Gmail/聊天 Worker 的 Agent 凭证代替。生产环境建议把 Tool 配置放在 `/opt/salesmate/shared/world-insights.env`，只允许 `salesmate` 服务用户读取，不要提交到 Git。

```bash
python -m agent.world_insights --dry-run
python -m agent.world_insights
sudo install -m 644 agent/deploy/salesmate-world-insights.service /etc/systemd/system/
sudo install -m 644 agent/deploy/salesmate-world-insights.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now salesmate-world-insights.timer
sudo systemctl start salesmate-world-insights.service
sudo journalctl -u salesmate-world-insights.service -n 100 --no-pager
```

服务默认每天 UTC 03:00、15:00 各执行一次，并设有随机延迟。`--dry-run` 会访问公开来源、地理编码及模型，但不调用后端写入工具；单次结果中的 `news`、`events`、`source_successes`、`source_errors`、`item_errors`、`write_errors` 可用于检查采集情况。全部来源不可用，或所有尝试的写入均失败时，任务会以非零状态退出；单个来源或条目出错仍会记录日志并继续处理其他条目。来源站点不可达或没有足够可靠的日期、地点时，本轮可能没有新记录。地理编码缓存路径可通过 `SALESMATE_WORLD_GEO_CACHE` 配置。

**全员共享：**后端已允许所有已登录员工读取全球资讯和活动，写入仍受 Tool 白名单和 owner 权限限制，客户及商机仍按各自权限隔离。现有部署脚本不会自动安装此 timer；新增销售线索字段完成后端适配前不要启用它。

更新日期：2026-09-20<br>
版本：v2.5<br>
状态：员工网页 Gmail 授权、一次性同步请求及原有 L1–L4 链路已完成联调；工作空间聊天已对接后端请求绑定的只读客户工具，真实模型与网页联合验收仍需执行。

## 1. 当前范围

2026-09-13 软件集成更新：产品入口由 Django 独立 `crm_worker` 调度，支持原文/L1 输出持久缓存、用户显式范围内的去重同步及人工补抽取（2026-09-20 起取消全量补采和 History 失效回退）。下文一次性 CLI 描述保留用于模块调试；已由 Worker 接管的邮箱不可再用旧 CLI 写游标。无采购阶段的入站邮件进入复核，来源变化自动失效并重算 L2–L4。当前产品运行与恢复边界见 [邮件处理适配](../backend/docs/processing-integration.md)。本轮验证使用模拟 Gmail/模型，不代表新增流程已完成真实外部联调。

本目录负责 Gmail 邮件理解和公司级销售分析：

```text
员工网页授权 Gmail
  → Django 保存员工邮箱连接，员工选择天数或封数后创建同步请求
  → Agent 一次性领取授权和同步范围
  → Gmail
  → L1 单封邮件事实抽取
  → DjangoBackendClient 提交真实后端
  → 后端保存、去重、归组并创建任务
  → L2 公司事实归并
  → L3 客户画像、客户分析和销售信号
  → L4 跟进优先级评分
  → DjangoBackendClient 保存到真实后端
```

Agent 不自行提供 HTTP 服务或数据库。现有 Django 后端负责员工会话、Google OAuth、邮箱凭证、公司、工单、报价、订单、任务和分析结果持久化；Agent 通过后端已经提供的 `/api/v1/agent/` HTTP 接口读取和写入。后端的服务认证、ETag 和任务租约由适配器处理，不进入 L1–L4 业务流程。

每个 Agent 服务凭证只绑定一名后端员工。共享 `crm_worker` 会为每个执行单元创建临时员工凭证并在结束后撤销；CLI 仍使用其配置的单员工凭证。Agent 领取的是这名员工从页面请求的邮箱同步任务，后端归组和页面查询也继续按员工隔离，因此前端表示“当前员工的 Gmail 收件箱”，不是整个公司的共享收件箱。

Agent 目录包含工作空间聊天的 Skill、模型适配、工作流、一次性 CLI 和离线测试，但不提供聊天 HTTP 服务、数据库、权威会话存储或轮询循环。当前聊天只使用后端按员工和请求授权的客户搜索、客户详情只读工具，不执行发信、日历操作或 CRM/文件写入。页面入口、会话保存、权限和实际持久化由后端与前端负责。

### 1.1 工作空间聊天（唯一聊天流程）

`workflows/chat.py` 只执行 `skills/workspace-chat/SKILL.md`（`workspace-chat-v3`），不再按客户绑定或环境变量切换聊天模式。员工从工作空间发起问题，Agent 内部请求不包含预选 `company_id`；当前后端直接省略该字段；HTTP 适配层仍接受过渡期的 `company_id: null` 并移除它，非空值会被拒绝。普通问题可直接回答；需要客户资料时，模型选择 `customers.search` 与 `customers.context`，后者的 `company_id` 仅用于本次客户查询。Agent 从本次请求发布的工具目录核对参数，最多执行 6 次工具调用。共享实验问题可使用 `experiments.catalog`、`experiments.rows`、`experiments.file_read`，保留合成标记、批次及原归属，读取证据由后端登记。明确的虚构数据维护请求还可使用 `experiments.create/update/delete`，参数取实际目录，修改删除携带最新指纹，回执由后端保存并可引用。后端按员工和请求验证权限，并登记每次读取的证据。Agent 只引用本轮实际展示的授权来源；较长客户详情以标记过的节选进入模型，完整来源仍由后端保存。

`llm/bailian.py` 的 `generate_chat_json()` 请求 JSON Object。模型输出可选择下一次只读查询或最终回答；引用须对应本轮授权来源，正文编号与引用列表须一致。无证据时可以进行普通对话、澄清或明确说明资料不足。直接要求发送邮件、安排日历或写入业务数据时返回未执行说明。工具级参数错误或详情不可用可以在同一请求内修正或回答；请求级错误结束本轮。

`process_chat_once()` 每次最多领取一个请求并尝试回报一次；无工作返回 `None`。回报响应丢失时仅查询一次后端权威状态，确认结果已保存才视为成功。工作空间聊天默认启用，没有功能开关和旧聊天回退。接口与权限边界见[后端工作空间聊天契约](../backend/docs/workspace-chat-tools.md)。

### 1.2 一次性聊天 CLI

在项目根目录运行：

```powershell
python -m agent.main --process-chat-once
```

命令复用现有 `.env` 加载和 `DjangoBackendClient`：领取零个或一个聊天请求、生成稳定结果、尝试回报后立即退出。标准输出是 UTF-8 JSON；无工作或 `completed` 正常退出，`failed`（包括本地 `report_failed`）返回非零。该参数与 `--analysis-company-id`、`--process-jobs-once`、`--sync-authorized-mailboxes-once` 互斥。它不是 daemon；Demo 期间的重复触发必须由外部调度器或现有 Worker 串行完成。

### 1.3 聊天接口联调契约

`DjangoBackendClient` 已实现下列 Agent 侧 HTTP 映射，均复用既有 `Authorization: Agent <service-token>` 和 timeout，并对响应做最小契约校验：

| Agent 方法 | 受保护路径 | Agent 侧行为 |
|---|---|---|
| `claim_answer_request()` | `POST chat/requests/claim/` | 发送 `{}`；把 `{"request": null}` 规范为无工作；省略 `company_id` 或返回 `null` 均归一为无预选公司请求，非空值拒绝 |
| `get_answer_context(request_id, scope)` | `POST chat/context/` | `scope` 仅允许 `internal|external`；独立校验客户上下文状态、知识状态、检索缺口和 external 可用性 |
| `get_chat_tools(request_id)` | `GET chat/tools/` | 获取当前请求实际发布的只读工具与参数 Schema；普通问题无需调用 |
| `get_chat_request_status(request_id)` | `GET chat/requests/<request_id>/` | 仅在回报响应无法确认时核对权威终态，不重新执行模型或工具 |
| `report_answer(result)` | `POST chat/answers/` | 回报 `chat_prompt_version`、`completed|failed`、回答、Citation 和安全错误；接受后端首存或幂等重复响应 |
| `read_chat_tool(request_id, name, arguments)` | `POST chat/tool-reads/` | 只允许目录发布的客户搜索、详情及共享实验读取，核对请求、工具、读取 ID 和后端登记的证据项；保留工具级或请求级错误范围 |

`backend/apps/chat/` 已提供员工绑定、证据快照、幂等结果和唯一助手消息。独立 `chat_worker` 调用 `process_chat_once()`；Agent 不覆盖员工身份或后端可见范围，也不直连知识库。当前只使用员工可见客户和显式内部知识，外部知识保持关闭。

### 1.4 Agent Delivery 与 Web Demo Delivery

Agent 本目录的单元测试继续使用 fake session/backend，不代表真实模型或生产网页验收。后端新增集成测试使用真实 PostgreSQL 和临时 Django HTTP 服务运行原 Agent HTTP 客户端/工作流，模型输出模拟；浏览器测试使用真实页面与模拟 API。

Web Demo 仍需部署对应后端迁移、配置模型与后端地址、启动 `python backend/manage.py chat_worker`，并完成真实模型及网页联合验收。Agent 只产出 `workspace-chat-v3` 结果；前端已移除客户专属聊天入口；新后端 Worker 启动时明确结束旧公司活动任务并保留历史，不转换或重派旧问题。失败请求不能重置后复用原 `request_id`。

### 1.5 聊天离线测试

```powershell
# 工作空间聊天、CLI/百炼回归和 HTTP mocked contract
python -m unittest agent.tests.test_workspace_chat agent.tests.test_core agent.tests.test_http_backend

# 包含 Existing L1–L4 Pipeline 回归的完整 Agent 离线套件
python -m unittest discover -s agent/tests -p "test_*.py"
```

`test_workspace_chat.py` 使用内存后端和模拟模型覆盖普通对话、客户搜索与详情、跨公司引用、工具错误、越权来源、长内容节选和结果回报确认。离线测试不发送客户数据或凭据到外部服务；真实模型与网页端到端验收仍需部署后执行。

开发阶段的 Agent 日志按 `request_id`、`gmail_message_id`、`company_id` 和 `job_id` 串联阶段：Gmail 读取与逐封提交、L1 抽取及重试、L2 归并、L3 模型与缓存、L4 信号与评分、聊天模型/工具/回报，以及后端 HTTP 失败。日志记录状态、数量、耗时和异常类型，不记录 OAuth token、API Key、完整模型输出或完整客户上下文。例如：

```bash
sudo journalctl -u salesmate-chat -f
sudo journalctl -u salesmate-crm -n 300 --no-pager | grep -E 'gmail_sync_|l1_email_|l3_analysis_|l4_|company_analysis_'
```

## 2. 目录职责

```text
agent/
├── main.py                         # Gmail、L2、聊天和真实后端一次性任务 CLI
├── config.py                       # 从项目根目录 .env 读取共享配置
├── clients/
│   ├── __init__.py                 # 外部服务客户端包出口
│   └── backend_api.py              # BackendClient 协议、Django API 与聊天工具映射
├── tools/
│   ├── gmail.py                    # 后端授权信息、Gmail History 与邮件读取
│   └── email_parser.py             # MIME、正文和历史回复解析
├── llm/
│   └── bailian.py                  # L1/L3 与有序聊天的百炼 JSON Object 请求
├── skills/
│   ├── loader.py                   # Skill 发现、元数据解析和进程内缓存
│   ├── email-fact-extraction/
│   │   └── SKILL.md                # L1 抽取指令、版本和输出上限
│   ├── customer-analysis/
│   │   └── SKILL.md                # L3 画像指令、版本和输出上限
│   └── workspace-chat/
│       └── SKILL.md                # workspace-chat-v3 只读客户工具选择与回答
├── workflows/
│   ├── l1_email.py                 # L1 单封邮件事实抽取
│   ├── gmail_sync.py               # 前端 Gmail 同步服务函数
│   ├── authorized_gmail_sync.py    # 领取并处理员工网页同步请求
│   ├── analysis_input.py           # L2 公司事实归并和指标
│   ├── customer_analysis.py        # L3 客户画像和分析
│   ├── lead_score.py               # L4 确定性优先级评分
│   ├── orchestration.py            # L2–L4 和一次任务处理
│   └── chat.py                     # 工作空间只读查询、回答与一次性编排
└── tests/
    ├── __init__.py                 # 测试包标记
    ├── email_submission_exploration.py  # L1 公共 fixture 与数据契约边界测试
    ├── fake_backend.py             # 仅供离线测试使用的协议假实现
    ├── test_workspace_chat.py      # 工作空间只读查询、引用和越权动作测试
    ├── test_core.py                # L1、CLI、百炼客户端和数据契约单元测试
    ├── test_integration.py         # Gmail 只读读取、History 与百炼客户端集成测试
    ├── test_analysis_input.py      # L2 AnalysisInput 行为测试
    ├── test_http_backend.py        # Django HTTP 传输与聊天 mocked contract 测试
    ├── test_mvp_pipeline.py        # Gmail 同步及 L2–L4 主链测试
    └── test_lead_score.py          # 公司级 L4 规则与排序测试
```

没有单独的 `schemas` 或 `prompts` 层。模型能力以 `agent/skills/<skill-name>/SKILL.md` 组织，frontmatter 提供路由名称、用途描述、版本和输出 token 上限，正文保存模型指令。workflow 按名称加载 Skill，只负责拼装本次输入、调用百炼和校验结果。数据结构继续使用普通字典和少量就地 dataclass。

当前提供工作流所需的 Skill：

| Skill | 调用阶段 | 输入边界 | 产出 |
|---|---|---|---|
| `email-fact-extraction` | L1 | 一封解析后的邮件主题与当前正文 | 带原文证据的邮件事实 |
| `customer-analysis` | L3 | 一份公司级 `AnalysisInput` | 客户画像、分析、信号与评分特征 |
| `workspace-chat` (`workspace-chat-v3`) | 工作空间聊天 | 当前问题、最近历史、内部知识及后端请求绑定的只读工具结果 | 客户搜索/详情查询选择和有来源的回答 |

`agent.skills.list_skills()` 可返回可路由 Skill 的名称、描述、版本、指令与输出上限。修改 Skill 正文且会改变模型行为时必须同步递增其 `metadata.version`。未来邮件发送、会议排期或其他写入动作须另行设计权限和确认流程；当前 `workspace-chat` 不执行这些动作。

QQ 邮箱已作为独立 IMAP 读取源追加，保留现有 Gmail 接入。`tools/qq_mail.py` 提供固定 QQ TLS 服务的只读适配，后端 `qq_sync` 持久同步并复用现有 L1–L4；无需 Google 回调域名。配置及协议兼容边界见 [QQ 邮箱试用](../backend/docs/qq-mailbox.md)。QQ 通过 `crm_worker` 运行，旧 Gmail CLI 不领取 QQ 任务。

## 2.1 模块交互流程图

总图只描述模块间的业务流程和数据流。模块内部的判断、校验和计算规则在后续章节分别说明。

```mermaid
flowchart TB
    subgraph CALLER["当前员工浏览器"]
        F1["Google OAuth 与同步请求<br/>员工 Session / mailbox_id"]
        F2["同步与分析结果"]
    end

    subgraph EXTERNAL["外部服务"]
        GMAIL["Gmail"]
        BAILIAN["阿里百炼 LLM"]
    end

    subgraph AGENT["Agent"]
        SYNC["邮件同步"]
        PARSER["邮件解析"]
        SKILLS["Skill 注册表<br/>名称 / 描述 / 版本 / 模型指令"]
        L1["L1 单封邮件事实抽取"]
        ORCH["任务与公司分析编排"]
        L2["L2 公司级事实归并"]
        L3["L3 客户画像与客户分析"]
        L4["L4 跟进优先级计算"]
    end

    subgraph BACKEND["真实 Django 后端"]
        AUTH["员工邮箱连接<br/>GmailCredential / sync_state"]
        MAIL_QUEUE["员工专属同步请求"]
        DATA["邮件与业务数据<br/>公司 / 联系人 / 工单 / 报价 / 订单"]
        QUEUE["待处理任务"]
        RESULT["分析结果<br/>AnalysisInput / Analysis / Score / JobReport"]
    end

    F1 -->|"授权码与同步操作"| AUTH
    AUTH <-->|"OAuth / Gmail profile"| GMAIL
    AUTH -->|"sync_requested"| MAIL_QUEUE
    MAIL_QUEUE -->|"mailbox_id / 地址 / 授权 / 读取上限"| SYNC
    SYNC -->|"只读授权 / 查询范围"| GMAIL
    GMAIL -->|"message_id / thread_id / raw MIME / received_at"| PARSER
    PARSER -->|"subject / body_text / from / to / cc / sent_at / direction"| L1
    SKILLS -->|"email-fact-extraction"| L1
    L1 -->|"EmailSubmission<br/>适配器补充 mailbox_id / source"| DATA
    DATA -->|"created_count / updated_count / duplicate_count / affected_company_ids"| SYNC
    SYNC -->|"GmailSyncResult / 刷新授权"| AUTH
    AUTH -->|"授权和同步状态"| F2

    DATA -->|"业务邮件发生有效变化<br/>company_id / trigger / job_id"| QUEUE
    QUEUE -->|"待处理公司任务"| ORCH

    ORCH -->|"company_id"| L2
    DATA -->|"公司归组<br/>company_name / crm_status / domains / contacts / member_dedupe_keys"| L2
    DATA -->|"业务上下文<br/>emails / customer / tickets / quotes / orders / snapshot_version<br/>正式 L4 所需 priority_context 待后端提供"| L2
    L2 -->|"AnalysisInput<br/>归并 facts / metrics / business_context / input_version"| RESULT
    L2 -->|"AnalysisInput"| ORCH

    RESULT -->|"相同 company_id + input_version 的历史 Analysis 或空值"| ORCH
    ORCH -->|"缓存未命中时传入 AnalysisInput"| L3
    SKILLS -->|"customer-analysis"| L3
    L3 -->|"公司事实与业务上下文"| BAILIAN
    BAILIAN -->|"画像、信号、分析、评分特征 JSON"| L3
    L3 -->|"Analysis<br/>list_view / detail_view / status / error"| ORCH
    ORCH -->|"Analysis + metrics + 正式评分所需 priority_context"| L4
    L4 -->|"Score<br/>score / score_reasons / score_version"| ORCH

    ORCH -->|"保存 Analysis / Score / JobReport"| RESULT
    ORCH -->|"AnalysisBundle 或 JobReport 数组"| F2
```

### 各阶段交换的数据

| 阶段 | 数据方向 | 主要输入字段 | 主要输出字段 | 作用 |
|---:|---|---|---|---|
| 1. 员工邮箱授权 | 浏览器 ↔ Django ↔ Google | 员工 Session、OAuth code | mailbox_id、mailbox_address、授权状态、sync_requested | 绑定当前员工实际选择的 Gmail，不把令牌交给浏览器 |
| 2. 同步领取与邮件读取 | Django → Agent ↔ Gmail | mailbox_id、授权信息、读取上限 | message_id、thread_id、raw MIME、received_at | 一次性读取该员工最近的收件和发件邮件 |
| 3. 邮件解析 | Gmail → 邮件解析 → L1 | raw MIME 和 Gmail 元数据 | subject、body_text、from、to、cc、sent_at、direction | 把 Gmail resource 转成统一邮件结构 |
| 4. 单封事实抽取 | Skill → L1 → 后端 | `email-fact-extraction` 指令、统一邮件结构 | EmailSubmission：dedupe_key、contact_email、extract_status、facts | 最多四封并发抽取；一封异常不终止其他邮件 |
| 5. 保存与归组 | Agent → 后端 | 已完成的单封 EmailSubmission | 单封保存结果、company_id、公司成员邮件、必要的待处理任务 | 不等待最慢邮件，任一 L1 完成后立即逐封提交；独立事务避免一个冲突回滚整批邮件 |
| 6. 同步结果 | Agent → Django → 浏览器 | 后端保存结果 | fetched_count、l1_processed_count、created_count、duplicate_count、failed_extraction_count、failed_submission_count、email_errors、同步状态 | 邮箱阶段完成后立即回报；失败邮件保留到下一轮重试 |
| 7. 任务进入分析 | 后端 → 编排 | job_id、trigger、company_id | 本批次待分析公司列表 | 把邮件变化或业务数据变化转换为公司分析任务 |
| 8. 公司数据准备 | 后端 → L2 | 公司归组、邮件、客户、联系人、工单、报价、订单、快照版本、评分所需公司背景 | 完整公司数据集合 | 为公司级事实归并和 L4 提供统一上下文 |
| 9. 公司级事实归并 | L2 → 编排和后端 | 公司数据集合 | AnalysisInput：company、business_context、facts、metrics、input_version、unparsed_message_count | 形成 L3 唯一可信的分析输入 |
| 10. 分析缓存判断 | 后端 → 编排 | company_id、input_version | 已存在的 Analysis 或空值 | 相同数据版本不重复调用模型 |
| 11. 客户画像与分析 | Skill → L3 ↔ 百炼 | `customer-analysis` 指令、精简后的 AnalysisInput 推理视图 | Analysis：公司信号、工单信号、行业、规模、摘要、三维画像、四维分析；旧评分特征暂留供后端校验 | 完整 L2 继续用于校验和存储；L4 不再使用旧评分特征计算分数 |
| 12. 跟进优先级 | 编排 → L4 | L1 采购阶段及公司评分上下文 | 公司级 Score、分项、原因、证据与下一步建议 | 纯 Python 规则计算；有阶段但业务资料缺失时给暂定分并标注缺项 |
| 13. 保存分析结果 | 编排 → 后端 | AnalysisInput、Analysis、Score、JobReport | 当前公司的最新分析状态 | 资料齐全时在同一次 Score 提交中附带 `score_details`；暂定分的缺项写在 `score_reasons` |
| 14. 返回调用方 | 编排 → 后端 → 浏览器 | 完整分析结果 | AnalysisBundle、JobReport 与公司页面投影 | CLI 输出处理报告；前端通过后端读取结果 |

当前没有“前端上传 JSON 文件”或“后端返回磁盘文件”的过程。Agent workflow 内部交换普通字典，`DjangoBackendClient` 将这些字典转换成 HTTP JSON 请求和响应。前端页面查询、筛选、排序和分页继续调用 Django 的浏览器接口。

本地配置和测试注入器使用以下文件；网页 OAuth 凭证由 Django 保存：

| 本地文件 | 读取者 | 用途 | 是否与前端或后端交换 |
|---|---|---|---|
| `.env` | Django、Agent 和百炼客户端 | 后端、模型与 Agent API 的共享本地配置 | 否 |
| `test_tools/gmail_inject_credentials.json` | `test_tools/gmail_test_injector.py` | 测试邮件注入器专用 Desktop OAuth 配置 | 否 |
| `test_tools/gmail_inject_token.json` | `test_tools/gmail_test_injector.py` | 测试邮件注入器专用 OAuth token 缓存 | 否 |

`agent/tests/fake_backend.py` 只用于不连接网络和数据库的自动测试，不参与 CLI 或实际部署。运行时数据全部由 Django 后端持久化。

## 3. L1：邮件事实抽取

入口：

```python
process_email(email, mailbox_address, extraction_provider) -> EmailSubmission
```

处理顺序：

1. Gmail 工具读取 raw 邮件。
2. `email_parser.py` 解析 MIME、主题、正文、地址和时间。
3. 根据 `from` 与授权邮箱判断入站或出站。
4. 选择主要外部联系人。
5. 自动邮件、营销邮件和 no-reply 邮件跳过 LLM。
6. 业务邮件调用百炼抽取事实。
7. 校验证据确实能在当前主题或正文中定位；默认百炼结果若仅因 JSON 结构或证据校验失败，会携带校验原因立即重试一次。

`EmailSubmission` 主要字段：

```json
{
  "dedupe_key": "sales@example.com:gmail-message-id",
  "mailbox_address": "sales@example.com",
  "gmail_message_id": "gmail-message-id",
  "thread_id": "thread-id",
  "from": "buyer@example.com",
  "to": ["sales@example.com"],
  "cc": [],
  "sent_at": "2026-09-12T10:00:00+08:00",
  "received_at": "2026-09-12T02:00:00+00:00",
  "subject": "采购咨询",
  "body_text": "需要 50 台检测设备，请提供报价。",
  "direction": "inbound",
  "contact_email": "buyer@example.com",
  "non_business_hint": false,
  "non_business_reason": null,
  "extract_status": "completed",
  "extract_prompt_version": "extract-v7",
  "extract_error": null,
  "facts": {}
}
```

`facts` 固定包含 17 个字段：

- `has_substantive_update`
- `message_summary`
- `intent_hint`
- `intent_evidences`
- `contact_name`
- `contact_title`
- `company_self_reported`
- `business_background`
- `employee_scale_hint`
- `product_need`
- `quantity`
- `budget`
- `delivery_time`
- `decision_process`
- `concerns`
- `quote_reference`
- `order_reference`

13 个普通事实字段都是：

```json
[
  {
    "value": "50 台",
    "evidences": ["需要 50 台检测设备"]
  }
]
```

未知事实返回空数组。一项事实可以有多个 value，每个 value 可以有多条证据。项目不再使用 `requirements` 字段。

`intent_hint` 表示单封客户邮件中可核实的最高采购阶段：`L1 Exploring`、`L2 Interested`、`L3 Qualified`、`L4 Evaluating`、`L5 Negotiating`、`L6 Purchase Ready`。没有可核实采购意向时为 `null`，此时 `intent_evidences=[]`；有阶段时必须附至少一条当前邮件原文证据。字段数量和其余普通事实结构不变。邮件提交与 L2 只接受 `extract-v7`，不读取旧版阶段枚举。

## 4. Gmail 同步接口

正式页面链路由员工在 Django 页面完成 OAuth；后端随后自动启动 Agent。以下命令保留为关闭自动运行后的手工调试入口：

```powershell
python -m agent.main --sync-authorized-mailboxes-once
```

产品入口由独立 `crm_worker` 领取持久批次；该调试命令通过 `claim_mailbox_syncs()` 取得邮箱地址、授权、冻结 `sync_options` 和显式重试 ID。Agent 必须执行所选范围，缺失范围及 ID 的旧请求会失败，不能隐式改为全量同步。浏览器提交范围后读取状态并轮询。

`sync_gmail()` 是底层同步函数。直接调用时需要后端提供的授权信息和 mailbox_id；调试入口 `sync_authorized_mailboxes_once()` 从后端领取这些数据，产品使用持久 Worker：

```python
from agent.clients.backend_api import DjangoBackendClient
from agent.workflows.gmail_sync import sync_gmail

backend = DjangoBackendClient(
    "http://127.0.0.1:8000/api/v1/agent/",
    "agent-service-token",
    mailbox_id="后端邮箱 UUID",
)
result = sync_gmail(
    {
        "mailbox_id": "mb1",
        "access_token": "direct-access-token",
        "mailbox_address": "sales@example.com",
        "max_results": 20,
    },
    backend=backend,
)
```

`mailbox_address` 可以省略，此时读取 Gmail profile。`max_results` 必须是 1–20，表示底层单页大小。网页领取的 `sync_options` 包含 `recent_days`、`max_messages`、`since`、`until`，由后端在请求时冻结；Gmail 默认最多 50 封，仅填天数也适用，超过 50 封须用户明确批准后携带 `allow_large_sync=true`，不能批准无限量；最近封数先截断，再按邮箱与 message ID 跳过已存记录，不继续向旧邮件补足数量。失败 ID 保留供明确重试，不会被普通范围同步隐式重做。`sync_gmail` 底层仍保留无 `sync_options` 时的模块测试游标协议，产品 Worker 和网页领取入口均不走该路径。

L1 既定最多四路并发、完成即提交、校验修正和失败隔离逻辑保持不变。Worker 使用持久原文及 L1 输出复用缓存；其接管邮箱不能混用旧 CLI 游标写入，已配置的读写错误明确向上报告。具体首次授权、范围校验与恢复边界见 [邮件处理适配](../backend/docs/processing-integration.md)。

L1 的标准 `EmailSubmission` 保持上一节的业务字段。HTTP 适配器提交时额外加入后端传输所需的 `mailbox_id` 和 `source=gmail_real`，但不会把多值 facts 降级成后端当前的旧单值结构。

成功结果：

```json
{
  "mailbox_id": "mb1",
  "status": "completed",
  "sync_mode": "incremental",
  "cursor_saved": true,
  "fetched_count": 5,
  "pending_message_count": 0,
  "retry_message_count": 0,
  "failed_email_count": 0,
  "l1_processed_count": 2,
  "skipped_existing_count": 3,
  "created_count": 1,
  "updated_count": 1,
  "duplicate_count": 3,
  "failed_extraction_count": 0,
  "failed_submission_count": 0,
  "email_errors": [],
  "affected_company_ids": ["company-1", "company-2"],
  "job_reports": [],
  "error": null
}
```

底层同步函数只负责 Gmail、L1 和逐封邮件提交。产品入口由独立 `crm_worker` 并行调度同步与公司画像，逐封完成即保存，前端分别读取批次进度和当前客户结果。保留的 Gmail CLI 仍先完成同步再处理公司 Job，仅用于单独调试，不与新 Worker 混用同一邮箱。`job_reports` 在调用 `process_jobs_once()` 后由调用方填入。

后端需要遵循的提交规则：

- 相同 `dedupe_key` 不重复保存。
- 原记录为 `failed`、新结果为 `completed` 时更新事实。
- 企业邮箱按域名归组。
- 常见公共邮箱按完整联系人邮箱归组。
- 新公司默认未建 CRM 档案。
- 只有业务邮件且 `has_substantive_update=true` 时创建分析任务。

## 5. L2：AnalysisInput

入口：

```python
build_analysis_input(
    company_id,
    backend=backend,
    merge_version="merge-v2",
    clock=clock,
) -> AnalysisInput | ValidationError
```

L2 不调用 LLM。它从后端读取 `Grouping` 和 `CompanyContext`，完成：

- 多封邮件事实无损归并
- 为每条事实补充 `dedupe_key` 和 `fact_time`
- 计算收发数量、有效入站数量和最近响应间隔
- 统计未解析邮件
- 携带联系人、客户、工单、报价和订单上下文
- 计算可用于缓存的 `input_version`

输出结构：

```json
{
  "company_id": "company:example.com",
  "input_version": "sha256:...",
  "merge_version": "merge-v2",
  "external_snapshot_version": "ext-3",
  "built_at": "2026-09-12T10:01:00+08:00",
  "company": {
    "company_name": "Example",
    "crm_status": "unregistered",
    "domains": ["example.com"],
    "contacts": []
  },
  "business_context": {
    "customer": {},
    "tickets": [],
    "quotes": [],
    "orders": []
  },
  "latest_message_summary": "客户要求提供正式报价",
  "member_dedupe_keys": [],
  "unparsed_message_count": 0,
  "facts": {},
  "metrics": {}
}
```

`input_version` 由邮件的 `dedupe_key`、抽取版本、抽取状态、`merge_version` 和后端 `external_snapshot_version` 计算。业务数据变化时，真实后端必须同步更新 `external_snapshot_version`。

后端业务上下文使用以下最小对象格式：

```json
{
  "contact": {
    "contact_email": "buyer@example.com",
    "contact_name": "王宇",
    "interaction_count": 3,
    "is_primary": true
  },
  "customer": {
    "customer_id": "customer-1",
    "industry_from_crm": "工业自动化",
    "employee_count": 260,
    "employee_count_source": "crm",
    "first_deal_at": "2025-06-01T08:30:00+08:00"
  },
  "ticket": {
    "ticket_id": "ticket-1",
    "name": "设备采购跟进",
    "stage": "需求沟通",
    "amount": null,
    "currency": "CNY",
    "owner": "Demo Sales"
  },
  "quote": {
    "quote_id": "quote-1",
    "ticket_id": "ticket-1",
    "sent_at": "2026-09-10T11:00:00+08:00",
    "amount": 280000,
    "currency": "CNY",
    "status": "sent",
    "evidence_type": "actual_outbound"
  },
  "order": {
    "order_id": "order-1",
    "closed_at": "2025-06-01T08:30:00+08:00",
    "amount": 450000,
    "currency": "CNY",
    "products": ["工业传感器"],
    "source_system": "erp"
  }
}
```

## 6. L3：客户画像和客户分析

入口：

```python
generate_analysis(
    analysis_input,
    analysis_provider=bailian_analysis_provider,
    clock=clock,
) -> dict
```

L3 一次百炼调用同时生成页面 A 和页面 B 所需的 Agent 字段。调用前会从完整 `AnalysisInput` 构造精简推理视图：保留公司、业务上下文、事实值、事实时间、来源、指标和分析基准时间，删除仅用于缓存的版本字段、重复成员列表以及 L1 已验证过的原文证据副本。完整 L2 仍用于结果校验和后端保存。

`list_view` 包含公司主信号和证据、逐工单信号、行业、规模档位、最新摘要，以及三个 0–3 或 `null` 的评分特征。

主信号枚举：

- `repeat_purchase`
- `quoted_not_closed`
- `inquiry_intent`
- `new_lead_no_profile`
- `unknown`

信号证据门槛：

- `quoted_not_closed` 必须有 `evidence_type=actual_outbound` 的报价。
- `repeat_purchase` 必须同时有历史订单和本次新采购动作。
- `inquiry_intent` 必须有明确产品、数量、预算或交期事实。
- `new_lead_no_profile` 必须是未建档且只有一次入站的新线索。
- 证据不足返回 `unknown`。

`detail_view.profile` 包含 `industry_context`、`company_ops`、`intent`；`detail_view.analysis` 包含 `timeline`、`opportunity`、`risk`、`guidance`。

七个维度统一使用：

```json
{
  "facts": [{"text": "...", "source_refs": ["来源ID"]}],
  "inferences": [
    {
      "text": "...",
      "basis": "...",
      "confidence": "high",
      "source_refs": ["来源ID"]
    }
  ],
  "missing_fields": []
}
```

后端接收的 `source_refs` 保持原契约：邮件 `dedupe_key`、`company_id`、`customer_id`、联系人邮箱、工单/报价/订单 ID 以及已登记的实验来源。`analysis-v5` 在模型侧使用本次输入专属的 `src_001` 等短编号，并给事实及业务对象附上 `source_ref`；Python 还原为完整来源后才执行原校验和提交。模型直接返回白名单中的完整标识也可接受；不会按邮件 ID 后缀或相近邮箱猜测来源。`latest_message_summary` 没有明确的邮件来源绑定，因此只保留在 L2 快照，不再作为 L3 模型的独立证据输入；`metrics`、`facts` 等字段名同样不是来源。

L3 会拒绝无效来源、无来源的事实或推断、非法枚举、成交概率、错误规模档位及不满足门槛的信号。`context_completeness` 由 Python 按 L2 计数生成：零封未解析时 note 为 null，否则写明数量与分析不完整；模型遗漏、空字符串或错误类型不再造成整份画像失败。存在未解析邮件且 missing_fields 为空时，程序补充该已知缺项。

单层 JSON Markdown 围栏、重复引用和系统完整性字段在本地处理，不额外调用模型。描述字段中完整独立的“目前无法判断成交概率”等无法评估说明会同义改写为“现有资料不足以判断交易结果。”，保持后端兼容并记录 `l3_probability_denial_normalized`；不改数字、引用、条件或肯定预测，复合句不会被这一规则删改。其他 JSON 或业务规则失败时，默认 provider 将具体错误、合法来源映射和上一版完整输出（最多 32000 字符，超限则不附带）交给模型定向修正一次，第二次仍不合法则失败；网络错误不自动重试。概率错误包含字段路径及命中关键词，日志不输出完整客户句子或整份模型结果。前后端关键词限制仍保留，更宽的否定/未知语义支持需要共同对齐；实际概率预测继续拒绝，付款比例等业务百分比本身不触发该限制。

`repeat_purchase` 的历史订单只能来自 `business_context.orders`；邮件自述和 `facts.order_reference` 不能代替后端订单记录。`size_band` 和 `size_source` 使用后端客户人数或已验证的实验人数，人数未知时输出 `unknown`。冲突字段只允许使用 L1 的十三个事实字段，模型偶发返回的 `company_name` 会规范为 `company_self_reported`，其他非法字段在提交后端前失败。此次调整不改变 L1、L2 保存格式、L4 公式或后端接口；旧分析缓存通过 `analysis-v5` 版本隔离，仍需显式发起分析才会生成新结果。

失败时返回 `status=failed`、`list_view=null`、`detail_view=null` 和本地调试错误，不写入分析缓存。

成功的 `Analysis` 示例：

```json
{
  "company_id": "company:example.com",
  "input_version": "sha256:...",
  "analysis_prompt_version": "analysis-v3",
  "generated_at": "2026-09-12T10:02:00+08:00",
  "analysis_base_time": "2026-09-12T10:01:00+08:00",
  "status": "completed",
  "list_view": {
    "signal": "repeat_purchase",
    "signal_evidence": {
      "text": "历史订单客户再次提出明确采购需求",
      "source_refs": ["order-1", "sales@example.com:gmail-message-id"]
    },
    "ticket_signals": [
      {
        "ticket_id": "ticket-1",
        "signal": "repeat_purchase",
        "reason": "存在历史订单和本次新采购动作"
      }
    ],
    "industry": "工业检测",
    "industry_evidence": {
      "text": "CRM 行业和邮件产品需求指向工业检测",
      "source_refs": ["customer-1", "sales@example.com:gmail-message-id"]
    },
    "size_band": "200_500",
    "size_source": "crm",
    "headline_summary": "客户要求在截止日前取得正式报价",
    "score_features": {
      "demand_clarity": {"value": 3, "basis": "产品、数量和报价要求明确"},
      "urgency": {"value": 3, "basis": "存在明确回复截止日"},
      "decision_visibility": {"value": 2, "basis": "已知项目负责人和内部汇报安排"}
    }
  },
  "detail_view": {
    "conflicts": [],
    "profile": {
      "industry_context": {
        "facts": [{"text": "CRM 行业为工业自动化", "source_refs": ["customer-1"]}],
        "inferences": [],
        "missing_fields": []
      },
      "company_ops": {
        "facts": [{"text": "公司员工数为 260", "source_refs": ["customer-1"]}],
        "inferences": [],
        "missing_fields": []
      },
      "intent": {
        "facts": [{"text": "客户要求正式报价", "source_refs": ["sales@example.com:gmail-message-id"]}],
        "inferences": [],
        "missing_fields": []
      }
    },
    "analysis": {
      "timeline": {
        "facts": [{"text": "客户再次发起采购咨询", "source_refs": ["sales@example.com:gmail-message-id"]}],
        "inferences": [],
        "missing_fields": []
      },
      "opportunity": {
        "facts": [{"text": "客户有一笔历史订单", "source_refs": ["order-1"]}],
        "inferences": [{"text": "存在复购机会", "basis": "历史订单加本次新采购动作", "confidence": "high", "source_refs": ["order-1", "sales@example.com:gmail-message-id"]}],
        "missing_fields": []
      },
      "risk": {
        "facts": [],
        "inferences": [],
        "missing_fields": ["最终审批人"]
      },
      "guidance": {
        "facts": [],
        "inferences": [{"text": "优先确认最终审批人", "basis": "当前决策链信息不完整", "confidence": "medium", "source_refs": ["ticket-1"]}],
        "missing_fields": []
      }
    },
    "missing_fields": ["最终审批人"],
    "context_completeness": {
      "unparsed_message_count": 0,
      "note": null
    }
  },
  "error": null
}
```

## 7. L4：跟进优先级

入口：

```python
compute_score(analysis, analysis_input, clock=clock, priority_context=context) -> dict
```

`score` 表示当前销售处理优先级，不表示成交概率。每家公司仍只生成一份分数，流程仍为 L2→L3→L4。L4 不调用 LLM；L1 的 `intent_hint` 阶段由 L2 连同邮件来源和证据传入 L4。缺少可核实采购阶段时返回 `score=null`；有阶段但缺少商机或匹配资料时按已有维度折算暂定分，并在 `score_reasons` 中说明缺项。

公式为 `35% × urgency + 35% × buying_intent + 30% × opportunity_value`，其中 `opportunity_value = 60% × deal_value + 40% × customer_fit`。紧急度按 4 小时内 100、24 小时内 90、2 天内 80、7 天内 65、14 天内 45、更晚 25、无明确时间节点 10；只有客户催促但无截止时间也按 10。邮件只给日期时按评分时钟的日历日保守计分：今天 90、未来 1–2 天 80，不虚构具体小时。已过去的普通截止时间不再持续拉高分数，只有明确的 `OVERDUE_ACTION` 可计入逾期紧急度。采购意向按一般咨询 20、产品/演示 40、数量/预算/采购时间 60、正式报价/决策人 75、合同/付款 90、批准/确认采购 100。金额与同币种历史平均成交额之比按 `<0.5 / [0.5,1) / [1,2) / [2,5] / >5` 映射 `20/40/60/80/100`。客户匹配按行业 25、规模 15、地区 10、产品 35、可比历史赢单 15 加权。

`priority_context` 是供 L4 使用、不会混入已保存的 L2 `AnalysisInput` 的公司级上下文：

```json
{
  "customer": {"customer_id": "C001", "company_name": "Example Manufacturing", "industry": "Manufacturing", "company_size": 100, "country": "Singapore"},
  "deal": {"deal_value": "250000", "currency": "SGD", "stage": "Proposal", "product": ["WMS", "OHT"], "quantity": 20, "status": "ACTIVE"},
  "seller": {
    "average_deal_value": "30000", "average_deal_currency": "SGD",
    "time_zone": "Asia/Singapore",
    "target_industries": ["Manufacturing"],
    "target_company_size": {"min": 20, "max": 500},
    "service_regions": ["Singapore"], "products": ["WMS"],
    "similar_won_deals": true
  }
}
```

`deal` 沿用正式文档的商机字段名，但此处已按产品约定归并到公司级；只对 `status=ACTIVE` 且金额大于零的公司级商机评分。L2 从最近 20 封已完成抽取的邮件读取客户采购阶段，选择有原文支持的最高阶段；`dedupe_key` 是来源 ID。L4 只做来源校验、规则评分和解释，不再重复调用模型。没有明确跟进截止时间时紧急度按基础档 10 分；现有 `delivery_time` 原文不自动等同于销售跟进截止时间，因此当前仅靠新阶段字段的公司通常使用基础档。缺失商机或匹配资料时不虚构金额：若两项都缺，按紧急度与采购阶段的 35:35 折算；只有其中一项可算时，用该项作为暂定商机分，仍按 35/35/30 折算。各贡献整数之和严格等于分数。`rank_company_scores()` 按公司分数降序、紧急度贡献、公司 ID 排序，空分排最后；后端公司列表也按此口径排序。

`compute_priority_result()` 在同一次 L4 计算中返回后端现有的 `score` 载荷，以及 `score_details`：原始三项分数和贡献、按影响排序的前三原因、可跳回邮件的证据、建议下一步动作。`analyze_company()` 保存 `score`；完整分项可计算时一并提交 `score_details`，资料不全的暂定分保留在 `score_reasons`，省略尚无法完整计算的 `score_details`。后端保存完整解释并通过公司详情返回；现有 `Score.score` 字段就是文档中的 `priority_score`，不再增加第二个分数字段。

后端已在 `CompanyContext.priority_context` 返回公司级活跃商机、客户与销售方资料，商机金额只汇总同币种记录；相关业务资料变化会更新版本并排入分析任务。`score-v2` 保存接口接受暂定分，不再依赖旧 L3 特征，完整解释可随分数保存并在公司详情读取。前端目前只展示总分和 `score_reasons`；如需展示前三原因、独立证据链接和建议动作，还需改前端页面。独立的 `rules` 模式仍是联调用旧占位算法，应在正式评价场景使用 Agent 模式。

离线验证：`python -m unittest agent.tests.test_lead_score`。此测试使用固定时钟和假信号，不连接百炼或真实后端。

## 8. 编排和后端接口

完整公司分析：

```python
analyze_company(
    company_id,
    backend=backend,
    analysis_provider=bailian_analysis_provider,
    clock=clock,
) -> AnalysisBundle
```

执行顺序：构建并保存 L2；查询 L3 缓存；未命中时调用百炼；只保存校验成功的 L3；计算并保存 L4；返回 L2、L3、L4、缓存状态和错误。

一次任务处理：

```python
process_jobs_once(
    backend=backend,
    limit=10,
    analysis_provider=bailian_analysis_provider,
    clock=clock,
) -> list[JobReport]
```

支持 `email_ingested`、`customer_detail_opened`、`external_updated` 和 `grouping_changed`。函数领取一批任务后立即返回；同一批中相同公司只分析一次。Agent workflow 不实现常驻轮询或任务级自动重试；L1 和 L3 各自的一次模型校验修正不属于任务重试。现有 Django 后端要求的租约和版本请求头由 `DjangoBackendClient` 管理。

后端请求异常的 `analysis_job_failed` 日志包含 `http_status`、`backend_code` 和 `retry=explicit`。HTTP 409 不会被 Agent 直接重试或绕过版本校验；任务报告保持既有契约，需要后端和业务方基于最新资料发起新的分析。

Agent workflow 依赖 `agent.clients.backend_api.BackendClient`：

```python
submit_emails(submissions)
get_company_grouping(company_id)
get_company_context(company_id)
save_analysis_input(analysis_input)
get_latest_analysis_input(company_id)
get_cached_analysis(company_id, input_version)
save_analysis(analysis)
save_score(score)
claim_jobs(limit)
report_job(report)
claim_mailbox_syncs(limit)
report_mailbox_sync(report)
```

`agent.clients.backend_api.DjangoBackendClient` 已针对当前 Django API 做以下传输适配：

- 使用 `Authorization: Agent <service-token>`。
- 为邮件提交补充 `mailbox_id` 和 `source=gmail_real`。
- 最多四路并发执行 L1，并把后端逐封邮件结果聚合为同步统计。
- 单封查询、L1 或提交失败只记录到 `email_errors` 和重试 ID，不使其他邮件回滚。
- 保存并传递 Grouping/CompanyContext 的 ETag。
- 读取后端 Job 的顶层 `company_id`。
- 内部保存 `lease_token` 和 `expected_version`，写入 L2/L3/L4 时自动添加请求头。
- 缓存未命中映射为 `None`；命中时要求后端返回完整 Analysis。
- 领取当前服务凭证所属员工的 Gmail 同步请求，并回报同步状态和刷新后的授权。
- 通过现有 `sync-state` 端点读取和保存 Gmail `historyId`，后续同步在 L1 之前跳过未变化的历史邮件。
- 通过现有单封邮件兼容查询识别同版本完成记录，首次建立游标时也不会用新的模型结果覆盖已有事实。

适配器保持 17 字段的多值事实结构，`extract-v7` 调整 `intent_hint` 的枚举含义。Django 邮件提交、人工补交和 L2 只接受新契约；无采购阶段的入站邮件进入人工复核。部署此版本前需清理旧邮件及持久同步游标，重新同步后才会由 L1 生成 `extract-v7` 事实。

## 9. 运行

在项目根目录安装依赖：

```powershell
python -m pip install -r requirements.txt
```

项目根目录 `.env` 至少包含：

```text
DASHSCOPE_API_KEY=...
BAILIAN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
BAILIAN_MODEL=模型名称
SALESMATE_BACKEND_AGENT_URL=http://127.0.0.1:8000/api/v1/agent/
SALESMATE_AGENT_SERVICE_TOKEN=后端生成的Agent服务令牌
SALESMATE_MAILBOX_ID=后端创建的邮箱UUID
SALESMATE_JOB_LEASE_SECONDS=120
SALESMATE_BACKEND_TIMEOUT=30
```

网页 Gmail OAuth 的 `GOOGLE_OAUTH_CLIENT_ID`、`GOOGLE_OAUTH_CLIENT_SECRET` 和回调地址由 Django 从同一个根 `.env` 读取，Agent 无需重复配置。

```powershell
# 从真实后端读取公司数据，只构建并输出 L2
python -m agent.main --analysis-company-id <COMPANY_UUID>

# 从真实后端领取一批任务，运行一次 L2-L4 后立即退出
python -m agent.main --process-jobs-once --job-limit 10

# 领取员工在网页请求的 Gmail 同步，并运行本次 L1-L4
python -m agent.main --sync-authorized-mailboxes-once

# 领取并回答至多一条聊天请求，然后立即退出
python -m agent.main --process-chat-once
```

`--process-chat-once` 是工作空间聊天的一次性 Agent 客户端入口，使用受保护的 claim、context、tool-read 和 report 接口；它不启动 HTTP 服务、数据库或常驻循环。后端负责请求、授权上下文、工具证据、assistant/Citation 持久化和浏览器状态投影；运行器负责持续领取任务。

网页授权通过 Django 建立员工邮箱连接；浏览器不接触 token，Django 按需启动的 Agent 或 `--sync-authorized-mailboxes-once` 调试命令从受保护的 Agent API 领取。旧的 `--message-id`、`--recent` 和 `--sync-gmail` 本机 Desktop OAuth 命令已经删除，避免与正式网页授权流程维护两套凭据。

### 9.1 向测试 Gmail 注入全链路样例

没有多个真实外部联系人邮箱时，可以使用独立测试工具把六封 RFC 2822 合成邮件直接插入开发者自己的 Gmail。该工具不调用或修改后端，也不会向外部地址发送邮件；网页仍通过原有只读 OAuth 拉取，所以邮件会经过 Gmail、L1、后端归组和 L2–L4。Gmail 官方 `messages.insert` 会绕过大部分正常投递扫描，因此该流程不验证 SMTP、SPF、DKIM 或垃圾邮件分类。

测试邮件注入器位于与 `agent/`、`backend/` 同级的 `test_tools/`，可由开发和测试人员独立使用。完整准备步骤、权限说明、全流程验证和清理方法见 [测试工具说明](../test_tools/README.md)。在 Google Cloud 的 Credentials 页面另外创建一个 **Desktop app** OAuth Client，将下载的 JSON 重命名为 `gmail_inject_credentials.json` 并放入 `test_tools/`。不要使用后端网页授权所需的 Web application Client；它只允许登记的 Django 回调地址，不能接收测试注入器生成的随机 localhost 端口。在 OAuth consent screen 中还要把测试 Gmail 加入 Test users。预览本次样例：

```powershell
python -m test_tools.gmail_test_injector --dry-run
```

实际插入：

```powershell
python -m test_tools.gmail_test_injector
```

工具默认读取 `test_tools/gmail_test_messages.template.json`，也可通过 `--messages-file` 指定测试人员自己的 JSON；收件箱由 JSON 顶层的 `mailbox_address` 指定。首次运行会读取被 Git 忽略的 `test_tools/gmail_inject_credentials.json`，在浏览器申请 `gmail.insert` 和 `gmail.readonly`，并把独立 token 保存在 `test_tools/gmail_inject_token.json`。工具会验证授权账号与 JSON 中的邮箱一致，然后插入模板定义的邮件。完成后打开 Gmail 确认主题前缀，再到 SalesMate 工作台点击“同步 Gmail”。需要重新选择账号或重新授权时，删除 `test_tools/gmail_inject_token.json` 后再次运行。

为了让 History 游标稳定捕获样例，推荐先在 SalesMate 完成一次正常 Gmail 授权和同步，再运行注入命令。测试邮件通过 Gmail 进入系统，当前传输来源会显示为 `gmail_real`；请使用专门的测试 Gmail 和测试数据库，完成后可按工具输出的主题前缀在 Gmail 中搜索并手工删除。

## 10. 测试

```powershell
# 完整离线测试
python -m unittest discover -s agent/tests -p "test_*.py"

# MVP 主链测试
python -m unittest agent.tests.test_mvp_pipeline
```

自动测试不连接真实 Gmail、百炼、数据库、HTTP 或知识服务；真实外部联调只使用非敏感 Demo 数据做人工冒烟验证。当前完整离线发现命令通过 189 项测试。

测试文件分工：

| 文件 | 职责 |
|---|---|
| `agent/tests/email_submission_exploration.py` | 提供 L1 公共 fixture，并覆盖 EmailSubmission 的数据契约边界；文件名不以 `test_` 开头，由 `test_core.py` 导入执行 |
| `agent/tests/test_workspace_chat.py` | 覆盖工作空间聊天的客户搜索、详情、证据引用、工具失败与一次性回报 |
| `agent/tests/test_core.py` | 覆盖百炼客户端、聊天 CLI 回归、Gmail resource、MIME、证据边界、L1 Prompt、事实抽取和 EmailSubmission 契约 |
| `agent/tests/test_integration.py` | 覆盖 Gmail 只读读取、History 分页与过期、profile 回退和百炼客户端集成边界 |
| `agent/tests/test_analysis_input.py` | 覆盖 L2 事实归并、业务上下文、版本和错误边界 |
| `agent/tests/test_mvp_pipeline.py` | 覆盖 Gmail 同步、L1 并发与失败隔离、L2–L4、缓存和既有端到端流程 |
| `agent/tests/test_lead_score.py` | 覆盖公司级正式公式、信号证据、截止时间档位、资料不足和排序 |
| `agent/tests/test_http_backend.py` | 覆盖 Django 服务认证、邮箱/游标/ETag/任务租约、响应归一化及聊天工具 mocked contract |
| `agent/tests/test_qq_mail.py` | 覆盖 QQ IMAP 只读适配边界 |

`agent/tests/fake_backend.py` 只是既有流程的测试 fixture；聊天测试中的内存 fake backend 也只模拟外部契约。二者都不参与运行时或真实后端持久化。

## 11. Django 后端已对齐的契约

当前真实后端已经实现以下契约：

1. 邮件去重键接受 `mailbox_address:gmail_message_id`，同时使用适配器补充的 `mailbox_id` 验证邮箱归属。
2. L1 使用 `intent_evidences` 数组；13 个普通事实字段使用多组 `{value, evidences[]}`，未知值为 `[]`。
3. 同一邮件原抽取为 `failed`、新抽取为 `completed` 时，普通邮件提交返回 `updated`。
4. 只有业务邮件且 `has_substantive_update=true` 时创建 `email_ingested` 任务。
5. AnalysisInput 接收并原样保存 `company`、`business_context` 和 `latest_message_summary`，L2 facts 保留所有 `evidences`。
6. 缓存命中返回完整 Analysis。
7. `detail_view` 内保存 `missing_fields` 和 `context_completeness`。
8. 三个评分特征的未知值使用 JSON `null`。
9. L3 的合法 `source_refs` 包含 customer_id 和联系人邮箱。
10. Job 顶层携带 `company_id`，合法重复公司任务可以回报 `skipped`。
11. `sync-state` 返回 ETag 和 version，Agent 保存 Gmail `historyId` 时使用 `If-Match`，不需要新增后端接口。
12. `failed-extractions` 的单封查询返回当前 EmailSubmission；Agent 用它在 L1 前跳过同版本可信终态，并继续处理失败或新邮件。
13. 邮件提交仍使用既有 `POST emails/`，但 Agent 每次只提交一个元素，利用后端现有原子事务隔离单封错误，不需要新增接口。

## 12. 当前限制

- 首次 Gmail 同步只回溯最近 20 封邮件；成功保存 `historyId` 后会分页读取全部新增记录，并把超过单轮上限的 message ID 留到后续轮次。游标过期时合并已有待处理/失败 ID 与最近扫描，避免清空未完成清单。
- 当前后端只能逐封查询已有邮件；首次扫描和游标回退最多增加 20 次轻量 HTTP 查询。后续若增加批量邮件状态接口，可把这些查询合并成一次，但不影响当前正确性。
- L1 固定最多四路并发，避免一次产生二十个百炼请求；若账号限流，应在 Agent 侧把并发数改小。L2–L4 按公司 Job 独立处理，同一公司的多封更新由后端合并为一项最新任务。
- 软件独立 `crm_worker` 消费持久批次和画像任务；CLI 保留一次性调试，Web 不启动 Agent 线程。
- Agent 仍会提交规则识别的 `skipped_non_business`；完成抽取但没有可证实采购阶段的入站邮件使用 `intent_hint=null`，后端送人工复核；前端继续展示后端分类。
- Web 重启不删除数据库任务；Worker 执行发生 HTTP 错误时显式失败，进度页可明确重试失败邮件。
- L3 不使用外部行业资讯或知识库。
- L4 权重尚未使用真实销售样本校准。
- 实际筛选、排序、分页、CRM 建档和前端渲染由后端与前端实现。

软件 Worker 为 `sync_gmail` 提供可选 progress 观察回调和显式重试 message_ids；观察模式在读取前登记消息并逐封隔离读取错误。原 CLI 默认参数保持不变。详见 [处理适配](../backend/docs/processing-integration.md)。

## 公司实验资料补充

L2 直接复制后端 CompanyContext.company_enrichment，并将完整补充对象纳入 input_version；无需新增 Tool 凭证、分页检索或在 Agent 重做实体匹配。L3 的 `analysis-v5` 支持该快照登记的实验来源，CRM 人数优先，缺失时使用实验人数并标注 `synthetic_sample`。发送给模型时只保留补充事实、`source_id`、对应的 `source_ref` 短编号、虚构标记及必要状态，不发送 owner、指纹、批次和版本等后端维护元数据。L1/L4 和模型预算不变。详见 [后端对接契约](../backend/docs/company-enrichment.md)。
