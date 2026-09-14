"""职责：定义 Lightsail HTTPS 试用部署的安全配置。
实现：继承基础设置，信任本机 Nginx 覆盖的协议头，并只通过 HTTPS 发送会话和 CSRF Cookie。
关联：由 deploy/lightsail 的 Web 与 CRM systemd 服务显式加载；Nginx 终止 TLS，ASGI 仅监听回环地址。

目录：
- 无

变量索引：
- SECURE_PROXY_SSL_HEADER：接受受信本机代理设置的 HTTPS 协议标记。
- SECURE_SSL_REDIRECT：将非 HTTPS 应用请求重定向到 HTTPS。
- SESSION_COOKIE_SECURE：会话 Cookie 只经 HTTPS 发送。
- CSRF_COOKIE_SECURE：CSRF Cookie 只经 HTTPS 发送。
- SECURE_HSTS_SECONDS：发送一小时 HSTS，短期试用不声明子域或预加载。
"""

from .base import *  # noqa: F403

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 3600
