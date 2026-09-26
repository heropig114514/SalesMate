/**
 * Responsibility: Reset account data while preserving the login identity.
 * Implementation: Send an idempotent request after explicit confirmation; on success clear caches, broadcast, and reload; on failure retain the operation key for an explicit retry.
 * Relationships: The 0919 interface and shared language/API resources use coordinated cache versions; workspace.js mounts the button, api.js supplies Session/CSRF requests, and account-cache.js manages caches.
 * Directory: resetAccountData, showResetRecovery.
 * Variable index: labels contains button and status text in the current language.
 */
import { request } from './api.js?v=20260921-product';
import { accountIdentity, finishAccountReset } from './account-cache.js?v=20260921-product';
import { language } from './i18n.js?v=20260921-product';

const labels = language === 'en' ? {
  confirm: 'Keep your login and password, and delete all internal data, connections, attachments and caches? This cannot be undone.',
  busy: 'Clearing…', failure: 'Clearing was not completed: ',
} : {
  confirm: '保留登录账号和密码，删除所有内部数据、连接授权、附件和缓存。此操作不可恢复，确定继续？',
  busy: '正在清空…', failure: '清理尚未完成：',
};

/** Function: Perform an account reset explicitly requested by the user. Inputs: button is the triggering button. Outputs: Reload the page on success.
 * Logic: Read the authenticated identity first; retain a unique key for each incomplete operation, never retry automatically, and clear caches after server confirmation.
 * Constraints: Send neither account selection parameters nor passwords; disabling the button prevents duplicate submissions only on this page, while backend idempotency handles duplicate network requests.
 */
export async function resetAccountData(button) {
  if (!confirm(labels.confirm)) return;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = labels.busy;
  try {
    await request('accounts/me/');
    const owner = accountIdentity();
    if (owner === null) throw new Error('无法核对当前账号，请刷新页面。');
    const storageKey = `salesmate:${owner}:reset-key`;
    const key = sessionStorage.getItem(storageKey) || crypto.randomUUID();
    sessionStorage.setItem(storageKey, key);
    const result = await request('accounts/me/reset/', { method: 'POST', idempotencyKey: key });
    await finishAccountReset(owner, result.generation);
  } catch (error) {
    console.error('account_reset_failed', { type: error.name, status: error.status });
    alert(labels.failure + error.message);
    button.disabled = false;
    button.textContent = original;
  }
}

/** Function: Provide accessible recovery after attachment cleanup fails or the page reloads. Inputs: None; triggered by a response-header event.
 * Outputs: None; create a recovery panel covering the old application page. Logic: Create the panel once; its button still requires explicit confirmation and uses the same idempotency key.
 * Constraints: Never run cleanup automatically or display old application content to the user.
 */
function showResetRecovery() {
  if (document.getElementById('account-reset-recovery')) return;
  const panel = document.createElement('section');
  panel.id = 'account-reset-recovery';
  panel.setAttribute('role', 'alert');
  panel.style.cssText = 'position:fixed;inset:0;z-index:100000;background:white;display:grid;place-content:center;gap:24px;padding:32px';
  const text = document.createElement('p');
  text.textContent = language === 'en' ? 'Account cleanup is incomplete. Continue to finish removing files and caches.' : '账号数据清理尚未完成，请继续处理剩余附件和缓存。';
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'primary';
  button.textContent = language === 'en' ? 'Continue cleanup' : '继续清空';
  button.onclick = () => resetAccountData(button);
  panel.append(text, button);
  document.body.append(panel);
}
window.addEventListener('salesmate:reset-incomplete', showResetRecovery);
