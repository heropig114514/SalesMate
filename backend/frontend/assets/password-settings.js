/** Responsibility: Provide a minimal password-change form on the existing personal-profile page.
 * Implementation: Reuse setup form styles; submit current/new/confirmation passwords only on explicit save, clear inputs on success, and retain visible errors on failure.
 * Relationships: company-settings.js mounts this after onboarding; api.js provides same-origin Session/CSRF requests to accounts/me/password/.
 * Directory: mountPasswordSettings, changePassword.
 * Variable index: labels contains Chinese or English form/status text; passwords exist only in form fields and the in-flight request.
 */
import { language } from './i18n.js?v=20260921-product';
import { request } from './api.js?v=20260921-product';

const labels = language === 'en' ? {
  title: 'Change password', current: 'Current password', next: 'New password', confirmation: 'Confirm new password',
  hint: '8–128 characters; no specific character combination required.', save: 'Save password', saving: 'Saving…',
  mismatch: 'New passwords do not match.', incorrect: 'Current password is incorrect.', success: 'Password changed.',
} : {
  title: '修改密码', current: '当前密码', next: '新密码', confirmation: '确认新密码',
  hint: '8–128 位即可，不要求特定字符组合。', save: '保存密码', saving: '正在保存…',
  mismatch: '两次输入的新密码不一致。', incorrect: '当前密码不正确。', success: '密码已修改。',
};

/** Function: Mount password editing directly below personal information. Inputs: The existing personal-form element after onboarding has rendered.
 * Outputs: A separate form shown only on the personal step. Logic: Reuse data-setup-step and setup-form styles, and bind one submit handler.
 * Constraints: No API reads, account selectors, extra pages, or nested forms; repeated mounting does not duplicate controls. */
export function mountPasswordSettings() {
  if (document.getElementById('password-form')) return;
  const form = document.createElement('form');
  form.id = 'password-form';
  form.className = 'setup-form';
  form.dataset.setupStep = '0';
  form.hidden = document.getElementById('personal-form').hidden;
  form.innerHTML = `<h2>${labels.title}</h2><fieldset><div class="setup-fields">
    <label>${labels.current}<input type="password" name="current_password" autocomplete="current-password" required></label>
    <label>${labels.next}<input type="password" name="new_password" autocomplete="new-password" minlength="8" maxlength="128" aria-describedby="password-hint" required></label>
    <label>${labels.confirmation}<input type="password" name="password_confirmation" autocomplete="new-password" minlength="8" maxlength="128" required></label>
    </div><p id="password-hint" class="muted">${labels.hint}</p><button class="primary" type="submit">${labels.save}</button></fieldset>
    <p id="password-status" role="status" aria-live="polite"></p>`;
  form.addEventListener('submit', changePassword);
  document.getElementById('personal-form').after(form);
}

/** Function: Submit a password change for the current session. Inputs: event supplies the submitted form and its three password fields.
 * Outputs: Inline success/error status; successful inputs and dirty markers are cleared.
 * Logic: Check confirmation, disable the fieldset while pending, await the CSRF-protected API, and restore controls in finally.
 * Constraints: No retries, browser storage, password logging, onboarding saves, or navigation; errors preserve input for explicit correction. */
async function changePassword(event) {
  event.preventDefault();
  const form = event.currentTarget, controls = form.querySelector('fieldset'), status = form.querySelector('#password-status');
  if (controls.disabled) return;
  const data = Object.fromEntries(new FormData(form));
  status.classList.remove('is-error');
  if (data.new_password !== data.password_confirmation) {
    status.textContent = labels.mismatch;
    status.classList.add('is-error');
    form.elements.password_confirmation.focus();
    return;
  }
  controls.disabled = true;
  status.textContent = labels.saving;
  try {
    await request('accounts/me/password/', { method: 'POST', data });
    form.reset();
    form.querySelectorAll('[data-language-dirty]').forEach(input => input.removeAttribute('data-language-dirty'));
    status.textContent = labels.success;
  } catch (error) {
    console.error('password_change_failed', { status: error.status, type: error.name });
    status.textContent = error.message === 'Current password is incorrect.' ? labels.incorrect : error.message;
    status.classList.add('is-error');
  } finally {
    controls.disabled = false;
  }
}
