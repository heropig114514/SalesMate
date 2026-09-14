# Lightsail 简易部署

目标为新加坡 Ubuntu 24.04 单实例 `Ubuntu-1`。入口为 `https://47.131.232.143/`。代码位于 `/opt/salesmate/app`，Python 环境位于 `/opt/salesmate/venv`；Nginx 提供 HTTPS 和静态资源，Uvicorn 仅监听 `127.0.0.1:8000`，PostgreSQL 仅供本机应用访问。

运行网站使用 `config.settings.lightsail`（DEBUG 关闭，无本地自动登录，Secure Cookie，信任本机代理协议头）。服务器 `.env` 独立生成 Django 密钥、数据库密码、Agent 服务令牌和凭证加密密钥；模型名称、分析提供方及既定分析参数保留本机配置。Agent 后端地址调整为本站 HTTPS `/api/v1/agent/`；CSRF 来源为本站 HTTPS。禁止提交 `.env`、私钥、登录凭证和数据库备份。

首次初始化新数据库时，离线使用 `config.settings.local` 执行已有的 `provision_local` 和 `seed_sales_demo` 命令，以满足这两个开发命令的 DEBUG 前提；不以该配置启动对外网站。数据为带标记的合成验收数据，不复制本机数据库、邮箱 OAuth token 或附件。

服务文件：

- `salesmate-web.service`：网站，启用开机启动。
- `salesmate-crm.service`：演示员工 CRM worker，保留项目默认并发与轮询。
- `nginx.conf`：HTTPS 入口、HTTP 跳转和 ACME 验证目录。
- `salesmate-cert-renew.service` / `.timer`：每日两次检查短期 IP 证书续期，成功续期后检查并重载 Nginx。

前端使用的 `crypto.randomUUID()` 需要安全上下文，公网 HTTP 会使页面初始化失败。因此已使用 Certbot 5.8.0 签发 Let's Encrypt IP 证书并启用 HTTPS；没有为 HTTP 添加前端备用实现。签发流程依据 [Let's Encrypt IP 证书说明](https://letsencrypt.org/2026/03/11/shorter-certs-certbot)。首次部署先用临时 HTTP 配置提供 `/var/www/letsencrypt/.well-known/acme-challenge/`，签发 `salesmate-ip` 证书后再启用仓库中的最终 Nginx 配置。

Lightsail 防火墙新增 TCP 443 的公网 IPv4 规则。原 22/80 规则保持现状；数据库和 ASGI 端口没有对外开放。当前地址尚未绑定静态 IP，停止再启动实例可能改变地址；届时必须更新 Nginx、Django Host/CSRF/Agent 地址并重新签发证书。

静态资源部署需先执行 `collectstatic --noinput` 收集管理站点资源，再将 `backend/frontend/assets/` 内容复制到 `backend/staticfiles/`，并保证 Nginx 可读。基础配置没有声明前端 `STATICFILES_DIRS`，只执行 collectstatic 无法收集自定义前端文件。

外部发信和日历动作的 `sales_worker` 在本次试用中不启动；本次验证不执行对外通信。真实 Gmail 流程需要用户在浏览器完成 OAuth，并保证回调地址与 Google Cloud 配置一致。现有 Google 回调仍是本机地址，尚未为公网部署完成配置；真实 Gmail 同步和 LLM 分析没有端到端验收。

网站已支持公开的简易账号注册：登录页点击“注册新账号”，填写用户名、密码和确认密码即可自动登录。无需邮箱、手机号或验证码，密码仍采用项目现有的 Django 校验与哈希存储。新账号没有 demo 数据，可直接创建自己的客户记录。当前 `salesmate-crm.service` 只处理 demo 用户；新用户的 Gmail 同步还需要配置其 Agent 凭证、对应 worker 和公网 OAuth 回调，注册本身不创建这些资源。

日常检查（服务器）：

```bash
sudo systemctl status salesmate-web salesmate-crm nginx postgresql --no-pager
sudo journalctl -u salesmate-web -u salesmate-crm -n 80 --no-pager
curl -fsS https://47.131.232.143/api/v1/health/ready/
sudo systemctl list-timers salesmate-cert-renew.timer --no-pager
```

修改部署配置后先运行 `sudo nginx -t` 和 `sudo systemd-analyze verify /etc/systemd/system/salesmate-*.service`。业务升级需先备份服务器数据库，再上传当前工作区代码、安装既定依赖、执行 migrate/collectstatic 并复制前端静态资源，最后重启对应服务。

不自动重启失败进程，不修改模型参数，不用替代数据库或规则模式掩盖部署失败。服务异常时查看 journal 的明确错误并处理。

## 本次验证（2026-09-13）

- 所有数据库迁移成功，包括工作区新增的 `crm.0006_durable_lineage`。
- 新建普通演示用户 `demo`，导入四组带标记的虚构业务数据；另保留一条 `[Deployment smoke test] Synthetic customer` 验证数据库写入和读取。
- 公网 HTTPS 首页、业务页、JavaScript/CSS、数据库健康检查返回 200；HTTP 正确跳转 HTTPS。
- 真实 HTTPS Session 登录、Secure Cookie、账号、客户目录、概览、报价、订单、跟进读取、注销后访问拒绝均通过。
- 浏览器实际登录成功，客户目录显示五条记录（四组验收示例及一条部署测试客户），HTTPS 页面未记录新 JavaScript 错误；未声称已在浏览器完成全部业务流程。
- `pip check`、Nginx 配置检查和 systemd unit 检查通过；`backend/tools/check_docs.py` 全量 96 个文件通过，并人工核对本次新增配置与说明。
- `check --deploy --settings=config.settings.lightsail` 无错误，保留 W005/W021 两条 HSTS 子域及 preload 提醒；当前裸 IP 试用未启用这两项。
- 未复制本机业务数据库或 OAuth token，未进行真实邮件发送、日历创建、Gmail/LLM 全链路测试或压力测试；尚未提交 Git。

## 注册更新验收（2026-09-13）

- 新增 `POST /api/v1/accounts/register/`、注册表单和自动登录衔接，同步生成 OpenAPI 与 API 文档；无新增数据库迁移或依赖。
- 本地注册、既有调试会话与 OpenAPI 契约共 11 项测试通过；覆盖真实测试库写入、CSRF、权限字段、重名、Unicode 规范化、密码规则、已登录身份保护、数据隔离、退出和重新登录。
- `check_docs.py` 全量 98 个文件通过，人工核对本次代码注释与目录；两个变更 JavaScript 文件的 `node --check`、`git diff --check` 通过。
- 更新前备份数据库与被替换文件至服务器 `/opt/salesmate/backups/signup-20260913/`，更新后 Django check、迁移检查、HTTPS 数据库健康检查通过，web 和 CRM 服务均 active。
- 公网浏览器实际验证：密码不一致提示、注册自动登录、空客户目录、保存首个客户、退出后以注册凭证重新登录。保留合成验收账号 `signup-smoke-20260914` 及其 `[注册验收] 合成测试客户`，与 demo 数据隔离。
- 本次没有验证新账号的真实 Gmail/LLM 流程，没有发送邮件或创建日历事件；未提交 Git。
