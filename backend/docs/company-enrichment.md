# 公司分析的实验资料补充

公司分析 Worker 直接沿用 `Authorization: Agent`，无需另行申请员工 Tool 令牌，也不依赖聊天 request_id。新账号和已有账号执行自己公司的分析时，都能获得来自批准共享批次的匹配资料。批准范围仍为 `KGSEED_20260921_01`，原归属不变。

## 对接方式

1. 通过既有 `GET /api/v1/agent/grouping/?company_id=...` 取得公司信息和 ETag。
2. 使用相同 Agent 凭证及 `If-Match: <ETag>` 调用 `GET /api/v1/agent/context/?company_id=...`。
3. 响应新增 `company_enrichment`。直接复制到 L2 的 `business_context.company_enrichment`，不必再分页搜索、申请读取回执或在 Agent 重做实体匹配。
4. 使用 `integrations.company_enrichment.input_version(emails, merge_version, external_snapshot_version, company_enrichment)` 计算输入版本，再通过原 `analysis-inputs/`、`analyses/`、`scores/` 保存。任务租约头沿用原契约。

仓库中的 Agent 已完成这些步骤。外部算法若仍需自行探索任意实验表，继续使用原 `experiments.*` Tool API 或 MCP；它们仍使用各账号的 Tool 凭证。MCP bridge 最终调用同一 HTTP Tool API。本次公司分析补充通过原 Agent HTTP context 接口完成，没有新增 MCP 工具。

匹配成功的补充对象示意：

```json
{
  "status": "matched",
  "match_basis": "exact_domain",
  "source": {
    "source_id": "experiment:KGSEED_20260921_01:crm.Company:<原始主键>",
    "batch": "KGSEED_20260921_01",
    "model": "crm.Company",
    "record_pk": "<原始主键>",
    "synthetic": true,
    "owner": {"id": 8, "username": "tst1"},
    "fingerprint": "<当前清单指纹>"
  },
  "facts": {"employee_count": 81, "industry": "精密量测"},
  "enrichment_version": "sha256:<内容摘要>"
}
```

`enrichment_version` 位于补充对象内，不新增数据库列或 L2 顶层必填字段。摘要使用 UTF-8 JSON，`ensure_ascii=False`、`sort_keys=True`、`separators=(',', ':')`，对补充对象去掉 `enrichment_version` 后计算 SHA-256。

新 `input_version` 同样摘要化 `[邮件身份列表, merge_version, external_snapshot_version, 完整补充对象]`；邮件身份列表按 `[dedupe_key, extract_prompt_version, extract_status]` 排序。CRM 的 `external_snapshot_version` 含义不变。旧客户端不传补充字段时仍使用原契约，但不能引用未登记的实验来源。

## 匹配与错误状态

后端复用实验工具的清单和当前行指纹核验，扫描批准批次的公司表，并集中完成一次唯一匹配：

- 优先匹配完整域名，忽略首尾空白、大小写和结尾的点。子域和子串不算匹配。
- 没有域名匹配时，仅允许带批准批次标记的公司全名匹配。双方都有不相交域名时，不按名称强行合并。
- 唯一候选为 `matched`；多个候选为 `ambiguous`；没有候选为 `not_found`。
- 批次缺失或已清理为 `unavailable / batch_unavailable`；清单或行指纹异常为 `unavailable / integrity_error`，服务日志记录公司、批次与原因。
- 非 matched 状态均不附来源和事实。明确不可用状态可以进入 L2，使没有补充资料的分析继续；未知数据库或程序异常不静默忽略。

共享的是批准清单内的资料。读取分析目标仍需该公司的原 Agent 权限；不因此获得其他员工普通私有公司的分析、邮件或凭证权限。

## 保存、引用和缓存

L2 保存时后端重新取得当前匹配对象，以一次业务上下文比较验证内容和来源，同时核对版本。读取后资料发生变化则返回 409，调用方需重新读取，不自动重试。无需 Agent 分别验证批次、owner、清单、哈希或字段。

L3 的 `analysis-v4` 允许引用该 L2 实际登记的 `source.source_id`。CRM 人数存在时优先使用；缺失时可以使用 matched 实验人数，`size_source` 固定为 `synthetic_sample`。行业补充也必须注明虚构实验来源。补充资料不写回 CRM，不进入邮件 L1 事实；`extract-v7`、人数档位阈值、模型预算和 L4 公式均不变。

保存 L3、保存评分、缓存命中、当前画像投影及最新 L2 查询都会检查实验资料是否仍一致。来源修改、删除、批准撤销、出现歧义或新增匹配使旧输入失去当前效力；历史记录保留，页面不再显示其画像，缓存返回 miss。重新分析由既有显式任务触发，不自动批量重算。

## 验证范围

`backend/tests/integration/test_company_enrichment.py` 使用隔离 PostgreSQL，覆盖新账号 Agent 凭证跨账号补全、篡改、同名和子域反例、歧义、CRM 优先、引用白名单、版本变化、批准撤销及完整性错误。LiveServer 测试使用真实 DjangoBackendClient 完成 L2/L3/L4 保存和再次缓存命中；仅模型输出由确定性样例替代，不表示验证了外部 LLM 的生成质量。
