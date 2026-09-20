# 算法侧的软件辅助接口

后端负责存储、权限、版本、文件和业务操作；评分、匹配、推荐、新闻采集及 PDF 文本提取由调用方处理。本次新增 32 个工具，复用既有 HTTP、Python SDK、CLI 和 stdio MCP，不建立第二套业务逻辑。

## 已有能力与新增能力

| 数据 | 工具前缀 | 支持范围 |
| --- | --- | --- |
| 客户、联系人、邮件、商机、工单、报价、订单 | 原有 `customers`、`contacts`、`emails`、`opportunities`、`tickets` 等 | 继续使用原工具，具体可写字段和确认要求见实时目录 |
| 公司资料、个人引导资料、卖方能力资料 | `company_profile`、`sales_setup`、`seller_profile` | `get/update`，显式 revision |
| 参考产品、销售方案 | `setup_products`、`solutions` | `list/get/create/update/delete`，稳定 UUID，全引导资料共用 revision |
| 引导文件 | `setup_documents` | `list/get/upload/read/delete`，PDF/UTF-8 TXT；删除前检查引用 |
| 已有业务附件 | `files.read` | Tool token 直接分块读，无需 Session 下载；原有文件工具继续可用 |
| 全球活动、行业资讯 | `world_events`、`world_news` | `list/get/create/update/archive`，支持归档及恢复、地区和时间筛选 |

`setup_products` 是参考资料目录；`products` 是交易目录。前者的 `linked_product_id` 可显式关联本人未归档交易产品，不自动创建交易产品、不同步价格、不计算匹配分数。方案必须关联自己的已上传文件。资料整份替换时保留条目 `id`；修改前先读取最新快照。历史无 ID 条目在读取时获得确定性 UUID，在下一次对应数组保存时持久化；前端编辑也保留该 ID 和关联。

`seller_profile.update` 沿用原有卖方能力字段与依赖更新机制，不新增或修改评分算法。其他本次新增工具不调用模型、抓取外站或生成评分。

## 一次授权后持续调用

登录用户通过 `GET /api/v1/agent-tools/permission-presets/` 查看精确列表，再通过 Session + CSRF 创建凭证：

```json
{"name":"算法数据接入","preset":"data_management","expires_in_hours":720}
```

发送到 `POST /api/v1/agent-tools/credentials/`。也可用 `read_only` 或原有 `allowed_tools` 明确列表；`preset` 与 `allowed_tools` 只能选一项。模板在创建时冻结为工具名列表，已有令牌不会随部署自动扩权。新工具未出现在旧授权时，登录用户需签发新范围的凭证。原始 token 仅返回一次，通过宿主私密环境注入 `SALESMATE_TOOLS_TOKEN`；有效期仍为显式 1–720 小时。

`data_management` 包含当前全部 read/write 工具，普通资料、目录、文件和活动资讯写入不逐次确认。该范围也包含原有业务写入，应先查看列表。外发只可准备草稿/待确认动作；确认类工具不在此模板内。账号隔离、已授予的团队权限、版本冲突和外部服务自身 OAuth 要求仍保留。Tool token 不能给自己授权、批准提案或绕过真实发信/会议确认。

授权之后调用仅需 `Authorization: Tool <token>`，不需要浏览器 Cookie 或 CSRF。独立 MCP 动态读取同一目录；本次未扩大内置聊天 Worker 的只读工具白名单。完整接入步骤见 [Agent 业务工具接入](agent-business-tools.md)。

## 调用示例

统一 `POST /api/v1/agent-tools/call/`。先读取版本：

```json
{"name":"sales_setup.get","arguments":{}}
```

然后用回执 `data.revision` 更新资料；下例 revision=0 仅适用于尚未保存的账号。每次新的逻辑写入生成新 UUID 幂等键，相同键与相同输入用于核对已有操作，不自动重试。

```json
{
  "name":"setup_products.create",
  "arguments":{
    "revision":0,
    "data":{
      "name":"检测设备","category":"光学",
      "specifications":["精度 1 mm"],"scenarios":["产线检测"],
      "price_min":null,"price_max":null,"currency":"SGD",
      "document_id":null,"linked_product_id":null
    }
  },
  "idempotency_key":"eb6c53cf-6613-4412-b9ee-ebd8f2680c51"
}
```

条目操作返回 `data.item` 和 `data.setup_revision`；列表返回分页结果及 `setup_revision`。后续 update/delete 使用最新版本。完整资料更新中的嵌套 personal 对象、products/solutions 数组采用原有完整替换语义；逐条工具 update 才是对指定条目的字段合并。

文件上传参数为 `name` 与 `content_base64`，文件原始字节最多 5 MiB。读取方式：

```json
{
  "name":"setup_documents.read",
  "arguments":{"id":"文件实际 UUID","format":"text","offset":0,"limit":16000}
}
```

`text` 仅 UTF-8 TXT，offset/limit 按字符，每次最多 16000 字符；`base64` 支持二进制，按字节，每次最多 262144 字节。返回 `sha256`、`total`、`unit`、`next_offset`；调用方自行处理后续分块，末尾为 null。PDF 只提供原始字节，不宣称解析出文字。`files.read` 使用相同参数，并校验已有附件大小与 SHA-256。仍被方案或产品引用的引导文件删除返回 409，先明确移除引用。

## 全球活动和资讯的数据接口

除工具外，浏览器 Session API 可用 `/api/v1/sales/records/world-events/` 与 `/api/v1/sales/records/world-news/`。工具身份走统一 call 入口，不能直接替代这些原业务路由的 Session。资源的 ID、owner、revision、归档状态和创建更新时间由服务器维护；更新及归档必须提交读取到的 revision。交易相关金额不由活动接口合成。

活动字段：title、event_type（exhibition/sales）、country（两位大写国家地区代码）、city、latitude/longitude、starts_at/ends_at、可空 registration_deadline、source_url、description、onsite、suggested_actions、opportunity_ids。商机关联只接受本人未归档且不重复的 UUID；这是保存时引用校验，后续商机变动不自动清除活动中的历史引用。现场信息和建议由调用方显式提供，服务端只保存。

资讯字段：title、category（regulation/industry/competition/price）、industry、country、published_at、source_url、summary、content。来源必须是无凭证 HTTPS URL，服务端不访问或验证来源可达性。时间必须包含时区。内容是纯文本，不执行 HTML。

列表支持 page/page_size、q、archived、country、from/to；活动另有 event_type，资讯另有 category。from/to 为带时区 ISO 时间，包含下界、不包含上界；活动按开始时间升序，资讯按发布时间降序。未知筛选字段报错。默认分页 30、最多 100，不隐式拉取全部数据。

当前全球洞察前端仍明确使用演示数据。本次提供真实存储与调用能力，没有将演示页面改成实时资讯、没有实现 SSE/WebSocket，也没有自动采集、评分或推荐任务。

## 运维和验证

部署需执行 `python backend/manage.py migrate`，新增迁移 `sales.0007_world_insights` 只创建两个私有数据表。现有部署脚本已包含 migrate。普通资料与文件工具沿用既有表，无新的第三方密钥要求。

`tests.integration.test_support_tools` 覆盖真实 HTTP + PostgreSQL CRUD、跨账号隔离、幂等、版本冲突、文件引用保护、分块读取、授权模板；没有连接真实外部数据源。`browser_onboarding.cjs` 使用模拟 API 和真实 Chrome 验证编辑保留关联。原 SDK 的 MCP stdio 测试使用本机 HTTP fixture，协议通过不代表已验证线上账号或外部发信。
