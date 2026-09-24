# 公共新闻销售线索接口

本契约适配 `WORLD_INSIGHTS_SIGNAL_BACKEND_HANDOFF.md` 的单篇新闻、单组公司线索与单组金额。字段保存于 `WorldNews`，不调用 `opportunity_signals.create`，不创建 CRM 公司、联系人或商机，也不按公司名称关联私人记录。来源金额不能解释为卖方订单金额。

## 字段

以下字段全部可省略。创建时文本默认 `""`，金额默认 `null`；旧记录经 `sales.0010_news_signal_fields` 迁移后使用同样的空值，不解析原正文或自动回填。

| 字段 | 类型与限制 | 语义 |
| --- | --- | --- |
| `company_name` | string，最多 240 字符 | 来源中的公司或机构 |
| `signal_type` | string，可空 | expansion / new_factory / tender / equipment_upgrade / procurement / other |
| `project_name` | string，最多 240 字符 | 来源项目名 |
| `demand_description` | string，最多 500 字符 | 新闻披露的需求 |
| `potential_sales_need` | string，最多 500 字符 | 潜在采购推断，不是已确认采购 |
| `opportunity_reason` | string，最多 500 字符 | 潜在需求与产品的相关性解释 |
| `time_window` | string，最多 240 字符 | 来源中的时间节点，不自动转换为日期 |
| `evidence` | string，最多 600 字符 | 支撑主体和事件的公开原文，保留空白 |
| `amount` | 非负 decimal string 或 null；24 位整数、6 位小数 | 币种主单位金额，不接受 JSON 数字、负数、科学计数法或超限值 |
| `currency` | string，可空 | CNY / USD / EUR / GBP / JPY / KRW / SGD / TWD / HKD / INR / CAD / AUD / CHF |
| `amount_type` | string，可空 | total_investment / procurement_budget / tender_amount / contract_amount / other |
| `amount_scope` | string，可空 | whole_project / equipment_procurement / other |
| `amount_evidence` | string，最多 400 字符 | 金额原文，必须逐字包含在同一条 evidence 中 |

金额范围由用户明确确认：最多 24 位整数与 6 位小数。超限返回 400，不静默舍入；返回值是固定 6 位小数的字符串，例如 `"50000000.000000"`，未知为 `null`。零是合法已知值。

提供 `amount` 时，币种、类型、范围、金额证据四字段都必须非空；`amount=null` 时四字段必须全空。PATCH 校验的是旧记录与补丁合并后的完整状态。清空金额必须在同一次更新中显式清空四字段，不隐式代替调用方删除证据。

后端验证枚举、长度、金额组合和证据包含关系；不重新抓取外站，包含关系不代表对外部新闻真实性的独立核实。日志只记录校验位置、字段名和原有写入回执，不打印原文或令牌。

## 调用与读取

- 沿用 `world_news.create` / `world_news.update`，新增字段放在 `arguments.data` 内；正式模式下写入仍需 Tool 凭证及幂等键，update 使用现有 revision。
- REST 创建沿用 `/api/v1/sales/records/world-news/`；详情更新为该路径下的 `<id>/`，正文只含可写字段，版本放 `If-Match` 请求头。
- 列表、详情、`world_news.list` 和 `world_news.get` 返回全部新增字段。其他员工可读公开线索，写权限、来源去重和归档规则不变。
- Agent 可从 `/api/v1/agent-tools/catalog/` 发现实际 `inputSchema`，其枚举接受空字符串，金额只接受字符串或 null，仍拒绝未知字段。通用 REST OpenAPI 原本以动态 resource + object 描述；本次不另建重复路由，其详细字段以工具目录和此契约为准。
- 每篇新闻只接收一组信息，不接受多公司或多金额数组。若将来扩展为一对多，应另行协商契约。

以下为 `arguments.data` 中新增字段的示例，需与原有标题、分类、发布时间、来源及正文合并；示例不是实际采集结果：

```json
{
  "company_name": "示例制造公司",
  "signal_type": "new_factory",
  "project_name": "示例生产基地",
  "demand_description": "建设新的生产线",
  "potential_sales_need": "新产线可能需要检测设备",
  "opportunity_reason": "潜在检测环节可能与现有产品相关，仍需核实",
  "time_window": "2027 年投产",
  "evidence": "示例制造公司宣布建设示例生产基地，总投资人民币 5000 万元，计划 2027 年投产。",
  "amount": "50000000",
  "currency": "CNY",
  "amount_type": "total_investment",
  "amount_scope": "whole_project",
  "amount_evidence": "总投资人民币 5000 万元"
}
```

## 页面与发布

新闻卡片显示主体、事件及来源金额口径；详情分别展示来源事实、原文证据、金额类型/范围及“需求推断 · 非已确认采购需求”。金额使用字符串千位分组，仅移除无意义的小数末尾零，不经过浮点运算，不截断有效小数、不换汇。缺失结构化数据与已知零值分别显示。

这些新闻字段不进入活动地图的 `map_amounts`，也不增加内部商机总额。新闻没有城市坐标，不伪造地图位置。

先完成数据库迁移及接口部署，再部署协作方对应版本的 Agent。后端适配过程中不改 Agent 文件或来源、模型参数。确认新旧载荷、权限与实际工具回执后再验证采集；本文不等于线上发布或真实采集验收记录。已有来源会被 Agent 跳过，旧文章需另行授权回填。

## 检查

`tests.integration.test_news_signals` 在隔离 PostgreSQL 库验收完整/空/旧载荷、24+6 位精度、证据组合、部分更新、Tool 目录、共享写隔离、数据库约束及迁移。已有共享资讯与工具测试继续验收来源并发冲突和展会兼容。

`node backend/tools/browser_world_map.cjs` 使用模拟 API 和实际页面验收公司、事实/推断、金额口径、精确字符、HTML 转义、零/未知值、旧新闻及手机布局；不代表真实新闻或更新后的 Agent 已完成联调。

## 显式刷新旧新闻

维护者可以在生产备份完成后，对已明确授权的新闻 UUID 执行：

```bash
python backend/manage.py refresh_world_news_signals NEWS_UUID [NEWS_UUID ...] --apply
```

省略 `--apply` 只预览。目标必须全部为未归档的 Agent 新闻；不支持全表隐式刷新。命令重新读取原来源页面，并直接调用现有 Agent 的 `summarize_news`，沿用模型、提示词及校验参数，旧生成摘要不作为来源证据。只更新十三个公开线索字段，不改标题、正文、时间、来源，不关联 CRM；旧 revision 检查保护并发编辑，相同字段不重复写入。

来源或 Agent 提取失败时保留该条旧记录，输出失败原因并退出非零；已完成的其他条目保留，不自动重试或降级。Agent 没有提取出合格金额时仍保存 null，不能通过放宽证据规则填造数值。该命令与普通采集分别运行，普通采集的来源去重行为不变。
