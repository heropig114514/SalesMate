# SalesMate Project Reference Overview

Updated 2026-09-14. The frontend, Django backend, and Agent have completed local HTTP primary-workflow integration. The software [README](../README.md) is the primary document for scope, data flow, configuration, startup, and testing.

## Current Implementation

- The native HTML/CSS/JavaScript workspace displays current-employee Gmail authorization, synchronization state, companies, mail, profiles, analysis, business context, and follow-up priority.
- Django + DRF persist users, employee Gmail authorization, mailboxes, companies, contacts, mail, extraction, tasks, and L2–L4 results.
- The Agent initially scans recent Gmail mail and later reads incrementally through History cursor. L1 uses at most four concurrent requests and submits each on completion. L2/L3/L4 run per company Job and communicate with Django through HTTP. Project Skills provide L1/L3 model capability for future routing extension.
- Root .env is the only configuration file. DATABASE_URL is mandatory; local development uses existing PostgreSQL. SQLite requires explicit configuration.
- rules mode remains a user-interface demonstration requiring neither Gmail nor Bailian; it is not a fallback for model failure.

## Documentation Locations

| Document | Purpose |
|---|---|
| [Software README](../README.md) | Current scope, complete workflow, shared configuration, startup, and acceptance |
| [Agent README](../../agent/README.md) | L1–L4 fields, Skills, validation, and Agent CLI |
| [OpenAPI](../contracts/openapi.yaml) | Current machine-readable HTTP contract |
| [API Contract](api-contract.md) | Authentication, route, and transport-consistency notes |
| [Data Model](data-model.md) | Django persistent objects and constraints |
| [Agent Integration](agent-integration.md) | Agent/Django responsibilities and one-shot task flow |
| [Local Development](local-development.md) | Compact local startup and check entry point |
| [Product Requirements Summary](references/product-requirements.md) | Historical requirements summary of the original MVP product document |
| [Early Design Summary](references/agent-and-early-design.md) | Early broad technical direction, background only |

Version numbers, paths, and plans in historical summaries do not define current implementation requirements. When current code, root README, Agent README, and OpenAPI disagree, verify actual behavior first and synchronize current documents. Mail-level durable tasks, company-profile parallel Workers, non-business hiding, and manual review are implemented. See [Mail Processing Integration](processing-integration.md) for current rules and outstanding work.

## Future Scope

Sales schema, business-management pages, private conversation drafts, team permissions, audit, attachments, and sales Worker are implemented. Gmail/calendar adapters have confirmation flows; real external execution requires write-permission authorization. Read-only chat and explicit internal knowledge are integrated; see [Chat Integration](chat-integration.md). WhatsApp, meeting notes, external knowledge/industry news, and autonomous chat tool calls are not implemented. Chat production launch still requires migration and dedicated-consumer deployment. See [Sales Expansion](backend-expansion.md) for scope and runtime steps.
