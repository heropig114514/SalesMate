# 从规则占位切换到 Agent

通信基准是工作区上一层 README.md v1.11。核心 JSON 对象不改名；HTTP 路径、版本与领取凭证扩展见 [api-contract.md](api-contract.md)。

## 规则占位

`ANALYSIS_PROVIDER=rules` 是本轮显式开发默认值。页面模拟来信生成 EmailSubmission，通过正式入库、Job 领取、L2/L3/L4 校验与保存服务执行。规则不会在 Agent 故障时自动启动。

| 版本 | 行为 |
|---|---|
| rules-extract-v1 | 只提取中文标签行并保留逐字 evidence；采购关键词提供粗略单封意向 |
| rules-merge-v1 | 保存全部事实历史，计算输入哈希、计数和同线程响应间隔 |
| rules-analysis-v1 | 将已有事实映射到三维画像、四维分析；不生成无依据推断、外部新闻或冲突判断 |
| rules-score-v1 | 三个特征有对应明确字段时各取 1，否则 unknown；全部已知且信号已知时等权归一为 33 分，否则 null |

评分只用于验证字段、排序、空值与说明展示，不代表正式优先级质量；不采用或改动 README 正式 score-v1 的待校准权重。规则版本与正式模型版本独立。合成邮件 source=synthetic_sample，研究数据库和实验条件未修改。

## 接入顺序

1. 为目标业务用户配置独立后端服务凭证。当前本地初始化将开发账号和服务令牌写入私有 backend/.local-access.json，文件被 Git 排除；不要将这个文件公开分享。
2. 前端与 Agent 单独实现 Gmail OAuth、邮箱所有权核验和 sync_gmail(GmailAuthorization)。当前网页没有假同步按钮，后端不保存 Gmail 授权。
3. Agent 读取 SyncState，读取 Gmail，提交 EmailSubmission[]；全部邮件持久化成功后更新游标。
4. POST jobs/claim，显式传 limit、lease_seconds，保存返回的 lease_token 和 expected_version。
5. 读取 Grouping，再带其 ETag 读取 CompanyContext。发现与任务 expected_version 不同，旧任务不得写入；显式回报失败并处理后端已有的新任务。
6. 构建、提交 AnalysisInput；带 analysis_prompt_version 查询 CachedAnalysis。
7. 未命中则执行真实分析与评分，保存 Analysis、Score，最后回报 JobReport。结果写入均携带 If-Match、X-Job-ID、X-Lease-Token。
8. 在 backend/.env 设置 `ANALYSIS_PROVIDER=agent` 并重启后端。页面分析只入队，模拟写入入口关闭；独立 Agent 主动消费。旧规则结果仍保留来源标记，直到对应 Agent 结果完成。

规则实现集中在 apps/crm/rules.py。独立 Agent 通过 HTTP 调用，不导入 Django；替换不要求修改页面与数据库。

## 待对齐边界

- 本轮通过了 Agent HTTP 契约测试，但尚未与团队真实 Agent 进程联调，不能视为模型效果验证。
- 业务邮箱与用户隔离已实现；OAuth 主体与业务邮箱 ID 的可信绑定仍待接入。
- 租约过期显式失败，不自动续期或重派。用户可明确请求新任务；不得自行加入隐藏重试策略。
- 新邮件都会递增上下文 revision，使旧结果 stale；规则消费者全量重建。README 中纯致谢邮件只更新摘要、不重跑 L3 的精细缓存策略尚未落地，需要与 Agent 共同定义。
- 最新抽取版本作为当前分析输入，旧版本保留审计。失败查询针对当前抽取。
- 工单、报价、订单快照容器已预留，当前为空且无编辑入口，不把邮件提及当作已发生交易。
- 后端不维护真实模型、提示词参数或正式评分权重，不更改实验划分、阈值或随机种子。

首次联调建议用一家公司两三封邮件，覆盖重复提交、预算历史保留、失败补交、旧版本拒绝、未知分数和提示词版本缓存不命中。
