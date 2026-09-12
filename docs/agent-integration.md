# Agent 与 Django 集成

更新：2026-09-12。当前 Agent 已通过 `agent/clients/backend_api.py` 接入真实 Django 后端。业务流程和 JSON 结构以 [Agent README](../agent/README.md) 为准，HTTP 传输以 [OpenAPI](../contracts/openapi.yaml) 为准。

## 职责边界

- Agent 负责 Gmail 读取、MIME 解析、L1 事实抽取、L2 公司事实归并、L3 客户画像与分析、L4 跟进优先级。
- Django 负责用户、员工 Google OAuth、邮箱、邮件、公司、联系人、业务快照、Job 和分析结果持久化。
- 浏览器从 Django 读取当前员工的 Gmail 状态、公司列表和详情，不读取 Gmail token、百炼 Key 或 Agent 服务令牌。
- Agent 不导入 Django，也不直接访问数据库。

## 一次同步

1. 当前员工通过网页 OAuth 连接 Gmail，并在页面请求同步。
2. Agent 从 `mailbox-syncs/claim/` 领取该员工邮箱的授权信息和读取上限。
3. Agent 读取最近邮件并形成 `EmailSubmission[]`。
4. `DjangoBackendClient` 补充后端 `mailbox_id` 和来源，提交到 `POST /api/v1/agent/emails/`。
5. 后端按 `mailbox_address:gmail_message_id` 去重，在当前员工范围内将业务邮件归组到公司；失败抽取在后续同步成功时可更新。
6. 只有已完成、属于业务且有实质变化的邮件创建 `email_ingested` Job。
7. Agent 领取 Job，依次读取 Grouping 和 CompanyContext，构建并保存 L2。
8. Agent 查询或生成 L3，计算并保存 L4，回报 Job 和邮箱同步状态。

Job 对 Agent workflow 暴露顶层 `company_id`。HTTP 层额外返回 `lease_token` 和 `expected_version`；适配器负责 ETag、If-Match 和租约请求头，使 L1–L4 保持简单的后端协议。

## 运行模式

`ANALYSIS_PROVIDER=agent` 是真实 Agent 模式。页面的“更新分析”只创建 Job，随后运行：

```powershell
python -m agent.main --process-jobs-once --job-limit 10
```

`ANALYSIS_PROVIDER=rules` 是离线演示模式。页面可导入合成样例或模拟来信，Django 内的确定性规则会写入演示分析。它不会在 Agent 网络或模型调用失败时自动接管。

## 当前限制

- Agent 是一次性 CLI，不常驻轮询。
- 网页授权的 Google 凭证由 Django 保存，只通过 AgentAuthentication 保护的同步领取接口提供给 Agent。旧 Desktop OAuth 文件只用于兼容调试命令。
- 后端保留 SyncState 和失败补交兼容接口，但当前 Gmail MVP 通过最近邮件重复扫描和正常 `emails/` 提交完成去重及失败更新。
- 租约和 revision 用于阻止过期任务覆盖新上下文；没有自动续租、指数退避或复杂调度。
- 工单、报价和订单可进入 CompanyContext，当前页面没有完整交易编辑入口。
- 真实 Gmail 与百炼不属于自动测试依赖。
