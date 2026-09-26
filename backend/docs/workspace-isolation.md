# Personal Workspace and Mail-Fact Compatibility

On 2026-09-21, at the project owner's request, each account's own view was restored after algorithm integration. Deployment configuration is:

    WORKSPACE_OWNER_ONLY=True
    LAB_OPEN_ACCESS=False
    LOCAL_DEBUG_AUTO_LOGIN=False

WORKSPACE_OWNER_ONLY takes precedence over laboratory mode. The web application requires real sign-in. Business records, mailbox originals, analysis, chat, knowledge, and files are isolated by account. Team sharing and KGSEED shared entry points are disabled: the experiment catalog is empty, and batch reads, export, attachments, edits, and MCP experiment tools are rejected. Existing database data and ownership are unchanged. An owner may still view that owner's KGSEED customers in ordinary business pages.

With account isolation, analysis no longer uses cross-account experiment material. Existing analysis caches that include it become invalid under current source-version rules. An explicit analysis refresh is needed to generate a result from currently available sources. Restoring views never invokes models automatically in bulk.

The Agent task API continues to use Authorization: Agent with that account's service token. Task writes include the claimed lease and company version. The business-tools API/MCP uses that account's Tool authorization, restricted tool list, and expiry. MCP configuration must provide SALESMATE_TOOLS_TOKEN; SALESMATE_TOOLS_USER alone can no longer select an account. Session tool calls obey current-account permissions. Write operations retain existing version and idempotency contracts.

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
