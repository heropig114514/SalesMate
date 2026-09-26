# Global Insights Collector Operations

Backend storage and page integration do not enable collection automatically. The Agent entry point, sources, model parameters, and schedule retain existing implementation; no new backend collection HTTP interface is needed. Deploy in this order.

1. Deploy backend and frontend static assets, back up and migrate the database through the existing release process. If sales.0009_shared_insights reports duplicates, a maintainer first examines conflict groups; migration never deletes or changes data on its own.
2. With the fixed collector account, create a minimum-permission credential through POST /api/v1/agent-tools/credentials/ under signed-in Session and CSRF protection. Reuse the authorization interface; do not use an Agent Worker credential or a broader preset.

    {"name":"world-insights-collector","expires_in_hours":720,"allowed_tools":["world_news.list","world_news.create","world_events.list","world_events.create"]}

3. Store the returned one-time token securely in /opt/salesmate/shared/world-insights.env on the server, as SALESMATE_TOOLS_URL (HTTPS service root) and SALESMATE_TOOLS_TOKEN, readable only by the service account. Never write it to Git, logs, or shared terminal output. The model retains existing Bailian environment configuration; dependencies use repository agent/requirements.txt, including pycountry. Do not mix local test endpoints with production.
4. Run python -m agent.world_insights --dry-run first to inspect sources and dates. It accesses sources, geocoding, and model but does not write backend and cannot replace actual persistence verification.
5. A deployment maintainer installs the existing service/timer on the release server:

    sudo install -m 644 agent/deploy/salesmate-world-insights.service /etc/systemd/system/
    sudo install -m 644 agent/deploy/salesmate-world-insights.timer /etc/systemd/system/
    sudo install -d -o salesmate -g salesmate -m 700 /opt/salesmate/shared/world-insights
    sudo install -d -m 755 /etc/systemd/system/salesmate-world-insights.service.d
    sudo install -m 644 backend/deploy/lightsail/world-insights-cache.conf /etc/systemd/system/salesmate-world-insights.service.d/cache.conf
    sudo systemctl daemon-reload
    sudo systemctl start salesmate-world-insights.service
    sudo journalctl -u salesmate-world-insights.service -n 100 --no-pager
    # Enable recurring execution only after verifying persistence and item_errors.
    sudo systemctl enable --now salesmate-world-insights.timer

The timer retains daily UTC 03:00 and 15:00 runs with random delay up to 20 minutes. The first real run creates shared records and must occur in a prepared production-release environment; this document is not an execution record.

/opt/salesmate/shared is root-managed mode 0750. The service account cannot create cache or .tmp files in its parent. Install the cache-path drop-in above: a writable JSON file alone cannot support Agent same-directory temporary writes and atomic replacement. Grant write permission only to the isolated subdirectory, never the full shared directory; Agent source remains unchanged.

## Rotation and Acceptance

- Credentials last at most 720 hours. Before expiry, a maintainer creates a new credential for the same collector account and updates the protected file. The next oneshot reads it; revoke the old credential only after successful verification. Do not bypass expiry through permanent tokens or open laboratory mode.
- With collector A and ordinary employee B, verify news list/detail, event list/map, and Tool reads under LAB_OPEN_ACCESS=false. B reads public facts but cannot modify A records or view A private opportunity IDs, customers, or amounts. owner_only mode also shares public facts.
- Monitor news/events/source_successes/source_errors/item_errors and persisted-record count. Partial Agent failure can exit 0, so systemd success alone is insufficient. Same-source competition across accounts returns 409 and is not success. No new records is not automatically failure because sources can lack qualifying date/location data.
- Event originals, event conditions, and recommendations are shared for all employees; do not enter private customer notes. There is no separate global-news administrator; update/archive remains owner-only except laboratory mode.
- Collector-account personal-data reset retains existing owner-deletion rules. Do not reset it to rotate credentials. Shared reading does not make records ownerless public data.

## Local PostgreSQL

This workspace PostgreSQL is in WSL Ubuntu-24.04. Start with:

    powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1 -WslDistro Ubuntu-24.04

It starts Web and applicable configured Workers; see [Local Development](local-development.md). To start only the database for testing, run wsl -d Ubuntu-24.04 -u root -- service postgresql start and retain a WSL session. Do not change database address or switch to SQLite.
