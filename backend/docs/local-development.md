# 本地开发与联调

更新：2026-09-20。完整初始化步骤见[软件 README](../README.md)。本页记录一键入口及本地运行边界。

## 一键启动（Windows）

在 `SalesMate` 仓库根目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1 -WslDistro Ubuntu-24.04
```

当前电脑的 PostgreSQL 16/pgvector 在上述 WSL 发行版中，根 `.env` 使用原 `127.0.0.1:5432` 数据库。一键入口在缺少 `.venv` 时创建环境，按原四份 requirements 清单安装依赖并运行 `pip check`；清单未变时跳过重复安装。安装器显式采用 UTF-8，PowerShell 文件保留 UTF-8 BOM 以兼容 Windows PowerShell 5.1 的中文注释。可通过 `-Python '解释器绝对路径'` 指定首次创建环境的 Python，通过 `-NoBrowser` 禁止自动打开浏览器。

启动顺序：独占运行锁与 8000 端口检查 → 保持指定 WSL 的 stdin 会话并启动 PostgreSQL → 校验既有本地配置、数据库与 pgvector → Django `check` 和 `migrate --noinput` → Web 就绪检查 → 启动适用的 Worker → 打开工作台。WSL 的 systemd 服务不能单独保证发行版保持运行，所持会话会持续到所有应用进程退出。首次 WSL 转发与 Web 就绪允许有界等待；不重试业务操作、不自动重启失败进程。

`ANALYSIS_PROVIDER=agent` 时启动原 `crm_worker`、`chat_worker`、`sales_worker`；`rules` 时仅启动 Web 和销售 Worker，不宣称模型聊天可用。Worker 使用原有默认参数，可能处理数据库中已经排队或批准的工作；启动器不创建邮箱同步、发信、聊天或样例导入任务。只允许 DEBUG、`TASK_EXECUTION_MODE=local` 和回环 PostgreSQL/明确配置的 SQLite，避免误用生产配置。

```powershell
# 查看监督器、各服务 PID 和 Web/数据库/静态资源健康状态
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1 -Action status
# 先排空 Worker，再停止 Web，并释放本次持有的 WSL 会话
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1 -Action stop
```

服务在后台运行，关闭启动终端不会主动停止它们。重复启动会复用当前受管服务；8000 被其他进程占用时明确失败，不杀进程或改端口。`stop` 等待最多 30 秒，长任务尚未结束会提示继续查看状态；不会强杀或重发任务。不会调用 PostgreSQL 停止命令，但释放最后一个 WSL 会话后发行版可能自行休眠。修改代码后执行 stop 再 start；此入口不启用热更新，避免 Web 与 Worker 使用不同代码版本。

日志位于被忽略的 `artifacts/local-server/`：`launcher.log` 记录环境、迁移及生命周期，`web.log`、`crm.log`、`chat.log`、`sales.log` 分别记录各进程。`state.json` 保存运行状态及退出码。每次冷启动覆盖上一轮同名日志，排查失败时先保留所需日志。依赖安装日志位于 `.venv/salesmate-install.log` 和 `.venv/salesmate-install-error.log`。

新电脑前提：已安装 Python 3.11+ 和数据库；使用 `-WslDistro` 时该发行版及 PostgreSQL/pgvector 必须已经安装。入口不会下载 WSL 或修改系统网络。无根 `.env` 时生成带随机 Django 密钥的模板并停止，需填写数据库和所选模式需要的凭据；已有 `.env` 从不覆盖。自动登录用户缺失时明确提示按下方 `provision_local` 初始化，或由用户明确关闭自动登录并使用网页注册。Google OAuth 和真实模型密钥必须由使用者提供。

启动器的隔离测试（Windows，不访问数据库或真实外部服务；shell 入口测试在 POSIX 平台执行）：

```powershell
.\.venv\Scripts\python.exe -X utf8 backend/tools/test_local_server.py
```

## 一键启动（macOS）

完整业务先安装 Python 3.11+，准备本地 PostgreSQL/pgvector、数据库账号及根 `.env`；仅预览可直接使用下方 [SQLite 配置](#sqlite-本地预览)。脚本兼容系统 Bash 3.2，不自动安装 Homebrew 或数据库，不要求 PowerShell/WSL；共享后端拒绝在 Mac 上传入 `--wsl-distro`。在仓库根目录运行：

```bash
bash start-local.sh
# 仅在使用已安装的 Homebrew PostgreSQL 时，显式传入本机实际公式名
bash start-local.sh --brew-service postgresql@16
# 管理已启动的服务
bash start-local.sh status
bash start-local.sh stop
```

macOS 与 Windows 使用同一 Python 监督器、配置检查、迁移、健康检查及 Worker 参数。差异仅在平台适配：`.venv/bin/python`、POSIX `flock` 文件锁、独立进程会话，以及可选 Homebrew 数据库启动。不硬编码 `/opt/homebrew` 或 `/usr/local`；`brew` 从当前 PATH 查找。POSIX 独立会话与标准流重定向避免服务依赖启动终端；停止仍先排空 Worker 再停止 Web。

`--brew-service` 仅接受 `postgresql` 或 `postgresql@版本号`，调用 `brew services run`，不新增登录启动项；行为依据 [Homebrew 官方命令文档](https://docs.brew.sh/Manpage#services-subcommand)。它要求对应服务已安装，不创建数据库、角色、扩展或密码，也不切换根 `.env` 的数据库地址。数据库需监听本地回环地址；服务启动后最多等待 30 秒 TCP 就绪，认证及 SQL 检查失败仍直接报错。应用 `stop` 不停止可能被其他软件使用的 Homebrew PostgreSQL。Postgres.app 等其他安装方式请自行启动数据库，并省略该选项。

首次执行时，缺少 `.venv` 会创建环境并安装仓库原依赖；缺少 `.env` 会生成随机 Django 密钥及模板，然后明确退出等待填写。请先编辑模板中的 `DATABASE_URL`，按既定模式填写必要凭据，再按需要初始化普通账号：

```bash
.venv/bin/python backend/manage.py migrate
.venv/bin/python backend/manage.py provision_local --username demo --mailbox-address your-account@gmail.com
bash start-local.sh
```

已有账号及 `.local-access.json` 时不要再次运行 `provision_local`。如要用网页注册，需明确设置 `LOCAL_DEBUG_AUTO_LOGIN=False`。若 Python 不在默认位置，可用 `bash start-local.sh --python /path/to/python3` 指定首次创建环境的解释器；`--no-browser` 可禁止打开浏览器。已有 `.venv` 缺少 POSIX 解释器时明确报错，不覆盖 Windows 环境；Intel 与 Apple Silicon 之间也应重新创建环境，不能直接复制虚拟环境。

依赖摘要保存在 `.venv/salesmate-unix-requirements.sha256`，清单变化才重新安装，安装日志为 `.venv/salesmate-install.log`；Web/Worker 日志与状态仍在 `artifacts/local-server/`。工作台地址为 `http://127.0.0.1:8000/`。停止超时、端口冲突、重复启动及无自动重试语义与 Windows 一致。

