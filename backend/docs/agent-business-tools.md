# Agent 业务工具接入

完整目录提供 123 个业务工具（默认 QQ 暂停时为 122 个，不发布 `actions.prepare_qq`）、独立用户委托、HTTP SDK、CLI 和 stdio MCP 服务，复用现有权限、序列化器和事务。供 Agent 开发侧接入；当前聊天工作流仍为只读问答，尚未增加自动选择/执行工具的循环。

## 文件职责

```text
backend/apps/agent_tools/
  registry.py          # 工具名称、用途、执行模式和固定路由
  schemas.py           # 从实际序列化器生成输入 Schema，严格预检
  authentication.py    # 用户、token 到期/撤销、工具白名单
  dispatch.py          # 固定适配 crm/sales/chat 业务处理器
  services.py          # 幂等回执、冻结提案、独立确认和权限复核
  models.py            # ToolCredential、ToolCall、ToolProposal
  views.py / urls.py   # 工具 HTTP 与 Session-only 授权/确认
  migrations/          # 新表，不修改既有业务表
backend/tests/integration/test_agent_tools.py
integrations/salesmate_tools/
  client.py            # 不依赖 Django 的 HTTP SDK
  cli.py               # list / describe / call
  mcp_server.py        # 动态 Schema、分页目录、独立命名 MCP tools
  requirements.txt     # 独立客户端依赖
  tests/test_clients.py # HTTP、CLI、真实 MCP stdio 握手
```

新增能力先在业务模块实现权限和规则，再登记 registry、绑定 dispatch、补工具边界测试。不要在 MCP、CLI 或 prompt 中重复实现数据库业务。

## 覆盖范围

| 业务 | 前缀/代表工具 | 能力 |
| --- | --- | --- |
| 客户 | `customers.search/create/context/register/analyze`、`customer_settings.*` | 目录、建档、私有上下文、带来源 CRM 资料、分析任务、设置 |
| 联系人 | `contacts.save`、`contact_profiles.*` | 联系人邮箱/姓名与补充资料；联系人 ID 是整数 |
| 归组 | `aliases.*`、`customers.merge/move` | 人工别名、合并客户、搬移指定邮件；先提出计划 |
| 协作 | `teams.*`、`memberships.*`、`grants.*`、`people.find` | 团队、成员、客户授权、完整用户名查找 |
| 销售 | `products.*`、`tickets.*`、`opportunities.*` | 产品、工单、商机 |
| 单据 | `quotes.*`、`quote_lines.*`、`orders.*`、`order_lines.*` | 单据/明细读写、金额和版本；状态变化先确认 |
| 跟进 | `follow_ups.*`、`notifications.list/get/read` | 跟进计划、分配、提醒与已读 |
| 助手 | `conversations.*`、`messages.list/get`、`drafts.*` | 会话、历史消息、草稿；不伪造助手回答 |
| 外部动作 | `actions.prepare_gmail/prepare_qq/prepare_calendar`、`actions.list/get` | 冻结发信或会议内容，等待人类确认 |
| 邮件 | `mailboxes.list/sync/sync_status/retry`、`emails.list/review` | 同步进度、已保存邮件和复核；写操作先确认 |
| 日历 | `calendar.events/freebusy` | 指定连接/日历/带时区窗口内的事件和忙闲 |
| 证据 | `knowledge.search/get`、`files.list/get/archive/download_link` | 已导入知识关键词检索、来源版本、附件元数据及 Session 下载链接 |
| 状态 | `sales.overview/audit`、`connections.list/get`、`proposals.get` | 概况、审计、无密钥连接信息、本人提案结果 |

常规可写资源提供 list/get/create/update/archive；不支持的操作不会登记。消息、动作、附件、提醒、连接不开放普通 create/update。具体字段以实时 `inputSchema` 为准。目录按 token 过滤，默认 30 项、最大 100，需处理分页。业务列表不自动遍历全部页。Agent 应按任务选择工具分类和授权子集，避免将全部工具放入每次模型上下文。

## 执行规则

- `read`：查询，不接受幂等键；日历查询会访问外部 API。
- `write`：已委托的普通写入，如跟进、草稿；必须提供 UUID `idempotency_key`。
- `confirm`：只保存有效期 24 小时的冻结提案，不执行业务；用户单独审阅批准。

写调用按「用户 + 幂等键」去重，跨凭证共享同一逻辑操作。相同工具和输入重放历史回执；同键不同内容返回 409。JSON 键顺序不影响去重。回执不是业务实体最新快照。

`status=completed` 表示工具操作完成；`accepted` 表示后台受理；`confirmation_required` 表示仍待确认。外部动作准备返回 `data.status=pending_confirmation` 和原 ToolAction；通用计划返回 `proposal`，两者确认路径不同。

更新需先 get，再传 `id`、整数 `revision` 和 `data`。Decimal 金额/数量使用字符串。只读字段不能通过更新写入。报价明细写入会推进父单据版本，后续操作须重新读取父单据。QQ 同步须明确 `sync_options.recent_days` 或 `max_messages`，Gmail 不接受 QQ 范围；不自动填默认范围。

## HTTP 与授权

在配置明确的开发数据库应用新迁移：`python backend/manage.py migrate`。工具使用 `Authorization: Tool <token>`，与 Worker 的 `Agent` 凭证独立，不可互换。身份只来自授权用户，输入不能冒充 owner。原有团队共享与个人邮件/草稿/附件隔离仍生效。

以下路径统一以 `/api/v1/agent-tools/` 为前缀：

