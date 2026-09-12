# 本地开发环境与验证状态

更新：2026-09-12。当前为可运行的前后端联调版本：Django + PostgreSQL，原生 HTML/CSS/JavaScript 工作台，显式规则占位。没有前端安装或构建步骤。

## 环境与启动

软件统一放在仓库 `backend/`。下文命令在软件根目录 `SalesMate/backend/` 执行；从仓库根目录进入时先执行 `cd backend`。

Python 环境仍在 D:/my_files/conda_envs/django_env，Python 3.12，依赖沿用 requirements 的已锁定版本。PostgreSQL 16 安装在 WSL Ubuntu-24.04 中；数据库、角色和密码与原 .env 配置一致。accounts 和 crm 初始迁移已执行。时区保留既有 UTC，页面显示统计时区；没有 SQLite 回退。

本机 WSL 在没有持续用户进程时会退出，数据库日志已观察到服务随发行版关闭。开发时保留一个 WSL 用户进程，让 PostgreSQL 持续可用；本轮已经启动隐藏的 WSL 保活进程及本地后端服务。

首次启动可按以下顺序执行：

    $databaseKeepalive = Start-Process -FilePath wsl.exe -ArgumentList @('-d','Ubuntu-24.04','--','sleep','infinity') -WindowStyle Hidden -PassThru
    wsl -d Ubuntu-24.04 -u postgres -- psql -Atc 'SELECT 1'
    conda activate django_env
    python -m uvicorn config.asgi:application --host 127.0.0.1 --port 8000

若 8000 已有本项目服务，使用现有实例，不重复启动。停止本次自己创建的保活进程时使用该次返回的 PID，勿停止其他任务的进程。保活只解决服务生命周期，不改变业务失败语义。

入口：[工作台](http://127.0.0.1:8000/)、[API 文档](http://127.0.0.1:8000/api/docs/)、[数据库就绪](http://127.0.0.1:8000/api/v1/health/ready/)。

本轮创建普通开发用户 demo，密码与独立 Agent 令牌保存在私有 .local-access.json。该文件被 Git 排除，不输出到日志、不嵌入前端。新环境迁移后可执行 python manage.py provision_local --username NAME，命令拒绝覆盖既有账号或凭证文件。

本地配置默认 `LOCAL_DEBUG_AUTO_LOGIN=True`、`LOCAL_DEBUG_USER=demo`，访问页面直接进入工作台并隐藏退出按钮。自动会话仅在 DEBUG 开启且直连地址为回环地址时生效；指定账号必须已存在、启用且无管理员权限，否则明确报错。已有登录身份保持不变；业务写入仍需要 CSRF，Agent 仍使用独立凭证。恢复登录页：在 .env 设置 `LOCAL_DEBUG_AUTO_LOGIN=False` 并重启后端，已有登录会话可点击退出。使用自定义开发用户名时同步设置 `LOCAL_DEBUG_USER`。

新开发者须安装 PostgreSQL 并创建匹配 .env 的库和角色，再运行 python manage.py migrate。本机开发角色有 CREATEDB，用于 Django 隔离测试库；生产权限需要另行配置。pgvector、知识库、Docker、生产配置和真实模型不在本轮范围内。

## 规则和页面验证

ANALYSIS_PROVIDER=rules 是本轮显式开发默认；设置 agent 并重启后端后，页面只入队，由独立 Agent 消费，模拟写入入口关闭。

直接进入工作台（关闭自动会话时先登录） → 导入演示样例 → 筛选公司 → 打开详情 → 点击原文依据 → 建立档案 → 模拟来信 → 查看更新。模拟入口不收发真实 Gmail。初始样例为四封邮件、三家公司；重复导入不新增旧样例、不改变其时间。浏览器测试会额外向 demo 写一封合成来信并更新 CRM，因此当前数据可能多于初始四封。

## 检查

在软件根目录运行：

    python tools/check_docs.py
    python tools/test_check_docs.py

    python manage.py check
    python manage.py makemigrations --check --dry-run
    python manage.py spectacular --file contracts/openapi.yaml --validate --fail-on-warn
    python manage.py test tests

新增业务测试使用真实 PostgreSQL 隔离测试库，覆盖 Agent HTTP、幂等、整批回滚、用户隔离、失败补交、租约、并发领取、缓存、CSRF、空值和事实全集。原九项框架测试仍使用 SimpleTestCase，数据库就绪分支仍为显式模拟。完整数量及结果见本轮交付说明。

前端语法检查：node --check frontend/assets/app.js，以及 node --check frontend/assets/api.js。Python 注释自动检查覆盖整个软件目录（包括 tools/）；原生前端与浏览器测试脚本的说明和索引人工核对。

浏览器复现：显式设置 SALESMATE_PLAYWRIGHT_MODULE 为已安装 Playwright 模块路径，SALESMATE_BROWSER_PATH 为 Chrome/Edge 可执行文件，运行 node tools/browser_smoke.cjs。本机使用 Codex bundled Node 的 node_modules/playwright。独立无头会话覆盖登录、导入、搜索/重置、详情、来源、建档、模拟来信、HTML 转义、刷新持久化和 390px 窄屏无水平溢出；截图保存到被 Git 排除的 artifacts/browser。

以上检查不代表真实 Gmail、团队 Agent、模型质量、生产部署或完整团队权限已经验证。
