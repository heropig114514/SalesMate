# Chinese and English interfaces

The three pages and the login page provide system-default, Simplified Chinese, and English options at the top. On first access, use browser language preferences (usually inherited from the operating system, but configurable separately), selecting supported Chinese or English in preference order. If none match, retain the project's original Chinese default.

Manual selection is stored in the current browser's `django_language` cookie for one year and persists across pages and refreshes. Selecting the system default removes the override. This preference is not stored in user profiles, so devices may use different interface languages.

Changing language reloads the page. After detecting input edits, the page asks for confirmation; cancellation preserves current input. Chat quick prompts are included. Detection is conservative: edited controls may still trigger a prompt after saving; closing or reopening forms resets detection. Passwords, email drafts, and other inputs are not copied to browser storage for language switching.

## Translation boundaries

- Covers login/registration, the customer workspace, mailbox connections/review, chat, sales forms, world-news navigation, date formats, and common HTTP errors.
- Original emails, company names, conversation history, saved AI analyses, cited evidence, and world-news demonstration bodies remain unchanged. Language switching does not update these database contents.
- Options such as industries translate display labels only; submitted values retain original protocol enums. Field names, status codes, currencies, permissions, and version control remain unchanged.
- Newly selected chat quick prompts use the current interface language and are submitted only when users explicitly send them. System prompts, models, and extraction/scoring rules are unchanged; this feature does not guarantee the language of model answers.
- Unregistered internal diagnostics and raw external-service errors retain their original text; no model translation or rewriting of failure meaning.
- Time displays use the selected language. Customer lists retain the backend-configured timezone, world news retains Shanghai time, and business forms/chat/review retain the device timezone.

## Maintenance

The frontend uses existing ES modules without new runtime frameworks or build dependencies:

- `frontend/assets/i18n.js`: language resolution, `t` text templates, `h` static HTML templates, marked-node initialization, and language-switch protection.
- `frontend/assets/translations.js`: 802 source-text keys. Ordinary messages use `t('source text')`; parameterized messages use tagged `t` templates, with `{0}` and `{1}` allowing translated word order.
- `h` processes only static template fragments before inserting existing dynamic values; it does not scan the resulting DOM. Callers must still apply `escapeHtml` to dynamic values. Fragment translations must not introduce HTML tags or attribute quotes; use complete `t` text templates for complex word order.
- HTML `data-i18n` and `data-i18n-aria-label/placeholder/title` mark interface source text only. When translating `<option>` labels, retain explicit `value` attributes, especially for Chinese industry options previously using label text as values.
- Dates use exported `locale`; JSON and attachment requests use exported `language` for `Accept-Language`.

The backend uses Django 5.2's built-in `LocaleMiddleware`, after Session and before Common. `LANGUAGES` is limited to `zh-hans/en`, and `LOCALE_PATHS` points to `backend/locale`. `common.exceptions.translate_detail` translates registered error values only at the HTTP error boundary, preserving field keys, error codes, status codes, and request IDs; the sales catalog translates only `label`.

`locale/en/LC_MESSAGES/django.po` contains 219 common interface/error messages. Dynamic gettext keys exist at the error boundary, so maintain the catalog manually alongside source references rather than replacing it wholesale with one `makemessages` run. Update `.po` alongside source messages, then compile using GNU gettext (run inside `backend` with the Django virtual environment):

```console
python manage.py compilemessages --locale en
```

On Windows, add the GNU gettext tools directory to PATH. Development machines can use Git for Windows' `usr/bin/msgfmt.exe`. Commit `.po` together with compiled `.mo`; servers can read compiled catalogs without startup compilation. CI runs `msgfmt --check` and compares `.mo` to prevent source-only updates.

## Pre-release validation record

- Five backend language tests were added for cookie/header precedence, login/registration errors, catalog contracts, Chinese-data round trips, and account isolation. The fully enabled backend scope passed **199 tests**, retaining the existing exclusion of disabled QQ-specific integration tests.
- Added `tools/browser_i18n.cjs`: English login, three entry points, cross-page/refresh persistence, automatic/manual selection, draft cancellation protection, static/dynamic body isolation, industry values, API language headers, and mobile layout.
- Real Django chat acceptance through `tools.chat_browser_e2e` passed **2 tests** across company/general modes. `browser_chat_live.cjs` explicitly fixes `zh-CN`, preserving existing Chinese assertions and removing host-language differences.
- Existing `browser_workspace.cjs`, `browser_chat.cjs`, `browser_processing.cjs`, and `browser_world_news.cjs` all passed; legacy Chinese tests explicitly use `zh-CN`.
- GNU gettext catalog checks, frontend syntax/translation-placeholder checks, `python tools/check_docs.py`, and `python tools/check_doc_changes.py --base HEAD --fail-on-review` passed. Added variable indexes, language boundaries, and date/timezone descriptions were manually reviewed.
- Real local Django was restarted: English pages read the existing 7 companies, and the catalog API returned 200, `Content-Language: en`, and English labels. Simulated browser tests do not verify real mailbox or model services.
- These are pre-release local validation records. Code, related documentation, and translation source/compiled files belong in the same version; remote CI and production deployment outcomes are determined by that commit's GitHub Actions "Verify and deploy" run.
