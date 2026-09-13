# 数据模型现状

更新：2026-09-13。数据库由必填 `DATABASE_URL` 显式指定；本轮沿用本机 PostgreSQL，不改动数据库、时区或分析配置。accounts、crm 和 sales 通过迁移共同维护 Schema。

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

Company.customer 保留已确认的 CRM 基础资料；tickets/quotes/orders 是原 Agent 契约投影。新增 `sales` 关系表维护业务单据与明细，仅替换投影中 `source=sales_record` 条目，原历史 JSON 保留。报价有真实发送证据后才进入投影，订单确认后才作为历史订单；事务同时递增 external_version/revision 并入队。完整关系和状态契约见 [销售 Schema 与接口](backend-expansion.md)。

## 约束

- 同一 owner 与 group_key 唯一。完整企业域名相同才自动合组；清单中的公共邮箱按完整联系人邮箱分别归组。清单有限，不宣称覆盖全部服务商。
- 客户自报公司名只用于展示，不参与自动归组。新邮件优先匹配人工 CompanyAlias（联系人优先、域名其次），无映射时采用原分组规则。所有者可以显式搬移选择的邮件、合并公司或配置多域名映射。
- 同用户跨业务邮箱的同域往来进入同一公司；不同用户同域仍隔离。
- 邮箱地址、公司联系人、抽取版本、输入版本、分析提示词版本均有相应联合唯一约束。
- 邮件/事实/CRM 变化递增 revision；CRM 变化同时递增 external_version。运行中任务保持自己的输入 revision。
- 完成抽取保持不变；失败抽取可在下一次正常同步或兼容补交接口中更新为成功。完整事实历史保留，不覆盖旧预算。
- 事实、人数、时间和分数未知时保留 null/unknown，不填推断值或零值。

当前 `Extraction.status` 可以保存 `skipped_non_business`，完成事实中也可以保存 `intent_hint=non_sales`，但数据库没有独立的业务分类或人工复核字段。`Email.company` 当前为必填，因此仅包含非业务邮件的 Company 仍可能进入默认公司列表；该行为是待修复的后端数据模型和查询缺口，不能由前端主题或域名规则代替。

## 验证边界

本轮在 PostgreSQL 隔离测试数据库验证关系记录、状态、权限隔离、金额快照、归组、草稿、附件、CSRF、动作确认及失败语义。Google SDK 边界采用模拟，不能证明真实账号授权或外部执行已完成。尚未进行生产部署、多进程压力测试、真实进程崩溃演练或 pgvector 检索。

## 旧版规则事实升级

迁移 0004 只处理仍为当前抽取的 rules-extract-v1 成功记录：将单值 value/evidence 转成多值数组，intent_evidence 转为 intent_evidences，并追加 rules-extract-v1+multivalue-v1 版本。转换不调用模型，不改写原邮件、原抽取或历史分析；公司 revision 递增，使旧分析显示过期。已有更新版本不覆盖，未知结构明确报错。迁移不提供自动逆操作，以免删除审计记录；数据库回退需另行制定数据方案。

旧邮件本体缺少的 mailbox_address 由查询投影从所属 Mailbox 补齐，原邮件去重标识保持不变。
