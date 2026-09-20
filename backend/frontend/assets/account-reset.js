/**
 * 职责：提供保留登录身份的账号清空操作。
 * 实现：一次明确确认后发送幂等请求；成功清理缓存、广播并刷新，失败保留操作键供显式重试。
 * 关联：共享语言/API 资源随需求界面统一版本；workspace.js 挂载按钮，api.js 提供 Session/CSRF 请求，account-cache.js 管理缓存。
 * 目录：resetAccountData、showResetRecovery。
 * 变量索引：labels 为当前语言的按钮和状态说明。
 */
import { request } from './api.js?v=20260920-requirements';
import { accountIdentity, finishAccountReset } from './account-cache.js';
import { language } from './i18n.js?v=20260920-requirements';

const labels = language === 'en' ? {
  confirm: 'Keep your login and password, and delete all internal data, connections, attachments and caches? This cannot be undone.',
  busy: 'Clearing…', failure: 'Clearing was not completed: ',
} : {
  confirm: '保留登录账号和密码，删除所有内部数据、连接授权、附件和缓存。此操作不可恢复，确定继续？',
  busy: '正在清空…', failure: '清理尚未完成：',
};

/** 功能：执行用户明确触发的账号清空。输入：button 为触发按钮。输出：成功后刷新页面。
 * 逻辑：先读取认证身份；每个未完成操作保留唯一键，失败不自动重试；服务器确认后清理缓存。
 * 约束：不发送账号选择参数或密码；按钮禁用只防止当前页面重复提交，后端幂等处理网络重复请求。
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

/** 功能：在附件清理失败或刷新后提供可达的恢复入口。输入：无，响应头事件触发。
 * 输出：无，创建覆盖旧业务页面的恢复面板。逻辑：只创建一次，按钮仍走明确确认与相同幂等键。
 * 约束：不自动执行清理，旧业务内容不再展示给用户。
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
