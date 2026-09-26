# 0919 Product UI Comparison and Acceptance

Source: [0919 changes](https://docs.google.com/document/d/1IdFW1A2IHP2ngN4vlYHXg2xN0QAdJthxCX-zwz8uOyQ/edit?tab=t.0). On 2026-09-21, implementation was compared against document text and all six screenshots. Scope is frontend only and does not adjust scoring, permission, or send-confirmation interfaces.

| Requirement | State before change | Result |
| --- | --- | --- |
| Remove Communication and materials and Settings and collaboration top-level menus | Implemented | Retain compact sidebar. |
| Move chat assistant to lower-right entry and bottom expansion | Implemented | Retain the same conversation and draft; Channel expands into a right panel on wide screens. |
| Remove World news and notification navigation | Implemented | Retain Global Insights and do not restore old entries. |
| Remove duplicate customer navigation inside Channel | Implemented | Keep hidden while retaining required customer context in business pages. |
| Dashboard, Global Insights, Channels, Customers, and four submenus | Implemented | Retain original hierarchy. |
| Company Setting and Emails Connections bottom entries | Partially implemented | Rename old Emails Setting consistently. |
| Hover Evidence for sender, time, and reference material | Not implemented | Hover, keyboard focus, and touch click preview; an explicit control locates and highlights original mail. |
| Remove duplicate Dashboard to-do bar, top review control, confirmation card, customer-creation card, and sorting footnote | Not implemented | Remove them; retain home review, follow-up, and synchronization-error cards. |
| Remove Channel top account/review block, duplicate to-do bar, workspace badge, and completed-sync report | Not implemented | Remove redundant entries; completed batches without pending/failure items collapse from regular list. |
| Simplify Channel detail to mail conversation and profile analysis layout | Partially implemented | Incoming/outgoing bubbles on separate sides; center profile, analysis, contacts, expandable priority; shared assistant right panel; mobile single column. |
| Reply input and AI polishing | Not implemented | Per-customer in-page reply draft, copy, and explicit fill into assistant; no automatic question or mail send. |
| Remove duplicate Customers to-do bar, return-to-workspace, workspace badge, and unread notice card | Not implemented | Remove them; three remaining statistic cards use responsive layout. |

## Interaction and Data Boundaries

- Source preview reads only authorized mail in current customer detail. It exactly matches source_refs by dedupe_key and shows sender, sent time (received time when absent), subject, and verbatim body. The current contract has no citation character offsets, so it explicitly states Mail original rather than fabricating an exact excerpt. Missing references are explicit and never guess sender or content.
- Preview permits mouse-entry reading/scrolling and exits through Esc or close control. Switching customer/account clears old preview. Business content is text-escaped; mail HTML never executes and no extra model/external-service request is made.
- Reply draft exists only in current page memory and is isolated per customer; account switch clears it. AI polishing appends to current chat input after reading conversation, never overwrites existing input or asks automatically. Actual sending retains Prepare communication action then Review confirmation; no direct-send control is added.
- Running synchronization, mail failure, and profile failure remain visible. #processing and mailbox-settings progress entries allow completed-report view and explicit retry. Review is available from the home card or Emails Connections.
- Priority score and reasons move only into an expandable area. Calculation, sorting, business parameters, and API contract remain unchanged.

## Verification

node backend/tools/browser_product0919.cjs uses mocked APIs and a real browser to check simplified items, incoming/outgoing direction, source content/escaping, missing source, keyboard/touch behavior, original location, read-only polishing, desktop three columns, mobile overflow, and English. It does not demonstrate integration with real mailbox or model services.

Existing workspace, processing, and i18n browser acceptance continues to cover chat conversation, cross-page navigation, review, sync scope, and retry. Python documentation tools run under project convention. HTML/JS/CSS responsibility, directory, variables, and implementation consistency are reviewed manually.
