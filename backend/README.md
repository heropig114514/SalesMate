# SalesMate 软件与 Agent 联调

本说明位于软件目录 `backend/`。除另有说明外，命令均从 **SalesMate 仓库根目录** 执行；Django 软件、页面、契约和工具归 backend/，Agent 实现归 agent/，根目录仅保留共享配置与依赖入口。返回[仓库概览](../README.md)。

SalesMate 是一个面向 B2B 销售人员的 Agent MVP。系统从 Gmail 读取往来邮件，提取客户意向和可定位证据，按公司归组，生成客户画像、销售分析与跟进优先级，并把结果展示在浏览器工作台中。

更新日期：2026-09-12
当前状态：前端、Django 后端和 Agent L1–L4 已按同一数据契约完成整合；本地 HTTP 全链路已验证。真实 Gmail 和阿里百炼需要开发者自己的授权与 API Key。

## 1. 当前 MVP 范围

已经实现：

- Gmail 只读 OAuth 和最近邮件同步。
- L1 单封邮件事实抽取，模型使用阿里百炼 OpenAI 兼容接口。
- 邮件去重、失败抽取更新、公司和联系人归组。
- 业务邮件变化触发一次性分析任务。
- L2 公司级事实归并及客户、工单、报价、订单上下文组装。
- L3 客户画像、客户分析、销售信号和评分特征生成。
- L4 可复现的 0–100 跟进优先级计算。
- Django 持久化、Agent 服务认证、分析缓存和任务状态。
- 参考 MVP 文档实现的员工 Gmail 收件箱、Google 授权管理和客户详情页面。
- 使用必填 `DATABASE_URL` 显式选择数据库；本机与模板使用 PostgreSQL，SQLite 仅作为显式选择。

销售扩展已提供客户/联系人/归组、产品、工单、商机、报价和订单明细、跟进、团队授权、审计、私有附件、会话草稿，以及独立销售 Worker。Gmail 发信和 Google 日历的适配器与明确确认流程已实现，真实执行需要新写权限授权和加密密钥。当前不包含 WhatsApp、会议纪要、知识库、行业新闻或聊天模型自由对话。页面中的分数表示处理优先级，不表示成交概率。

客户详情页“AI 助手”提供私有会话、历史消息和显式草稿保存；桌面使用右侧栏，1000px 及以下全屏显示。保存后可以跨刷新恢复；未保存文本只保存在本页。当前“保存消息”仅记录用户消息，不调用聊天模型。工具计划从顶部“业务管理”进入，先准备并审阅完整内容，再单独确认。`frontend/assets/assistant.js` 管理侧栏，`business.js` 管理销售工作区，现有“更新分析”仍独立。

业务管理：`http://127.0.0.1:8000/business/`。模型、状态、API、外部授权和 Worker 部署步骤见 [销售扩展说明](docs/backend-expansion.md)。

工作台的收件箱归属于当前登录员工。后端先按员工隔离邮箱和邮件，再在该员工的数据范围内按客户公司归组；它不是把全公司所有员工邮件混在一起的共享收件箱。

## 2. 系统架构

```mermaid
flowchart TB
    USER[当前销售员工] --> WEB[员工 Gmail 收件箱]
    WEB <-->|Session JSON API| DJANGO[Django + DRF]
    WEB -->|发起 Google OAuth| DJANGO
    DJANGO <-->|授权码与只读凭证| GMAIL[Gmail]
    DJANGO -->|员工专属同步请求、凭证与 History 游标| SYNC[一次性 Agent Gmail Sync]
    GMAIL -->|新增 message ID 与只读邮件| SYNC
    SYNC --> PARSE[邮件解析]
    PARSE --> L1[L1 单封事实抽取<br/>最多四路并发]
    L1 <-->|JSON Object| BAILIAN[阿里百炼]
    L1 -->|任一完成即逐封提交 EmailSubmission| DJANGO

    DJANGO -->|Job + CompanyContext| ORCH[Agent 一次性任务编排]
    ORCH --> L2[L2 公司事实归并]
    L2 --> L3[L3 客户画像与分析]
    L3 <-->|JSON Object| BAILIAN
    L3 --> L4[L4 跟进优先级]
    L2 -->|AnalysisInput| DJANGO
    L3 -->|Analysis| DJANGO
    L4 -->|Score| DJANGO
    DJANGO -->|公司列表与详情| WEB
```

