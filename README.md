# SalesMate

面向销售人员的邮件理解与客户跟进系统。本仓库按 Django 软件应用与独立 Agent 划分维护边界，当前已打通员工 Gmail OAuth、增量同步、L1–L4 分析和浏览器工作台。

```text
SalesMate/
├── backend/          # 软件应用：Django 服务、网页、测试、契约、文档和开发工具
│   ├── README.md     # 软件开发说明与注释规范入口
│   ├── apps/         # 用户、CRM、邮件、分析和任务接口
│   ├── common/       # 日志、错误和健康检查
│   ├── config/       # Django 配置与路由
│   ├── frontend/     # 原生 HTML/CSS/JavaScript 工作台
│   ├── tests/        # 框架、协议及业务集成测试
│   ├── contracts/    # OpenAPI 与响应示例
│   ├── docs/         # 软件架构、数据模型、开发规范和 Agent 接入说明
│   ├── tools/        # 注释检查与浏览器验证工具
│   ├── requirements/ # Python 依赖
│   └── manage.py     # Django 管理入口
├── agent/            # Gmail History、L1–L4、可路由 Skill、后端 HTTP 客户端和测试
├── integrations/     # 面向用户协作的业务工具 HTTP SDK、CLI 与 MCP 客户端
├── test_tools/       # 开发和测试人员可独立使用的全流程测试数据工具
├── .env.example      # 软件与 Agent 共用的配置模板
├── start-local.ps1   # Windows 一键准备 Python 环境、启动系统、查看状态与停止
├── start-local.sh    # macOS/POSIX 一键入口，复用同一套服务管理逻辑
├── requirements.txt  # 两侧依赖的安装入口
└── .gitignore        # 仓库级凭证、缓存及运行产物排除规则
```

Agent 代码位于与 `backend/` 同级的 `agent/`，通过 HTTP 协议读取上下文和提交结果；不直接导入 Django 或写业务数据库。首次同步最多读取最近 20 封邮件，后续优先使用 Gmail History 游标；需要调用模型的 L1 邮件最多四路并发，任一完成后立即逐封提交。L2–L4 以公司 revision 为单位运行，软件仍可使用规则占位进行离线联调。

```text
员工授权 Gmail
→ Django 记录同步请求
→ Agent 增量读取邮件
→ 单封 L1 并发抽取并完成即提交
→ Django 归组公司并创建分析 Job
→ Agent 按公司生成 L2、L3、L4
→ 前端静默轮询并逐步显示结果
```

- [软件开发与启动说明](backend/README.md)
- [Agent 实现与数据契约](agent/README.md)
- [图谱模型与 Agent 工具交接](backend/docs/semantic-agent-handoff.md)：HTTP/MCP 接入、写入副作用、幂等与失败处理、当前性能边界；聊天 Agent 尚未自动接入。
- [Gmail 测试邮件注入器](test_tools/README.md)
- [目录与职责](backend/docs/project-structure.md)
- [代码注释规范](backend/docs/coding-agent-guidelines.md)
- [API 契约](backend/docs/api-contract.md)
- [Agent 联调说明](backend/docs/agent-integration.md)
- [Agent 业务工具接入](backend/docs/agent-business-tools.md)：动态授权工具目录、独立用户委托、版本/幂等保护及人工确认，供 Agent 开发侧接入。
- [世界消息地图与后续推送契约](backend/docs/world-news.md)：`/world/` 提供行业消息地图、摘要侧栏和详情页；当前使用明确标注的虚构演示数据。

## 本地一键启动

**在已安装 Python、配置好本地数据库及 `.env` 的电脑上，一条命令即可自动准备项目 Python 环境并运行系统。新电脑首次使用仍有下表中的人工准备项。** 以下命令从包含本 README 的 `SalesMate` 仓库根目录执行。

