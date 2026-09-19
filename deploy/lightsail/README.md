# 当前部署

当前已改用 Redis/Celery、Gunicorn 双实例与 PostgreSQL/pgvector。完整布局、首次初始化、自动发布、验证和失败恢复见 [服务器基础设施与在线发布](../../backend/docs/server-infrastructure.md)；受限 GitHub Actions 入口见 [自动部署](../../backend/deploy/lightsail/README.md)。

公网入口为 https://milkdragon.dev/，Nginx 终止 TLS。服务器运行配置和数据库凭证不提交到仓库。

域名证书安装和 IP/域名统一续期见 [域名部署](domain.md)；两个 Nginx 入口共用发布片段，跟随当前蓝绿实例切换。

只读聊天使用独立 `salesmate-chat.service`；其凭证、首次安装、排空和恢复边界见 [聊天适配](../../backend/docs/chat-integration.md)。服务须在切流后启动，与新版 CRM/Celery 调度相互独立。