模块职责：

| 模块 | 技术 | 职责 |
|---|---|---|
| `backend/frontend/` | 原生 HTML、CSS、JavaScript | 当前员工 Gmail 授权、同步状态、公司列表、邮件原文、画像、分析、业务记录和跟进分数 |
| `backend/` | Django、DRF | 员工会话、Google OAuth、邮箱凭证、邮件、公司、联系人、业务上下文、任务和分析结果持久化 |
| `agent/` | Python、Gmail API、百炼 | 领取员工邮箱同步请求、Gmail 读取、L1–L4、后端 HTTP 客户端和一次性编排 |
| `backend/contracts/` | OpenAPI YAML | 当前 HTTP 接口结构 |
| `backend/tools/` | Python/Node 脚本 | 文档一致性和可选浏览器检查 |

Agent 不直接访问数据库，后端不执行真实模型推理。两者只通过 `/api/v1/agent/` 下的 JSON API 通信。浏览器只接收授权跳转地址和邮箱同步状态，不接触 Gmail token、百炼 Key 或 Agent 服务令牌。当前本地 MVP 由 Django 保存 Google 授权信息，Agent 通过服务认证接口在同步时领取。

## 3. 完整业务流程与交换数据

| 阶段 | 输入 | 输出 | 保存位置 |
|---|---|---|---|
| 员工 Gmail 授权 | 当前员工 Session、Google OAuth code | 邮箱地址、授权状态、`sync_requested` | Django `GmailCredential` 与 `Mailbox.sync_state` |
| 同步任务领取 | Agent 服务凭证、领取数量 | `mailbox_id`、邮箱地址、Google 授权信息、读取上限 | 状态变为 `sync_running` |
| Gmail 读取 | 已领取的员工授权、读取上限和后端 History 游标 | 首次最近邮件或游标后的新增 message resource | Agent 内存；游标由 Django `Mailbox.sync_state` 保存 |
| 邮件解析 | raw MIME、message/thread ID | 发件人、收件人、主题、正文、时间、方向 | Agent 内存 |
| L1 抽取 | 当前邮件主题和正文 | `EmailSubmission` | 最多四路并发；任一完成后立即逐封保存到 Django `Email` 与 `Extraction` |
| 后端归组 | 联系人邮箱和自报公司 | `company_id`、联系人、成员邮件键 | Django `Company` 与 `Contact` |
| 任务入队 | 业务邮件且 `has_substantive_update=true` | `email_ingested` Job | Django `Job` |
| L2 归并 | Grouping、邮件、客户、工单、报价、订单 | `AnalysisInput` | Django `AnalysisInput` |
| L3 分析 | 完整 `AnalysisInput` | `Analysis` | Django `Analysis` |
| L4 评分 | L3 信号与特征、L2 时间指标 | `Score` | Django `Score` |
| 页面读取 | 当前员工 Session、公司列表或公司 ID | 该员工的邮箱状态、公司列表、详情、邮件、画像、分数、任务状态 | 浏览器展示 |

重复同步规则：

- `dedupe_key` 为 `mailbox_address:gmail_message_id`。
- 相同邮件和相同抽取结果返回 `duplicate`。
- 原抽取为 `failed`，下次同步成功时返回 `updated` 并更新事实。
- 非业务邮件仍保存，但不创建分析任务；当前后端尚未把仅含非业务邮件的公司从默认列表中排除。
- 没有实质变化的业务邮件仍保存，但不自动重跑公司分析。
- 一封邮件的抽取或提交错误不会回滚其他邮件；失败 message ID 保留到下一轮重试。
- 企业邮箱按域名归组，常见公共邮箱按完整联系人邮箱独立归组。