| 项目 | 启动器自动处理 | 首次使用者准备 |
| --- | --- | --- |
| Python 环境 | 缺少时创建 `.venv`；按原清单安装依赖并检查兼容性 | 安装 Python 3.11+；Windows 用 `-Python`、macOS 用 `--python` 指定解释器 |
| 数据库 | SQLite 预览模式自动创建文件库并迁移；PostgreSQL 可显式启动已安装的 WSL/Homebrew 服务并检查 pgvector | 预览按下方选择 SQLite，无需安装数据库；完整业务需准备 PostgreSQL/pgvector 并填写 `DATABASE_URL` |
| 应用配置 | 缺少 `.env` 时生成模板和随机 Django 密钥，然后提示配置；已有文件不覆盖 | 填写所选模式的配置；初始化本地普通账号，或明确关闭自动登录后网页注册 |
| 真实外部服务 | 按现有 `ANALYSIS_PROVIDER` 启动对应 Worker | 自行提供模型密钥和模型名；使用 Gmail 时配置 Google OAuth 并完成授权 |
| 网页与进程 | 后台启动、健康检查、打开浏览器、状态查询、协作停止 | 无需 npm、前端编译或手工双击 HTML |

### SQLite 本地预览（Windows / macOS）

**只看前端、注册登录和体验基础业务，可以使用 SQLite，不需要 PostgreSQL、Homebrew 或 WSL。此方式不代表完整业务已兼容 SQLite。**

首次运行启动脚本会创建 `.venv`、安装依赖并生成 `.env`；如果提示 `Created .env ... Configure DATABASE_URL`，依赖已准备好，继续编辑仓库根目录生成的 `.env` 即可。将下列已有配置项改为这些值（不要重复追加同名项），保留生成的 Django 密钥和其他配置：

```dotenv
DATABASE_URL=sqlite:///backend/db.sqlite3
LOCAL_DEBUG_AUTO_LOGIN=False
ANALYSIS_PROVIDER=rules
```

这里明确选择规则预览模式，不调用真实模型；关闭自动登录是为了首次从网页注册自己的普通账号，无需预建 `demo` 或执行 `provision_local`。已经有真实模型配置时，仅在决定使用此预览模式后修改 provider。

macOS 在项目目录执行：

```bash
bash start-local.sh
```

Windows 执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1
```

不要附加 `--brew-service` 或 `-WslDistro`。启动器自动创建 `backend/db.sqlite3`、应用迁移并打开 [本地工作台](http://127.0.0.1:8000/)，在登录窗口注册账号即可。之后每次都用同一条启动命令。若已有受管服务，修改配置后先 stop 再 start；SQLite 是独立数据文件，不会导入原 PostgreSQL 的账号或业务数据。

**限制：** pgvector 相似度检索不可用；邮件纠错、历史抽取升级涉及未适配的 JSON 查询；并发任务领取和多个 Worker 写入存在 SQLite 锁冲突。预览时不要依赖这些功能，真实 Gmail/模型链路及多人并发仍使用 PostgreSQL。注册、登录、页面与部分基础业务已有 SQLite 测试覆盖，但不能据此承诺全部业务可用。验证结果和适用范围见 [SQLite 预览说明](backend/docs/local-development.md#sqlite-本地预览)。

### Windows

如果 PostgreSQL 位于已配置的 WSL `Ubuntu-24.04`（本机采用此方式）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1 -WslDistro Ubuntu-24.04
```

