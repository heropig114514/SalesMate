# milkdragon.dev domain deployment

The domain's A record points to `47.131.232.143`. Install `nginx-milkdragon.conf` as `/etc/nginx/sites-available/salesmate-domain` with a same-name link in `sites-enabled/`. The original `nginx.conf` IP entry retains its separate `salesmate-ip` certificate. The existing deployment SSH address is unchanged; automated deployment uses the domain for HTTPS health checks. This configuration covers only the root domain, not `www`.

DNS locates the server, but the TLS certificate must also include the requested domain. With only DNS configured, the default IP entry returns a certificate without the domain, causing `ERR_CERT_COMMON_NAME_INVALID`.

## Installation order

1. Confirm the A record points to the server and the ACME validation directory is reachable on port 80.
2. Reuse the server's existing ACME account to issue a domain certificate with webroot mode:

   ```bash
   sudo /opt/certbot/bin/certbot certonly --webroot -w /var/www/letsencrypt \
     --cert-name milkdragon.dev -d milkdragon.dev --non-interactive \
     --deploy-hook '/usr/sbin/nginx -t && /usr/bin/systemctl reload nginx'
   ```

3. Prepare two instances and `/etc/nginx/snippets/salesmate-release.conf` according to [infrastructure initialization](../../backend/docs/server-infrastructure.md). Both domain and IP entries reference the current release snippet. Back up `/opt/salesmate/shared/runtime.env` and Nginx configuration. Append `milkdragon.dev` to `DJANGO_ALLOWED_HOSTS` and `https://milkdragon.dev` to `DJANGO_CSRF_TRUSTED_ORIGINS`. Preserve other settings and existing addresses, and restrict backup access to administrators.
4. Install the domain Nginx configuration and run `sudo nginx -t`. Use the protected blue/green release process so new Web instances read the updated shared environment, then reload Nginx. Web services are `salesmate-web@8001` / `salesmate-web@8002`; `/opt/salesmate/active-port` identifies the active instance.
5. The existing `salesmate-cert-renew.timer` runs `certbot renew` twice daily without restricting certificate names, managing both domain and IP certificates. After updating the unit, run `systemctl daemon-reload` and verify the timer is enabled.

## Verification and maintenance

```bash
curl -fsS https://milkdragon.dev/api/v1/health/ready/
curl -fsS https://47.131.232.143/api/v1/health/ready/
sudo /opt/certbot/bin/certbot renew --cert-name milkdragon.dev --dry-run
sudo systemctl list-timers salesmate-cert-renew.timer --no-pager
```

Do not disable TLS certificate validation. For renewal failures, inspect `/var/log/letsencrypt/letsencrypt.log` and `journalctl -u salesmate-cert-renew`; do not restore access by ignoring certificate errors. Routine Git releases do not overwrite the server's root-owned Nginx configuration or separate `.env`; administrators must install changes described here. Domain binding does not establish Gmail OAuth configuration or verification. Register HTTPS callbacks for receiving at `/api/v1/mailboxes/gmail-callback/` and sending at `/api/v1/sales/oauth/` in Google Console, and make the server's receiving callback exactly match the Console entry.

## Deployment acceptance: 2026-09-18

- Domain configuration was installed and Django Host/CSRF origins appended, retaining the original IP entry.
- Configuration backup: `/opt/salesmate/backups/domain-20260918T115112Z`; no database migration was performed.
- The domain certificate contains `DNS:milkdragon.dev`; the issued certificate expires on 2026-12-17.
- With certificate validation enabled, the domain homepage, static assets, and database-readiness endpoint returned 200; HTTP returned a same-domain HTTPS 301.
- The browser displayed the login form without a certificate block; no login or email send was submitted.
- The IP readiness endpoint still returned 200; Web, CRM, Sales, Nginx, and PostgreSQL services were active.
- Domain `certbot renew --dry-run --run-deploy-hooks` succeeded; the twice-daily renewal timer was enabled/active.
- Nginx and renewal systemd unit validation passed, as did the existing `backend/tools/check_docs.py` check over 128 files. That checker covers Python only; headers in the new Nginx configuration and unit received manual review.
- Other workspace business changes were not released, and domain configuration/documentation had not been committed to Git.
