/**
 * Responsibility: Manage account data versions, browser caches after reset, and notifications across tabs.
 * Implementation: Version headers prevent stale response rendering; BroadcastChannel notifies pages for the same account; reloading releases page memory.
 * Relationships: api.js calls this module at read/write boundaries; account-reset.js calls it after server cleanup succeeds.
 * Directory: accountVersion, accountIdentity, clearAccountCache, finishAccountReset, observeAccountVersion, refreshAccountVersion.
 * Variable index: identity/generation hold this tab's identity and data version; resetting records whether reload has started; incomplete marks pending attachment cleanup; channel provides same-origin notifications.
 */
let identity = null, generation = null, resetting = false, incomplete = false;
const channel = typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel('salesmate-account-reset');

/** Function: Return the data version to attach to requests. Inputs: Module state. Outputs: A string or null.
 * Logic: Do not generate a version before the first authenticated response. Constraints: The version is not an authentication credential. */
export function accountVersion() { return generation; }

/** Function: Return the current account identifier. Inputs: Module state. Outputs: A string or null.
 * Logic: Read the value assigned from backend response headers. Constraints: This identifier cannot select the backend account to delete. */
export function accountIdentity() { return identity; }

/** Function: Clear this account's persistent browser caches. Inputs: owner is the account identifier. Outputs: Asynchronous completion.
 * Logic: Clear Web Storage, Cache Storage, and IndexedDB entries with the agreed salesmate:owner: prefix; the subsequent reload releases current application memory.
 * Constraints: Preserve authentication cookies and other accounts' storage; the application does not currently use IndexedDB, and enumeration is limited to browsers supporting that API.
 */
export async function clearAccountCache(owner) {
  const prefix = `salesmate:${owner}:`;
  for (const storage of [localStorage, sessionStorage]) {
    for (const key of Object.keys(storage)) if (key.startsWith(prefix) && key !== prefix + 'reset-key') storage.removeItem(key);
  }
  if ('caches' in window) {
    for (const key of await caches.keys()) if (key.startsWith(prefix)) await caches.delete(key);
  }
  if (typeof indexedDB !== 'undefined' && indexedDB.databases) {
    for (const database of await indexedDB.databases()) {
      if (!database.name?.startsWith(prefix)) continue;
      await new Promise((resolve, reject) => {
        const deletion = indexedDB.deleteDatabase(database.name);
        deletion.onsuccess = resolve;
        deletion.onerror = () => reject(deletion.error);
        deletion.onblocked = () => reject(new Error('请关闭其他打开此账号数据的页面后重试清理缓存。'));
      });
    }
  }
  sessionStorage.removeItem(prefix + 'reset-key');
}

/** Function: Complete client cleanup and return to the account's fresh workspace. Inputs: owner, version, and broadcast, which controls notifications to other tabs.
 * Outputs: Navigate after asynchronous completion. Logic: Broadcast to release other pages' state, delete this account's caches, and reload to discard module memory and unsaved drafts.
 * Constraints: Preserve passwords and login cookies; surface cache cleanup failures instead of reporting completion.
 */
export async function finishAccountReset(owner, version, broadcast = true) {
  resetting = true;
  if (broadcast) channel?.postMessage({ owner: String(owner), generation: String(version) });
  try {
    await clearAccountCache(owner);
    location.replace('/?account_reset=' + encodeURIComponent(version) + '#home');
  } catch (error) {
    resetting = false;
    throw error;
  }
}

/** Function: Verify that a response belongs to the current data version. Inputs: response is a fetch response. Outputs: None; stale responses throw AbortError.
 * Logic: The first response establishes the version; a completed new version triggers cleanup, an incomplete version shows recovery, and an older version cannot overwrite the page.
 * Constraints: Do not trust account identity from the body; AbortError only signals invalidated page data and never automatically replays writes.
 */
export async function observeAccountVersion(response) {
  const owner = response.headers.get('X-Account-ID'), version = response.headers.get('X-Account-Data-Version');
  if (resetting) throw new DOMException('Account data reset', 'AbortError');
  if (owner === null || version === null) return;
  if (response.headers.get('X-Account-Reset-Status') === 'cleaning') {
    identity = owner;
    generation = version;
    incomplete = true;
    window.dispatchEvent(new Event('salesmate:reset-incomplete'));
    return;
  }
  if (identity === owner && incomplete) {
    await finishAccountReset(owner, version);
    throw new DOMException('Account data reset', 'AbortError');
  }
  if (identity === owner && generation !== null && version !== generation) {
    if (BigInt(version) > BigInt(generation)) await finishAccountReset(owner, version);
    throw new DOMException('Account data reset', 'AbortError');
  }
  identity = owner;
  generation = version;
}

/** Function: Check the server data version when a tab resumes. Inputs: Current module identity. Outputs: Asynchronous completion.
 * Logic: Query the current identity without writing to recover missed broadcasts. Constraints: Log the type of network failure without clearing a still-valid page or retrying automatically.
 */
async function refreshAccountVersion() {
  if (identity === null || resetting) return;
  try {
    const response = await fetch('/api/v1/accounts/me/', { credentials: 'same-origin', cache: 'no-store' });
    await observeAccountVersion(response);
  } catch (error) {
    if (error.name !== 'AbortError') console.error('account_version_refresh_failed', { type: error.name });
  }
}

if (channel) channel.onmessage = event => {
  if (identity === event.data?.owner && !resetting && (incomplete || BigInt(event.data.generation) > BigInt(generation))) {
    finishAccountReset(identity, event.data.generation, false).catch(error => {
      console.error('account_cache_clear_failed', { type: error.name });
      alert('账号数据已清空，但本页缓存清理失败，请关闭页面后重新打开。');
    });
  }
};
window.addEventListener('focus', refreshAccountVersion);
window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
