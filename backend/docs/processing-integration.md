# 邮件处理与复核适配

依据 2026-09-13 的需求及后续用户决策实施。Web 只排队，独立 Worker 调用 Agent；保留既有评分和模型参数。历史补采每页 20 封直至扫描完成，后续使用 History 增量。当前 Agent 从 `customer-analysis` Skill 读取 `analysis-v3`，后端按请求中的版本保存和查询缓存，无需固定分析版本配置。

## 已实现

| 需求 | 实现 |
|---|---|
| 持久批次和逐封状态 | MailboxSyncRun、EmailProcessingJob；发现时登记，回报 fetching/extracting/persisting/completed/failed |
| 快速返回 | request-sync 返回 202，附 run_id、queued 和原邮箱字段；重复点击复用活动批次 |
| 失败隔离 | Worker 启用 Agent 观察回调，单封 Gmail 读取、L1 或提交失败独立记录 |
| 独立调度 | crm_worker：一个同步通道，默认两个画像通道；L1 保留四路 |
| 公司互斥 | 复用 Job，所有者行锁串行化领取，同公司运行时后继等待，其他公司可领取 |
| 整体进度 | 批次接口从数据库任务派生计数，画像统计覆盖关联公司，不受前端分页影响 |
| 非业务与复核 | 规则跳过默认隐藏；v7 无采购阶段的入站邮件进入待复核；人工决定优先 |
| 一致查询 | 收件箱、统计、详情邮件和 Agent 上下文使用同一业务分类；业务管理客户目录保持独立 |
| 历史处理 | classify_emails 默认预览，--apply 应用；原文、抽取、交易和人工决定保留 |
| 游标完整性 | 游标过期重新扫描时仍保留已有 pending/failed ID；显式重试只选择失败范围 |
| 原文与 L1 缓存 | StoredMessage 先保存原文，再保存已完成的 L1 输出；HTTP 提交失败重试不重复调用模型 |
| 血缘自动修正 | SnapshotSource 记录邮件、抽取、复核版本；失效沿 L2→L3→L4 传播，有剩余业务来源则自动重算 |

所有逐封计数由任务表派生，避免维护多个可漂移的累加字段。邮件本体已落库但事实抽取失败时，该邮件任务仍是 failed；批次有成功和失败为 partial，全部失败为 failed。

## 启动与升级

在仓库根目录，使用现有 Python 环境和 PostgreSQL：

```powershell
python backend/manage.py migrate
python backend/manage.py classify_emails
python backend/manage.py classify_emails --apply
```

HTTP 后端运行后，在独立终端启动：

```powershell
python backend/manage.py crm_worker --analysis-workers 2 --poll 1
```

`--once` 排空当前可领取队列后退出，会实际调用 Gmail 与百炼，不能用于纯只读检查。Worker 要求 `ANALYSIS_PROVIDER=agent`；规则演示仍由页面的既有 rules 入口执行，不作为 Agent 失败回退。此次没有改变现有 provider。

Web 内的旧调度线程及其开关已移除。共享 Worker 从数据库轮转调度所有有效员工；每个工作单元使用独立临时凭证和客户端，执行后撤销凭证，员工无需手工绑定进程。代码不创建操作系统服务，也不在 Web 启动时创建子进程。`sales_worker` 继续只负责已确认的发信/日历动作与跟进提醒。

## 状态、恢复和兼容

