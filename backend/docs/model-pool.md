# Bailian 模型池与额度切换

共用 `agent.llm.bailian` 的聊天、L1 邮件抽取、客户分析和新闻整理支持同一有序模型池。独立语义图谱推理和嵌入服务使用各自既有配置，不在本模型池中。

## 配置

```dotenv
BAILIAN_MODELS=qwen3.7-max,qwen3.7-max-2026-06-08,qwen3.7-max-2026-05-20,qwen3.7-plus-2026-05-26,qwen3.7-max-2026-05-17,qwen3.7-plus,qwen3.7-flash-2026-07-15,qwen3.7-flash,qwen3.8-flash,qwen3.8-2.4t-a95b,qwen3.8-max-0902,qwen3.8-max
BAILIAN_MODEL_STATE_DB=/opt/salesmate/shared/llm/model-pool.sqlite3
BAILIAN_ENABLE_THINKING=true
```

模型按列表顺序选择。未设置 `BAILIAN_MODELS` 时保留原有 `BAILIAN_MODEL` 单模型行为；设置模型池后该旧值不参与选模。列表拒绝空项和重复项。状态文件必须为绝对路径，父目录须事先创建并允许服务用户写入。所有相关进程应使用同一文件；将其放在 shared 中可跨发布保留。目录应由服务用户持有并设置为 0700。

2026-09-27 按控制台截图追加 7 个新模型，已有 3 个截图条目不重复添加。追加保留此前 5 个候选的顺序及持久禁用记录；新增名称尚未逐个调用验证，不改变既有思考设置、输出预算或计费设置。

不可用状态以端点和 API Key 的摘要分组，文件不保存原始 API Key、提示词或响应正文。独立 SQLite 事务保证同机多进程写入不会覆盖彼此的记录。数据库不可访问或损坏时明确失败，不静默退回内存列表。已在途的请求可能与另一个进程的标记重叠，后续选模会重新读取共享状态。

## 自动切换条件

只有 HTTP 403/429 且结构化错误码精确为 `insufficient_quota` 或 `AllocationQuota.FreeTierOnly` 才标记额度耗尽，然后在同一次模型生成中选择下一个候选。后一错误码见 [百炼官方免费额度说明](https://www.alibabacloud.com/help/en/model-studio/new-free-quota)；前者已在当前国际工作空间端点实测。

每次生成最多尝试各候选一次。请求消息、JSON 模式、思考设置、输出预算和原有网络超时不变。切换只重复被明确拒绝的模型生成，不重放已完成的工具、数据库写入或发送操作。若所有候选都被禁用，立即报告池已耗尽，后续请求不会反复调用这些模型。

401、普通 403、无配额错误码的 429 限流、5xx、网络超时、截断/错误输出均不标记额度耗尽，也不触发模型切换。模型名称错误和权限配置错误应先修复配置。

## 查看与恢复

在仓库根目录执行，CLI 自动读取根目录 `.env`，不会覆盖进程中已存在的环境变量：

```powershell
python -m agent.llm.pool status
python -m agent.llm.pool restore qwen3.7-max-2026-05-17
```

线上使用服务用户及部署虚拟环境运行。status 返回 candidates、available、current、unavailable；不可用项包含模型、错误码、HTTP 状态与首次记录时间。available 表示未被额度阻断，不是实时余额查询，也不承诺模型后续一定可用。

额度恢复后显式 restore 才重新参与选模；不会定时重试耗尽模型。restore 本身不验证额度恢复，下一次真正请求如果仍被额度拒绝，会再次禁用。

日志记录选用模型、完成模型、额度错误和切换，不输出密钥或未经审查的服务端正文。修改模型池环境变量后应正常重启 Web、Chat、CRM、Sales 和 Celery 进程。

## 验证

`python -m unittest agent.tests.test_model_pool -v` 使用真实 SQLite、并发连接、CLI 子进程与模拟 HTTP 验证持久化及错误边界。它不替代线上 provider 验收。现有 Agent 单元测试继续验证单模型契约、输出验证、工具确认及调用顺序。
