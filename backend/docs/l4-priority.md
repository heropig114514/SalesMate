# 公司级 L4 后端适配

本次实现对接 Agent `score-v2`，保留公司级单一当前分数、L1→L4 流程、Agent HTTP 协议和员工邮件隔离。分数由 Agent 计算，后端提供权威输入、验证结果、保存解释并投影查询；不在后端复制评分算法。

## 1. 已确认的数据口径

- 销售方资料、产品目录、商机及订单统计均按业务 `owner` 隔离，不把团队共享权限等价为私人邮箱或全组织统计权限。
- 活跃商机是同客户未归档的 `new`、`qualified`、`proposal`。只汇总 `Opportunity`，不叠加报价。全部金额已知且同币种才求和；混币种、部分金额未知或总额不大于零时不输出 `deal_value`。
- 商机产品使用显式填写的 `product_names`；任一活跃商机产品未知时，公司级产品也保持未知。名称去首尾空白、忽略大小写去重，不做同义词推断。
- 历史均值来自该 owner 全部未归档 `confirmed`/`fulfilled` 订单，每单一次，按商机币种筛选。复用订单明细的 Decimal 净额算法，不统计草稿、取消订单或其他币种，不将报价或商机 won 再次计入。无明细的历史订单保持金额未知；同币种存在此类未知样本时不输出均值。不额外将均值截为整数或两位小数。
- 相似赢单要求客户行业相同且至少一个相同规范产品。没有可靠历史、当前行业/产品未知，或缺失历史资料且没有已证实匹配时不输出布尔值。资料完整且无匹配才返回 `false`。
- 国家、行业、人数来自权威客户资料，不从邮件补造。销售方目标资料由显式维护接口提供。
- 列表按分数降序、紧急度贡献降序、公司 ID 升序排序；空分最后。

## 2. 资料维护入口

### 销售方画像

`GET /api/v1/sales/seller-profile/` 返回当前登录员工自己的配置：

```json
{"revision": 0, "profile": {}}
```

响应附带 `ETag`。不存在配置时 GET 不创建记录，不补默认目标客户。

`PATCH /api/v1/sales/seller-profile/` 携带 `If-Match`，仅合并明确提交字段：

```json
{
  "target_industries": ["Manufacturing"],
  "target_company_size": {"min": 20, "max": 500},
  "service_regions": ["Singapore"],
  "time_zone": "Asia/Singapore"
}
```

目标范围必须完整且非负，时区必须是有效 IANA 名称。空数组或允许的 `null` 清除资料；字段省略保留原值。均值、相似赢单、产品清单和 owner 不能从该接口写入。产品清单来自现有产品目录。配置发生实际变化时，同一事务递增画像版本、相关公司两个版本并合并 `external_updated` 待办。

### 客户与商机

- 现有 `POST /api/v1/companies/{id}/register/` 增加可选 `country`；省略保留原值，`null` 清除。人数非空仍要求来源。
- 现有 `/api/v1/sales/records/opportunities/` 创建和更新接口增加 `product_names`，例如 `["WMS", "OHT"]`。`null` 或 `[]` 表示未知；不从商机描述猜测产品。
- 产品目录、订单、商机仍使用原有权限、状态机和 If-Match 协议。

## 3. Agent 上下文

`GET /api/v1/agent/context/?company_id=...` 保留原有字段、ETag 和授权范围，新增：

```json
{
  "priority_context": {
    "customer": {
      "customer_id": "公司 UUID",
      "company_name": "Example",
      "industry": "Manufacturing",
      "company_size": 100,
      "country": "Singapore"
    },
    "deal": {"status": "ACTIVE", "currency": "SGD", "deal_value": "250000.00", "product": ["WMS"], "stage": "proposal"},
    "seller": {
      "target_industries": ["Manufacturing"],
      "target_company_size": {"min": 20, "max": 500},
      "service_regions": ["Singapore"],
      "time_zone": "Asia/Singapore",
      "products": ["WMS"],
      "average_deal_value": "30000.00",
      "average_deal_currency": "SGD",
      "similar_won_deals": true
    }
  }
}
```

这是结构示例，真实资料不足时缺失相应字段。后端不重复传 `communications`，由 Agent 使用现有邮件生成。该上下文不插入 L2 的 `business_context` 或改变 L2 提交字段。