脚本会保持 WSL 数据库会话，验证就绪后打开 [本地工作台](http://127.0.0.1:8000/)。复用已有 `.env`、账号和数据；不更改模型模式，不自动导入样例。发行版名称不同时替换 `Ubuntu-24.04`；使用 Windows 本地数据库时省略 `-WslDistro`：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1
```

查看状态或停止：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1 -Action status
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1 -Action stop
```

启动成功后服务在后台运行，关闭启动终端不会主动停止服务。重复启动复用受管进程；8000 端口冲突或依赖缺失会明确报错，不改端口、不自动切换数据库或模型模式。日志在 `artifacts/local-server/`；首次依赖安装日志在 `.venv/salesmate-install*.log`。更新代码后先 stop 再 start。完整前提、日志与停止语义见[本地一键启动](backend/docs/local-development.md#一键启动windows)。

### macOS

在项目目录执行，无需 PowerShell 或 WSL：

```bash
bash start-local.sh
```

如果需要同时启动 **已安装的** Homebrew PostgreSQL，可显式指定实际使用的版本（示例为 16，不会自动安装或切换版本）：

```bash
bash start-local.sh --brew-service postgresql@16
```

脚本使用 `.venv/bin/python`，兼容系统 Bash 3.2；按本机 Python 架构创建环境，不硬编码 Apple Silicon/Intel 的 Homebrew 路径。使用现有数据库、Postgres.app 或其他本地 PostgreSQL 时，先自行启动数据库，再省略 `--brew-service`。两平台使用相同的 8000 端口、根 `.env`、健康检查、Worker 参数和失败语义。不要跨操作系统复制 `.venv`。

```bash
bash start-local.sh status
bash start-local.sh stop
# 可选：指定 Python 或禁止自动打开浏览器
bash start-local.sh --python /path/to/python3 --no-browser
```

首次环境、账号准备、Homebrew 行为及验证范围见 [macOS 本地启动说明](backend/docs/local-development.md#一键启动macos)。平台 CI 验证锁、协作停止、后台会话和空依赖环境初始化，不代表真实模型、邮箱或完整数据库链路已通过验证。

### 手动启动与热更新

如需手动启动并启用代码热更新，在仓库根目录运行：

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn --app-dir backend config.asgi:application --host 127.0.0.1 --port 8000 --reload
```

macOS 使用 `source .venv/bin/activate` 激活环境，其余手动 Python 命令相同。

访问 [工作台](http://127.0.0.1:8000/)。本地默认以已有 demo 普通账号直接进入。环境初始化、恢复登录与验证命令见软件开发说明。

所有软件修改须遵循 `backend/README.md` 中的开发原则，代码、注释、目录与文档同步维护。在 `backend/` 执行 `python tools/check_docs.py`；修改检查器时同时执行 `python tools/test_check_docs.py`。

Django 与 Agent 共同读取仓库根目录 `.env`。数据库须显式设置 `DATABASE_URL`，不自动改用 SQLite。真实模型联调使用 `ANALYSIS_PROVIDER=agent`；分析版本由对应 `agent/skills/*/SKILL.md` 管理。一键入口在该模式下已启动 CRM、聊天和销售 Worker，无需再手动启动；采用上述手动 Web 命令时，需按软件开发说明另开终端运行所需 Worker。

同步批次和逐封状态持久化到数据库，独立 Worker 并行调度邮箱同步与公司画像。后端已提供非业务隐藏、人工复核、进度和明确重试接口；执行中断保留失败记录。

## 销售业务扩展

`/business/` 已提供客户关系、交易单据、跟进、团队授权、附件和动作确认；助手侧栏支持持久化会话与草稿。新增 `backend/apps/sales/` 管理关系 Schema 和业务事务，原 Agent 协议不变。详细模型、接口、验证边界及 Worker/OAuth 配置见 [销售扩展说明](backend/docs/backend-expansion.md)。只读销售聊天已通过独立 `apps.chat` 接入，支持任务状态、证据快照、引用和 `chat_worker`；启动与验证边界见 [聊天适配](backend/docs/chat-integration.md)。自主工具选择未开放，真实 Gmail 发信和日历执行须完成明确的写权限授权。

## 邮件持久处理

同步请求返回批次 ID，独立 `crm_worker` 执行逐封处理及公司画像；工作台新增准确进度、失败邮件重试和人工复核。升级与启动见 [适配说明](backend/docs/processing-integration.md)。
