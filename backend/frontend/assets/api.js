/**
 * Responsibility: Centralize same-origin API calls, session CSRF handling, and error-display structures.
 * Implementation: Disable application caching in fetch; send JSON or multipart data with the current language header, adding CSRF and account data versions to writes; validate response versions and support idempotency keys and cancellation of read-only observation.
 * Internationalization: i18n.js translates explicitly marked static text only; dynamic business content and API values remain unchanged.
 * Relationships: The 0919 interface and shared language/API resources use coordinated cache versions; app.js calls this module; the backend uses SessionAuthentication and separate Agent routes.
 * Directory: csrfToken (read the cookie), errorMessage (extract error text), request (make requests), escapeHtml (escape text).
 * Variable index: No mutable module state; BASE is the versioned business API prefix.
 */
import { t, language } from './i18n.js?v=20260921-product';
import { accountVersion, observeAccountVersion } from './account-cache.js?v=20260921-product';

const BASE = '/api/v1/';

/** Function: Read the Django CSRF cookie. Inputs: Implicit document.cookie. Outputs: The token or an empty string.
 * Logic: Parse the exact csrftoken key. Constraints: Never print or persist the token. */
function csrfToken() {
  return document.cookie.split('; ').find(item => item.startsWith('csrftoken='))?.split('=').slice(1).join('=') || '';
}

/** Function: Convert backend validation errors into readable messages. Inputs: detail is a string, array, or field-error object. Outputs: Message text.
 * Logic: Recursively flatten field and array messages and join them with semicolons; let the caller display the HTTP status when no message exists.
 * Constraints: Process received error responses only, without changing their status or executing HTML. */
function errorMessage(detail) {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(errorMessage).filter(Boolean).join('；');
  if (detail && typeof detail === 'object') return Object.values(detail).map(errorMessage).filter(Boolean).join('；');
  return '';
}

/** Function: Call a business API. Inputs: Relative path and options including method, data (JSON or FormData), version, signal, and idempotencyKey.
 * Outputs: Successful JSON or null; throw Error on failure. Logic: Attach language, account data version, and optional idempotency headers; disable HTTP caching, reject stale responses, retain HTTP failure semantics, and flatten field errors.
 * Constraints: Propagate cancellation as AbortError for observers to handle; no retries or fallbacks, and no secrets in URLs. */
export async function request(path, { method = 'GET', data, version, signal, idempotencyKey } = {}) {
  const headers = { 'Accept': 'application/json', 'Accept-Language': language };
  if (data !== undefined && !(data instanceof FormData)) headers['Content-Type'] = 'application/json';
  if (method !== 'GET') headers['X-CSRFToken'] = csrfToken();
  if (version !== undefined) headers['If-Match'] = String(version);
  if (accountVersion() !== null) headers['X-Account-Data-Version'] = accountVersion();
  if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey;
  let response;
  try {
    response = await fetch(BASE + path, { method, headers, signal, cache: 'no-store', credentials: 'same-origin', body: data === undefined ? undefined : data instanceof FormData ? data : JSON.stringify(data) });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error(t('无法连接后端，请检查服务是否启动。'));
  }
  if (path !== 'accounts/me/reset/') await observeAccountVersion(response);
  if (response.status === 204) return null;
  const isJson = response.headers.get('content-type')?.includes('application/json');
  const body = isJson ? await response.json() : null;
  if (!response.ok) {
    const detail = body?.error?.detail;
    const message = errorMessage(detail) || t`请求失败（HTTP ${response.status}），请检查服务日志。`;
    const error = new Error(message);
    error.status = response.status;
    error.requestId = body?.request_id;
    console.error("api_request_failed", { path: path.split("?")[0], status: response.status, requestId: error.requestId });
    throw error;
  }
  return body;
}

/** Function: Escape untrusted text for template interpolation. Inputs: value is any raw field. Outputs: HTML-safe text.
 * Logic: Escape the five HTML metacharacters. Constraints: Use only for text or quoted attributes, never executable code or URL schemes. */
export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
}