商机变化刷新当前公司；订单和产品变化传播到同 owner 的其他公司；客户行业变化及公司合并也传播相似赢单依赖。写入事务保持 `revision`、`external_version` 和任务一致，旧版本 Agent 写入返回冲突。待领取任务沿用现有合并机制，不增加自动失败重试。

当前共享依赖采用保守的同 owner 全量版本更新，包含可能尚无业务邮件的公司；规模增大后应依据依赖关系缩小受影响范围。外部版本变化会改变 L2 输入版本，当前 Agent 的 L3 缓存也会失效。

## 4. 评分与解释提交

`POST /api/v1/agent/scores/` 保留现有版本和租约头，新增可选 `score_details`。`score-v2` 非空结果必须有且仅有三项唯一整数贡献：`urgency`、`buying_intent`、`opportunity_value`，合计严格等于 `score`。不再检查旧 L3 特征是否齐全。

解释对象包含 `score_breakdown`、`top_reasons`、`evidence`、`recommended_next_action`。分项取 0–100 整数，解释贡献与主评分一致，原因最多三项且按影响降序。邮件原因必须提供来源并可定位原文；`DEAL_VALUE` 原因的 `source_id` 必须为 `null`。

空分若提交解释，只接受：

```json
{"score_breakdown": null, "top_reasons": [], "evidence": [], "recommended_next_action": null}
```

后端在当前版本与租约内验证邮件属于当前公司、属于对应 L2 成员且仍为业务邮件，再按 Agent 的空白归一规则验证原文。分数与解释一起存入现有 `Score.payload`，列表使用现有 `Score.value`，不新增第二个分数字段。同版本同 `scored_at` 的重复请求必须内容完全一致；不能事后覆盖已保存解释。

公司详情现有 `score_detail` 是完整评分载荷，解释读取路径为 **`score_detail.score_details`**。邮件定位键为 `source_id == dedupe_key`，可在详情的授权 `context.emails` 中查找。原有血缘失效及邮件可见范围检查继续生效。

**Agent 尚需配合**：当前 `analyze_company()` 仅把 `score` 传入 `backend.save_score()`。请将同一次计算的 `score_details` 作为该评分载荷的可选字段一起提交，不新增第二次写入。后端已接收、验证、保存并通过详情返回；本次未修改 Agent 编排或前端展示。

## 5. 上线与后续范围

- 应用 `sales.0006_l4_priority_context`：创建 SellerProfile，并给商机添加可空 `product_names`。历史资料保持未知，不自动生成测试业务记录。
- 生产继续显式使用 `ANALYSIS_PROVIDER=agent`；代码没有修改已有运行配置。正式模式页面仅取 `score-v2`，旧分数保留在数据库但不冒充正式结果；未重新评分时页面分数为空。
- 维护真实客户、商机产品及销售方资料后，通过既有分析任务重新评分。解释完整展示需 Agent 与前端完成上述对接。
- 定时 `priority_refresh`、独立 Signal 生命周期、自动外部操作不在此次后端适配范围；没有为时间流逝伪造业务版本。

## 6. 验证

在 `backend/` 使用项目虚拟环境运行：

```text
python manage.py test tests.integration.test_priority --noinput
python manage.py test tests --noinput
python manage.py makemigrations --check --dry-run
python tools/check_docs.py
python tools/check_doc_changes.py
```

数据库应具备项目已有的 PostgreSQL/pgvector 依赖。普通测试角色没有安装扩展权限时，可由数据库管理员仅在独立测试库预安装扩展，再以 `--keepdb` 使用该库；不要提升应用账号权限或跳过迁移。当前 CI 使用 pgvector 0.8.2。

测试使用合成记录和结构化信号，不调用真实模型或邮箱。新增覆盖包括同币种逐单统计、权限隔离、商机变更、共享依赖传播、旧 L3 特征缺失时保存新结果、解释及原文校验、幂等、空分、旧版本冲突和列表排序。

2026-09-19 本地验证：在独立 PostgreSQL 测试库预安装与 CI 一致的 pgvector 0.8.2 后，使用 `--keepdb` 运行新增 10 项测试及完整后端 198 项测试，均通过。OpenAPI 契约测试通过；模型迁移检查无差异；162 个 Python 文件注释结构检查通过，变更检查为 0 错误、0 待复核，并人工核对本次变更说明。新迁移的操作均满足现有保守发布门禁；该操作级检查不等于已核验生产迁移计划。未部署，未在业务数据库应用迁移，未进行真实模型或前端联调。
