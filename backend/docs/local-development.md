# 本地开发与联调

更新：2026-09-12。完整步骤以项目根目录的 [README](../README.md) 为准。本页只记录本地运行边界，避免维护第二套启动说明。

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

在 Google Cloud 创建 Web application OAuth Client，把
`http://127.0.0.1:8000/api/v1/mailboxes/gmail-callback/` 配为 Authorized redirect URI，
并在根 `.env` 填写 `GOOGLE_OAUTH_CLIENT_ID`、`GOOGLE_OAUTH_CLIENT_SECRET` 和
`GOOGLE_OAUTH_REDIRECT_URI`。随后在工作台用当前员工账号点击“连接 Gmail”。

授权返回工作台后，在第二个终端运行：

```powershell
python -m agent.main --sync-authorized-mailboxes-once
```

命令领取该员工的邮箱同步请求，执行 Gmail 读取、L1、邮件提交、Job 领取、L2、L3、L4、结果保存和回报。页面再次点击“同步 Gmail”后，需要再运行一次该命令。页面手动创建新分析任务后可单独运行：

```powershell
python -m agent.main --process-jobs-once --job-limit 10
```

无 Gmail 或百炼配置时，可把 `ANALYSIS_PROVIDER` 暂时改为 `rules`，重启后端后使用页面的演示数据和模拟来信。规则结果只用于界面和后端联调。

## 检查

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

本机沿用 `D:/my_files/conda_envs/django_env` 与 WSL PostgreSQL，原 backend/.env 已迁移至仓库根 .env；数据库身份与现有数据保持不变。当前仍为 UTC、rules、免登录模式，SALESMATE_AUTO_RUN_AGENT=False；不会在验证时调用 Gmail 或百炼。已有用户和 .local-access.json 保留，不重复执行初始化。

共享 Python 环境安装项目依赖时提示若干其他已安装包存在缺失依赖；项目测试结果单独记录，不据此宣称整个共享环境依赖完全一致。
