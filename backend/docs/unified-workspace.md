# Unified Sales Workspace

On 2026-09-13, mail and business pages began sharing workspace.js/workspace.css for unified main navigation, to-do entry points, customer context, and mobile layout. Existing Django routes and business permissions remain; no frontend framework or new business model is introduced.

Customer detail now updates independently: mail, profile, and score results appear separately while reading position and unsaved drafts persist. A read failure can be explicitly recovered; see [Progressive Display](live-results.md).

## Navigation

- / or /#home: Review-pending, follow-up-pending, confirmation-pending actions, mailboxes with synchronization error, and mail customers in existing priority order.
- /#inbox: Mail and analysis. /#company/{id}: Customer mail detail, which can open a new quote or follow-up form.
- /business/?company={id}#quotes: Current-customer quotes; orders, follow-ups, and actions use the same customer parameter and retain identity after refresh.
- ?company={id}&create=1#quotes: Opens only a prefilled customer form, consumes create on opening, and neither saves nor confirms a record.
- /#reviews, /#gmail, /#processing: Review, mailbox connection, and latest-batch progress; repeat clicks can reopen review.
- /business/?status=open#follow-ups and ?status=pending_confirmation#actions: To-do cards enter actual filtered lists; status options come from backend contract.

Main navigation retains workspace, customers, mail and analysis, follow-up, and notifications. Other functions are grouped under sales business, communication/materials, and settings/collaboration. Unavailable channels do not occupy primary navigation and existing business resources remain accessible.

## Customer and Statistics Boundaries

Customer is passed in the URL and validated through the authorized catalog, never localStorage. An invisible customer gives an explicit error and never silently switches customer. Global resources do not inherit customer filtering. Existing permission, version-conflict, review, and confirmation flows remain.

Home Mail customers counts only companies with business mail. Business-page All visible customers also includes manually created and authorized visible customers. Business overview explicitly covers all visible customers and does not change with current-customer filter.

To-dos use backend totals. Error mailboxes count only each mailbox's latest failed/partial batch, not all historical errors or Worker health. Overview reads on page entry, business-list refresh, review completion, synchronization completion, and manual refresh. A single read failure shows unknown and error rather than zero. No periodic synchronization, automatic confirmation, or automatic retry is added.

## Verification

    node backend/tools/browser_workspace.cjs
    node backend/tools/browser_processing.cjs

The two isolated browser suites passed and cover shared navigation, total fields, cross-page customer passing, prefilling, refresh restoration, status filters, repeated review, unauthorized customer, read failure, and desktop/390px mobile layout. Business APIs are fully mocked and do not write the local database.

Separate read-only verification against local real interfaces passed for workspace, customer catalog, and quote-form prefilling from actual field contract; it did not save a quote or send mail. JavaScript syntax, Git whitespace checks, and existing backend documentation checks passed. JS/HTML/CSS documentation and directory were manually reviewed. Workspace unification introduced no Python change. Backend regression was rerun for this release; results and commit scope are in [Progressive Results](live-results.md).
