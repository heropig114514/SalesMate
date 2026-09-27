# Frontend Structure and Visual Maintenance

Django serves the pages and browser ES Modules retain the business logic. The product style follows the repository-external frontend_example examples for Global Insights and Social Media Intelligence: a dark blue-gray background, purple accents, fine borders, and compact information cards.

## Login and Logout

`/login` and `/login/` reuse the existing login/registration forms. Anonymous workspace, business, company-settings, and global-insight visitors enter `/login/`. Authenticated login visitors return to the workspace, or to required onboarding. The login entry reads `/api/v1/session/?auto_login=false` so local automatic login does not bypass the form; it does not log out an existing session.

Every workspace sidebar exposes **Profile → 退出登录 / Sign out**. This sends the existing CSRF-protected `DELETE /api/v1/session/` and returns to `/login/` only after success. Failures remain visible and require an explicit retry. Successful logout invalidates the old session and retains an anonymous suppression marker so local debug login cannot immediately sign the browser back in. Password login clears that marker. Global defaults, accounts, business records, and independent Agent/mailbox credentials are unchanged.

`node backend/tools/browser_auth.cjs` uses the same browser environment variables as the checks below. It verifies actual frontend interactions against mocked APIs; `python backend/manage.py test tests.integration.test_debug_session tests.integration.test_registration --keepdb --noinput` separately checks real database-backed sessions, registration, invalid credentials, logout invalidation, and CSRF enforcement.

## Shared Components

| File | Responsibility |
| --- | --- |
| assets/design-system.css | Color, semantic state, typography baseline, top bar, and focus behavior; load first on every entry point. |
| assets/product-header.js | Native salesmate-header Web Component that shows product sections and marks the current section from the URL. |
| assets/workspace.js / workspace.css | Compact left navigation, live to-do counts, business-page customer context, and mobile layout. |
| assets/app.js / app.css | Sign-in, customer mail cards, filtering, detail display, and mailbox management. |
| assets/business.js / business.css | Business tables, editing, and confirmation forms rendered from server field contracts. |
| assets/assistant-widget.js / assistant-widget.css | Shared floating entry point, collapsible bottom chat bar, and mobile layout across workspace pages. |
| assets/assistant-markdown.js / markdown-it.vendor.js | Safe assistant-body rendering with pinned markdown-it and a same-origin standalone ESM distribution. |
| assets/world-news.js/css / world-map.js | Database-backed global-insight events, amount map, detail view, and industry news. |
| assets/onboarding.js/css / company-settings.js | Four-step personal, company, product, and solution onboarding/settings persisted through account APIs. |

The top bar uses light DOM, shared design variables, and native links. It does not introduce a routing system, query accounts, or create writes. The home page is not labeled Social Media Intelligence. The chat widget keeps the current page and product section. The Social Media Intelligence entry maps to existing mail and customer analysis; no unintegrated social channel was added.

The product top bar displays only Global Insights, Social Media Intelligence, and Sales Business; the Experiment Data entry was removed. The internal /experiments/ page and its data APIs remain available to existing integration links. Hiding the entry does not modify data or backend permissions.

The standalone opportunity-priority page and its Global Insights entry have been retired. `/priorities/` returns 404; its template and dedicated scripts/styles have been removed. Backend opportunity-score APIs and stored results remain available for algorithm integration.

Product navigation is Dashboard, Channels (formerly Emails and analysis), and Customers. Opportunities, quotes, orders, and tickets are customer subentries. The left Opportunity Priority, World news, notifications, communication and materials, settings and collaboration, and legacy sales-business groups were removed; mail/customer detail no longer shows an upper customer navigation bar. Product sections and business routes retain their routes, data, and functionality.

## Modification Rules

- Adjust semantic variables in design-system.css first; do not redefine another theme palette inside a page.
- Page styles handle only their corresponding layout and state. Preserve DOM IDs, hidden, native forms, and business-module event contracts.
- Chat evidence uses an outer master switch and inner original-text disclosure. Citation cards use theme variables while original text scrolls independently. Tests locate both details elements and verify closed-by-default and readable expanded behavior.
- New components provide keyboard focus, clear links, and selected state. Components that register global events remove listeners when detached.
- Preserve the server QQ switch, unknown-data states, and synthetic-source markers. Global Insights reads the database and labels synthetic batches explicitly.
- Entry points and changed shared modules use consistent asset-version query parameters so deployments do not load stale components.
- This implementation requires no npm dependency or build step. A future framework migration may proceed one business page at a time while retaining these semantic variables and APIs.

