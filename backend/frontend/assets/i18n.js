/**
 * Responsibility: Unify Chinese/English interface language, static translation, and user preferences without processing business content.
 * Implementation: An explicit language cookie overrides browser preferences; translation applies only to t/h literals and data-i18n markers.
 * Relationships: The 0919 interface and shared language/API resources use coordinated cache versions; Django LocaleMiddleware shares django_language; API requests use language; translations.js contains the English catalog.
 * Directory: resolveLanguage, t, h, initializeLanguage, changeLanguage.
 * Variable index: language is this page's protocol language; locale controls date formatting; preference is an explicit selection or auto; literalChunks identifies static HTML text boundaries.
 */
import { EN } from './translations.js?v=20260921-product';

/** Function: Resolve a supported interface language. Inputs: Explicit preference and browser languages list.
 * Outputs: en or zh-hans. Logic: Prefer an explicit choice, otherwise find Chinese/English in browser preferences.
 * Constraints: Retain the project's Chinese default when no supported language exists; never infer business-content language. */
export function resolveLanguage(preference, languages) {
  if (['en', 'zh-hans'].includes(preference)) return preference;
  for (const item of languages) {
    if (/^zh(?:-|$)/i.test(item)) return 'zh-hans';
    if (/^en(?:-|$)/i.test(item)) return 'en';
  }
  return 'zh-hans';
}

const preference = typeof document === 'undefined' ? 'auto'
  : document.cookie.split(';').map(item => item.trim()).find(item => item.startsWith('django_language='))?.slice(16) || 'auto';
export const language = resolveLanguage(preference, typeof document === 'undefined' ? [] : navigator.languages || [navigator.language]);
export const locale = language === 'en' ? 'en-US' : 'zh-CN';
const literalChunks = /[^\x00-\x1f<>"'=]*[\u3400-\u9fff][^\x00-\x1f<>"'=]*/g;

/** Function: Translate explicit interface text or parameterized templates. Inputs: source is a string/template array; values are untranslated interpolations.
 * Outputs: A current-language string. Logic: Numbered placeholders support English word order; retain leading/trailing source whitespace.
 * Constraints: Never recursively translate interpolations or user input; unknown keys retain source text according to gettext source-language semantics. */
export function t(source, ...values) {
  const template = Array.isArray(source);
  const original = template ? source.map((part, i) => part + (i < values.length ? `{${i}}` : '')).join('') : String(source);
  const key = original.trim();
  const translated = language === 'en' && Object.hasOwn(EN, key) ? original.replace(key, () => EN[key]) : original;
  return template ? translated.replace(/\{(\d+)\}/g, (match, index) => Number(index) < values.length ? String(values[index]) : match) : translated;
}

/** Function: Translate static HTML literals. Inputs: source string/template array and interpolated values.
 * Outputs: HTML with the original template concatenation semantics. Logic: Translate registered literal fragments before concatenating dynamic values.
 * Constraints: Never scan assembled HTML; callers must escape dynamic data. Catalog translations must not introduce HTML tags or attribute quotes into fragments. */
export function h(source, ...values) {
  const parts = Array.isArray(source) ? source : [source];
  return parts.map((part, i) => part.replace(literalChunks, text => t(text)) + (i < values.length ? String(values[i]) : '')).join('');
}

/** Function: Apply language and bind preference controls. Inputs: Parsed document. Outputs: None.
 * Logic: Update explicitly marked static text/attributes only, retaining surrounding whitespace; capture user edits, including non-bubbling quick-prompt events, to protect drafts before reload.
 * Constraints: Never modify control values, business nodes, or saved content, or issue business requests. */
function initializeLanguage() {
  document.documentElement.lang = language;
  for (const node of document.querySelectorAll('[data-i18n]')) node.textContent = t(node.textContent);
  for (const attr of ['aria-label', 'placeholder', 'title']) {
    for (const node of document.querySelectorAll(`[data-i18n-${attr}]`)) node.setAttribute(attr, t(node.getAttribute(`data-i18n-${attr}`)));
  }
  const selector = document.getElementById('interface-language');
  if (selector) {
    selector.value = ['en', 'zh-hans'].includes(preference) ? preference : 'auto';
    selector.addEventListener('change', changeLanguage);
  }
  document.addEventListener('input', event => {
    if (event.target.matches('input, textarea, select') && event.target !== selector) event.target.dataset.languageDirty = 'true';
  }, true);
}

/** Function: Save or clear the explicit language preference and reload. Inputs: event's language selector.
 * Outputs: None. Logic: Confirm reloading if edits exist; cancellation retains the page and previous choice; auto mode deletes the override cookie.
 * Constraints: Never persist input/passwords or submit forms; the cookie stores language only and applies to all pages in this browser. */
function changeLanguage(event) {
  if (document.querySelector('[data-language-dirty="true"]') && !window.confirm(t('切换语言会重新加载页面，未保存的输入将丢失。是否继续？'))) {
    event.target.value = ['en', 'zh-hans'].includes(preference) ? preference : 'auto';
    return;
  }
  const selected = event.target.value;
  document.cookie = `django_language=${selected === 'auto' ? '' : selected}; Path=/; Max-Age=${selected === 'auto' ? 0 : 31536000}; SameSite=Lax${location.protocol === 'https:' ? '; Secure' : ''}`;
  location.reload();
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initializeLanguage, { once: true });
  else initializeLanguage();
}