## 4. Agent 数据结构

### 4.1 L1：EmailSubmission

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
  "extract_prompt_version": "extract-v6",
  "extract_error": null,
  "facts": {}
}
```

`facts` 包含控制字段：

- `has_substantive_update`
- `message_summary`
- `intent_hint`
- `intent_evidences`

`intent_hint` 枚举为 `purchase_inquiry`、`meeting`、`support`、`non_sales`、`unknown`。

其余 13 个事实字段为 `contact_name`、`contact_title`、`company_self_reported`、`business_background`、`employee_scale_hint`、`product_need`、`quantity`、`budget`、`delivery_time`、`decision_process`、`concerns`、`quote_reference`、`order_reference`。

每个事实字段都是多值数组，每个值可以对应多条原文证据：

```json
[
  {
    "value": "50 台",
    "evidences": ["需要 50 台检测设备", "首批数量为 50 台"]
  }
]
```

未知事实使用空数组。所有 evidence 必须能在当前邮件主题或有效正文中逐字定位。

### 4.2 L2：AnalysisInput

L2 不调用模型。它保留全部事实历史并补充来源邮件和事实时间，同时携带后端业务上下文。

```json
{
  "company_id": "后端公司 UUID",
  "input_version": "sha256:...",
  "merge_version": "merge-v2",
  "external_snapshot_version": "ext-0",
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
  "latest_message_summary": "客户要求正式报价",
  "member_dedupe_keys": [],
  "unparsed_message_count": 0,
  "facts": {},
  "metrics": {}
}
```

`metrics` 包含收发数量、有效入站数、首次联系时间、最近收发时间、最近同线程响应间隔、是否存在历史订单和 CRM 状态。`input_version` 用于分析缓存；邮件抽取状态、归并版本或后端业务快照变化时版本随之变化。

### 4.3 L3：Analysis

一次百炼调用同时生成：

- 页面 A：公司主信号、逐工单信号、行业、规模、摘要和三个评分特征。
- 页面 B：行业情况、公司经营、客户意向、时间轴、商机、风险和引导建议。
- 事实变化与来源冲突、缺失项和上下文完整度。

主信号枚举：`repeat_purchase`、`quoted_not_closed`、`inquiry_intent`、`new_lead_no_profile`、`unknown`。

三个评分特征为 `demand_clarity`、`urgency`、`decision_visibility`，值只能是 0–3 或 JSON `null`。七个详情维度都使用以下结构：

```json
{
  "facts": [{"text": "客户要求正式报价", "source_refs": ["邮件 dedupe_key"]}],
  "inferences": [{"text": "...", "basis": "...", "confidence": "high", "source_refs": ["来源 ID"]}],
  "missing_fields": []
}
```

允许引用邮件 `dedupe_key`、`company_id`、`customer_id`、联系人邮箱、`ticket_id`、`quote_id` 和 `order_id`。模型输出不是合法 JSON、枚举非法、来源不存在、缺少推断依据或出现成交百分比时，L3 返回失败且后端不保存分析和评分。

### 4.4 L4：Score

| 特征 | 权重 |
|---|---:|
| signal | 0.30 |
| demand_clarity | 0.20 |
| urgency | 0.20 |
| decision_visibility | 0.10 |
| recency | 0.15 |
| substantive_inbound_count | 0.05 |

分数是各项整数贡献之和。信号未知、任一模型特征为 `null` 或缺少最近入站时间时，`score=null`，并只返回一条 `insufficient_data` 原因。

## 5. 目录结构

```text
SalesMate/
├── README.md                     # 仓库概览
├── .env.example                  # 双方共享配置模板
├── requirements.txt              # 后端和 Agent 的统一安装入口
├── backend/                      # 软件应用
│   ├── README.md                 # 本文：软件开发及联调
│   ├── apps/、config/、common/    # Django API、模型和基础模块
│   ├── frontend/                 # HTML 与静态资源
│   ├── contracts/                # OpenAPI 和响应示例
│   ├── docs/                     # 软件文档与接入说明
│   ├── tools/                    # 注释和浏览器检查
│   ├── tests/、requirements/      # 软件测试和依赖
│   └── manage.py
└── agent/
    ├── README.md、main.py、config.py
    ├── clients/                  # Django HTTP 客户端
    ├── tools/、llm/               # Gmail 读取与百炼调用
    ├── workflows/                # L1–L4 与编排
    └── tests/