出现 `No broken requirements found` 只表示 Python 依赖检查通过，不代表数据库或应用已就绪。启动失败时终端会显示本轮已记录的脱敏原因；没有本轮诊断时仍指向日志，不复用历史错误。旧版本若只显示 `Start failed. Inspect ...`，请先读取下面的日志，不要直接重装 Python 或覆盖 `.env`：

```bash
tail -n 80 artifacts/local-server/launcher.log
```

`Created .env ... Configure DATABASE_URL` 表示首次仅生成了模板，需要填写真实数据库配置；`OperationalError` 需要核查本地数据库连接；缺少 `vector` 或本地账号时按明确提示补齐。必须根据本次日志判断，不能仅凭终端摘要认定故障原因。向他人提供诊断时不要附上 `.env`、密钥或完整连接字符串。

跨平台验证：

```bash
/bin/bash -n start-local.sh
python3 -X utf8 backend/tools/test_local_server.py
```

测试只依赖 Python 标准库；测试中的 Django/Homebrew 使用明确模拟，shell 初始化只安装空依赖清单。CI 的 `Local launcher` 工作流在 Windows、macOS、Linux 与 Python 3.11/3.12 组合运行；POSIX 用 `/bin/bash` 验证宿主系统 shell。只有对应任务实际通过才算该平台验证完成，不能据此声称真实 Homebrew 服务、完整业务依赖、数据库、邮箱或模型链路已验证。Windows 现有真实服务另做回归检查。

