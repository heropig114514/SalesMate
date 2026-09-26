# 分析冲突、Worker 连接生命周期与新闻来源维护

版本：v1.2.2。适配 Agent 提交 `851d9e8`（analysis-v5），不改变 Job 状态枚举、租约、模型预算或重试规则。

## 分析冲突

L2/L3/L4 保存仍遵守 revision、租约、来源和不可变载荷校验。以下冲突仍返回 HTTP 409，`error.code` 细化为：

| 错误代码 | 原因 |
| --- | --- |
| `analysis_revision_changed` | 读取 revision 或任务 revision 与公司当前资料不一致 |
| `analysis_lease_required` / `analysis_lease_invalid` | 未提供完整租约或租约凭证不匹配 |
| `analysis_lease_expired` / `analysis_job_inactive` | 租约到期或任务已结束 |
| `analysis_snapshot_changed` | 当前输入快照缺失、失效或与业务上下文不一致 |
| `analysis_input_conflict` | 同一 input_version 对应不同内容 |
| `analysis_result_conflict` / `analysis_score_conflict` | 相同不可变键对应不同分析/评分载荷 |

无效版本格式仍返回原 400。其他业务 API 的通用 `conflict` 不变。Agent SDK 无需新增任务状态，可记录具体 backend_code，并在既有 failed/job_failed 报告中保留可读提示。详情页沿用 `job_error.message` 展示；历史笼统错误不自动改写。

冲突日志含阶段、公司 ID、规范化任务 ID、输入/当前 revision，以及输入版本的 SHA-256 指纹，不含租约令牌或业务正文。日志关联用于定位，不在回滚事务中额外写入业务状态。

显式重新分析继续使用现有 `POST /api/v1/companies/{id}/analyze/`。同公司 pending 任务合并，同 revision 的有效 running 任务复用；旧结果不能通过刷新 If-Match 后重复提交。新增回归验证旧结果拒绝、失败提示、重复点击去重以及后继任务按最新 revision 重建输入。

## Worker 数据库生命周期

长时间邮箱/模型 HTTP 调用期间，本地数据库连接可能已过期或失效。Worker 在外部调用后保存终态前、失败回报前，以及撤销临时身份前，调用 Django `close_old_connections()`，按原连接配置清理旧连接。

不修改 CONN_MAX_AGE，不重试邮箱、模型或失败事务。数据库仍不可达时，保持失败并记录回报失败；原租约到期机制仍负责解释未完成批次。隔离测试通过关闭本线程的真实 PostgreSQL 底层连接复现：修改前批次滞留 running，修改后正常工作可完成，失败工作可持久化 failed 并撤销临时身份。该复现不证明三条线上历史同步失败必然由同一原因造成。

## 新闻来源维护

`maintain_news_sources` 只维护明确选择的 Eurostat 指标旧/新 URL。它复用 Agent 的规范化规则，但不调用网站、模型或新闻线索刷新。

```bash
python backend/manage.py maintain_news_sources \
  86c674ff-c726-4b22-96d0-fa582efaf5c3:0 \
  49a256cb-bbfb-4639-9449-148a659c73d7:0
```

默认只预览；`ID:revision` 中的版本必须以执行前数据库为准。确认后显式加 `--apply` 才写入。命令先核对全部目标、预期 revision 和全局旧/新 URL 重复，再通过原保存服务写入并追加审计；任何错误整批回滚。实验模式下同样不允许使用旧 revision 覆盖新数据。相同规范地址重复执行不增加 revision。

来源维护不会把旧摘要当作原文，不会自动合并重复记录，也不会把系统服务 failed 状态清除当成刷新成功。需要调用模型的指定新闻刷新、客户重新分析，以及历史邮件重试，仍是独立的显式业务操作。
