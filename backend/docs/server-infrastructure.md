# 服务器基础设施与在线发布

生产环境使用 PostgreSQL 16、pgvector、Redis、Celery、Gunicorn + Uvicorn Worker、Nginx 和 systemd。本地仅安装 Python 依赖即可运行不涉及基础设施的检查；完整数据库与消息集成测试在 CI 或隔离服务器运行，不要求本机安装 Redis/Celery 服务。向量相关迁移需要装有 pgvector 的 PostgreSQL，不能连接生产数据库运行开发测试。

## 任务执行边界

`TASK_EXECUTION_MODE=local` 是既有本地线程/进程执行路径；服务器明确设为 `celery`，必须同时提供 `CELERY_BROKER_URL` 与 `CELERY_RESULT_BACKEND`。连接失败不会回退本地执行。

数据库继续保存业务队列、员工归属、批准状态、租约及最终结果。CRM 调度器按员工公平轮转，以原有 1 路同步、2 路画像上限向 `crm` 队列提交；销售调度器按原有逐项边界向 `sales` 队列提交。消费者分别使用 3/1 个进程。消息只包含类型和主键；员工在执行时重新校验，CRM 单元仍使用可撤销的员工 HTTP 凭证。

Redis 的 0/1 数据库分别保存传输与临时结果，只监听本机，启用密码、AOF everysec 和 noeviction。正常消费结果读取后删除，未消费结果一天后过期。Redis 不替代业务数据库；机器故障下 AOF everysec 也不保证每条尚未落盘的消息都保留。

Celery 使用开始执行前确认、预取 1、不自动重试业务或发布、不在连接失败后自动重连。强杀、机器故障或确认结果丢失需要核查数据库和执行现场；对于已提交的外部邮件，不能把超时当作未发送而自动重发。销售原有 approved/running/unknown 等状态边界保持不变。

## 向量接口

`apps.vectors.services.put_document` 接收员工、空间、来源、模型版本、文本和实际嵌入；`search_documents` 按相同员工、空间、模型及维度进行精确余弦检索。不同模型版本分开保存，同模型维度变化明确拒绝。当前没有 ANN 索引。

此轮接入存储和检索能力，不默认选择嵌入模型、不批量处理历史邮件，也不改变 CRM/Agent 的上下文检索语义。接入业务语义搜索前，需要明确嵌入模型、资料范围与更新/删除规则。测试中的二维向量只是可核验的合成输入。

## 发布目录和流程

- `/opt/salesmate/releases/<SHA>-<随机后缀>/`：每版代码和独立虚拟环境；保留旧版供人工恢复。
- `/opt/salesmate/shared/`：受保护的 runtime.env、media 和 private_uploads，普通发布不覆盖。
- `/opt/salesmate/slots/8001/`、`8002/`：指向各自固定版本；两套 Gunicorn 各 2 个 ASGI 进程。
- `/opt/salesmate/app`、`venv`：当前后台任务使用的版本链接。
- `/etc/nginx/snippets/salesmate-release.conf`：当前代理目标和同版本静态资源路径。
- `/opt/salesmate/active-port`、`deployed-revision`：切流状态及完整成功版本。

首次由管理员审阅后执行 `deploy/lightsail/bootstrap-infrastructure.sh <已审查代码目录>`，安装 Redis 和 postgresql-16-pgvector 是其前置步骤。初始化把原代码复制到独立目录，以原代码先验证 Gunicorn；原安装保存在备份中。初始化后应确认旧 Nginx 请求已结束，再停止并禁用旧 `salesmate-web`。首次新版本部署成功前，Celery 消费者不启动；原调度器继续运行旧逻辑。

常规发布：CI 测试通过 → 构建新目录/依赖/静态文件 → 检查迁移兼容性和基础设施 → 停止调度器并等待任务，再停止消费者 → 备份数据库 → 执行兼容迁移 → 启动候选 Web 并探测 → Nginx 平滑切流 → 启动新消费者并验证两个队列真实往返 → 启动调度器 → 旧 Nginx 请求结束后停止旧 Web。

构建、迁移和后台排空期间，旧 Web 继续服务；后台任务会有短暂停领窗口。候选失败不会自动切流。迁移门禁只允许新表、新索引、可空非唯一新增字段和 vector 扩展创建；其他操作明确拒绝，须另行设计维护发布。数据库 DDL 的锁等待超过 5 秒直接失败。门禁不能证明业务 API 新旧版本完全兼容，也不保证任意数据库变更都能零中断。

部署失败不自动回滚数据库、不重试外部动作。查看 `/var/lib/salesmate-deploy/status` 和 `deploy.log`，同时核对 active-port、deployed-revision、服务状态及备份目录。切流后后台启动失败时，网站可能已经运行新版本而成功版本记录仍是旧值，不能只看提交文件判断恢复方式。旧发布目录和备份不自动删除。

## 验证和运维

```bash
systemctl is-active salesmate-web@8001 salesmate-web@8002
systemctl is-active salesmate-crm salesmate-sales salesmate-celery@crm salesmate-celery@sales redis-server
cd /opt/salesmate/app
sudo -u salesmate /opt/salesmate/venv/bin/python backend/manage.py check_infrastructure --workers
curl -fsS https://milkdragon.dev/api/v1/health/ready/
```

常态只有 active-port 对应的 Web 实例运行，另一个处于 inactive 是预期。公开 ready 接口仍只检查数据库；完整 Redis/向量/消费者检查使用上述管理命令，不修改已有 API 契约。

实现依据：[Celery Django 集成](https://docs.celeryq.dev/en/stable/django/first-steps-with-django.html)、[Celery Worker 停止语义](https://docs.celeryq.dev/en/stable/userguide/workers.html)、[Gunicorn 信号](https://gunicorn.org/signals/)、[pgvector Python 接口](https://github.com/pgvector/pgvector-python)。