## SQLite 本地预览

已明确选择轻量本地预览时，可以使用 Python 自带的 SQLite。缺少 `.env` 时先运行平台启动脚本生成模板；这次退出是等待配置，不是依赖安装失败。在根 `.env` 中替换下列已有项，保留其他配置和随机密钥：

```dotenv
DATABASE_URL=sqlite:///backend/db.sqlite3
LOCAL_DEBUG_AUTO_LOGIN=False
ANALYSIS_PROVIDER=rules
```

从仓库根目录运行 `bash start-local.sh`（macOS）或 `powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1`（Windows）；不要传入 PostgreSQL 启动选项。启动器执行全部迁移，首次创建被 Git 忽略的 `backend/db.sqlite3`，然后启动 Web 和 rules 模式原有的 sales Worker。打开 `http://127.0.0.1:8000/` 后，从登录窗口注册普通账号，不需要预建 demo、配置邮箱或运行 `provision_local`。无自动导入的样例数据，空列表属于正常情况。

这是显式选择的规则预览模式；不会调用真实模型。已有服务须先 stop 再 start 才会读取新配置。切换文件库不迁移 PostgreSQL 的数据、账号或授权；切回原数据库需恢复原配置。不要共享 `.env`、凭证或包含业务数据的 SQLite 文件。

已知限制保留原失败语义，不引入替代算法、重试或降低并发参数：

- pgvector 余弦相似度 SQL 仅适用于 PostgreSQL，SQLite 不能执行向量检索。
- 邮件人工纠错、失败抽取更新和历史版本升级中的血缘失效查询使用 JSON 数组包含操作，SQLite 尚未适配。
- SQLite 不提供项目依赖的 PostgreSQL 行锁语义；并发任务领取、共享 Worker 及多进程写入可能报数据库锁冲突。不要用此模式验证多人/并发执行或真实外部动作。

2026-09-20 在 Windows 隔离 SQLite 文件库上验证：全部数据库迁移成功；真实 Uvicorn HTTP 验证通过健康检查、首页/业务/公司设置/世界消息页面、JavaScript 资源、CSRF 注册及客户创建/列表读取。使用临时端口隔离现有服务，未修改默认启动端口。完整后端测试运行 265 项，其中 22 项错误、3 项失败，问题涉及上述 JSON、向量和并发路径。此记录说明完整业务尚不兼容，不能把启动成功当作全功能验收。测试不读取或修改实际 PostgreSQL 业务数据，外部模型和邮件使用既有测试边界；未在用户的 Mac 上完成真实应用验证。

## 环境

- Python 3.11 或更高版本。
- 依赖统一从根目录 `requirements.txt` 安装。
- Django 和 Agent 只读取根目录 `.env`。
- `DATABASE_URL` 必填，本机和模板使用 PostgreSQL；SQLite 仅按上方说明用于明确选择的本地预览。
- 前端是 Django 同源提供的原生 HTML、CSS 和 JavaScript，无需单独安装或构建。

从项目根目录首次初始化：

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python backend/manage.py migrate
python backend/manage.py provision_local --username demo --mailbox-address your-account@gmail.com
```

`provision_local` 创建普通开发用户、业务邮箱和 Agent 服务凭证，将 Agent token、邮箱 UUID和本地用户名写入根 `.env`，将仅供本机使用的登录凭证写入 `backend/.local-access.json`。这两个文件都被 Git 忽略。

## 启动

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn --app-dir backend config.asgi:application --host 127.0.0.1 --port 8000 --reload
```

入口：

- 工作台：`http://127.0.0.1:8000/`
- API 文档：`http://127.0.0.1:8000/api/docs/`
- 存活检查：`http://127.0.0.1:8000/api/v1/health/live/`
- 默认数据库检查：`http://127.0.0.1:8000/api/v1/health/ready/`

`LOCAL_DEBUG_AUTO_LOGIN=True` 时，只在 DEBUG 模式和回环地址上自动建立 `.env` 中 `LOCAL_DEBUG_USER` 的普通用户会话。设为 `False` 并重启后端即可测试登录页。浏览器写操作仍使用 CSRF；Agent 使用独立服务令牌。