```

Agent 内没有单独的 `schemas`、`prompts` 或模拟后端运行层。Prompt 直接放在对应 workflow 中；`agent/tests/fake_backend.py` 只服务于离线测试。

## 6. 统一环境配置

项目只读取根目录 `.env`。`agent/.env` 和 `backend/.env` 已取消。

首次配置：

```powershell
Copy-Item .env.example .env
```

编辑 `.env`：

```dotenv
DJANGO_SECRET_KEY=本地随机字符串
DJANGO_TIME_ZONE=UTC
ANALYSIS_PROVIDER=rules
SALESMATE_AUTO_RUN_AGENT=False
LOCAL_DEBUG_AUTO_LOGIN=True
LOCAL_DEBUG_USER=demo

GOOGLE_OAUTH_CLIENT_ID=你的Web客户端ID
GOOGLE_OAUTH_CLIENT_SECRET=你的Web客户端密钥
GOOGLE_OAUTH_REDIRECT_URI=http://127.0.0.1:8000/api/v1/mailboxes/gmail-callback/

DASHSCOPE_API_KEY=你的百炼Key
BAILIAN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
BAILIAN_MODEL=你的模型名称
BAILIAN_ENABLE_THINKING=false

SALESMATE_BACKEND_AGENT_URL=http://127.0.0.1:8000/api/v1/agent/
SALESMATE_AGENT_SERVICE_TOKEN=
SALESMATE_MAILBOX_ID=
SALESMATE_ANALYSIS_PROMPT_VERSION=analysis-v2
SALESMATE_JOB_LEASE_SECONDS=120
SALESMATE_BACKEND_TIMEOUT=30
```

`DATABASE_URL` 必填；缺失或无效配置会直接失败。本机使用原 PostgreSQL，配置示例：

```dotenv
DATABASE_URL=postgresql://salesmate:password@127.0.0.1:5432/salesmate?connect_timeout=3
```

`.env`、`agent/credentials.json`、`agent/gmail_token.json`、`backend/.local-access.json` 和数据库文件均被 Git 忽略。

## 7. 首次安装和初始化

以下命令从项目根目录执行：

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python backend/manage.py migrate
python backend/manage.py provision_local --username demo --mailbox-address your-account@gmail.com
```

`provision_local` 会创建普通用户、业务邮箱和 Agent 服务凭证，把服务令牌、邮箱 UUID 和用户名写入根 `.env`，并把登录密码等本地凭证写入被忽略的 `backend/.local-access.json`。命令只用于第一次初始化，拒绝覆盖已有用户和凭证文件。

在 PyCharm 中打开整个 `SalesMate` 根目录，然后进入 **Settings → Project → Python Interpreter**，选择 `SalesMate\.venv\Scripts\python.exe`。所有 Run Configuration 的 Working directory 都设为项目根目录。这样 `agent.*`、Django 设置和根 `.env` 会按相同路径解析。

如果当前工作区已经存在根 `.env`、已配置数据库和 `backend/.local-access.json`，不要再次执行 `Copy-Item` 或 `provision_local`。激活已有解释器后只需运行：

```powershell
python backend/manage.py migrate
python backend/manage.py check
```

