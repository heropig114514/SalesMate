# 当前 API 契约

更新：2026-09-12。字段的唯一机器可读定义是由 Django 生成的 [OpenAPI](../contracts/openapi.yaml)。Agent 业务对象语义见 [Agent README](../../agent/README.md)。

## 身份

浏览器使用 Django Session 和 CSRF。Agent 路由只接受：

```http
Authorization: Agent <service-token>
```

服务令牌绑定一个后端用户，不能用浏览器 Session 或 Gmail access token 替代。`mailbox_id` 和 `company_id` 由后端创建，均为 UUID。错误响应保留 `error.code`、`error.detail` 和 `request_id`。

## Agent 路由

以下路径以 `/api/v1/agent/` 开头：

| Agent 操作 | HTTP | 主要交换数据 |
|---|---|---|
| 批量提交邮件 | `POST emails/` | `EmailSubmission[]` → 每封的 `dedupe_key`、`company_id`、`created/updated/duplicate` |
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

兼容接口还包括 `POST facts/`、`GET failed-extractions/`、`GET sync-state/` 和 `POST sync-state-save/`。当前 Gmail MVP 不依赖 History cursor，失败抽取可在下一次正常邮件同步时直接更新。

## 写入一致性

领取 Job 后，后端返回 `job_id`、顶层 `company_id`、`trigger`、`expected_version`、`lease_token` 和 `lease_until`。

保存 L2、L3 和 L4 时，HTTP 适配器发送 `If-Match`、`X-Job-ID` 和 `X-Lease-Token`。上下文 revision 已变化、任务不是运行中、凭证错误或租约过期时，后端拒绝写入。MVP 不自动续租或重试过期任务。

邮件天然键必须为 `mailbox_address.casefold():gmail_message_id`。相同载荷返回 `duplicate`；原记录抽取失败、下一次同邮件抽取成功时返回 `updated`。非业务邮件或无实质变化邮件会保存，但不会创建分析 Job。

L2、L3 和 L4 的核心约束：

- L2 必须保留当前公司的所有邮件事实、来源、时间和后端业务快照。
- L3 的事实与推断引用必须属于当前 L2 输入，详情中的缺失项和完整度位于 `detail_view`。
- 评分特征只能是 0–3 整数或 JSON `null`。
- 信号未知、任一评分特征为 `null` 或缺少最近入站时间时，Score 为 `null`。
- 缓存要求公司、当前 revision、`input_version` 和 `analysis_prompt_version` 全部匹配。

## 浏览器路由

浏览器使用 `/api/v1/` 下的 Session、mailboxes、companies 和 demo 路由。页面可以查看当前员工的 Gmail 连接和公司列表/详情、完成 Google OAuth、请求邮箱同步、建档、请求更新分析，以及在 `rules` 模式导入样例和模拟来信。浏览器不会收到 Google 凭证。

员工 Gmail 路由：

- `POST mailboxes/gmail-authorize/`：生成 Google 授权地址。
- `GET mailboxes/gmail-callback/`：交换授权码、验证 Gmail 地址、绑定当前员工并请求首次同步。
- `POST mailboxes/{mailbox_id}/request-sync/`：将已授权邮箱标为 `sync_requested`。
- `DELETE mailboxes/{mailbox_id}/gmail-authorization/`：移除授权，保留历史业务数据。

邮箱同步状态由一次性 Agent 命令处理，不代表已经启动常驻 Worker。

生成并校验契约：

```powershell
python backend/manage.py spectacular --file backend/contracts/openapi.yaml --validate --fail-on-warn
```
