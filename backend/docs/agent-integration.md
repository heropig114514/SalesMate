# Agent 与 Django 集成

更新：2026-09-13。当前 Agent 已通过 `agent/clients/backend_api.py` 接入真实 Django 后端。业务流程和 JSON 结构以 [Agent README](../../agent/README.md) 为准，HTTP 传输以 [OpenAPI](../contracts/openapi.yaml) 为准。

## 职责边界

- Agent 负责 Gmail 读取、MIME 解析、L1 事实抽取、L2 公司事实归并、L3 客户画像与分析、L4 跟进优先级。
- Django 负责用户、员工 Google OAuth、邮箱、邮件、公司、联系人、业务快照、Job 和分析结果持久化。
- 浏览器从 Django 读取当前员工的 Gmail 状态、公司列表和详情，不读取 Gmail token、百炼 Key 或 Agent 服务令牌。
- Agent 不导入 Django，也不直接访问数据库。

## 一次同步

1. 当前员工通过网页 OAuth 连接 Gmail，并在页面请求同步。
2. Agent 从 `mailbox-syncs/claim/` 领取该员工邮箱的授权信息和读取上限。
3. 首次同步读取最近邮件并保存 Gmail History 游标；后续优先读取游标之后新增的邮件，游标过期时退回最近邮件扫描。
4. Agent 根据 `dedupe_key` 复用后端已有的成功抽取，只对新邮件和可重试邮件执行 L1。
5. 需要执行 L1 的邮件最多四路并发；任一邮件完成后，`DjangoBackendClient` 立即向 `POST /api/v1/agent/emails/` 逐封提交。
6. 后端按 `mailbox_address:gmail_message_id` 去重，在当前员工范围内将邮件归组到公司；失败抽取在后续同步成功时可更新。
7. 只有已完成、属于业务且有实质变化的邮件创建 `email_ingested` Job。
8. 邮箱读取和逐封保存结束后，Agent 先回报邮箱同步结果；浏览器此时可以显示已保存邮件并继续轮询公司任务。
9. Agent 领取 Job，依次读取 Grouping 和 CompanyContext，构建并保存 L2。
10. Agent 查询或生成 L3，计算并保存 L4，回报 Job。公司画像以公司 revision 为单位，同一公司的多封邮件共同组成一次分析输入。

Job 对 Agent workflow 暴露顶层 `company_id`。HTTP 层额外返回 `lease_token` 和 `expected_version`；适配器负责 ETag、If-Match 和租约请求头，使 L1–L4 保持简单的后端协议。

## 运行模式

`ANALYSIS_PROVIDER=agent` 是真实 Agent 模式。页面的“更新分析”只创建 Job，随后运行：

```powershell
python -m agent.main --process-jobs-once --job-limit 10
```

`ANALYSIS_PROVIDER=rules` 是离线演示模式。页面可导入合成样例或模拟来信，Django 内的确定性规则会写入演示分析。它不会在 Agent 网络或模型调用失败时自动接管。

## 当前限制

- Agent 是一次性 CLI，不常驻轮询。
- 网页授权的 Google 凭证由 Django 保存，只通过 AgentAuthentication 保护的同步领取接口提供给 Agent。Agent 不再维护旧的本机 Desktop OAuth 读取命令；`test_tools/` 中的测试邮件注入器使用独立的 Desktop OAuth 凭据和 token，具体见其 README。
- 后端 SyncState 保存 Gmail History 游标、积压 message ID 和失败 message ID；后续同步优先增量读取，仍以 `dedupe_key` 保证保存幂等。
- L1 最多四路并发，并按实际完成顺序逐封提交。一封邮件失败不会阻止其他邮件保存，但邮箱同步目前仍由 Django 进程内线程执行，没有持久化邮件任务。
- L3/L4 是公司级 Job。当前本地自动运行器一次只领取一个公司 Job，不同公司的画像尚未并行。
- Agent 会提交明确的 `skipped_non_business`，并保留模型给出的 `intent_hint=non_sales`。后端当前只阻止它们创建自动分析任务，尚未提供默认隐藏和人工复核接口。
- 租约和 revision 用于阻止过期任务覆盖新上下文；没有自动续租、指数退避或复杂调度。
- 工单、报价和订单由 sales 关系记录维护并投影到 CompanyContext；业务管理页提供编辑和状态入口，只有已发送报价及已确认订单提供相应分析证据。
- 真实 Gmail 与百炼不属于自动测试依赖。
