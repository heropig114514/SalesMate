/** 职责：独立本公司资料设置页，按账号读取、编辑和保存。
 * 实现：共享导航和底部助手；显式保存附版本，失败保留输入；冲突要求用户重新读取。
 * 关联：company-settings.html/css；accounts/company-profile API；api.js 处理 CSRF 与错误。
 * 目录：text、showStatus、renderForm、loadProfile、saveProfile、boot。
 * 变量索引：fields 为字段及中英文名称；revision 为当前已读取版本；busy 防止重叠操作；$ 查询 DOM。
 */
import { language } from './i18n.js?v=20260920-i18n';
import { request, escapeHtml as e } from './api.js';
import { mountWorkspace } from './workspace.js?v=20260920-profile';

const $ = id => document.getElementById(id);
const fields = [
  ['company_name', '公司名称', 'Company name', 'text', 240],
  ['industry', '行业', 'Industry', 'text', 100],
  ['website', '公司网站', 'Website', 'url', 500],
  ['email', '联系邮箱', 'Contact email', 'email', 254],
  ['phone', '联系电话', 'Phone', 'tel', 80],
  ['address', '公司地址', 'Address', 'text', 500],
  ['description', '公司简介', 'About your company', 'textarea', 5000],
];
let revision = null, busy = false;

/** 功能：选择本页双语文案。输入：zh 中文、en 英文。输出：当前界面语言文本。
 * 逻辑：沿用共享语言偏好。约束：不翻译用户资料。 */
function text(zh, en) { return language === 'en' ? en : zh; }

/** 功能：显示可访问状态。输入：message 文案、error 错误标志。输出：无。
 * 逻辑：textContent 防止数据解释为 HTML。约束：错误保留表单内容。 */
function showStatus(message, error = false) {
  $('company-status').textContent = message;
  $('company-status').classList.toggle('is-error', error);
}

/** 功能：呈现固定字段并填入资料。输入：profile 授权 API 结果。输出：无。
 * 逻辑：值转义，保留后端长度与必填约束；读取成功后才允许保存。约束：不保存数据，不填业务默认值。 */
function renderForm(profile) {
  $('company-fields').innerHTML = fields.map(([key, zh, en, type, limit]) => {
    const value = e(profile[key] || '');
    return `<label class="${type === 'textarea' ? 'company-wide' : ''}" for="company-${key}">${e(text(zh, en))}${key === 'company_name' ? ' *' : ''}${type === 'textarea'
      ? `<textarea id="company-${key}" name="${key}" maxlength="${limit}" rows="5">${value}</textarea>`
      : `<input id="company-${key}" name="${key}" type="${type}" maxlength="${limit}" value="${value}" ${key === 'company_name' ? 'required' : ''}>`}</label>`;
  }).join('');
  revision = profile.revision;
}

/** 功能：读取服务器资料。输入：无，读取 busy 和表单修改状态。输出：异步完成。
 * 逻辑：重新读取前确认舍弃编辑；失败保留现有资料，首次失败禁用保存。约束：不自动重试、不覆盖未确认的草稿。 */
async function loadProfile() {
  if (busy) return;
  if ($('company-form').querySelector('[data-language-dirty="true"]') && !window.confirm(text('重新读取将丢弃未保存的修改，是否继续？', 'Reloading will discard unsaved changes. Continue?'))) return;
  busy = true;
  $('company-controls').disabled = true;
  showStatus(text('正在读取公司资料…', 'Loading company profile…'));
  try {
    renderForm(await request('accounts/company-profile/'));
    showStatus(revision ? text('公司资料已载入。', 'Company profile loaded.') : text('填写公司资料后点击保存。', 'Enter your company details and save.'));
  } catch (error) {
    console.error('company_profile_load_failed', { status: error.status });
    showStatus(error.message, true);
  } finally {
    busy = false;
    $('company-controls').disabled = false;
    $('company-save').disabled = revision === null;
  }
}

/** 功能：保存用户明确编辑的资料。输入：event 为表单提交。输出：异步完成。
 * 逻辑：带已读版本 PATCH；成功更新版本并清除草稿标记；冲突保留输入并要求重新读取。
 * 约束：无自动覆盖、无重试；日志仅含状态码，不记录用户资料。 */
async function saveProfile(event) {
  event.preventDefault();
  if (busy || revision === null) return;
  const data = Object.fromEntries(new FormData($('company-form')));
  busy = true;
  $('company-controls').disabled = true;
  showStatus(text('正在保存…', 'Saving…'));
  try {
    renderForm(await request('accounts/company-profile/', { method: 'PATCH', data, version: revision }));
    showStatus(text('公司资料已保存。', 'Company profile saved.'));
  } catch (error) {
    console.error('company_profile_save_failed', { status: error.status });
    showStatus(error.status === 409 ? text('资料已在其他页面更新。请先保留你的修改，再点击“重新读取”后编辑。', 'This profile changed in another page. Keep a copy of your edits, then reload before saving.') : error.message, true);
  } finally {
    busy = false;
    $('company-controls').disabled = false;
  }
}

/** 功能：认证并挂载独立设置页。输入：当前会话与页面 DOM。输出：异步完成。
 * 逻辑：未登录跳转登录页；已登录显示账号、共享导航、双语表单并读取资料。
 * 约束：仅显式提交时写 API；会话错误显示诊断，不假定已登录。 */
async function boot() {
  $('company-description').textContent = text('管理本公司的基本资料。资料保存在当前账号的工作空间中。', 'Manage your company details, saved in your current account workspace.');
  $('company-save').textContent = text('保存修改', 'Save changes');
  $('company-reload').textContent = text('重新读取', 'Reload');
  try {
    const session = await request('session/');
    if (!session.authenticated) { location.replace('/'); return; }
    $('account').textContent = session.username;
    mountWorkspace('company-settings');
    $('company-form').addEventListener('submit', saveProfile);
    $('company-reload').addEventListener('click', loadProfile);
    await loadProfile();
  } catch (error) {
    console.error('company_settings_boot_failed', { status: error.status });
    showStatus(error.message, true);
  }
}
void boot();
