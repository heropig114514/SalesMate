# 数据模型现状

更新：2026-09-12。默认本地数据库为 `backend/db.sqlite3`，通过 `DATABASE_URL` 可切换 PostgreSQL。accounts 与 crm 迁移适用于两者。

## 数据表

| 模型 | 职责 |
|---|---|
| accounts.User | 项目用户模型，普通用户没有管理员权限 |
| Mailbox | 员工业务邮箱、同步请求状态和同步版本 |
| GmailCredential | Mailbox 一对一的 Google 授权 JSON；只供后端 OAuth 与 Agent 同步接口使用 |
| AgentCredential | 单用户服务凭证 SHA-256 摘要；无令牌原文 |
| Company | 用户归属、归组键、公司名称/域名、CRM 状态、业务快照、revision、external_version |
| Contact | 公司内联系人邮箱与可空姓名；往来数按邮件计算 |
| Email | 标准邮件 JSON，邮箱/公司外键、可空联系人、方向与时间；dedupe_key 主键 |
| Extraction | 邮件的提示词版本、状态、事实、错误及创建时间 |
| AnalysisInput | Agent 输入版本、原始 L2 JSON 和后端 revision |
| Analysis | 所属输入快照、提示词版本、原始 L3 JSON、rules/agent 来源 |
| Score | 所属具体分析、原始 L4 JSON、规则版本、可空分值和创建时间 |
| Job | 公司、事件、revision、状态、attempt、租约、领取凭证和最终回报 |

关系为 User → Mailbox/Company；Mailbox → GmailCredential/Email；Company → Contact/Email/Job/AnalysisInput；Email → Extraction；AnalysisInput → Analysis → Score。账号、邮箱和公司查询均按 owner 隔离，因此页面是当前员工收件箱，不是全公司共享收件箱。

## 本轮内部结构调整

初期业务模型集中在 `apps/crm`，以 ingestion.py、jobs.py、results.py、selectors.py 和 rules.py 分离事务写入、领取、结果验证、查询和占位规则。原多 app 目录是规划，不创建空模块；后续内部拆分不影响 HTTP 契约。

关系字段承担权限、查询和唯一约束，JSON 保存协议载荷。邮件本体与抽取分表，L2/L3/L4 分表。后端 revision 与 Agent input_version 独立：前者防止旧任务覆盖，后者保持 Agent 定义的缓存身份。

Company.customer/tickets/quotes/orders 暂为业务快照容器。CRM 基础字段已有建档服务；工单、报价、订单默认空数组，本轮没有伪造交易或实现其编辑入口。后续维护服务必须验证结构，并在同一事务递增 external_version、revision 和创建任务；不能直接改 JSON 绕过版本规则。业务扩大后可迁移为关系表，保持 CompanyContext 表示不变。

## 约束

- 同一 owner 与 group_key 唯一。完整企业域名相同才自动合组；清单中的公共邮箱按完整联系人邮箱分别归组。清单有限，不宣称覆盖全部服务商。
- 客户自报公司名只用于展示，不参与归组。不猜测子域/集团关系，当前没有人工合并拆分操作。
- 同用户跨业务邮箱的同域往来进入同一公司；不同用户同域仍隔离。
- 邮箱地址、公司联系人、抽取版本、输入版本、分析提示词版本均有相应联合唯一约束。
- 邮件/事实/CRM 变化递增 revision；CRM 变化同时递增 external_version。运行中任务保持自己的输入 revision。
- 完成抽取保持不变；失败抽取可在下一次正常同步或兼容补交接口中更新为成功。完整事实历史保留，不覆盖旧预算。
- 事实、人数、时间和分数未知时保留 null/unknown，不填推断值或零值。

## 验证边界

本轮自动测试覆盖迁移、业务 CRUD、事务回滚、用户隔离、任务租约、版本冲突、缓存和页面读取，并在 SQLite 上完成真实 HTTP 主链路冒烟。尚未验证生产部署、完整团队权限、大规模并发、进程崩溃恢复或 pgvector 检索；真实 Gmail OAuth 与百炼需要人工验证。
