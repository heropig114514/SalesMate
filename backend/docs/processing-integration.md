# 邮件处理与复核适配

依据 2026-09-13 的 `AGENT_INTEGRATION_TASKS.md` 实施。Web 只排队，独立 Worker 调用 Agent；已有评分、模型和单轮 20 封扫描上限保持原值。当前 Agent 从 `customer-analysis` Skill 读取 `analysis-v3`，后端继续按请求中的版本保存和查询缓存，无需固定分析版本配置。

## 已实现

| 需求 | 实现 |
|---|---|
| 持久批次和逐封状态 | MailboxSyncRun、EmailProcessingJob；发现时登记，回报 fetching/extracting/persisting/completed/failed |
| 快速返回 | request-sync 返回 202，附 run_id、queued 和原邮箱字段；重复点击复用活动批次 |
| 失败隔离 | Worker 启用 Agent 观察回调，单封 Gmail 读取、L1 或提交失败独立记录 |
| 独立调度 | crm_worker：一个同步通道，默认两个画像通道；L1 保留四路 |
| 公司互斥 | 复用 Job，所有者行锁串行化领取，同公司运行时后继等待，其他公司可领取 |
| 整体进度 | 批次接口从数据库任务派生计数，画像统计覆盖关联公司，不受前端分页影响 |
| 非业务与复核 | 规则跳过默认隐藏；completed + non_sales + false 进入待复核；人工决定优先 |
| 一致查询 | 收件箱、统计、详情邮件和 Agent 上下文使用同一业务分类；业务管理客户目录保持独立 |
| 历史处理 | classify_emails 默认预览，--apply 应用；原文、抽取、交易和人工决定保留 |
| 游标完整性 | 游标过期重新扫描时仍保留已有 pending/failed ID；显式重试只选择失败范围 |

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

Web 内的旧调度线程及其开关已移除。一个 Worker 绑定当前 Agent 服务令牌所属员工；多员工分别使用各自服务凭证运行 Worker。代码不创建操作系统服务，也不在 Web 启动时创建子进程。`sales_worker` 继续只负责已确认的发信/日历动作与跟进提醒。

## 状态、恢复和兼容

- queued/pending 数据跨 Web 和 Worker 重启保留。Web 重启时，执行单元若遭遇 HTTP 连接失败，会留下可见失败记录；重新启动服务后由员工明确重试。
- 邮箱批次租约为 600 秒，阶段事件续期。硬中断遗留的 running 在租约过期后标为 failed，原逐封状态保留；页面重试仅为失败邮件创建新批次，不隐式重试。
- 公司 Job 保留既定租约、revision 和失败语义；旧结果不能覆盖新上下文。一个公司运行期间的多次更新合并到待办后继。
- 新 Worker 的最终回报校验 run_id、租约和状态。旧 Agent CLI 的 mailbox-syncs 领取/回报接口保留用于迁移；其回报仍按当前邮箱运行批次匹配，缺少执行者身份的旧协议不要与新 Worker 混用同一邮箱。
- Agent 可选 `progress` 回调仅传 ID、阶段及受控代码。回调失败向上报告。旧 CLI 未启用回调时仍保持原调用行为，完整逐封读取隔离请使用 crm_worker。
- 游标保存沿用 Agent 的乐观锁接口，活动批次身份不因保存游标被覆盖。游标读写异常的统一强失败协议留待双方后续补齐。

## 接口

| 接口 | 行为 |
|---|---|
| POST `/api/v1/mailboxes/{mailbox_id}/request-sync/` | 返回 202 和批次；无需等待模型 |
| GET `/api/v1/mailbox-sync-runs/{run_id}/` | 邮件进度、公司分析进度、逐封安全错误 |
| POST `/api/v1/mailbox-sync-runs/{run_id}/` | 明确重试 failed/partial 批次，返回新批次 202 |
| GET `/api/v1/email-reviews/?status=pending&page=1` | 当前员工全部邮箱复核分页 |
| GET `/api/v1/mailboxes/{mailbox_id}/email-reviews/` | 单邮箱复核分页 |
| PATCH `/api/v1/email-reviews/{email_id}/` | review_status 为 confirmed_business/confirmed_non_business；携带 If-Match 复核 revision |

复核列表支持 `status=non_business` 查看规则隐藏邮件，`status=all` 查看隐藏、待复核和人工决定。原始 email_id 在 URL 中应编码；不同员工访问返回同样的 404。所有写入使用 Session/CSRF，不能自报 owner。

## 按用户要求留待后续补齐的规则

1. 文档分类表仅把 `non_sales + has_substantive_update=false` 送复核，验收文字却要求所有 non_sales。当前采用表格规则，其他情况保持既有业务可见性，未扩大隐藏范围。
2. 人工确认原先 skipped_non_business 邮件后立即进入业务上下文并创建画像任务；原抽取继续标为未解析，不伪造事实。同版本跳过记录如何重新执行 L1、是否增加人工重做版本，后续单独约定。
3. 确认非业务后，不生成新画像；已引用隐藏邮件的旧画像停止展示。需要时由员工对剩余业务邮件明确重算。
4. Gmail 首次或游标失效的历史回溯范围仍为最近 20 封，范围之外的完整历史补采不在此次实现中。已持久化的待处理 ID 不会因此清空。
5. 批量查询已保存邮件属于可选性能优化，尚未增加；现有逐封查询接口继续可用。

## 验证边界

测试使用隔离 PostgreSQL 数据库和模拟 Gmail/模型，覆盖复核权限、版本冲突、人工优先、批次合并、逐封失败、租约过期、公司互斥及完整 Worker 回调持久化。不将模拟外部服务成功解释为真实授权、模型质量或生产恢复已经验收。提交状态与本轮实际检查结果以交付说明为准。

2026-09-13 初次实现检查（下列为历史验证记录，后续发布检查见 [结果逐步展示](live-results.md)）：

- 已合入远程 `0a4667e` 文档提交，当时功能变更保留在工作区，尚未提交；本次发布另合入 `19abc8b`。
- Django 隔离 PostgreSQL 测试 84 项、Agent 离线测试 141 项通过；浏览器模拟接口验证复核、版本头、转义、失败重试及移动端布局通过。
- Schema 生成校验、迁移一致性、Django 系统检查、JS 语法检查通过；Python 注释结构检查覆盖后端 90 个文件及改动的两个 Agent 文件，后端差分检查 0 错误、0 待复核，并人工核对相关语义。
- 实际管理入口 `crm_worker --help` 已验证；没有启动真实队列消费，也没有进行真实 Gmail/百炼联调或生产压力、进程崩溃验收。
- 本机已应用 `crm.0005_persistent_processing`；历史分类更新 21 封，再次预览 0 项差异。邮件原文及人工决定保留，21 是元数据变化数，不等于新增隐藏邮件数。
- 当前 Python 环境未安装 Ruff，未完成该项静态检查；未改动检查器，也未声称验证 Git 提交原子性。
