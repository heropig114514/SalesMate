# Baseline Information and First-Time Onboarding

This implementation delivers interface repairs from the requirements, four-step information entry, and Global Insights display. Follow-up-priority formulas, weights, thresholds, and algorithm inputs remain unchanged.

## Persistence and Isolation

A newly registered account receives SalesSetup(completed=False), and Session returns onboarding_required. Completing or skipping the final step sets it true and enters /#inbox. Historical accounts do not receive onboarding state through migration and may complete it from Company Setting.

| Path under /api/v1/ | Operation | Behavior |
| --- | --- | --- |
| accounts/company-profile/ | GET/PATCH | Existing company profile with nullable size_band; company name remains required and version validation remains. |
| accounts/onboarding/ | GET | Current account personal, products, solutions, completed, revision, and attachment metadata; reads do not create records. |
| accounts/onboarding/ | PATCH | Submit a complete object or array for one step with If-Match; unknown fields are rejected and conflict is 409. |
| accounts/onboarding/documents/ | POST multipart | Accepts file only; maximum 5 MiB, validates PDF signature or UTF-8 TXT, and returns id/name/content_type. |
| accounts/onboarding/documents/<uuid>/ | GET | Current-account private inline preview; ?download=1 forces download; anonymous is 403 and another account is 404. |

Every interface retains Session, CSRF, and owner constraints. Files use a database BinaryField with no public media URL. Responses enable nosniff, disable caching, and use CSP sandbox. Upload persists the file immediately; associating product/solution lists still requires explicit user save. Removing a list row changes only the association and does not delete an uploaded file.

Personal fields are name, title, email, phone, regions[], and industries[]. Supplying email does not authorize Gmail. Company industry uses the same four enumerations as inbox; size uses existing range values and historical free-text values remain.

Every product has stable UUID id, name, category, specifications[], price_min/price_max (unknown is null), currency, scenarios[], nullable document_id, and nullable linked_product_id, which explicitly links an owned non-archived transaction product. Save/edit retains existing IDs and links. Legacy material IDs are filled deterministically on read and persisted when their array is saved. Price is reference material rather than an actual quote; minimum cannot exceed maximum. Supported currencies are SGD/USD/CNY/EUR/JPY and there are at most 200 rows.

Every solution has stable UUID id, name, and document_id, with at most 100 rows. Every file reference belongs to the current account. PDFs/text are readable online; no other Office-file parsing or automatic model reading is integrated. Algorithm callers can use [Software Support Tools](software-support-tools.md) for item CRUD, upload, and chunked file reads without browser Session; existing browser routes continue to use Session.

The CSV template fields are name,category,specifications,price_min,price_max,currency,scenarios. Multiple specifications/scenarios use the pipe separator, with quoted fields and quoted newlines supported. The browser reads at most 1 MiB, validates every row before adding drafts, and final save still uses backend field and price validation.

## Password and Page State

Passwords retain minimum length 8 and registration maximum 128. Common-password, numeric-only, and username-similarity restrictions are removed. Password hashes, CSRF, ordinary-account permissions, and duplicate-name validation remain. Request identifiers stay in server responses and browser error metadata/diagnostic logs but are not added to user-facing notices.

The search field uses autocomplete=off. Account switching clears filters, company lists, detail, and assistant memory; an obsolete account-list response cannot update the page. This does not delete browser history or alter backend data authorization.

## Database and Verification

Migration accounts.0003_salessetup_companyprofile_size_band_setupdocument only creates tables and adds nullable size. Before deployment/startup, run python manage.py migrate through the project migration process. New material is not injected into existing score input, preserving current algorithm behavior and conclusions.

Backend checks are python manage.py test tests.integration.test_onboarding tests.integration.test_company_profile tests.integration.test_registration tests.contracts.test_schema. They use an isolated database and cover persistence, version conflict, file format/size, account isolation, CSRF, and registration/onboarding lifecycle.

Browser checks are node backend/tools/browser_onboarding.cjs, browser_workspace.cjs, and browser_i18n.cjs using real HTML/JS and mocked business APIs. browser_world_news.cjs reads the real local laboratory database, requires an explicit local-service address and initialized placeholder data, and is documented in [Development Support](development-support.md). None of these checks demonstrates Gmail, real news collection, AI, or external calendar integration.