员工网页 Gmail 授权使用 Google Cloud 的 **Web application** OAuth Client。首次准备：

1. 在 Google Cloud Console 启用 Gmail API。
2. 配置 OAuth consent screen；测试阶段把每个要授权的员工 Gmail 地址加入 Test users，否则 Google 会返回 `403 access_denied`。
3. 创建 **Web application** 类型的 OAuth Client。
4. 在 Authorized redirect URIs 中精确添加 `http://127.0.0.1:8000/api/v1/mailboxes/gmail-callback/`。
5. 在根 `.env` 填写 `GOOGLE_OAUTH_CLIENT_ID`、`GOOGLE_OAUTH_CLIENT_SECRET` 和相同的 `GOOGLE_OAUTH_REDIRECT_URI`。
6. 真实联调时显式设置 `ANALYSIS_PROVIDER=agent`。若需要网页自动触发 Agent，再设置 `SALESMATE_AUTO_RUN_AGENT=True`；默认关闭自动运行，仍可用 CLI 单次执行。
7. 在根 `.env` 填写 `DASHSCOPE_API_KEY` 和百炼模型名 `BAILIAN_MODEL`。

`agent/credentials.json` 和 `agent/gmail_token.json` 只供旧的本机 Desktop OAuth 命令 `--sync-gmail` 使用。员工从网页授权时不需要这两个文件，也不需要手工配置 `SALESMATE_MAILBOX_ID`。`--sync-authorized-mailboxes-once` 保留为自动运行关闭时的调试入口。

## 8. 启动与真实完整测试

### 第一步：启动 Django 和前端

打开第一个 PyCharm Terminal，在项目根目录执行：

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn --app-dir backend config.asgi:application --host 127.0.0.1 --port 8000 --reload
```

看到 `Uvicorn running on http://127.0.0.1:8000` 后打开：

- 工作台：http://127.0.0.1:8000/
- API 文档：http://127.0.0.1:8000/api/docs/
- 存活检查：http://127.0.0.1:8000/api/v1/health/live/
- 数据库检查：http://127.0.0.1:8000/api/v1/health/ready/

`LOCAL_DEBUG_AUTO_LOGIN=True` 时会自动使用 `.env` 中的本地用户进入工作台。设为 `False` 并重启即可测试登录页。

这一步已经同时启动前端和后端。不要双击打开 `backend/frontend/index.html`；页面依赖同源 Session、CSRF 和 `/api/v1/`，必须从 `http://127.0.0.1:8000/` 打开。四个地址都能访问，且 ready 返回 `{"status":"ok","database":"ok"}` 后再继续。

### 第二步：让员工在网页授权 Gmail

1. 在工作台左侧点击 Gmail，或点击页面右上角“连接 Gmail”。
2. 在弹窗点击“使用 Google 账号授权”。
3. 选择当前员工自己的 Google 账号并同意只读权限。
4. Google 返回工作台后，页面应显示该邮箱，并自动开始 Gmail 同步和 Agent 分析。
5. 页面每三秒读取一次同步状态，完成后自动刷新客户列表。

不同 SalesMate 登录用户拥有独立的邮箱连接、客户公司和邮件范围。即使两名员工联系相同客户域名，他们也不会在当前 MVP 中互相看到对方邮件。

### 第三步：等待自动同步和分析

授权回调或页面“同步并刷新”按钮会让后端自动启动一次后台 Agent。该 Agent 领取当前服务凭证所属员工的同步请求，并执行：

```text
首次读取最近收件和发件，后续按 Gmail History 游标读取新增邮件
→ 最多四路并发执行单封 L1 百炼抽取
→ 任一 L1 完成即逐封提交 Django
→ 领取本次产生的任务
→ L2 归并
→ L3 百炼分析
→ L4 评分
→ 保存并回报任务
```

页面显示“Gmail 同步和客户分析已完成”即表示本轮结束。后端终端会记录邮箱批次和任务批次状态；`failed_extraction_count` 大于零时，先查看终端中的 `[DEBUG]` 错误，再重试同步。