| 方法/路径 | 身份 | 用途 |
| --- | --- | --- |
| `GET catalog/?category=follow_ups&page=1&page_size=100` | Tool 或 Session | 名称、描述、Schema、执行模式和 annotations |
| `POST call/` | Tool 或 Session | 一次结构化调用 |
| `GET/POST credentials/` | Session，写入要求 CSRF | 本人授权列表/创建 |
| `DELETE credentials/{id}/` | Session + CSRF | 撤销本人凭证 |
| `GET proposals/`、`GET proposals/{id}/` | Session | 待确认列表/详情 |
| `POST proposals/{id}/decision/` | Session + CSRF | `{"decision":"approve"}` 或 `cancel` |

创建授权的请求示例：

```json
{"name":"跟进助手","allowed_tools":["customers.search","follow_ups.list","follow_ups.get","follow_ups.create","follow_ups.update","proposals.get"],"expires_in_hours":24}
```

期限必须明确为 1–720 小时，不支持通配符。原始 token 只返回一次，数据库只存摘要，响应禁止缓存。不要将令牌放进聊天、工具参数、日志或 Git。授权和批准没有 MCP/CLI 工具。

调用示例，UUID 应替换为真实客户和本次逻辑操作标识：

```json
{
  "name": "follow_ups.create",
  "arguments": {
    "data": {
      "company": "11111111-1111-4111-8111-111111111111",
      "title": "确认设备规格",
      "due_at": "2026-10-01T10:00:00+08:00"
    }
  },
  "idempotency_key": "22222222-2222-4222-8222-222222222222"
}
```

错误保留原 HTTP 400/401/403/404/409 等。网络中断可能发生在已提交之后；客户端不自动重试。调用者核对时复用原键和原输入，不能假定请求未执行。业务失败回滚本次回执。

## MCP、CLI、Python

独立安装 SDK，避免改变 Web/Worker 依赖。命令从仓库根目录执行：

```powershell
python -m venv .tools-venv
.tools-venv/Scripts/python.exe -m pip install -r integrations/salesmate_tools/requirements.txt
$env:SALESMATE_TOOLS_URL = "http://127.0.0.1:8000"
# SALESMATE_TOOLS_TOKEN 由宿主私密环境注入，不写入仓库。
.tools-venv/Scripts/python.exe -m integrations.salesmate_tools.cli list --category follow_ups
.tools-venv/Scripts/python.exe -m integrations.salesmate_tools.cli describe follow_ups.create
.tools-venv/Scripts/python.exe -m integrations.salesmate_tools.cli call follow_ups.create --arguments-file follow-up.json --idempotency-key 22222222-2222-4222-8222-222222222222
```

`follow-up.json` 只保存示例中的 `arguments` 对象。CLI stdout 是 JSON，错误到 stderr，失败返回非零；不执行 Shell 字符串。URL 为 HTTPS 服务根地址，仅本机可使用 HTTP。

MCP 宿主配置：可执行文件为独立环境的 Python，参数为 `-m integrations.salesmate_tools.mcp_server`，工作目录为仓库根目录，环境注入 URL/TOKEN。只提供 stdio，未开放公网 MCP 端口。固定 `mcp==2.2.0`，使用[官方 Python SDK](https://github.com/modelcontextprotocol/python-sdk)低层 Server。

MCP 发布具体工具名与 Schema。写工具在参数最外层要求 `idempotency_key`，适配器将它转为 HTTP 信封字段。宿主须处理 `tools/list.nextCursor`。结果有 JSON 文本和 `structuredContent`；HTTP/网络失败为 `isError=true`，待确认是有效回执。

```python
from integrations.salesmate_tools.client import ToolClient
client = ToolClient.from_env()
result = client.call("customers.search", {"q": "设备", "page": 1})
```

## 接入边界

- 本次预留开发接口，尚无新增的凭证管理、通用 ToolProposal 审阅前端。前端可直接对接 Session API。发信/会议仍可使用原业务页面的 ToolAction 确认。
- 提案保存时校验结构，批准时重新验证实体权限、版本与原 token 有效性。过期/撤销/冲突拒绝执行。确认请求不能修改冻结内容，同一决定重复提交不会重复执行。Agent 只能用 `proposals.get` 查询结果。
- 聊天 Skill、模型参数和只读提示未改变。后续需要 Agent 工具选择、参数澄清、执行循环、确认卡片及业务评估。
- 世界消息仍为演示，没有新闻后端；知识检索是已导入内容的关键词匹配；附件未解析，下载链接要求 Session。未虚构推送、向量检索或文件解析能力。
- OAuth/QQ 密钥连接仍由人类原页面完成。不开放 SQL、Shell、运维、凭证读取、直接批准外发或硬删除。

## 验证

```powershell
python backend/manage.py test tests.integration.test_agent_tools --noinput
.tools-venv/Scripts/python.exe -m unittest discover -s integrations/salesmate_tools/tests -v
python backend/tools/check_docs.py
python backend/tools/check_docs.py integrations
python backend/tools/check_doc_changes.py --base HEAD --fail-on-review
```

后端测试覆盖全部资源列表、输入校验、权限/CSRF、版本、幂等、跨凭证并发、报价金额、联系人主键、知识来源、三种外部动作只准备、提案确认/到期/撤销与回滚。客户端测试包含真实 SDK stdio 握手及分页，HTTP 为本机 fixture；不代表真实外部账号授权或发信成功。CI 使用独立 SDK 环境运行测试。
