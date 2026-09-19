# 历史事实升级：白盒测试记录

本次只补充测试和说明；未改变业务逻辑、模型、评分权重或实验数据。测试文件为 `tests/integration/test_extraction_upgrades.py`，在原有 7 项基础上新增 9 项，共 16 项。

## 测试与实现分支的对应关系

| 新增用例 | 目标分支/不变量 | 验证手段 |
| --- | --- | --- |
| test_concurrent_queue_serializes_revision | 公司行锁、If-Match、单次排队与 revision 推进 | 两个线程、独立 PostgreSQL 连接和 APIClient，屏障同步；要求恰好一个 202、一个 409 |
| test_queue_rolls_back_partial_batch | 多邮件事务中途失败、旧 failed 状态恢复 | 两次真实 request_repair 写入后注入异常；核对任务、失败状态、revision 和 Job 完全回滚 |
| test_queue_rolls_back_revision_and_analysis | revision 已保存且分析调度后发生异常 | 原 schedule_analysis 执行后注入故障；核对修复、公司和分析队列一起回滚 |
| test_expired_lease_rejects_result_without_retry | lease_until 等于当前时刻；完成拒绝与过期清理 | 固定时钟于真实租约截止点；核对 Conflict、failed/error 和无自动重试 |
| test_replaced_source_rejects_result | 新旧抽取身份不一致 | 保持其他条件有效，仅注入新来源，单独验证旧完成结果拒绝 |
| test_duplicate_completion_has_no_side_effects | 已完成状态再次提交 | 检查不重复保存抽取、不再次推进 revision、不变更 Job |
| test_mixed_batch_blocks_until_all_repairs_complete | 新旧版本、待复核邮件、部分成功及失败 | 真实队列和 Worker，模型替身；仅目标旧业务来源升级，明确重试后才解锁分析 |
| test_latest_version_and_empty_batch_are_noops | 空循环、跳过当前版本、仅统计最新抽取 | 检查零创建/复用、旧历史不重复统计、revision/Job 不变 |
| test_upgrade_http_rejects_missing_version_and_payload | HTTP 缺失版本、非空请求参数 | 两类请求返回既有 400，且无数据库副作用 |

## 执行与边界

定向命令（在仓库根目录使用项目虚拟环境）：

```powershell
.\.venv\Scripts\python.exe backend/manage.py test tests.integration.test_extraction_upgrades --keepdb --noinput
```

定向 16 项全部通过。随后一次性完整运行 194 项后端测试，120.572 秒全部通过，包含本次新增用例及 OpenAPI 契约；沿用 CI 的 QQ 专项排除范围。日志分别位于仓库忽略的 artifacts/extraction-whitebox-tests.log 和 artifacts/backend-whitebox-full-tests.log。

Python 文档结构检查覆盖 166 文件，变更检查 0 错误、0 待复核；已人工核对本次测试注释与文件顶部目录。git diff --check 通过。改动尚未提交或部署，未声称验证 Git 提交原子性。

真实数据库事务和锁参与测试；仅外部模型、时钟和指定异常注入点被替换，不访问真实邮箱或模型，不更改本地 demo/tst1 数据。线程结束关闭独立连接；并发场景验证本次交错下的互斥，不声称穷尽所有线程调度。

没有配置行/分支覆盖率阈值，也没有量化分支覆盖率报告；当前项目虚拟环境未安装 coverage。本次使用上述逐分支测试矩阵说明覆盖范围，不据测试通过宣称 100% 分支覆盖，也未为测试安装新依赖。

首轮新增 HTTP 边界测试曾将缺少 If-Match 的预期误设为 409；根据既有 check_version 契约（缺失/格式错误 400，版本过期 409）修正测试后通过，生产行为未调整。
