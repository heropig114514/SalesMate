# SalesMate 项目参考总览

更新：2026-09-13。当前前端、Django 后端和 Agent 已完成本地 HTTP 主链路整合。软件 [README](../README.md) 是范围、数据流、配置、启动和测试的首要文档。

## 当前实现

- 原生 HTML/CSS/JavaScript 工作台展示当前员工 Gmail 授权、同步状态、公司、邮件、画像、分析、业务上下文和跟进优先级。
- Django + DRF 保存用户、员工 Gmail 授权、邮箱、公司、联系人、邮件、抽取、任务和 L2–L4 结果。
- Agent 首次扫描最近 Gmail 邮件、后续使用 History 游标增量读取；L1 最多四路并发并完成即逐封提交，L2/L3/L4 按公司 Job 执行，通过 HTTP 与 Django 通信。
- 根 `.env` 是唯一配置文件；`DATABASE_URL` 必填，本机使用原 PostgreSQL；SQLite 需显式配置。
- `rules` 模式保留为无需 Gmail 和百炼的界面演示，不是模型失败回退。

## 文档定位

| 文档 | 定位 |
|---|---|
| [软件 README](../README.md) | 当前范围、完整流程、统一配置、启动和验收 |
| [Agent README](../../agent/README.md) | L1–L4 字段、提示词、校验和 Agent CLI |
| [OpenAPI](../contracts/openapi.yaml) | 当前 HTTP 机器可读契约 |
| [API 契约](api-contract.md) | 认证、路由和传输一致性说明 |
| [数据模型](data-model.md) | Django 持久化对象与约束 |
| [Agent 接入](agent-integration.md) | Agent/Django 职责与一次任务过程 |
| [本地开发](local-development.md) | 精简的本地启动和检查入口 |
| [产品需求摘要](references/product-requirements.md) | 原 MVP 产品文档的历史需求摘要 |
| [早期设计摘要](references/agent-and-early-design.md) | 早期宽范围技术路线，仅作背景 |

历史摘要中的版本号、路径和规划不构成当前实现要求。当前代码、根 README、Agent README 和 OpenAPI 不一致时，应先核对实际行为并同步这些当前文档。当前后端尚未实现邮件级持久任务、公司画像并行 Worker，以及非业务邮件默认隐藏和人工复核。

## 后续范围

销售 Schema、业务管理页、私有会话草稿、团队权限、审计、附件和销售 Worker 已实现；Gmail/日历适配器有确认流程，实际外部执行需完成写权限授权。尚未实现 WhatsApp、会议纪要、知识库、行业新闻、聊天模型自主工具调用和生产部署。范围及运行步骤见 [销售扩展](backend-expansion.md)。
