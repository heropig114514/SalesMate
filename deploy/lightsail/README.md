# Current Deployment

Deployment now uses Redis/Celery, dual Gunicorn instances, and PostgreSQL/pgvector. See [Server Infrastructure and Online Release](../../backend/docs/server-infrastructure.md) for complete layout, first initialization, automatic release, verification, and failure recovery. See [Automatic Deployment](../../backend/deploy/lightsail/README.md) for the restricted GitHub Actions entry point.

The public endpoint is https://milkdragon.dev/, with TLS terminated by Nginx. Server runtime configuration and database credentials are not committed to the repository.

See [Domain Deployment](domain.md) for domain certificate installation and unified IP/domain renewal. The two Nginx entry points share a release snippet and follow the current blue-green instance cutover.

Read-only chat uses standalone salesmate-chat.service. Its credentials, first installation, draining, and recovery boundaries are in [Chat Integration](../../backend/docs/chat-integration.md). Start it after traffic cutover; it remains independent from new CRM/Celery scheduling.
