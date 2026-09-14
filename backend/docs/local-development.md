# 本地开发与联调

更新：2026-09-13。完整步骤以项目根目录的 [README](../README.md) 为准。本页只记录本地运行边界，避免维护第二套启动说明。

## 环境

- Python 3.11 或更高版本。
- 依赖统一从根目录 `requirements.txt` 安装。
- Django 和 Agent 只读取根目录 `.env`。
- `DATABASE_URL` 必填，本机和模板使用 PostgreSQL；若明确选择 SQLite，可设置 `DATABASE_URL=sqlite:///backend/db.sqlite3`。
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
