# SalesMate

面向销售人员的邮件理解与客户跟进系统。本仓库按软件应用与独立 Agent 划分维护边界。

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
└── .gitignore        # 仓库级凭证、缓存及运行产物排除规则
```

后续 Agent 代码放在与 `backend/` 同级的 `agent/`（尚未创建），通过 HTTP 协议读取上下文和提交结果；不直接导入 Django 或写业务数据库。当前软件中的规则占位用于前后端联调。

- [软件开发与启动说明](backend/README.md)
- [目录与职责](backend/docs/project-structure.md)
- [代码注释规范](backend/docs/coding-agent-guidelines.md)
- [API 契约](backend/docs/api-contract.md)
- [Agent 联调说明](backend/docs/agent-integration.md)

在仓库根目录启动本地工作台：

```powershell
conda activate django_env
cd backend
python -m uvicorn config.asgi:application --host 127.0.0.1 --port 8000 --reload
```

访问 [工作台](http://127.0.0.1:8000/)。本地默认以已有 demo 普通账号直接进入。环境初始化、恢复登录与验证命令见软件开发说明。

所有软件修改须遵循 `backend/README.md` 中的开发原则，代码、注释、目录与文档同步维护。在 `backend/` 执行 `python tools/check_docs.py`；修改检查器时同时执行 `python tools/test_check_docs.py`。
