# 项目目录与职责

更新：2026-09-12。已实现后端业务闭环和无需构建的原生前端，Agent 仍由团队独立开发。

    SalesMate/
    ├── backend/
    │   ├── config/                  # Django、数据库、页面及根路由
    │   ├── apps/accounts/           # 用户模型
    │   ├── apps/crm/
    │   │   ├── models.py            # 邮箱、公司、邮件、版本、分析和任务
    │   │   ├── serializers.py       # README 载荷与表单校验
    │   │   ├── response_schemas.py   # Agent 查询和批量响应的 OpenAPI 结构
    │   │   ├── access.py            # 用户归属、服务凭证、版本错误
    │   │   ├── ingestion.py         # 入库、归组、补交与同步状态
    │   │   ├── jobs.py              # 入队、合并、领取、租约与回报
    │   │   ├── results.py           # L2/L3/L4 验证、保存及缓存
    │   │   ├── selectors.py         # 上下文、页面投影与统计
    │   │   ├── rules.py             # 可替换的显式规则占位
    │   │   ├── views.py / urls.py   # 浏览器和 Agent 的 HTTP 入口
    │   │   ├── migrations/          # 数据表与唯一约束
    │   │   └── management/commands/ # 本地账号初始化
    │   ├── common/                  # 日志、错误和健康检查
    │   └── tests/                   # 框架、契约、真实业务及并发测试
    ├── frontend/
    │   ├── index.html               # 登录、列表、详情及表单骨架
    │   └── assets/
    │       ├── api.js               # 同源请求、CSRF、错误、HTML 转义
    │       ├── app.js               # 哈希路由、页面投影与交互
    │       └── app.css              # 桌面与窄屏样式
    ├── contracts/openapi.yaml       # 从后端生成的契约
    ├── docs/                        # 状态、协议、数据模型、Agent 接入
    └── tools/                       # 注释检查器、浏览器冒烟测试

初期将紧密关联的业务模型放在 crm app，以独立服务文件分工；不为规划中的模块创建空壳。后续按 mailbox/customers/jobs/analysis 拆应用时保持 HTTP 契约不变。用户模型仍归 accounts。

浏览器不访问数据库、不在客户端生成假业务状态。Agent 不导入 Django、不直接写业务表。规则只在 rules 模式的明确页面动作中运行，不作为网络或模型失败时的隐式回退。

源码、迁移、契约和文档应一起提交；.env、.local-access.json、日志、数据库与真实客户资料不提交。本轮未创建 Git 提交。Python 说明由已有工具检查，JS/CSS/HTML 与 browser_smoke.cjs 人工核对。

后续交易维护、知识库、发送、翻译和助手按确认范围引入，本轮未搭建 RAG、LangGraph、Celery 或容器。
