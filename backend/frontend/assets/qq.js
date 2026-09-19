/**
 * 职责：管理 QQ 邮箱连接、同步、按账号查看原文和移除交互。
 * 实现：每次要求显式选择天数或封数，无预填；只在提交时读取授权码并立即清空，复用批次轮询。
 * 国际化：i18n.js 仅翻译显式标记的静态文案；动态业务正文和接口值保持原样。
 * 关联：app.js 注入共享回调，index.html 提供 QQ 独立弹窗，api.js 处理 Session/CSRF。
 * 目录：readQQScope（校验选择）、chooseQQScope（每次选择范围）、renderQQAccounts（渲染安全状态）、initQQ（注册连接及账号操作）。
 * 变量索引：statusLabels 为同步状态的当前语言映射。
 */
import { t, h } from './i18n.js?v=20260920-i18n';

import { request, escapeHtml as e } from './api.js';

const statusLabels = { authorization_required: t('未连接'), sync_requested: t('等待同步'), sync_running: t('正在同步'), completed: t('同步完成'), partial: t('部分完成'), failed: t('同步失败') };

/** 功能：读取一次明确的同步限制。输入：form 为本次表单。输出：天数和封数对象。
 * 逻辑：空项转 null，至少一项为正安全整数。约束：不读取历史值，不用默认值替代空选择。 */
function readQQScope(form) {
  const options = Object.fromEntries(['recent_days', 'max_messages'].map(name => [name, form.elements[name].value === '' ? null : Number(form.elements[name].value)]));
  if (!Object.values(options).some(value => value !== null)) throw new Error(t('请填写最近 N 天或最多 N 封，至少一项。'));
  if (Object.values(options).some(value => value !== null && (!Number.isSafeInteger(value) || value <= 0))) throw new Error(t('同步限制必须为正整数。'));
  return options;
}

/** 功能：同步前等待用户选择空白范围。输入：address 为展示用邮箱地址。输出：所选范围，取消返回 null。
 * 逻辑：每次重置表单，通过提交或关闭完成 Promise。约束：取消不发请求，不缓存选择，避免无界同步。 */
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

/** 功能：渲染当前员工 QQ 账号。输入：mailboxes 为安全邮箱状态数组。输出：无。
 * 逻辑：展示 qq_authorized 账号及同步/查看原文/移除按钮。约束：所有外部文本转义，活动批次只允许只读查看。 */
export function renderQQAccounts(mailboxes) {
  document.getElementById('qq-accounts').innerHTML = mailboxes.filter(item => item.qq_authorized).map(item => {
    const status = item.sync_state?.status || 'authorization_required';
    const active = ['sync_requested', 'sync_running'].includes(status);
    return h`<article class="gmail-account"><div><strong>${e(item.address)}</strong><p>${e(statusLabels[status] || status)}</p>${item.sync_state?.error ? `<small class="failure">${e(item.sync_state.error)}</small>` : ''}</div><div class="account-actions"><button type="button" class="secondary" data-qq-sync="${e(item.mailbox_id)}" ${active ? 'disabled' : ''}>${active ? t('同步处理中…') : t('同步 QQ')}</button><button type="button" class="secondary" data-qq-view="${e(item.mailbox_id)}" data-qq-address="${e(item.address)}">查看已同步邮件</button><button type="button" class="text-btn" data-qq-disconnect="${e(item.mailbox_id)}" ${active ? 'disabled' : ''}>移除连接</button></div></article>`;
  }).join('') || h('<div class="gmail-empty"><strong>尚未连接 QQ 邮箱</strong><p>在下方填写邮箱和客户端授权码。</p></div>');
}

/** 功能：绑定 QQ 连接与同步入口。输入：refresh 刷新邮箱状态，sync 请求同步，track 跟踪已排队邮箱，view 查看指定邮箱原文。输出：无。
 * 逻辑：先校验范围再连接，成功后清空范围并轮询；已有账号交给共享 sync 回调询问范围。约束：授权码不进 URL、日志或浏览器存储。 */
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
