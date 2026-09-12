# 项目目录与职责

更新：2026-09-12。当前目录以 Agent 和真实 Django 后端通过 HTTP 解耦为边界。

```text
SalesMate/
├── .env.example                 # 唯一环境变量模板
├── requirements.txt             # Agent 与后端统一安装入口
├── agent/
│   ├── main.py                  # Gmail 和任务一次性 CLI
│   ├── config.py                # 读取根 .env
│   ├── clients/backend_api.py   # BackendClient 协议与 Django HTTP 适配器
│   ├── tools/                   # Gmail 读取和 MIME 解析
│   ├── llm/                     # 百炼调用
│   ├── workflows/               # 员工邮箱同步、L1、L2、L3、L4 和编排
│   └── tests/                   # 离线行为与集成测试
├── backend/
│   ├── config/                  # Django 设置、ASGI 和根路由
│   ├── apps/accounts/           # 用户模型
│   ├── apps/crm/
│   │   ├── models.py            # 员工邮箱授权、公司、邮件、版本、分析和任务
│   │   ├── gmail_oauth.py       # 网页 Google OAuth 与一次性邮箱同步队列
│   │   ├── serializers.py       # Agent 载荷与浏览器请求校验
│   │   ├── response_schemas.py  # Agent 响应的 OpenAPI 结构
│   │   ├── ingestion.py         # 邮件入库、去重、归组和任务创建
│   │   ├── jobs.py              # 任务领取、租约和回报
│   │   ├── results.py           # L2–L4 验证、保存和缓存
│   │   ├── selectors.py         # 上下文与页面投影
│   │   ├── rules.py             # 离线页面演示规则
│   │   └── views.py / urls.py   # 浏览器与 Agent HTTP 入口
│   ├── common/                  # 日志、错误和健康检查
│   └── tests/                   # Django 行为与 HTTP 契约测试
├── frontend/
│   ├── index.html               # 工作台页面骨架
│   └── assets/                  # 同源 API、交互和样式
├── contracts/openapi.yaml       # 从 Django 生成的 HTTP 契约
├── docs/                        # 当前说明和历史需求摘要
└── tools/                       # 文档及可选浏览器检查
```

Agent 不导入 Django、不直接访问数据库。浏览器不生成 Agent 业务结论，也不接收 Google 或服务令牌。Django 保存员工 Gmail 授权但不执行真实模型推理；`rules.py` 仅在明确的离线演示模式运行。

运行时机密、OAuth 文件、本地登录凭证和数据库均被 Git 忽略。源码、迁移、OpenAPI 和当前文档应随契约变更一起更新。
