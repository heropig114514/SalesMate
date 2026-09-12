# SalesMate 项目参考总览

更新：2026-09-12。当前前端、Django 后端和 Agent 已完成本地 HTTP 主链路整合。根目录 [README](../README.md) 是范围、数据流、配置、启动和测试的首要文档。

## 当前实现

- 原生 HTML/CSS/JavaScript 工作台展示当前员工 Gmail 授权、同步状态、公司、邮件、画像、分析、业务上下文和跟进优先级。
- Django + DRF 保存用户、员工 Gmail 授权、邮箱、公司、联系人、邮件、抽取、任务和 L2–L4 结果。
- Agent 读取 Gmail，调用百炼执行 L1 和 L3，确定性执行 L2 和 L4，通过 HTTP 与 Django 通信。
- 根 `.env` 是唯一配置文件；SQLite 是本地默认数据库，`DATABASE_URL` 可切换 PostgreSQL。
- `rules` 模式保留为无需 Gmail 和百炼的界面演示，不是模型失败回退。

## 文档定位

| 文档 | 定位 |
|---|---|
| [根 README](../README.md) | 当前范围、完整流程、统一配置、启动和验收 |
| [Agent README](../agent/README.md) | L1–L4 字段、提示词、校验和 Agent CLI |
| [OpenAPI](../contracts/openapi.yaml) | 当前 HTTP 机器可读契约 |
| [API 契约](api-contract.md) | 认证、路由和传输一致性说明 |
| [数据模型](data-model.md) | Django 持久化对象与约束 |
| [Agent 接入](agent-integration.md) | Agent/Django 职责与一次任务过程 |
| [本地开发](local-development.md) | 精简的本地启动和检查入口 |
| [产品需求摘要](references/product-requirements.md) | 原 MVP 产品文档的历史需求摘要 |
| [早期设计摘要](references/agent-and-early-design.md) | 早期宽范围技术路线，仅作背景 |

历史摘要中的版本号、路径和规划不构成当前实现要求。当前代码、根 README、Agent README 和 OpenAPI 不一致时，应先核对实际行为并同步这些当前文档。

## 后续范围

尚未实现 Gmail 发信、WhatsApp、真实公司日历、会议纪要、常驻 Worker、知识库、行业新闻、自由对话助手、完整工单/报价/订单编辑和生产部署。员工 Gmail 网页授权和一次性同步请求已经实现；公共邮箱归组、集团多域名和评分权重仍是 MVP 简化规则。
