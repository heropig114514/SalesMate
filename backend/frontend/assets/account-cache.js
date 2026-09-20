/**
 * 职责：管理账号数据版本、重置后的浏览器缓存和多标签页通知。
 * 实现：版本头防止旧响应渲染，BroadcastChannel 通知同账号页面；刷新释放页面内存。
 * 关联：api.js 在读写边界调用；account-reset.js 在服务端清理成功后调用。
 * 目录：accountVersion、accountIdentity、clearAccountCache、finishAccountReset、observeAccountVersion、refreshAccountVersion。
 * 变量索引：identity/generation 为本标签页身份与数据版本；resetting 为已开始刷新标记；incomplete 为附件清理待继续标记；channel 为同源通知通道。
 */
let identity = null, generation = null, resetting = false, incomplete = false;
const channel = typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel('salesmate-account-reset');

/** 功能：返回请求应携带的数据版本。输入：模块状态。输出：字符串或 null。
 * 逻辑：首次认证响应前不生成版本。约束：版本不作为身份认证。 */
export function accountVersion() { return generation; }

/** 功能：返回当前账号标识。输入：模块状态。输出：字符串或 null。
 * 逻辑：由后端响应头赋值。约束：不能用于选择待删除的后端账号。 */
export function accountIdentity() { return identity; }

/** 功能：清除本账号的持久浏览器缓存。输入：owner 为账号标识。输出：异步完成。
 * 逻辑：清理约定 salesmate:owner: 前缀的 Web Storage、Cache Storage 和 IndexedDB；当前业务内存由后续刷新释放。
 * 约束：保留认证 cookie，不删除其他账号存储；当前业务不使用 IndexedDB，枚举仅覆盖支持该 API 的浏览器。
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

/** 功能：完成客户端清理并返回新账号工作台。输入：owner、version、broadcast 是否通知其他标签页。
 * 输出：异步完成后导航。逻辑：广播让其他页释放状态，删除当前账号缓存，刷新丢弃所有模块内存及未保存草稿。
 * 约束：不清除密码或登录 cookie；缓存清理失败保留错误，不伪装完成。
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

/** 功能：验证响应属于当前数据版本。输入：response 为 fetch 响应。输出：无，过时响应抛 AbortError。
 * 逻辑：首次响应建立版本；完成的新版本触发清理，未完成的版本显示恢复入口；旧版本禁止覆盖页面。
 * 约束：不信任正文提供的账号身份；AbortError 只表示页面数据已失效，不自动重发写操作。
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

/** 功能：恢复标签页时核对服务端数据版本。输入：当前模块身份。输出：异步完成。
 * 逻辑：只读查询当前身份，处理错过的广播。约束：网络失败记录类型，不清空仍有效的页面或自动重试。
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
