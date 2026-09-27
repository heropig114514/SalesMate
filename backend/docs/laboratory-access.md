# Authenticated laboratory experiments

`LAB_OPEN_ACCESS` now controls explicit synthetic-fixture experiments only. It never bypasses authentication, CSRF, ownership, team grants, Tool delegation, business versions, confirmation, or Agent leases. `WORKSPACE_OWNER_ONLY=False` preserves team collaboration; private resources remain owner-only in either mode.

Browser APIs require a real employee Session; Agent APIs require that employee’s Agent credential; standalone MCP uses an explicitly delegated Tool token. Identity-selection headers no longer authenticate or change ownership. No anonymous laboratory account is created.

The Chat Agent and authenticated browser retain the complete published tool catalog. Standalone Tool credentials retain the permissions the employee explicitly delegated. Catalog discovery never grants access to another employee’s conversations, mailboxes, credentials, attachments, or knowledge.

Google sending, calendar, and read-only mailbox OAuth save the initiating employee ID alongside one-time state and PKCE. A callback under a different account is rejected before contacting Google. Old in-progress OAuth flows without an owner binding must be restarted. Existing stored connections are unchanged.

This deployment retains the previously authorized `LAB_OPEN_ACCESS=True` for legacy synthetic fixtures; it no longer enables anonymous or cross-account business access. Strict fixture validation remains available with `False`, but historical batch changes must be reviewed before switching. Web, Agent and background processes must restart for code or environment changes. Shared synthetic batch interfaces remain available to authenticated users under their explicit manifest/fingerprint contract. They do not authorize access to arbitrary real business records.

The integration suite verifies two employee Sessions, forged identity headers, invalid/expired/revoked credentials, CSRF, private CRUD, team customer grants, chat ownership, and OAuth account switching. External provider calls in these tests are mocked; real sending needs a separately authorized acceptance test.
