/** Responsibility: Read, edit, and save the company step of four-step onboarding per account.
 * Implementation: Shared navigation/bottom assistant; explicit saves include a version, failures preserve input, and conflicts require reloading.
 * Relationships: Navigation cache versions reflect removal of sidebar priority/experiment entries and opportunity-priority support. Profile item identifiers, chat Markdown, 0919 interface, account-reset navigation, and shared language/API resources use coordinated versions. Workspace chat upgrades avoid cached company-specific entry points. Uses company-settings.html/css, accounts/company-profile API, and api.js for CSRF/errors.
 * Directory: text, showStatus, renderForm, loadProfile, saveProfile, boot.
 * Variable index: choices holds industry/size options; fields holds fields and bilingual names; revision is the observed version; busy prevents overlapping operations; $ queries the DOM.
 */
import { mountOnboarding } from './onboarding.js?v=20260921-support';
import { language } from './i18n.js?v=20260921-product';
import { request, escapeHtml as e } from './api.js?v=20260921-product';
import { mountWorkspace } from './workspace.js?v=20260922-sidebar';

const $ = id => document.getElementById(id);
const choices = { industry: [['半导体检测','半导体检测','Semiconductor inspection'],['精密量测','精密量测','Precision metrology'],['光学检测','光学检测','Optical inspection'],['工业检测','工业检测','Industrial inspection']], size_band: [['lt_50','少于 50 人','Under 50'],['50_100','50–99 人','50–99'],['100_200','100–199 人','100–199'],['200_500','200–499 人','200–499'],['gte_500','500 人及以上','500+']] };
const fields = [
  ['company_name', '公司名称', 'Company name', 'text', 240],
  ['industry', '行业', 'Industry', 'select', 100],
  ['size_band', '公司规模', 'Company size', 'select', 30],
  ['website', '公司网站', 'Website', 'url', 500],
  ['email', '联系邮箱', 'Contact email', 'email', 254],
  ['phone', '联系电话', 'Phone', 'tel', 80],
  ['address', '公司地址', 'Address', 'text', 500],
  ['description', '公司简介', 'About your company', 'textarea', 5000],
];
let revision = null, busy = false;

/** Function: Select bilingual text for this page. Inputs: zh is Chinese and en is English. Outputs: Current-language text.
 * Logic: Use the shared language preference. Constraints: Never translate user profiles. */
function text(zh, en) { return language === 'en' ? en : zh; }

/** Function: Display accessible status. Inputs: message and error flag. Outputs: None.
 * Logic: textContent prevents interpretation as HTML. Constraints: Errors preserve form content. */
function showStatus(message, error = false) {
  $('company-status').textContent = message;
  $('company-status').classList.toggle('is-error', error);
}

/** Function: Render fixed fields and populate profile values. Inputs: profile is the authorized API result. Outputs: None.
 * Logic: Escape values, align industries with the inbox, and use explicit size options. Preserve historical custom values and backend length/required constraints; enable saving only after a successful read. Constraints: No data writes or inferred business defaults. */
function renderForm(profile) {
  $('company-fields').innerHTML = fields.map(([key, zh, en, type, limit]) => {
    const value = e(profile[key] || '');
    if (type === 'select') {
      const options = [...choices[key]];
      if (profile[key] && !options.some(([v]) => v === profile[key])) options.push([profile[key], profile[key], profile[key]]);
      return `<label for="company-${key}">${e(text(zh,en))}<select id="company-${key}" name="${key}"><option value="">${text('尚未填写','Not specified')}</option>${options.map(([v,zh,en]) => `<option value="${e(v)}" ${v === profile[key] ? 'selected' : ''}>${e(text(zh,en))}</option>`).join('')}</select></label>`;
    }
    return `<label class="${type === 'textarea' ? 'company-wide' : ''}" for="company-${key}">${e(text(zh, en))}${key === 'company_name' ? ' *' : ''}${type === 'textarea'
      ? `<textarea id="company-${key}" name="${key}" maxlength="${limit}" rows="5">${value}</textarea>`
      : `<input id="company-${key}" name="${key}" type="${type}" maxlength="${limit}" value="${value}" ${key === 'company_name' ? 'required' : ''}>`}</label>`;
  }).join('');
  revision = profile.revision;
}

/** Function: Read the server profile. Inputs: None; reads busy and form-dirty state. Outputs: Asynchronous completion.
 * Logic: Confirm discarding edits before reloading; retain existing data on failure and disable saving after an initial read failure. Constraints: No automatic retries or unconfirmed draft replacement. */
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

/** Function: Save explicitly edited profile data. Inputs: event is the form submission. Outputs: Asynchronous completion.
 * Logic: PATCH with the observed version; on success update revision, clear the draft marker, and notify onboarding to advance. Conflicts retain input and require reloading.
 * Constraints: No automatic overwrites or retries; logs contain status codes only, never user profiles. */
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
    window.dispatchEvent(new Event('company-profile-saved'));
  } catch (error) {
    console.error('company_profile_save_failed', { status: error.status });
    showStatus(error.status === 409 ? text('资料已在其他页面更新。请先保留你的修改，再点击“重新读取”后编辑。', 'This profile changed in another page. Keep a copy of your edits, then reload before saving.') : error.message, true);
  } finally {
    busy = false;
    $('company-controls').disabled = false;
  }
}

/** Function: Authenticate and mount the separate settings page. Inputs: Current session and page DOM. Outputs: Asynchronous completion.
 * Logic: Redirect anonymous users to login; authenticated users see the account, shared navigation, and bilingual form. Read the profile before mounting four-step onboarding.
 * Constraints: Write APIs only on explicit submission; display session diagnostics without assuming authentication. */
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
    await mountOnboarding();
  } catch (error) {
    console.error('company_settings_boot_failed', { status: error.status });
    showStatus(error.message, true);
  }
}
void boot();
