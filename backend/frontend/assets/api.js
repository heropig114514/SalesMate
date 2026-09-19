/**
 * 职责：集中处理同源 API、会话 CSRF 和错误显示所需的结构。
 * 实现：fetch 发送 JSON，写请求附 CSRF；支持调用方取消只读观察，保留失败状态并展开表单错误。
 * 关联：app.js 调用此模块；后端使用 SessionAuthentication 与独立 Agent 路由。
 * 目录：csrfToken（读取 cookie）；errorMessage（提取错误文本）；request（执行请求）；escapeHtml（转义文本）。
 * 变量索引：无模块状态；BASE 为版本化业务 API 前缀。
 */
const BASE = '/api/v1/';

/** 功能：读取 Django CSRF cookie。输入：隐式 document.cookie。输出：令牌或空字符串。
 * 逻辑：解析精确 csrftoken 键。约束：不打印或持久化令牌。 */
function csrfToken() {
  return document.cookie.split('; ').find(item => item.startsWith('csrftoken='))?.split('=').slice(1).join('=') || '';
}

/** 功能：将后端校验错误转换为可读提示。输入：detail 为字符串、数组或字段错误对象。输出：提示文本。
 * 逻辑：递归展开字段和数组中的消息并以分号连接；无消息时交由调用方显示 HTTP 状态。
 * 约束：仅处理已收到的错误响应，不修改错误状态、不执行 HTML。 */
function errorMessage(detail) {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(errorMessage).filter(Boolean).join('；');
  if (detail && typeof detail === 'object') return Object.values(detail).map(errorMessage).filter(Boolean).join('；');
  return '';
}

/** 功能：调用业务 API。输入：path 相对路径，options 可包含 method、data、version、signal。
 * 输出：成功 JSON 或 null；失败抛 Error。逻辑：保持 HTTP 失败语义，展开字段错误并附 request_id。
 * 约束：取消保留 AbortError 交给观察者处理；无重试、无降级，不将秘密放入 URL。 */
export async function request(path, { method = 'GET', data, version, signal } = {}) {
  const headers = { 'Accept': 'application/json' };
  if (data !== undefined) headers['Content-Type'] = 'application/json';
  if (method !== 'GET') headers['X-CSRFToken'] = csrfToken();
  if (version !== undefined) headers['If-Match'] = String(version);
  let response;
  try {
    response = await fetch(BASE + path, { method, headers, signal, credentials: 'same-origin', body: data === undefined ? undefined : JSON.stringify(data) });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error('无法连接后端，请检查服务是否启动。');
  }
  if (response.status === 204) return null;
  const isJson = response.headers.get('content-type')?.includes('application/json');
  const body = isJson ? await response.json() : null;
  if (!response.ok) {
    const detail = body?.error?.detail;
    const message = errorMessage(detail) || `请求失败（HTTP ${response.status}），请检查服务日志。`;
    const error = new Error(message + (body?.request_id ? ` 请求编号：${body.request_id}` : ''));
    error.status = response.status;
    throw error;
  }
  return body;
}

/** 功能：为模板插值转义不可信文本。输入：value 任意原始字段。输出：HTML 安全文本。
 * 逻辑：转义五个 HTML 元字符。约束：仅用于文本或加引号属性，不用于执行代码或 URL 协议。 */
export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
}