## Agent 联调

QQ 邮箱无需 Google OAuth 回调，按 [QQ 邮箱接入与试用](qq-mailbox.md) 配置现有 vault 密钥、应用迁移并在页面连接。Gmail 原流程继续保留；QQ 与 Gmail 共用当前员工的 `crm_worker`。

在 Google Cloud 创建 Web application OAuth Client，把
`http://127.0.0.1:8000/api/v1/mailboxes/gmail-callback/` 配为 Authorized redirect URI，
并在根 `.env` 填写 `GOOGLE_OAUTH_CLIENT_ID`、`GOOGLE_OAUTH_CLIENT_SECRET` 和
`GOOGLE_OAUTH_REDIRECT_URI`。随后在工作台用当前员工账号点击“连接 Gmail”。

授权回调和页面“同步 Gmail”持久创建同步批次，页面静默轮询进度。HTTP 后端运行后，在第二个终端启动 `python backend/manage.py crm_worker`；Web 重启不删除已排队任务，失败由员工明确重试。

旧 CLI 仅用于单独逐步调试，不与新 Worker 混用同一邮箱：

```powershell
python -m agent.main --sync-authorized-mailboxes-once
```

该兼容命令领取一次同步请求，首次扫描最近邮件、后续按 History 游标读取，最多四路执行 L1 并逐封提交，再执行 L2–L4。完整逐封状态与读取失败隔离使用新 Worker。公司 Job 也保留单次调试入口：

```powershell
python -m agent.main --process-jobs-once --job-limit 10
```

无 Gmail 或百炼配置时，可把 `ANALYSIS_PROVIDER` 暂时改为 `rules`，重启后端后使用页面的演示数据和模拟来信。规则结果只用于界面和后端联调。

## 检查

### 本地验收示例

需要业务管理页的临时数据时，在仓库根目录运行：

```powershell
python backend/manage.py seed_sales_demo --username demo
```

命令仅允许 DEBUG 环境下已有的普通员工账号。它创建 4 个带 `【验收示例】` 标记的独立虚构客户、8 个联系人、6 个产品，以及关联商机、报价、订单、工单、跟进、会话、人工消息和邮件草稿；单据编号以 `DEMO-V1-` 开头。不同订单状态是验收情景，不是真实成交或履约证明。报价没有真实外发记录，命令不会发送邮件、创建会议或调度模型分析。

批次 `sales-demo-v1` 的实体 ID 与数量保存在 `acceptance_seed_completed` 审计事件中。重复运行返回原批次清单，不覆盖验收过程中修改的记录；冲突时整个导入回滚，已有数据保留。记录会进入当前员工的业务统计，验收时应按带标记的客户筛选。示例不会自动到期删除；后续清理可按审计清单精确定位。

### 自动检查

```powershell
python -m unittest discover -s agent/tests -p "test_*.py"
python backend/manage.py test tests
python backend/manage.py check
python backend/manage.py makemigrations --check --dry-run
python backend/manage.py spectacular --file backend/contracts/openapi.yaml --validate --fail-on-warn
python backend/tools/check_docs.py
```

自动测试不访问 Gmail 或百炼。真实 OAuth、网络、百炼账户余额和模型输出质量需要人工冒烟验证。

## 本机合并后的运行状态

本机沿用 `D:/my_files/conda_envs/django_env` 与 WSL PostgreSQL，原 backend/.env 已迁移至仓库根 .env。此次保留 provider、数据库身份和时区，分析版本由 `customer-analysis` Skill 管理，移除旧 Web 线程开关；应用持久批次迁移并重新分类历史邮件。测试模拟外部 SDK，已有用户和 .local-access.json 保留。

共享 Python 环境安装项目依赖时提示若干其他已安装包存在缺失依赖；项目测试结果单独记录，不据此宣称整个共享环境依赖完全一致。

## 持久处理升级

升级后先执行 `python backend/manage.py migrate` 和历史分类预览，再明确 `classify_emails --apply`。HTTP 后端启动后另开终端运行 `python backend/manage.py crm_worker`；启动要求及规则边界见 [邮件处理适配](processing-integration.md)。Web 不再创建后台 Agent 线程。
