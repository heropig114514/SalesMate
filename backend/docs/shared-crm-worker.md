# 多员工共享 CRM Worker

原 Worker 在启动时把环境 Agent 令牌解析为唯一员工，因此其他员工提交的批次即使
服务正常也不会被领取。现在 `crm_worker` 对所有启用员工的同步、人工补抽取和公司画像
分别轮转调度，不再要求管理员把环境令牌重新绑定到新员工。

## 身份与并发

- `dispatch.next_owner` 从数据库发现待办，按员工 ID 游标循环选择；这只是任务边界的
  公平性，不中断一个正在执行的大邮箱批次，也不改变消息数量/时间范围。
- 全进程仍是 1 路同步、默认 2 路画像、原有 1 秒轮询；多个进程的领取依靠原有事务锁
  和租约。单个员工不同公司的画像仍可并行，相同公司的互斥不变。
- `dispatch.scoped_backend` 为每个工作单元创建随机临时 AgentCredential，只保存摘要。
  原始令牌显式传入独立 DjangoBackendClient，邮箱、ETag、公司租约和连接池也按实例隔离。
  不修改 `os.environ`，不会把另一员工的环境邮箱继承进任务。
- 任务通过原 Agent HTTP API；认证和每个邮箱、公司的 owner 过滤保持不变。
  浏览器和外部 Agent 不能调用服务器内部凭证工厂申请其他员工身份。
- 单元正常结束或异常退出均关闭客户端并撤销临时凭证；SIGTERM 等待在途单元退出。
  若进程被强杀，可能遗留 `name=crm-work-unit` 的摘要记录，原始令牌未持久化。
  管理员只有在确认无相关在途工作后才能清理这些记录，不能按名称批量删除运行中凭证。
- 固定的 `SALESMATE_AGENT_SERVICE_TOKEN` 和 `SALESMATE_MAILBOX_ID` 保留给原有 CLI；
  共享 Worker 不再使用它们确定员工。内部 HTTP 地址、超时、租约和模型配置保持原值。

## 部署与故障定位

推送 main 后，现有 Actions 验证并自动部署，无新增数据库迁移、依赖或操作系统服务。
运行中的 Worker 排空后重启，即会发现此前排队的新员工批次。部署不重试已 failed 的批次，
也不迁移邮箱归属。若任务领取后遭遇邮箱授权或模型错误，应根据实际失败修复并由用户明确重试。

日志 `crm_worker_started scope=all_active_owners` 表明共享模式已启动；
`crm_work_scheduled` 包含 channel/owner_id；`mailbox_run_claimed` 包含批次 ID；
`crm_identity_created` / `crm_identity_revoked` 记录凭证生命周期且不记录令牌。
页面 queued 表示尚未领取，不能直接解释为邮箱认证失败；结合日志、服务状态、批次开始时间
和租约诊断。active 仅表示进程存活，不能单独证明邮箱同步成功。

## 验证范围

`tests.integration.test_shared_worker` 使用 PostgreSQL 和本机真实 HTTP 服务，验证两个
无预配凭证员工均完成同步调度、跨员工请求 404、撤销令牌 401、画像身份隔离、公平轮转、
停用员工排除、租约过期显式失败与并发领取唯一。Gmail/QQ 原有流水线回归仍模拟邮箱和模型。
这些测试不证明真实邮箱授权或模型服务有效，部署后应另外检查用户原批次的实际终态。