如需关闭自动运行并逐步调试，可将 `SALESMATE_AUTO_RUN_AGENT=False`，然后手工执行：

```powershell
python -m agent.main --sync-authorized-mailboxes-once
```

重点核对这些字段：

- `created_count`：第一次保存的邮件数量。
- `updated_count`：之前抽取失败、这次成功更新的邮件数量。
- `duplicate_count`：后端已有且内容相同的邮件数量。
- `failed_extraction_count`：本次 L1 未能形成可信 facts 的邮件数量。
- `failed_submission_count`：本次逐封提交后端失败的邮件数量。
- `failed_email_count` 和 `email_errors`：本次需要重试的 message ID 数量及逐封错误阶段。
- `job_reports[].status`：应为 `completed`；没有新的实质业务邮件时数组可以为空。

### 第四步：在前端核对结果

同步完成后工作台会自动刷新，也可以点击“同步并刷新”：

1. 当前员工的 Gmail 收件箱应出现归组后的公司。
2. 列表显示摘要、销售信号、行业、规模、分数和 Agent 来源。
3. 打开公司详情，在左侧核对邮件主题和正文。
4. 点击画像或分析旁的“依据”，应定位到来源邮件；业务记录来源会显示对应 ID。
5. 中间区域显示三维画像和四维分析。
6. 右侧显示跟进优先级、贡献原因、缺失信息、联系人和业务记录数量。
7. 信息不足时显示“资料不足，未评分”，不能显示虚构的 0 分。

### 第五步：验证去重和动态更新

在 Gmail 管理弹窗再次点击“同步 Gmail”，或点击收件箱的“同步并刷新”。不改变邮箱内容时，已保存邮件应进入 `duplicate_count`，公司邮件数不应重复增加。

随后向该员工 Gmail 账号发送一封带明确需求、数量、预算或会议意图的新邮件，再点击“同步并刷新”。完成后应看到邮件数、摘要、画像、分析和任务状态更新。

页面点击“更新分析”会创建后端任务并自动启动 Agent；处理完成后点击详情页“刷新状态”查看结果。

### 常见问题定位

| 现象 | 先检查什么 | 处理方式 |
|---|---|---|
| Google 显示 `403 access_denied` | OAuth consent screen 的发布状态和 Test users | 测试状态下把当前员工 Gmail 加入 Test users，然后从页面重新授权 |
| 页面授权后提示失败 | OAuth Client 类型与 Redirect URI | 使用 Web application，并确保 Google Cloud 和根 `.env` 都是 `http://127.0.0.1:8000/api/v1/mailboxes/gmail-callback/` |
| 页面一直显示“等待 Agent 同步” | 自动 Agent 配置无效或后台执行失败 | 确认 `SALESMATE_AUTO_RUN_AGENT=True`、Agent token 和百炼配置有效，再查看后端终端中的 `automatic_agent_sync_failed` |
| CLI 返回 `configuration_failed` | 根 `.env` 的服务令牌、邮箱 UUID | 先运行 `provision_local`；确认没有继续维护 `agent/.env` 或 `backend/.env` |
| CLI 返回后端 401 | Agent token 与数据库记录不匹配 | 不要手工复制旧 token；重新初始化一套本地数据库和凭证，或核对当前根 `.env` |
| CLI 返回后端 404 | `SALESMATE_MAILBOX_ID` 不属于当前 token 用户 | 使用同一次 `provision_local` 生成的 token 与 mailbox ID |
| `job_reports` 为空 | 邮件是重复、非业务或无实质更新 | 查看 created/duplicate 和 L1 的 `has_substantive_update`，也可在页面点击“更新分析”后运行 `--process-jobs-once` |
| L3 失败 | 百炼返回非 JSON、枚举错误或 `source_refs` 越界 | 查看终端详细错误；修正 Prompt/模型后再次创建分析任务并运行一次 Job |
| 页面没有新结果 | 后台仍在处理，或同步/分析执行失败 | 查看邮箱状态和后端日志；需要逐步调试时关闭自动运行并执行一次性 CLI |
| ready 返回 503 | 默认数据库连接失败 | SQLite 模式检查 `backend/` 是否可写；PostgreSQL 模式检查 `DATABASE_URL` |

