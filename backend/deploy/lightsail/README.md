# Lightsail 自动部署

CRM Worker 现为所有有效员工共享调度，升级后自动处理已排队的新员工批次，不需要重新绑定旧凭证。身份隔离与验收说明见 [多员工共享 CRM Worker](../../docs/shared-crm-worker.md)。

`main` 推送触发 GitHub Actions 的 **Verify and deploy**：隔离 PostgreSQL 上的后端测试、Agent 测试、邮箱工具测试、注释和迁移检查，以及模拟 API 的页面测试通过后，才允许部署。PR 只执行验证；可从 Actions 手动运行 `main` 的工作流。流程采用 [GitHub Actions 部署与并发控制](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/control-deployments)。

## 运行方式

1. Actions 使用专用 SSH 密钥连接 `salesmate-deploy@47.131.232.143`，只传已验证的完整提交 SHA。
2. 强制 SSH 命令只允许 root 安装的 `deploy-trigger.sh`；任意 Shell、端口转发及非 SHA 输入均不被允许。它启动独立 systemd 临时服务，连接断开不会主动中止服务器部署。
3. 服务器用自己的只读 GitHub Deploy Key 拉取 `main`。如果提交已过时，记录跳过；同一版本且服务健康时不重复部署。服务器部署锁与 Actions 并发组共同阻止发布交叉执行。
4. 在独立目录构建候选代码、虚拟环境和静态资源；检查保守迁移门禁与 Redis/pgvector。
5. 先排空独立聊天服务，再停止调度器并等待在途工作，最后停 Celery 消费者；旧 Web 保持运行。备份数据库并执行兼容迁移。
6. 启动候选 Gunicorn 实例，健康检查通过后 Nginx 切流；启动新消费者、验证消息往返，再启动调度器和独立聊天服务，排空旧请求后停旧 Web。

首次初始化、目录、失败边界、向量接口和服务配置见 [服务器基础设施与在线发布](../../docs/server-infrastructure.md)。后台有短暂停领窗口，破坏性迁移默认拒绝自动发布；不能把所有数据库变更都视为无中断更新。

## 一次性安装与凭证边界

以下路径由管理员初始化，权限不能交给网站进程：

| 位置 | 用途 |
|---|---|
| `/usr/local/sbin/salesmate-deploy-trigger` | 安装的 `deploy-trigger.sh`，root 所有、0755 |
| `/usr/local/sbin/salesmate-deploy-from-git` | 安装的 `deploy-from-git.sh`，root 所有、0755 |
| `/var/lib/salesmate-deploy/` | root 私有 Git 缓存、锁、状态与日志 |
| `/etc/salesmate-deploy/repository_ed25519` | 只读仓库私钥，仅服务器 root 可读 |
| `/etc/salesmate-deploy/github_known_hosts` | 从 GitHub HTTPS API 元数据确认的 SSH 主机公钥 |
| `/home/salesmate-deploy/.ssh/authorized_keys` | 专用触发公钥，带 `restrict` 和强制命令，root 管理 |
| `/etc/sudoers.d/salesmate-deploy` | 仅允许专用用户执行 SHA 校验入口 |
| `/etc/systemd/system/salesmate-{crm,sales}.service.d/graceful-stop.conf` | 安装的 `graceful-stop.conf`，更新后执行 daemon-reload |

仓库注册只读 Deploy Key。GitHub Actions 配置两个 [加密 secret](https://docs.github.com/en/actions/concepts/security/secrets)：`LIGHTSAIL_DEPLOY_SSH_KEY` 是只能触发部署的专用私钥；`LIGHTSAIL_DEPLOY_KNOWN_HOSTS` 固定部署服务器 SSH 身份。原管理员 SSH 密钥、数据库密码和邮箱授权码不上传到 Actions。

仓库缓存固定为 `git@github.com:heropig114514/SalesMate.git`，只拉取 `main`。代码文件必须为普通文件；子模块、软链接及 Git 接管 `.env`、运行数据目录会明确失败。根目录运行时数据不由仓库替换。新增运行时目录前应更新部署校验边界。

业务代码可自动发布；root 部署脚本及 systemd 配置若需修改，管理员须另行审阅并重新安装，不让普通代码更新直接替换提权入口。

## 失败与人工恢复

失败不会隐式重试、回滚数据库或重发邮件。GitHub Actions 保留失败步骤；服务器日志记录提交、阶段和备份位置。拉取或预检失败时应用尚未停机；排空后台后失败则保留现场，可能需要人工恢复后台；切流后失败应同时核对 active-port。

```bash
sudo cat /var/lib/salesmate-deploy/status
sudo tail -n 80 /var/lib/salesmate-deploy/deploy.log
sudo journalctl -u salesmate-deploy --no-pager
systemctl is-active salesmate-crm salesmate-sales salesmate-chat salesmate-celery@crm salesmate-celery@sales
cat /opt/salesmate/deployed-revision
```

备份位于 `/opt/salesmate/backups/online-<SHA>-<UTC时间>/`，包含 `database.dump`、旧代码/环境路径、Nginx 配置及 `previous-revision`；旧代码和独立环境保留在 releases 中。恢复前先确认当前进程、迁移结果和发信状态，再由管理员明确选择恢复代码、环境或数据库；备份不会自动清理，应定期检查磁盘空间。

需要暂停自动部署时，在 GitHub Actions 中禁用 **Verify and deploy**，同时确认已有部署任务是否结束。不要通过删除邮箱配置或停止 PostgreSQL 来暂停发布。

## 首次启用聊天消费者

本次增加 `salesmate-chat.service` 和部署脚本预检/排空/启动。管理员须先审阅并安装该 service，执行 daemon-reload 和 enable（代码及迁移就绪前不启动），并更新 root 所有的 `/usr/local/sbin/salesmate-deploy-from-git`。新脚本缺少聊天 service 时会在停机前失败，不从普通 Git 更新自动替换 systemd 配置。使用同一员工 token 和 `.env`，失败不自动重启。详见 [聊天部署与恢复](../../docs/chat-integration.md)。安装后必须核对部署提交、迁移和服务状态，不能仅凭代码合并判断消费者已运行。
