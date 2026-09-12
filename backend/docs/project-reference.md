# SalesMate 项目参考总览

更新：2026-09-12。当前负责人承担前后端，Agent 团队独立开发。本地已有可运行的邮件理解联调闭环，分析暂用明确标注的规则占位。

## 当前实现

原生 HTML/CSS/JavaScript 前端与 Django 同源运行，支持登录、公司收件箱、行业/规模/信号筛选、分页、三栏客户详情、邮件方向筛选、来源跳转、CRM 建档、模拟来信和演示样例导入。

Django + DRF + PostgreSQL 已执行用户及业务迁移，实现不可变邮件、公司归组、版本化抽取与补交、Job Pull、租约、上下文 revision、L2/L3/L4 保存及缓存。浏览器用 Session + CSRF，Agent 用单用户服务凭证。

可演示：模拟来信 → 邮件和事实入库 → 公司归组 → Job → 规则分析评分 → 页面查询。设置 ANALYSIS_PROVIDER=agent 后保持业务 API 和页面不变，交由独立 Agent 主动领取。

真实 OAuth、Gmail 读取、团队 Agent、模型调用、翻译、发送、右栏对话助手、新闻、知识库、完整交易维护和生产部署尚未接入。

## 路线与来源

| 来源 | 定位 |
|---|---|
| SalesMate 仓库上一层 README.md，Agent v1.11 | 当前通信对象基准，原文未修改 |
| references/product-requirements.md | 产品 0909 版本的页面与功能意图 |
| references/agent-and-early-design.md | 历史宽范围路线与 Agent 设计摘要 |
| api-contract.md | 实际 HTTP 路径、版本、领取凭证和失败语义 |
| data-model.md | 当前 Schema 及本轮内部调整 |
| agent-integration.md | 规则范围及真实 Agent 替换步骤 |
| local-development.md | 本机启动、数据库状态与检查边界 |

## 后续对齐

- OAuth 主体、Agent Gmail 读取与邮箱业务 ID 的可信绑定；业务地址不等于授权证据。
- 新增 HTTP ETag/If-Match、Job lease_token、expected_version 与显式 lease_seconds。
- 真实 Agent 的提示词、评分与缓存版本，以及纯致谢邮件避免 L3 重算的策略。
- 产品中的助手、翻译、发送、知识库和新闻是否属于本期以及负责人。
- 工单/报价/订单录入或同步入口，不把邮件提及当作权威交易。
- 公共邮箱清单、子域/集团人工归组、完整团队权限及部署。

近期优先单公司 Agent 联调，验证重复提交、失败补交、预算历史、旧结果拒绝、空分与缓存版本。研究数据集未自动导入，实验条件未修改。
