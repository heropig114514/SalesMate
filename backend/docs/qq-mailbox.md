# QQ mailbox integration and trial

## Current status: temporarily disabled

QQ receiving/sending is disabled by default (`QQ_MAIL_ENABLED=false`). Pages hide connection, synchronization, and sending entry points; the backend rejects QQ connections, new syncs, retries, send preparation/approval, and external reconciliation. Tool catalogs no longer publish `actions.prepare_qq`. Gmail remains available.

Existing QQ emails, profiles, encrypted authorizations, and checkpoints remain. Historical emails are accessible through company details/saved-email APIs. Queued sync claims pause; approved sending tasks reaching workers fail before external calls without automatic resending. Local repair/analysis of historical emails remains available without connecting to QQ.

To restore, explicitly set `QQ_MAIL_ENABLED=true` in the target environment and restart Web, CRM, and sales Worker/Celery processes for consistent settings; queued syncs then become claimable. Inspect queue/connection state first. The following describes the enabled workflow.

QQ coexists with Gmail OAuth. It uses TLS at `imap.qq.com:993` and client authorization codes, without Google OAuth, browser callbacks, or an owned domain. Receiving reads inbox/sent folders and reuses L1–L4/persistent batches; sending uses separate QQ SMTP connections and manual confirmation. Existing Gmail sending/calendar features remain.

## Deployment preparation

