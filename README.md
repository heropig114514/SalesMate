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
├── agent/            # Gmail History 增量读取、并发 L1、L2–L4、后端 HTTP 客户端和测试
├── test_tools/       # 开发和测试人员可独立使用的全流程测试数据工具
├── .env.example      # 软件与 Agent 共用的配置模板
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
- [Gmail 测试邮件注入器](test_tools/README.md)
- [目录与职责](backend/docs/project-structure.md)
- [代码注释规范](backend/docs/coding-agent-guidelines.md)
- [API 契约](backend/docs/api-contract.md)
- [Agent 联调说明](backend/docs/agent-integration.md)

在仓库根目录启动本地工作台：

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn --app-dir backend config.asgi:application --host 127.0.0.1 --port 8000 --reload
```

访问 [工作台](http://127.0.0.1:8000/)。本地默认以已有 demo 普通账号直接进入。环境初始化、恢复登录与验证命令见软件开发说明。

所有软件修改须遵循 `backend/README.md` 中的开发原则，代码、注释、目录与文档同步维护。在 `backend/` 执行 `python tools/check_docs.py`；修改检查器时同时执行 `python tools/test_check_docs.py`。

Django 与 Agent 共同读取仓库根目录 `.env`。数据库须显式设置 `DATABASE_URL`，不自动改用 SQLite。真实模型联调使用 `ANALYSIS_PROVIDER=agent` 和 `SALESMATE_ANALYSIS_PROMPT_VERSION=analysis-v2`；网页自动触发本地 Agent 时还需启用 `SALESMATE_AUTO_RUN_AGENT=True`。

当前 Django 自动 Agent 使用 Web 进程内线程，适合本地 MVP。它没有持久化的邮件级任务和并行公司画像 Worker；服务重启可能中断任务。Agent 已输出非业务标记，但后端尚未提供默认隐藏与人工复核分类。

## 销售业务扩展

`/business/` 已提供客户关系、交易单据、跟进、团队授权、附件和动作确认；助手侧栏支持持久化会话与草稿。新增 `backend/apps/sales/` 管理关系 Schema 和业务事务，原 Agent 协议不变。详细模型、接口、验证边界及 Worker/OAuth 配置见 [销售扩展说明](backend/docs/backend-expansion.md)。聊天模型与自主工具选择仍未接入，真实 Gmail 发信和日历执行须完成明确的写权限授权。
