"""Responsibility: Define security settings for the Lightsail HTTPS trial deployment.
Implementation: Inherits base settings, trusts protocol headers set by local Nginx, and sends session and CSRF cookies only over HTTPS.
Relationships: Explicitly loaded by deploy/lightsail Web and CRM systemd services; Nginx terminates TLS and ASGI listens only on loopback.

Directory:
- None

Variable index:
- SECURE_PROXY_SSL_HEADER: Accepts the HTTPS protocol marker set by the trusted local proxy.
- SECURE_SSL_REDIRECT: Redirects non-HTTPS application requests to HTTPS.
- SESSION_COOKIE_SECURE: Sends session cookies only over HTTPS.
- CSRF_COOKIE_SECURE: Sends CSRF cookies only over HTTPS.
- SECURE_HSTS_SECONDS: Sends one-hour HSTS; the short trial does not declare subdomains or preload.
"""

from .base import *  # noqa: F403

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 3600