1. From the repository root in the project Python environment, run `python backend/manage.py migrate` to apply `crm.0007_qq_mailbox` and `crm.0008_mailboxsyncrun_sync_options`, adding QQ credentials, checkpoints, and batch scope fields.
2. Configure existing `SALESMATE_VAULT_KEY` in root `.env`. Web and `crm_worker` must share the same Fernet key. Preserve an already configured value; replacing it makes existing external connections/QQ authorization codes undecryptable.
3. Generate once only if no key exists:

   ```powershell
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   Save the output as `.env` `SALESMATE_VAULT_KEY` and back it up securely. Never commit, place in pages, or share it. The application neither generates keys automatically nor falls back to plaintext authorization storage.
4. Restart Web/shared `crm_worker`, retaining internal HTTP addresses. Workers discover queued tasks for all active employees and use separate temporary identities per unit, without a fixed employee's `SALESMATE_AGENT_SERVICE_TOKEN`. QQ/Gmail share this worker; legacy `--sync-authorized-mailboxes-once` claims Gmail only.
5. Servers require outbound `imap.qq.com:993`. No OAuth callback domain is needed, but public authorization-code submission should use HTTPS; retain existing HTTPS/secure-cookie settings.

## Page workflow

1. In QQ Mail web settings, enable IMAP/SMTP under account/security settings and generate a 16-character authorization code. See [QQ's official instructions](https://help.mail.qq.com/detail/106/985). Supply this code, not the QQ login password.
2. Log in to SalesMate, open QQ Mail from the top bar/company list, and enter a complete `@qq.com`/`@foxmail.com` address plus authorization code. Supply at least recent N days or maximum N messages, then validate/connect/synchronize. Neither field is prefilled; use positive integers. Both constraints apply when both are supplied. Use the actual sending/receiving address; aliases are not inferred.
3. The backend validates login and receiving/sent folders before encrypted persistence and initial synchronization. Submission clears the authorization-code form; lists/progress never return codes or ciphertext.
4. View batch scope, individual-email progress, and company profiles in the workspace. Subsequent QQ sync/inbox refresh asks for scope again; cancellation creates no batch. Gmail retains its rules. Pages contain 20 emails and L1 supports at most four concurrent calls, using existing prompts/parameters.
5. On failure inspect batch/worker logs, then explicitly retry unfinished emails. Ordinary new synchronization does not retry old failures. Active batches prevent credential replacement/removal.
6. Removing a connection deletes local QQ ciphertext only, retaining historical emails, profiles, and cursors. Revoke the authorization code on QQ's official page to remove access from other clients.

To reconcile emails, open the account's saved-email view. It reads only persisted emails for that mailbox, including business, nonbusiness, and review-pending messages by default, showing source, received time, classification, and expandable bodies without sync/model calls. The company workspace groups business emails and cannot verify the complete mailbox list. Imported demonstration emails remain labeled as demonstrations rather than QQ sources; existing examples/history remain.

Recent N days means N × 24 hours before request confirmation, using IMAP INTERNALDATE with frozen lower/upper bounds, not the email's self-declared Date header. Maximum N messages counts the combined selected inbox/sent workload, newest internal dates first; failed attempts count. Emails already extracted successfully at the current version do not count; ordinary sync does not retry failures. A day-only limit may still match many emails, so also specify a message limit to bound volume.

Filtering reads candidate UIDs/date metadata, potentially more entries than the message cap. Only selected emails have bodies read/analyzed; existing pending work cannot bypass limits. Unselected emails await later manual sync. Expanding days can backfill old emails without maximum-UID skipping. Retries retain original failed IDs or frozen ranges. Legacy batches without scope or registered IDs require a newly selected range; registered failed emails remain explicitly retryable. New ranges never delete processed history.

QQ authorization codes may themselves permit sending/receiving. Receiving synchronization uses only read-only EXAMINE, UID SEARCH, date-metadata FETCH, and BODY.PEEK[], without changing read status, sending, or deletion.

## API and compatibility boundaries

- `POST /api/v1/mailboxes/qq-connect/`: `address`, `authorization_code`, `sync_options`; success returns 202 with safe mailbox state and initial sync queued, using employee Session/CSRF.
- `DELETE /api/v1/mailboxes/{mailbox_id}/qq-authorization/`: remove the current employee's inactive QQ connection; unauthorized access returns 404, active conflicts 409.
- Existing `GET /api/v1/mailboxes/` adds `qq_authorized`; `gmail_authorized` still means Google authorization only.
- `GET /api/v1/mailboxes/{mailbox_id}/email-reviews/?status=saved`: all persisted mailbox emails by descending received time, including bodies, `source`, `received_at`, and classification; 20 per page with employee isolation. Company lists additionally expose `email_sources` from actual business emails.
- Existing `POST /api/v1/mailboxes/{mailbox_id}/request-sync/`, batch queries, and failure retries support both Gmail/QQ. QQ requires `{"sync_options":{"recent_days":7,"max_messages":20}}` (examples, not defaults); either field may be omitted/null, but both empty returns 400. Progress returns frozen `sync_options`. Gmail also requires explicit scope, defaults to at most 50 emails, and requires explicit approval above that. Gmail's recent N first limits then skips synchronized messages without backfilling older ones. QQ retains its rule that completed emails do not consume the cap.
- QQ emails use `source=qq_real`. For existing L1/HTTP compatibility, `gmail_message_id` carries `qq:{folderBase64URL}:{UIDVALIDITY}:{UID}`, not a Gmail server ID. `dedupe_key` still combines mailbox address and this ID; QQ `thread_id=null`.
- Folders are independent identity namespaces; repeatable MIME Message-ID alone never merges physical copies across folders. Moving imported messages out of folders retains local history.
- Persist message registration before checkpoint advancement and source text before model calls; failed writes can reuse successful extractions. UIDVALIDITY changes or renamed sent folders stop explicitly without silent resets, rescans, or fabricated success.
- Support only the unique server-returned `\\Sent` special folder or known QQ Sent/Sent Messages/已发送 names. Unrecognized layouts fail completely rather than falling back to inbox-only synchronization.

## Validation

```powershell
python -m unittest agent.tests.test_qq_mail
python backend/manage.py test tests.integration.test_qq_mail
python backend/tools/check_docs.py
```

Tests mock IMAP/LLM while exercising real database, protocol validation, authorization, and persistent-state implementations. They do not establish real QQ authorization/network access/folder layouts or Bailian output quality; confirm initial trials with your mailbox through the page.

## QQ sending

1. Apply `sales.0004_qq_smtp_send` and restart Web. `python backend/manage.py sales_worker` must share Web's database and `SALESMATE_VAULT_KEY`. It processes explicitly approved external actions for all employees; inspect the execution queue before starting.
2. In business management, add a QQ sending connection under external connections. Enter mailbox/client authorization code. The backend validates only TLS/SMTP login at `smtp.qq.com:465`, then encrypts/stores separately. It neither reuses receiving authorization automatically nor sends test email. No OAuth callback/domain is required; outbound service access is necessary.
3. Save an email draft with recipients, subject, and body in a company assistant conversation. Add an external action selecting QQ send, the sending connection, company, and draft; optionally attach a reviewed quote.
4. Review the complete sender, recipients, subject, and body, then explicitly confirm/enqueue. Preparation does not send. Execution uses displayed frozen content regardless of later draft edits; changed connection versions/quotes reject execution.
5. Submit the body only after every recipient is accepted; any rejection prevents the entire body submission. Explicit SMTP rejection is failed; interrupted body submission is uncertain. Actions never resend automatically.
6. Success/server acceptance means SMTP accepted submission, not guaranteed inbox delivery. For unknown outcomes, external reconciliation read-only queries a unique Message-ID in QQ's sent folder and checks sender, recipients, and subject. This requires IMAP and server-retained sent copies. Missing copies remain unknown, never proof of no send. The program never appends sent copies itself.

API: `POST /api/v1/sales/connections/qq/` accepts `address` and write-only `authorization_code`, returning 201 on success; invalid inputs return 400, SMTP authentication failures 409. Nonterminal actions prevent credential replacement. It inherits employee Session/CSRF and never returns ciphertext. `POST /api/v1/sales/records/actions/` adds `tool=qq.send` with existing preparation, confirmation, execution, and reconciliation contracts.

Sending checks: `python backend/manage.py test tests.integration.test_qq_send`. Fully mocked networking covers authentication without sending, encryption, permissions, frozen content, all-recipient acceptance, failed/unknown distinctions, and read-only reconciliation. Real SMTP authentication, delivery, and server sent-copy retention require separate trials.

On Lightsail, install `backend/deploy/lightsail/salesmate-sales.service` under `/etc/systemd/system/`, run `systemctl daemon-reload`, and enable `salesmate-sales`. Inspect approved queues first. Stop this process alongside website updates, restarting after migrations/readiness checks. It depends on `salesmate-web` and PostgreSQL and does not automatically restart failed processes. View logs with `journalctl -u salesmate-sales`; logs omit email bodies/credentials.