- queued/pending 数据跨 Web 和 Worker 重启保留。Web 重启时，执行单元若遭遇 HTTP 连接失败，会留下可见失败记录；重新启动服务后由员工明确重试。
- 邮箱批次租约为 600 秒，阶段事件续期。硬中断遗留的 running 在租约过期后标为 failed，原逐封状态保留；页面重试仅为失败邮件创建新批次，不隐式重试。
- 公司 Job 保留既定租约、revision 和失败语义；旧结果不能覆盖新上下文。一个公司运行期间的多次更新合并到待办后继。
- 新 Worker 的最终回报校验 run_id、租约和状态。旧 Agent CLI 的 mailbox-syncs 领取/回报接口保留用于迁移；其回报仍按当前邮箱运行批次匹配，缺少执行者身份的旧协议不要与新 Worker 混用同一邮箱。
- Agent 可选 `progress` 回调仅传 ID、阶段及受控代码。回调失败向上报告。旧 CLI 未启用回调时仍保持原调用行为，完整逐封读取隔离请使用 crm_worker。
- Worker 的 SyncCheckpoint 与消息发现记录在同一数据库事务提交；游标只在 ID 已持久登记后推进。数据库或网络失败不会被当作首次同步。旧 CLI 已支持的游标协议也改为异常向上报告；Worker 接管邮箱后，后端拒绝旧 CLI 修改该邮箱游标，避免双写。

## 接口

| 接口 | 行为 |
|---|---|
| POST `/api/v1/mailboxes/{mailbox_id}/request-sync/` | 返回 202 和批次；无需等待模型 |
| GET `/api/v1/mailbox-sync-runs/{run_id}/` | 邮件进度、公司分析进度、逐封安全错误 |
| POST `/api/v1/mailbox-sync-runs/{run_id}/` | 明确重试 failed/partial 批次，返回新批次 202 |
| GET `/api/v1/email-reviews/?status=pending&page=1` | 当前员工全部邮箱复核分页 |
| GET `/api/v1/mailboxes/{mailbox_id}/email-reviews/` | 单邮箱复核分页 |
| PATCH `/api/v1/email-reviews/{email_id}/` | review_status 为 confirmed_business/confirmed_non_business；携带 If-Match 复核 revision |

复核列表支持 `status=non_business` 查看规则隐藏邮件，`status=all` 查看隐藏、待复核和人工决定。新增 `status=saved` 包含该范围全部已入库邮件（也包含未经人工复核的业务邮件），供 QQ 账号的「查看已同步邮件」入口核对原文；响应中的 `source`、`received_at` 和 `classification` 显示邮件来源、接收时间与分类。查询不修改分类。原始 email_id 在 URL 中应编码；不同员工访问返回同样的 404。所有写入使用 Session/CSRF，不能自报 owner。

## 已统一的规则与持久化边界

1. `extract-v7` 中 `intent_hint=null` 的入站邮件进入人工复核，包括有实质更新的情况；`null` 不直接等同于非业务，员工可以确认业务或非业务。规则阶段直接跳过的邮件仍在非业务筛选下可纠正。
2. 人工确认尚未完成 L1 的邮件时创建 `ExtractionRepair`。Worker 从持久正文补抽取，使用原 L1 提示词，跳过已被人工否定的自动邮件过滤。旧抽取和原文保留，新抽取以 `repair_generation` 标记代次，不伪造新的提示词版本。此公司的画像等待 L1 修复；失败在“全部”复核列表显示，再次点击“确认业务”才重试。
3. 分类、抽取变化沿 `邮件 → Extraction → AnalysisInput → Analysis → Score` 失效。旧结果保留用于追溯，展示与缓存立即排除；仍有业务邮件时自动合并一个公司重算任务，无业务邮件时停止待办。运行中的旧 revision 回报被拒绝。人工变更决定会撤销未完成补抽取，过时模型结果不能写回。
4. 原文保存到 `StoredMessage.raw`，包括 Gmail 解析出的头、正文及 eligible body；LLM 输出保存到 `submission` 后才调用业务提交接口。已经落库且同提示词版本的终态不重复拉原文、不重复 L1。失败 L1 复用原文，失败 HTTP 提交复用 L1 输出。批量 ORM 查询替代每页逐封 HTTP 查询，仍沿用原业务写入 API。
5. `SyncCheckpoint` 保存历史页位置、扫描前 History 锚点及增量游标。每页 20 封，不再把 20 封当作完整历史上限；查询范围仍为 inbox/sent。历史完成后补扫扫描期间的新消息。History 404 才重新完整扫描，已缓存邮件和旧 pending/failed ID 不清空；其他分页错误明确失败，重试从已保存页继续。
6. L1 只处理新增、缺失或明确重试的邮件。L2 仍从数据库读取该公司完整有效上下文，L3 对受影响公司整体生成新画像，L4 使用既定规则计算；没有把 LLM 改成只看最后一封邮件。人工维护的客户资料、报价、订单等权威记录不会因邮件误判而自动删除或改写。