## Verification

At the repository root, set SALESMATE_PLAYWRIGHT_MODULE to the Playwright module path and SALESMATE_BROWSER_PATH to the Chrome/Chromium executable, then run:

    node backend/tools/browser_workspace.cjs
    node backend/tools/browser_chat.cjs
    node backend/tools/browser_processing.cjs
    node backend/tools/browser_qq_send.cjs
    node backend/tools/browser_world_news.cjs
    node backend/tools/browser_onboarding.cjs

Except browser_world_news.cjs, these checks use local static assets and mocked APIs. The Global Insights check requires initialized, running local laboratory services and explicit SALESMATE_TEST_URL; see Development Support. The QQ check uses an explicit mock switch and does not verify live external mailbox transport. Desktop and mobile screenshots are written to the ignored backend/artifacts directory.

From backend, run python tools/check_docs.py and python tools/check_doc_changes.py --base HEAD --fail-on-review. Existing checkers cover Python. Developers must manually verify that JS, CSS, and HTML top-of-file documentation remains consistent with implementation.

## 2026-09-20 Required Interface

Registration passwords are 8–128 characters and require no particular character combination. Request identifiers remain in error objects and diagnostics but are not shown in ordinary notices. The search box disables browser-history completion. When sign-in identity changes, clear search and list/chat memory so a previous account response is never rendered.

On the next session read, a new account enters /settings/company/?onboarding=1. Every personal, company, product, and solution step may be skipped; completing or skipping the final step enters the Social Media Intelligence inbox. Existing accounts edit from Company Setting and are not forced through onboarding by a migration. Company profile continues to use its versioned interface; other steps use accounts/onboarding/, preserving the page draft on version conflict.

Products support per-row add, edit, and removal plus UTF-8 CSV import from a downloadable template; multi-value specifications and scenarios use the pipe separator. CSV is limited to 1 MiB and 200 total products; parse failure never imports partially. Specifications and solutions accept PDF or UTF-8 TXT up to 5 MiB per file through a sign-in-protected private URL for display or download. Product and solution rows require explicit save; a successfully uploaded file is already saved to the current account.

These materials are independent user-provided context. They are not injected into score input, do not change scoring, and do not automatically create a quote, authorize Gmail, call a model, or send mail. See the onboarding contract for detail.

## Product UI Completion, 2026-09-19

The requirement-by-requirement comparison and boundary are in the product UI acceptance record. Channels gained conversation bubbles, a reply draft for the current customer, and Evidence source preview. Dashboard, Channels, and Customers removed duplicated status bars and designated statistics cards. The global chat, scoring algorithm, and external-send confirmation retain their contracts.

Additional acceptance is node backend/tools/browser_product0919.cjs, using mocked API, desktop/mobile/English views, escaped sources, missing citations, and read-only interaction.

## Chat Markdown

Assistant history and new responses use markdown-it 15.0.2 for headings, emphasis, strikeout, nested lists, quotations, links, inline/fenced code, and tables. Ordinary line breaks are retained. Long code and wide tables scroll horizontally in their own area and use theme variables. User input and source evidence remain plain text; API and stored originals are unchanged.

Raw HTML never executes. Links accept only HTTP, HTTPS, mailto, or relative URLs resolving to those protocols and isolate new tabs. Images appear as descriptive links to avoid automatic requests for model-provided remote resources. Formulae, Mermaid, syntax highlighting, and token-stream repair are not provided. An unclosed fence is rendered as code according to CommonMark. The project retains existing completed-result polling and does not change algorithms, backend behavior, or retry semantics.

Streamdown targets React streaming LLM rendering. This project has neither React nor a build chain, so it uses the official markdown-it standalone browser ESM. Source, version, and integrity information are recorded in Third Party Resources. Added acceptance is included in browser_chat.cjs: formatting semantics, safe links/HTML/images, unclosed fences, desktop/mobile overflow, plain-text user messages and sources, and the existing chat lifecycle.