### 第六步：单独调试各阶段

```powershell
# 只读取并抽取指定 Gmail 邮件
python -m agent.main --message-id GMAIL_MESSAGE_ID

# 只读取并抽取最近五封
python -m agent.main --recent

# 只从后端读取公司上下文并构建 L2
python -m agent.main --analysis-company-id COMPANY_UUID

# 只处理已有任务的 L2–L4
python -m agent.main --process-jobs-once --job-limit 10

# 使用旧的本机 Desktop OAuth 直接同步指定邮箱（兼容调试入口）
python -m agent.main --sync-gmail --mailbox-address your-account@gmail.com
```

## 9. 自动检查

在项目根目录执行：

```powershell
# Agent 离线测试：141 项
python -m unittest discover -s agent/tests -p "test_*.py"

# Django 测试：39 项
python backend/manage.py test tests

python backend/manage.py check
python backend/manage.py makemigrations --check --dry-run
python backend/manage.py spectacular --file backend/contracts/openapi.yaml --validate --fail-on-warn
python backend/tools/check_docs.py
```

若安装了 Node.js，可额外检查：

```powershell
node --check backend/frontend/assets/api.js
node --check backend/frontend/assets/app.js
```

自动测试不访问真实 Gmail 或百炼。真实权限、余额、网络和模型输出质量必须使用第 8 节的人工流程验证。

## 10. 离线前端演示

没有 Gmail 或百炼配置时，把 `.env` 暂时改成：

```dotenv
ANALYSIS_PROVIDER=rules
```

重启后端后，工作台会显示“导入演示样例”和“模拟新邮件”。规则模式只用于界面与数据库联调，输出会标记为规则占位。测试真实 Agent 前改回 `ANALYSIS_PROVIDER=agent` 并重启。

## 11. 开发约定

- Agent workflow 使用普通字典和少量就地 dataclass，不增加独立 schemas 或 prompts 目录。
- 后端负责持久化和页面查询，Agent 负责邮件理解与 L1–L4 业务计算。
- 前端只通过 Django 读取结果，不保存服务密钥。
- 修改 `backend/`（包括 tools/） 下 Python 文件时，同步维护文件顶部职责、目录、变量索引和关键函数注释。
- 完成后运行 `python backend/tools/check_docs.py`。

更细的 Agent 提示词和校验规则见 [agent/README.md](../agent/README.md)。当前 HTTP 定义见 [contracts/openapi.yaml](contracts/openapi.yaml)。

## 12. 已知限制

- 首次 Gmail 同步最多读取最近 20 封收件和发件邮件；后续优先使用 Gmail History 游标读取新增邮件，游标过期时退回最近邮件扫描。
- L1 最多四路并发并按完成顺序逐封保存，但 Gmail raw 正文仍按顺序读取。
- 本地 MVP 由 Django 进程中的轻量后台线程按需启动 Agent，没有持久化的邮箱同步批次或常驻分析任务队列；服务重启会中断正在执行的同步，可再次点击“同步并刷新”。
- 公司画像以公司 revision 为单位；当前后台线程一次领取一个分析 Job，不同公司画像尚未并行。
- `skipped_non_business` 和 LLM 的 `non_sales` 会被 Agent 保留，但后端尚未提供人工复核分类或从默认公司列表排除非业务公司的查询规则。
- 原 Gmail 只读同步仍使用原 GmailCredential JSON；本轮新发信/日历 Connection 使用独立 Fernet 密钥加密，浏览器既不接收明文也不接收密文。现有只读凭证迁移与生产密钥服务不在本轮变更内。
- 公共邮箱按原清单自动归组；已支持显式公司合并、选择邮件搬移及人工域名/联系人映射，不猜测集团关系。
- 工单、商机、产品、报价及明细、订单及明细、跟进均有关系记录和管理页入口；库存为人工记录，不自动扣减，不推断税费或收入确认。
- L3 不使用外部新闻或知识库。
- L4 权重尚未使用真实销售结果校准。
- 数据库由 DATABASE_URL 显式选择；本机已验证 PostgreSQL，SQLite 不会作为故障回退。

