/**
 * Responsibility: Manage QQ mailbox connection, synchronization, per-account source viewing, and removal.
 * Implementation: Require an explicit day/message scope each time without prefilling; read the authorization code only on submission and clear it immediately; reuse batch polling.
 * Internationalization: i18n.js translates explicitly marked static text only; dynamic business content and API values remain unchanged.
 * Relationships: The 0919 interface and shared language/API resources use coordinated cache versions; app.js injects shared callbacks, index.html supplies separate QQ dialogs, and api.js handles Session/CSRF.
 * Directory: readQQScope validates selections; chooseQQScope asks for scope each time; renderQQAccounts renders safe state; initQQ registers connection/account actions.
 * Variable index: statusLabels maps sync states to current-language labels.
 */
import { t, h } from './i18n.js?v=20260921-product';

import { request, escapeHtml as e } from './api.js?v=20260921-product';

const statusLabels = { authorization_required: t('未连接'), sync_requested: t('等待同步'), sync_running: t('正在同步'), completed: t('同步完成'), partial: t('部分完成'), failed: t('同步失败') };

/** Function: Read one explicit sync-limit selection. Inputs: form is the current form. Outputs: A day/message-limit object.
 * Logic: Convert blank entries to null and require at least one positive safe integer. Constraints: Never read historical values or replace empty selections with defaults. */
function readQQScope(form) {
  const options = Object.fromEntries(['recent_days', 'max_messages'].map(name => [name, form.elements[name].value === '' ? null : Number(form.elements[name].value)]));
  if (!Object.values(options).some(value => value !== null)) throw new Error(t('请填写最近 N 天或最多 N 封，至少一项。'));
  if (Object.values(options).some(value => value !== null && (!Number.isSafeInteger(value) || value <= 0))) throw new Error(t('同步限制必须为正整数。'));
  return options;
}

/** Function: Wait for selection from a blank scope form before syncing. Inputs: address is the displayed email address. Outputs: Selected scope, or null on cancellation.
 * Logic: Reset each time and settle the Promise on submission/closing. Constraints: Cancellation sends no request; never cache choices or allow unbounded sync. */
export function chooseQQScope(address) {
  const dialog = document.getElementById('qq-scope-dialog');
  const form = document.getElementById('qq-scope-form');
  const errorBox = document.getElementById('qq-scope-error');
  form.reset();
  errorBox.textContent = '';
  document.getElementById('qq-scope-address').textContent = address;
  return new Promise(resolve => {
    let selected = null;
    form.onsubmit = event => {
      event.preventDefault();
      try { selected = readQQScope(form); dialog.close(); }
      catch (error) { errorBox.textContent = error.message; }
    };
    dialog.addEventListener('close', () => { form.onsubmit = null; resolve(selected); }, { once: true });
    dialog.showModal();
  });
}

/** Function: Render the current employee's QQ accounts. Inputs: mailboxes is an array of safe mailbox states. Outputs: None.
 * Logic: Display qq_authorized accounts with sync/source-view/remove buttons. Constraints: Escape all external text; active batches allow read-only viewing only. */
export function renderQQAccounts(mailboxes) {
  document.getElementById('qq-accounts').innerHTML = mailboxes.filter(item => item.qq_authorized).map(item => {
    const status = item.sync_state?.status || 'authorization_required';
    const active = ['sync_requested', 'sync_running'].includes(status);
    return h`<article class="gmail-account"><div><strong>${e(item.address)}</strong><p>${e(statusLabels[status] || status)}</p>${item.sync_state?.error ? `<small class="failure">${e(item.sync_state.error)}</small>` : ''}</div><div class="account-actions"><button type="button" class="secondary" data-qq-sync="${e(item.mailbox_id)}" ${active ? 'disabled' : ''}>${active ? t('同步处理中…') : t('同步 QQ')}</button><button type="button" class="secondary" data-qq-view="${e(item.mailbox_id)}" data-qq-address="${e(item.address)}">查看已同步邮件</button><button type="button" class="text-btn" data-qq-disconnect="${e(item.mailbox_id)}" ${active ? 'disabled' : ''}>移除连接</button></div></article>`;
  }).join('') || h('<div class="gmail-empty"><strong>尚未连接 QQ 邮箱</strong><p>在下方填写邮箱和客户端授权码。</p></div>');
}

/** Function: Bind QQ connection and synchronization entries. Inputs: refresh updates mailbox state; sync requests synchronization; track follows queued mailboxes; view opens mailbox source text. Outputs: None.
 * Logic: Validate scope before connecting, then clear scope and poll after success; existing accounts use the shared sync callback to select scope. Constraints: Authorization codes never enter URLs, logs, or browser storage. */
export function initQQ({ refresh, sync, track, view }) {
  const dialog = document.getElementById('qq-dialog');
  const form = document.getElementById('qq-form');
  const errorBox = document.getElementById('qq-error');
  const report = error => { errorBox.textContent = error.message; };
  for (const id of ['qq-manage', 'qq-manage-top']) {
    document.getElementById(id).onclick = async () => {
      errorBox.textContent = '';
      form.elements.recent_days.value = '';
      form.elements.max_messages.value = '';
      dialog.showModal();
      try { await refresh(); } catch (error) { report(error); }
    };
  }
  dialog.addEventListener('close', () => { form.elements.authorization_code.value = ''; });
  form.onsubmit = async event => {
    event.preventDefault();
    let syncOptions;
    try { syncOptions = readQQScope(form); }
    catch (error) { report(error); return; }
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    errorBox.textContent = '';
    const data = { address: form.elements.address.value.trim(), authorization_code: form.elements.authorization_code.value.trim(), sync_options: syncOptions };
    form.elements.authorization_code.value = '';
    try {
      const mailbox = await request('mailboxes/qq-connect/', { method: 'POST', data });
      form.elements.recent_days.value = '';
      form.elements.max_messages.value = '';
      await refresh();
      void track(mailbox.mailbox_id).catch(report);
    } catch (error) { report(error); }
    finally { data.authorization_code = ''; button.disabled = false; }
  };
  document.getElementById('qq-accounts').onclick = async event => {
    const button = event.target.closest('[data-qq-sync], [data-qq-disconnect], [data-qq-view]');
    if (!button) return;
    button.disabled = true;
    errorBox.textContent = '';
    try {
      if (button.dataset.qqView) { dialog.close(); await view(button.dataset.qqView, button.dataset.qqAddress); button.disabled = false; }
      else if (button.dataset.qqSync) { await sync(button.dataset.qqSync); button.disabled = false; }
      else {
        await request(`mailboxes/${encodeURIComponent(button.dataset.qqDisconnect)}/qq-authorization/`, { method: 'DELETE' });
        await refresh();
      }
    } catch (error) { report(error); button.disabled = false; }
  };
}
