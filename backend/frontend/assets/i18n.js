/**
 * 职责：统一中英文界面语言、静态翻译及用户偏好，不处理业务正文。
 * 实现：显式语言 cookie 优先于浏览器语言；翻译仅作用于 t/h 字面量和 data-i18n 标记。
 * 关联：0919 界面及共享语言资源统一缓存版本；共享语言/API 资源随需求界面统一版本；Django LocaleMiddleware 使用同一 django_language cookie；API 请求使用 language；translations.js 保存英文目录。
 * 目录：resolveLanguage、t、h、initializeLanguage、changeLanguage。
 * 变量索引：language 为本页协议语言；locale 为日期显示区域；preference 为手动选择或 auto；literalChunks 为静态 HTML 文案边界。
 */
import { EN } from './translations.js?v=20260921-product';

/** 功能：解析支持的界面语言。输入：preference 手动选择及 languages 浏览器语言列表。
 * 输出：en 或 zh-hans。逻辑：手动选择优先，否则按浏览器偏好查找中英文。
 * 约束：没有支持的语言时沿用项目中文默认值；不猜测业务内容语言。 */
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

/** 功能：翻译显式界面文本或带参数的模板。输入：source 字符串/模板数组，values 为未翻译的插值。
 * 输出：当前语言的字符串。逻辑：使用编号占位符支持英文语序；保留源文案首尾空白。
 * 约束：不递归处理插值，不将用户输入交给翻译；未知键保持源文案，属于 gettext 源语言语义。 */
export function t(source, ...values) {
  const template = Array.isArray(source);
  const original = template ? source.map((part, i) => part + (i < values.length ? `{${i}}` : '')).join('') : String(source);
  const key = original.trim();
  const translated = language === 'en' && Object.hasOwn(EN, key) ? original.replace(key, () => EN[key]) : original;
  return template ? translated.replace(/\{(\d+)\}/g, (match, index) => Number(index) < values.length ? String(values[index]) : match) : translated;
}

/** 功能：翻译静态 HTML 字面量。输入：source 字符串/模板数组及 values 插值。
 * 输出：与原模板相同拼接语义的 HTML。逻辑：只翻译字面量中的已登记片段，再拼接动态值。
 * 约束：不扫描拼接后的 HTML；调用方仍须转义动态数据。目录不允许 HTML 标记或属性引号进入片段译文。 */
export function h(source, ...values) {
  const parts = Array.isArray(source) ? source : [source];
  return parts.map((part, i) => part.replace(literalChunks, text => t(text)) + (i < values.length ? String(values[i]) : '')).join('');
}

/** 功能：应用语言并绑定偏好控件。输入：已解析的 document。输出：无。
 * 逻辑：仅更新静态标记的文本/属性并保留文本首尾空白；捕获用户输入（包括不冒泡的快捷提示事件）以保护重载前的草稿。
 * 约束：不修改控件 value、业务节点或已保存内容；不发起业务请求。 */
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

/** 功能：保存或清除显式语言偏好并重载。输入：event 的语言选择控件。
 * 输出：无。逻辑：存在用户编辑时确认重载；取消则保留页面与原选择；自动模式删除覆盖 cookie。
 * 约束：不持久化输入/密码，不提交表单；cookie 仅保存语言，适用于此浏览器所有页面。 */
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