## Coding Agent 必须遵循的开发原则


所有 Coding Agent 在新增、修改、重构或删除本软件目录内代码（包括前端、测试与工具）时，必须遵循以下要求：

1. **学术风格的实现注释**：在函数、方法及关键代码块处提供准确、严谨、可核验的注释，说明功能、输入与输出、实现逻辑、设计依据和适用约束；涉及状态转换、边界条件、异常或副作用时，应说明其处理方式。注释应解释实现原因与逻辑关系，避免仅复述代码，也不得编造学术引用或未经验证的结论。
2. **文件顶部的功能说明与目录**：每个代码文件顶部必须说明文件职责、主要实现逻辑及与相关模块的关系，并列出文件中实际实现的函数、类与关键方法，以及关键变量、常量和配置项的名称与用途，供 Coding Agent 和开发者快速定位。目录应与当前实现一致，不保留已删除或重命名的条目。
3. **代码与注释同步原子修改**：代码实现、对应注释和文件顶部目录必须作为同一逻辑变更单元同步更新、检查和交付；如提交代码，必须纳入同一次提交。修改函数签名、行为、数据流、关键变量或模块职责时，必须同时修订受影响的说明；删除或替换实现时，必须同步清理失效注释及目录引用。不得先交付代码，再以“后续补充”为由延迟更新注释。
4. **完成前检查一致性**：交付前逐项核对变更涉及的注释和目录，确认其准确反映实际行为，并在软件根目录（`SalesMate/backend/`）运行 `python tools/check_docs.py` 检查声明注释、目录及模块变量索引。修改检查器时还须运行 `python tools/test_check_docs.py`。自动检查不能替代对功能说明、实现逻辑、关键状态与代码一致性的人工核对，也不能证明 Git 提交原子性。

检查工具已提供：在软件根目录运行上述命令。默认扫描本目录下全部 `.py` 文件，包括 `tools/`，包含测试、迁移和包初始化文件。检查失败返回非零退出码，并报告文件、行号与具体问题。统一注释格式、索引范围、检查能力及人工核对要求见[代码注释与一致性检查规范](docs/coding-agent-guidelines.md)。

后续修改还可执行 `python tools/check_doc_changes.py`，比较 HEAD 与工作区的实现及说明；提交前使用 `--staged` 检查实际暂存内容。结构错误阻断，说明未同步的实现变化列为待复核；可用 `--fail-on-review` 显式启用严格复核。对应回归测试为 `python tools/test_check_doc_changes.py`。仓库已提供 pre-commit 配置及 GitHub Actions 工作流；本地安装、远程必需检查设置与能力边界见上述规范，添加配置不等于所有克隆已安装或分支保护已启用。

## 合并旧版本工作区

从旧版本升级时先拉取代码、安装根 requirements.txt，再运行 `python backend/manage.py migrate`。若原配置位于 backend/.env，须将其迁到根 .env，把原 PostgreSQL 连接等价写入 DATABASE_URL（包括原连接超时）；保留原密钥、时区、运行模式和开发账号，不重复初始化数据库或用户。规则模式与免登录均可继续使用。

0004 数据迁移以追加版本的方式转换旧规则事实，保留原邮件及抽取记录，并使旧分析过期；打开公司或明确请求分析后生成新结果。详见[数据模型](docs/data-model.md)。
