# 项目目录与职责

更新：2026-09-12。软件统一放在仓库 `backend/`，包括 Django 服务、原生前端、测试、契约、文档和开发工具。后续独立 Agent 放在同级 `agent/`；目前未创建该目录或空壳实现。

```text
SalesMate/
├── README.md                         # 仓库概览与两方职责入口
├── .gitignore                        # 仓库级凭证及运行产物排除
└── backend/                          # 软件应用根目录、命令执行位置
    ├── README.md                     # 软件开发说明和注释规范
    ├── .env.example                  # 环境配置模板
    ├── manage.py                     # Django 管理命令
    ├── requirements/                 # Python 依赖
    ├── config/                       # Django、数据库、页面及根路由
    ├── apps/accounts/                # 用户模型
    ├── apps/crm/
    │   ├── models.py                 # 邮箱、公司、邮件、版本、分析和任务
    │   ├── serializers.py            # 协议载荷与表单校验
    │   ├── response_schemas.py        # Agent 查询和批量响应的 OpenAPI 结构
    │   ├── access.py                  # 用户归属、服务凭证、版本错误
    │   ├── ingestion.py               # 入库、归组、补交与同步状态
    │   ├── jobs.py                    # 入队、合并、领取、租约与回报
    │   ├── results.py                 # L2/L3/L4 验证、保存及缓存
    │   ├── selectors.py               # 上下文、页面投影与统计
    │   ├── rules.py                   # 可替换的显式规则占位
    │   ├── views.py / urls.py         # 浏览器和 Agent 的 HTTP 入口
    │   ├── migrations/                # 数据表与唯一约束
    │   └── management/commands/       # 本地账号初始化
    ├── common/                       # 日志、错误和健康检查
    ├── tests/                        # 框架、契约、真实业务及并发测试
    ├── frontend/
    │   ├── index.html                # 登录、列表、详情及表单骨架
    │   └── assets/                   # 同源 API、页面交互及响应式样式
    ├── contracts/openapi.yaml        # 从后端生成的契约
    ├── docs/                         # 状态、协议、数据模型、Agent 接入
    ├── tools/                        # 注释检查器、浏览器冒烟测试
    └── artifacts/                    # 本地产物和截图，不提交 Git
```

`backend/` 表示本项目的软件维护边界，其中 `frontend/` 仍单独组织页面代码。Django 入口、Python 导入路径和 `.env` 位置保持不变；前端与契约按软件根目录定位。所有软件文档，包括软件侧 Agent 接口说明和历史设计摘要，统一归入 `backend/docs/`。

初期将紧密关联的业务模型放在 crm app，以独立服务文件分工；不为规划中的模块创建空壳。后续按 mailbox/customers/jobs/analysis 拆应用时保持 HTTP 契约不变。用户模型仍归 accounts。

浏览器不访问数据库、不在客户端生成假业务状态。Agent 不导入 Django、不直接写业务表。规则只在 rules 模式的明确页面动作中运行，不作为网络或模型失败时的隐式回退。

在 `SalesMate/backend/` 执行 `python tools/check_docs.py`，默认覆盖本目录全部 Python 文件，包括迁移、测试、工具和包初始化。修改检查器时同时执行 `python tools/test_check_docs.py`。JS/CSS/HTML 与 browser_smoke.cjs 的说明和目录人工核对。

源码、迁移、契约和文档应一起提交；`.env`、`.local-access.json`、日志、数据库和运行产物不提交。后续交易维护、知识库、发送、翻译和助手按确认范围引入；尚未搭建 RAG、LangGraph、Celery 或容器。
