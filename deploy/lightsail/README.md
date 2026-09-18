# 当前部署

当前已改用 Redis/Celery、Gunicorn 双实例与 PostgreSQL/pgvector。完整布局、首次初始化、自动发布、验证和失败恢复见 [服务器基础设施与在线发布](../../backend/docs/server-infrastructure.md)；受限 GitHub Actions 入口见 [自动部署](../../backend/deploy/lightsail/README.md)。

公网入口为 https://milkdragon.dev/，Nginx 终止 TLS。服务器运行配置和数据库凭证不提交到仓库。
