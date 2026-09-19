# Agent 与 Django 集成

更新：2026-09-13。当前 Agent 已通过 `agent/clients/backend_api.py` 接入真实 Django 后端。业务流程和 JSON 结构以 [Agent README](../../agent/README.md) 为准，HTTP 传输以 [OpenAPI](../contracts/openapi.yaml) 为准。

## 职责边界

- Agent 负责 Gmail 读取、MIME 解析、L1 事实抽取、L2 公司事实归并、L3 客户画像与分析、L4 跟进优先级。
- Django 负责用户、员工 Google OAuth、邮箱、邮件、公司、联系人、业务快照、Job 和分析结果持久化。
- 浏览器从 Django 读取当前员工的 Gmail 状态、公司列表和详情，不读取 Gmail token、百炼 Key 或 Agent 服务令牌。
- Agent 不导入 Django，也不直接访问数据库。

## 一次同步

1. 当前员工通过网页 OAuth 连接 Gmail，并在页面请求同步。
2. Django 持久保存同步批次；独立 Worker 领取批次与员工授权，再调用 Agent。旧 CLI 的 `mailbox-syncs/claim/` 仅保留迁移调试。
3. Worker 分页补采 inbox/sent 历史，每页 20 封；先持久保存发现 ID 与页位置，再处理原文。历史完成后使用 History 增量；游标过期才重扫历史并复用已缓存结果。
4. Worker 按 `dedupe_key` 批量复用成功抽取；原文与已完成的 L1 输出分阶段持久化。新邮件执行 L1，明确重试根据失败阶段复用原文或直接重交抽取结果。
5. 需要执行 L1 的邮件最多四路并发；任一邮件完成后，`DjangoBackendClient` 立即向 `POST /api/v1/agent/emails/` 逐封提交。
6. 后端按 `mailbox_address:gmail_message_id` 去重，在当前员工范围内将邮件归组到公司；失败抽取在后续同步成功时可更新。
7. 只有已完成、属于业务且有实质变化的邮件创建 `email_ingested` Job。
8. Worker 持续记录逐封进度，邮箱处理结束后保存批次结果；浏览器轮询该批次的全量计数。
9. 独立画像通道与同步并行，Agent 领取 Job，依次读取 Grouping 和 CompanyContext，构建并保存 L2。
10. Agent 查询或生成 L3，计算并保存 L4，回报 Job。公司画像以公司 revision 为单位，同一公司的多封邮件共同组成一次分析输入。

Job 对 Agent workflow 暴露顶层 `company_id`。HTTP 层额外返回 `lease_token` 和 `expected_version`；适配器负责 ETag、If-Match 和租约请求头，使 L1–L4 保持简单的后端协议。

## 运行模式

`ANALYSIS_PROVIDER=agent` 是真实 Agent 模式。页面的“更新分析”只创建 Job，独立终端中的 Worker 持续消费：

```powershell
python backend/manage.py crm_worker
```

`ANALYSIS_PROVIDER=rules` 是离线演示模式。页面可导入合成样例或模拟来信，Django 内的确定性规则会写入演示分析。它不会在 Agent 网络或模型调用失败时自动接管。

## 当前限制

- Agent CLI 保留一次性调试；产品链路由独立 `crm_worker` 消费数据库批次和公司任务。
- 网页授权的 Google 凭证由 Django 保存，只通过 AgentAuthentication 保护的同步领取接口提供给 Agent。Agent 不再维护旧的本机 Desktop OAuth 读取命令；`test_tools/` 中的测试邮件注入器使用独立的 Desktop OAuth 凭据和 token，具体见其 README。
- Worker 使用 SyncCheckpoint/StoredMessage 保存游标、页位置、原文和 L1 输出；旧 SyncState 提供兼容投影。Worker 接管后拒绝旧 CLI 游标双写，仍以 `dedupe_key` 保证保存幂等。
- L1 最多四路并发，逐封失败隔离；批次与邮件任务保存到数据库。公司画像默认两路，同公司互斥。
- 后端依据 Agent 信号保存独立分类，隐藏非业务和待复核邮件；人工确认优先于后续自动分类。
- `extract-v7` 无采购阶段的入站邮件进入复核；人工确认缺失事实的业务邮件先补 L1，再自动重算画像。邮件分类或事实变化沿快照血缘使 L3/L4 失效，并对剩余业务来源重算。
- 租约和 revision 用于阻止过期任务覆盖新上下文；公司任务没有自动续租或隐式重试；邮箱批次通过阶段事件刷新租约。
- 工单、报价和订单由 sales 关系记录维护并投影到 CompanyContext；业务管理页提供编辑和状态入口，只有已发送报价及已确认订单提供相应分析证据。
- 真实 Gmail 与百炼不属于自动测试依赖。

持久批次、Worker、复核及迁移兼容边界见 [邮件处理适配](processing-integration.md)。

## 只读聊天适配

2026-09-18 新增独立 `apps.chat`，复用员工 Agent 服务认证；固定 claim/context/answers 三接口与原 Agent 聊天工作流兼容。运行使用独立 `chat_worker`，不修改本页的邮箱或 L1–L4 流程。精确契约、迁移、知识与恢复说明见 [聊天适配](chat-integration.md)。
