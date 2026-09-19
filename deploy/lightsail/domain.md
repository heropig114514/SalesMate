# milkdragon.dev 域名部署

域名 A 记录指向 `47.131.232.143`。`nginx-milkdragon.conf` 安装为
`/etc/nginx/sites-available/salesmate-domain`，并在 `sites-enabled/` 创建同名链接。
原 `nginx.conf` 的 IP 入口继续使用独立的 `salesmate-ip` 证书，现有自动部署的
SSH 地址不受影响；自动部署的 HTTPS 健康检查使用域名。此配置仅覆盖根域名，不覆盖 `www`。

DNS 只负责定位服务器，TLS 证书还必须包含访问的域名。只配置 DNS 时，默认 IP
入口返回的证书不包含域名，会导致 `ERR_CERT_COMMON_NAME_INVALID`。

## 安装顺序

1. 确认域名 A 记录指向服务器，80 端口的 ACME 验证目录可达。
2. 复用服务器现有 ACME 账号，以 webroot 模式签发域名证书：

   ```bash
   sudo /opt/certbot/bin/certbot certonly --webroot -w /var/www/letsencrypt \
     --cert-name milkdragon.dev -d milkdragon.dev --non-interactive \
     --deploy-hook '/usr/sbin/nginx -t && /usr/bin/systemctl reload nginx'
   ```

3. 按 [基础设施初始化](../../backend/docs/server-infrastructure.md) 准备双实例和
   `/etc/nginx/snippets/salesmate-release.conf`；域名与 IP 入口共同引用当前发布片段。
   备份服务器 `/opt/salesmate/shared/runtime.env` 和 Nginx 配置。在运行配置的 `DJANGO_ALLOWED_HOSTS` 追加
   `milkdragon.dev`，在 `DJANGO_CSRF_TRUSTED_ORIGINS` 追加 `https://milkdragon.dev`。
   保留其他配置和已有地址，备份须仅限管理员访问。
4. 安装域名 Nginx 配置，执行 `sudo nginx -t`；通过受保护的蓝绿发布流程使新 Web
   实例读取更新后的共享环境，再重载 Nginx。Web 服务名为 `salesmate-web@8001` /
   `salesmate-web@8002`，活动实例以 `/opt/salesmate/active-port` 为准。
5. 现有 `salesmate-cert-renew.timer` 每日两次执行不限定证书名称的 `certbot renew`，
   因此会同时管理域名和 IP 证书。更新 unit 后执行 `systemctl daemon-reload`，
   验证定时器处于启用状态。

## 验证与维护

```bash
curl -fsS https://milkdragon.dev/api/v1/health/ready/
curl -fsS https://47.131.232.143/api/v1/health/ready/
sudo /opt/certbot/bin/certbot renew --cert-name milkdragon.dev --dry-run
sudo systemctl list-timers salesmate-cert-renew.timer --no-pager
```

验证不得关闭 TLS 证书校验。续期失败查看 `/var/log/letsencrypt/letsencrypt.log`
及 `journalctl -u salesmate-cert-renew`，不通过忽略证书错误恢复访问。
服务器的 root Nginx 配置与独立 `.env` 不会被日常 Git 代码发布覆盖；修改本文配置后
仍需管理员安装。域名绑定不代表 Gmail OAuth 已配置或验证，Google Console 还需登记
收信 `/api/v1/mailboxes/gmail-callback/` 和发信 `/api/v1/sales/oauth/` 的 HTTPS 回调地址，
并使服务端收信回调设置与 Console 完全一致。

## 部署验收（2026-09-18）

- 已安装域名配置并追加 Django Host/CSRF 来源，原 IP 入口保留。
- 配置备份：`/opt/salesmate/backups/domain-20260918T115112Z`，未执行数据库迁移。
- 域名证书包含 `DNS:milkdragon.dev`，本次签发证书到期日为 2026-12-17。
- 开启证书校验的域名首页、静态资源和数据库就绪接口均为 200，HTTP 为同域 HTTPS 301。
- 浏览器实际显示登录表单，没有证书拦截；未提交登录或发送邮件。
- IP 就绪接口仍为 200；Web、CRM、Sales、Nginx、PostgreSQL 服务均 active。
- 域名 `certbot renew --dry-run --run-deploy-hooks` 成功；每日两次续期 timer 为 enabled/active。
- Nginx 与续期 systemd unit 校验通过；现有 `backend/tools/check_docs.py` 检查 128 个文件通过。
  该检查器只覆盖 Python，新增 Nginx 配置和 unit 顶部说明已人工核对。
- 本次未发布工作区其他业务改动，域名配置及文档尚未提交 Git。