新快照保存精确关系血缘；历史快照只能按其已有 `member_dedupe_keys` 识别依赖，不能倒填当时未知的抽取代次。分析快照唯一键扩展到公司、input_version 和 revision，支持分类撤销后恢复相同内容并保留各代记录。升级使用 `crm.0006_durable_lineage`，再执行历史分类预览及应用。已有人工决定不会被覆盖。

Gmail 当前 History 流仍只消费 messageAdded；Gmail 中删除邮件或更改标签不会自动删除本地业务档案。原文缓存不等于附件二进制归档。已发现但尚未开始的任务可继续处理；已经失败的任务需要明确重试。恢复和增量依赖 Worker 运行，Web 重启不会自动启动 Worker。

## 验证边界

2026-09-13 血缘与持久化补强验证：本机完整 Django 测试 96 项通过（含原有 3 项本地演示测试）；Agent 离线测试 120 项通过。浏览器处理页检查覆盖补抽取失败说明、版本化确认、明确重试、转义和移动布局；JS 语法、迁移一致性及接口 Schema 回归通过。后端 95 个 Python 文件注释结构检查通过，变更检查 0 错误、0 待复核；Agent 修改文件单独通过检查，并人工核对行为与说明。测试数据库正常销毁。

本机已应用 `crm.0006_durable_lineage`，历史分类预览、应用及再次预览均为 0 项变化；Web 重启后页面 HTTP 200、数据库就绪检查正常。本轮尚未提交 Git，也未启动真实 Gmail/LLM 队列消费；未验证真实模型效果、实际 Gmail 分页 token 寿命或生产规模吞吐。旧历史快照没有伪造精确来源版本。

测试使用隔离 PostgreSQL 数据库和模拟 Gmail/模型，覆盖复核权限、版本冲突、人工优先、批次合并、逐封失败、租约过期、公司互斥及完整 Worker 回调持久化。不将模拟外部服务成功解释为真实授权、模型质量或生产恢复已经验收。提交状态与本轮实际检查结果以交付说明为准。

2026-09-13 初次实现检查（下列为历史验证记录，后续发布检查见 [结果逐步展示](live-results.md)）：

- 已合入远程 `0a4667e` 文档提交，当时功能变更保留在工作区，尚未提交；本次发布另合入 `19abc8b`。
- Django 隔离 PostgreSQL 测试 84 项、Agent 离线测试 141 项通过；浏览器模拟接口验证复核、版本头、转义、失败重试及移动端布局通过。
- Schema 生成校验、迁移一致性、Django 系统检查、JS 语法检查通过；Python 注释结构检查覆盖后端 90 个文件及改动的两个 Agent 文件，后端差分检查 0 错误、0 待复核，并人工核对相关语义。
- 实际管理入口 `crm_worker --help` 已验证；没有启动真实队列消费，也没有进行真实 Gmail/百炼联调或生产压力、进程崩溃验收。
- 本机已应用 `crm.0005_persistent_processing`；历史分类更新 21 封，再次预览 0 项差异。邮件原文及人工决定保留，21 是元数据变化数，不等于新增隐藏邮件数。
- 当前 Python 环境未安装 Ruff，未完成该项静态检查；未改动检查器，也未声称验证 Git 提交原子性。
