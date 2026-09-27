# Personal Workspace and Mail-Fact Compatibility

The current account policy always isolates personal conversations, messages, knowledge, mailboxes, external connections, attachments, and profile material. Production configuration is:

    WORKSPACE_OWNER_ONLY=False
    LAB_OPEN_ACCESS=True
    LOCAL_DEBUG_AUTO_LOGIN=False

The retained laboratory flag applies only to explicit synthetic fixtures; it never bypasses account permissions. Historical fixture integrity differs from current rows, so this change deliberately preserves the existing fixture setting without rewriting audit manifests.

Team sharing remains enabled through active memberships and explicit customer grants. Global news/events remain readable by authenticated employees. Private resources do not inherit customer grants. `WORKSPACE_OWNER_ONLY=True` is a separate optional policy that also disables team sharing and synthetic batch interfaces; it is not needed for private account isolation.

Browser calls require Session and CSRF. Agent calls require the employee’s Agent credential and claimed leases. Standalone MCP requires a delegated Tool token with valid expiry and scope. Chat and browser tool discovery retain the full catalog; execution uses the authenticated employee’s permissions. Public identity headers cannot select an account. Writes retain version, idempotency, and explicit confirmation contracts.

Both Google OAuth entry points bind state and PKCE to the initiating employee. Switching account before callback rejects the exchange; existing grants and stored owners are unchanged. Restart any OAuth flow initiated before this deployment.

The M in the workspace top-right is a Gmail-connection indicator, not a login avatar. The mailbox list always returns only current-account connections. Switching account immediately clears the old mailbox display, and a late response for the old account cannot overwrite the current interface.

## Mail-Fact Versions

extract_prompt_version identifies generation provenance. Synthetic examples retain batch:fixture-extract-v1 and do not present themselves as real model output. New examples also record extract_schema_version. The audited KGSEED_20260921_01:fixture-extract-v1 uses extract-v7 structure and is recognized by an explicit compatibility mapping shared by backend and Agent; it does not change existing database rows or manifest fingerprints. Unknown historical structures still require upgrade. Fact fields, enumerations, and evidence validation remain effective.

Customer detail is read-only by default. Analysis failure does not prevent viewing saved mail. Detail extraction_upgrade reports version and repair state. Genuine legacy facts can be upgraded with the Upgrade mail facts action or:

- customers.extraction_status: Read-only preview with company_id.
- customers.upgrade_extractions: Explicit upgrade with company_id, current revision, and a tool idempotency key.
- Legacy HTTP GET/POST /api/v1/companies/{id}/extraction-upgrade/: POST has an empty body and If-Match.

Upgrade reads saved bodies, retains historical extraction, and queues analysis after completion; it does not refetch the mailbox. Upgrading a compatibility fixture is a no-op. Failed repair requires an explicit retry and never loops model calls automatically.

A placeholder score remains unknown and the page states Score not generated. Synthetic vectors are not semantic-retrieval models, and synthetic mailboxes contain no OAuth authorization. Legacy company-bound chat remains readable. Continuing a conversation uses the existing new-workspace-session entry point and does not silently change historical-question context.

Exact batch cleanup still retains fingerprint and external-reference validation. Records outside the manifest produced by genuine analysis must be handled according to actual lineage before cleanup. This change does not ignore reference constraints or delete historical results automatically.
