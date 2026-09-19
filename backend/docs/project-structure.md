# 项目目录与职责

更新：2026-09-14。软件统一放在仓库 `backend/`，包括 Django 服务、原生前端、测试、契约、文档和开发工具。独立 Agent 位于同级 `agent/`，开发和测试人员使用的测试数据工具位于 `test_tools/`；软件与 Agent 共用仓库根目录的 .env 和依赖入口。

```text
SalesMate/
├── README.md                         # 仓库概览与两方职责入口
├── .gitignore                        # 仓库级凭证及运行产物排除
├── agent/                            # Gmail History、L1–L4、可路由 Skill、CLI 和 Agent 测试
├── integrations/salesmate_tools/      # 业务工具 HTTP SDK、CLI、stdio MCP 及协议测试
├── test_tools/                       # 独立 Gmail 测试邮件注入器和使用说明
├── .env.example                      # 共享配置模板
├── requirements.txt                  # 双方依赖入口
└── backend/                          # 软件应用根目录、命令执行位置
    ├── README.md                     # 软件开发说明和注释规范
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
    │   ├── gmail_oauth.py             # 员工授权、同步领取及凭证管理
    │   ├── worker.py                  # 独立 Worker 的邮箱与画像执行单元
    │   ├── rules.py                   # 可替换的显式规则占位
    │   ├── views.py / urls.py         # 浏览器和 Agent 的 HTTP 入口
    │   ├── migrations/                # 数据表与唯一约束
    │   └── management/commands/       # 本地账号初始化
    ├── apps/sales/                    # 销售关系模型、权限、事务、外部动作和 sales_worker
    ├── apps/chat/                     # 只读聊天任务、证据、引用、知识版本和 chat_worker
    ├── apps/agent_tools/              # 业务工具注册、独立用户委托、幂等与人工确认
    ├── common/                       # 日志、错误和健康检查
    ├── tests/                        # 框架、契约、业务与权限测试
    ├── frontend/
    │   ├── index.html                # 统一工作台、邮件列表、客户详情及复核
    │   ├── business.html              # 共享导航下的业务管理及动作审阅
    │   └── assets/                   # workspace.js/CSS 共享外壳；业务模块及同源 API
    ├── contracts/openapi.yaml        # 从后端生成的契约
    ├── docs/                         # 状态、协议、数据模型、Agent 接入
    ├── tools/                        # 注释检查器、浏览器冒烟测试
    └── artifacts/                    # 本地产物和截图，不提交 Git
```

`backend/` 表示本项目的软件维护边界，其中 `backend/frontend/` 仍单独组织页面代码。Django 入口和 Python 导入路径保持不变；前端与契约按软件根目录定位。双方只读取仓库根目录 `.env`；原 backend/.env 的本机 PostgreSQL 和调试配置已迁移，数据库未替换。所有软件文档，包括软件侧 Agent 接口说明和历史设计摘要，统一归入 `backend/docs/`。

crm 保持邮件与 L1–L4 分析持久化职责，sales 管理交易、协作与工具动作，用户模型归 accounts。Agent 当前通过既有数组接口逐封提交邮件，HTTP 契约和邮件存储结构不变。L1 和 L3 的模型指令位于 `agent/skills/*/SKILL.md`，workflow 从 Skill 元数据读取版本和输出上限。

浏览器不访问数据库、不在客户端生成假业务状态。Agent 不导入 Django、不直接写业务表。规则在 rules 模式的明确业务变更或分析请求后运行，不作为网络或模型失败时的隐式回退。

在 `SalesMate/backend/` 执行 `python tools/check_docs.py`，默认覆盖本目录全部 Python 文件，包括迁移、测试、工具和包初始化。修改检查器时同时执行 `python tools/test_check_docs.py`。JS/CSS/HTML 与 browser_smoke.cjs 的说明和目录人工核对。

源码、迁移、契约和文档应一起提交；`.env`、`.local-access.json`、日志、数据库和运行产物不提交。交易维护、发送/日历确认、会话草稿与团队权限已放入 sales；只读聊天及内部知识版本归 chat；外部检索、翻译和自主工具仍待后续接入。未引入 RAG、LangGraph、Celery 或容器。

新增 `processing_models.py`、`processing.py`、`classification.py`、`processing_views.py` 管理持久批次、阶段和复核；`dispatch.py` 轮转员工并管理临时 HTTP 身份，`management/commands/crm_worker.py` 共享消费任务，`classify_emails.py` 回填历史分类。
