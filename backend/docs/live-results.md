# Progressive Display of Mail, Profiles, and Scores

Customer detail has an independent read-only observer. On initial open or an explicit Update analysis click, it first reads the currently saved result and then checks again roughly every three seconds after each GET completes. Mail synchronization and company analysis need not both finish: new L3 profiles and later L4 scores for the same company revision appear independently.

## Page Behavior

- Workspace lists retain their progressive refresh during synchronization. Detail observes the current customer independently and can receive results from manual analysis or later work after the mailbox batch ends.
- Compare complete responses, not only company revision. Unchanged responses do not rebuild the detail DOM.
- Changed regions update by stable node key. Mail direction, text selection in unchanged originals, reading anchor, scroll containers, and independent assistant drafts and edit dialogs persist.
- Switching customer, signing out, or leaving the page stops subsequent checks. An issued GET may complete, but its response is no longer eligible to update the page.
- Read failure shows Auto refresh paused and warns that existing content may be stale, then stops polling. Resume auto refresh only reads; it does not recreate analysis or retry failed business tasks.
- The observer interval remains 3000 ms. Actual display latency also includes request time and browser background-tab scheduling. Displayed units are validated, persisted complete mail extraction, profile, and score.

live-detail.js manages single-customer observation and node reuse. app.js manages current identity, routing, detail, and explicit analysis requests. Each observation cycle has one GET in flight. No push service is added, and model parameters, scoring, and server task-failure semantics remain unchanged.

## Verification

    node backend/tools/browser_live_detail.cjs
    node backend/tools/browser_workspace.cjs
    node backend/tools/browser_processing.cjs

Browser tests use mocked APIs and verify separate appearance of new mail, L3, and L4 under a fixed revision; display of already completed parts while a batch runs; preservation of DOM, reading position, original-text selection, draft focus, and unsubmitted forms; ongoing manual-analysis updates; failure pause and read-only resume; stale slow responses from an old customer; and stopping reads after leaving the page. The tests do not call a real LLM or Gmail.

This release incorporates remote commit 19abc8b's Gmail test-tool update and retains its removal of the legacy Desktop OAuth entry. 120 Agent offline tests passed. Isolated PostgreSQL Django tests in the release scope passed 81 cases; the complete local workspace passed 84, with three additional pre-existing local demonstration-data cases excluded from release scope. Three browser checks, JavaScript syntax, schema validation, migration consistency, documentation, and whitespace checks were performed separately. Commit content passed staged documentation checks for 88 backend Python files with zero errors and zero review findings. The two modified Agent Python files also passed structural checks and their change descriptions were manually reviewed. Real-model quality, production concurrency, and process-crash exercises are outside this verification claim.
