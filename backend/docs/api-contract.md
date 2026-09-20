# 当前 API 契约

更新：2026-09-13。字段的唯一机器可读定义是由 Django 生成的 [OpenAPI](../contracts/openapi.yaml)。Agent 业务对象语义见 [Agent README](../../agent/README.md)。

## 身份

浏览器先用 `GET /api/v1/session/` 获取 CSRF Cookie，再用 `POST /api/v1/accounts/register/` 提交 `{"username":"...","password":"..."}`。注册仅接受这两个字段，不要求邮箱、手机号或验证码；用户名遵守现有模型规则，密码复用 Django 已配置的校验器。成功返回 201、`authenticated`、`username` 和轮换后的 `csrf_token`，同时建立普通用户 Session。输入错误或重名返回 400，缺少有效 CSRF 返回 403，已登录时再次注册返回 409。密码以哈希存储，不回传。

新账号拥有独立的空工作空间，不复制 demo 数据，也不自动创建 Gmail 授权或 Agent 服务令牌。退出后继续使用 `POST /api/v1/session/` 登录；`DELETE /api/v1/session/` 注销。注册页面中的确认密码仅用于浏览器一致性检查，不作为后端字段发送。

浏览器使用 Django Session 和 CSRF。Agent 路由只接受：

```http
Authorization: Agent <service-token>
```

服务令牌绑定一个后端用户，不能用浏览器 Session 或 Gmail access token 替代。`mailbox_id` 和 `company_id` 由后端创建，均为 UUID。错误响应保留 `error.code`、`error.detail` 和 `request_id`。

## Agent 路由

以下路径以 `/api/v1/agent/` 开头：

| Agent 操作 | HTTP | 主要交换数据 |
|---|---|---|
| 提交邮件 | `POST emails/` | 接口仍接受 `EmailSubmission[]`；当前 Agent 每次传一封，返回该邮件的 `dedupe_key`、`company_id`、`created/updated/duplicate` |
| 读取公司归组 | `GET grouping/?company_id=...` | 公司、域名、联系人、成员邮件键；响应含 ETag |
| 读取公司上下文 | `GET context/?company_id=...` | 邮件、客户、工单、报价、订单；请求携带 Grouping 的 If-Match |
| 保存 L2 | `POST analysis-inputs/` | 完整 `AnalysisInput` |
| 读取最新 L2 | `GET latest-analysis-input/?company_id=...` | `AnalysisInput`，不存在返回 404 |
| 查询 L3 缓存 | `GET cached-analysis/?company_id=...&input_version=...&analysis_prompt_version=...` | 命中时返回完整 `Analysis`，否则 `analysis=null` |
| 保存 L3 | `POST analyses/` | 完整 `Analysis` |
| 保存 L4 | `POST scores/` | 完整 `Score` |
| 领取任务 | `POST jobs/claim/` | `limit`、`lease_seconds` → 顶层含 `company_id` 的 `Job[]` |
| 回报任务 | `POST jobs/report/` | `JobReport`，请求携带领取凭证 |
| 领取员工邮箱同步 | `POST mailbox-syncs/claim/` | `limit` → 邮箱地址、Google 授权信息和读取上限 |
| 回报员工邮箱同步 | `POST mailbox-syncs/report/` | 同步汇总、错误及可选刷新凭证 → 浏览器安全状态 |

兼容接口还包括 `POST facts/`、`GET failed-extractions/`、`GET sync-state/` 和 `POST sync-state-save/`。产品 Gmail 同步必须提供 `sync_options`（`recent_days` 或 `max_messages` 至少一项），普通 Gmail 批次默认最多 50 封，超量必须明确提供 `max_messages` 和 `allow_large_sync=true`；由 Worker 在冻结范围内先限量再去重；StoredMessage 保存原文和 L1 输出，SyncCheckpoint 仅保留 Worker 接管标记及旧审计状态；失败明确重试，按阶段复用缓存。Worker 接管邮箱后拒绝旧 CLI 游标双写。旧 CLI 的已配置游标读写异常向上报告。

## 写入一致性

领取 Job 后，后端返回 `job_id`、顶层 `company_id`、`trigger`、`expected_version`、`lease_token` 和 `lease_until`。

保存 L2、L3 和 L4 时，HTTP 适配器发送 `If-Match`、`X-Job-ID` 和 `X-Lease-Token`。上下文 revision 已变化、任务不是运行中、凭证错误或租约过期时，后端拒绝写入。MVP 不自动续租或重试过期任务。

邮件天然键必须为 `mailbox_address.casefold():gmail_message_id`。相同载荷返回 `duplicate`；原记录抽取失败、下一次同邮件抽取成功时返回 `updated`。Agent 逐封调用提交接口，因此单封冲突不会回滚其他邮件。非业务邮件或无实质变化邮件会保存，但不会创建分析 Job；默认公司列表、统计与 Agent 上下文已排除非业务和待复核邮件。

L2、L3 和 L4 的核心约束：

- L2 必须保留当前公司的所有邮件事实、来源、时间和后端业务快照。
- L3 的事实与推断引用必须属于当前 L2 输入，详情中的缺失项和完整度位于 `detail_view`。
- 评分特征只能是 0–3 整数或 JSON `null`。
- 信号未知、任一评分特征为 `null` 或缺少最近入站时间时，Score 为 `null`。
- 缓存要求公司、当前 revision、`input_version` 和 `analysis_prompt_version` 全部匹配。
- 失效血缘对应的 L2/L3/L4 不参与展示和缓存；同一 `input_version` 可在不同 revision 保存独立快照。人工补抽取沿用真实提示词版本，以内部 `repair_generation` 保留原抽取历史，不改变 Agent 的 EmailSubmission 字段。

## 浏览器路由

浏览器使用 `/api/v1/` 下的 Session、mailboxes、companies 和 demo 路由。页面可以查看当前员工的 Gmail 连接和公司列表/详情、完成 Google OAuth、请求邮箱同步、建档、请求更新分析，以及在 `rules` 模式导入样例和模拟来信。浏览器不会收到 Google 凭证。

员工 Gmail 路由：

- `POST mailboxes/gmail-authorize/`：生成 Google 授权地址。
- `GET mailboxes/gmail-callback/`：交换授权码、验证 Gmail 地址、绑定当前员工并请求首次同步。
- `POST mailboxes/{mailbox_id}/request-sync/`：持久排队并返回 HTTP 202、run_id 和 queued；重复请求复用活动批次。
- `DELETE mailboxes/{mailbox_id}/gmail-authorization/`：移除授权，保留历史业务数据。

独立 `crm_worker` 处理同步批次和公司任务。新增批次进度、明确重试、复核查询和 If-Match 确认接口见 [邮件处理适配](processing-integration.md)。Web 不启动后台线程。

生成并校验契约：

```powershell
python backend/manage.py spectacular --file backend/contracts/openapi.yaml --validate --fail-on-warn
```
## QQ 邮箱增量接口

新增 QQ 连接与删除接口，复用现有邮箱同步、进度和明确重试 API。邮箱响应追加 `qq_authorized`，邮件来源追加 `qq_real`；Gmail 路由和 `gmail_authorized` 语义不变。请求格式、认证及兼容字段详见 [QQ 邮箱接入](qq-mailbox.md#api-与兼容边界)，机器可读定义见 `../contracts/openapi.yaml`。
