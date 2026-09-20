/**
 * 职责：封装销售业务 JSON、分页和私有附件上传。
 * 实现：复用现有会话/CSRF 客户端，分页逐页读取；上传保持 multipart 边界。
 * 国际化：i18n.js 仅翻译显式标记的静态文案；动态业务正文和接口值保持原样。
 * 关联：共享语言/API 资源随需求界面统一版本；business.js 和 assistant.js 共用，API 位于 /api/v1/sales/。
 * 目录：salesRequest、allRows、uploadFile。
 * 变量索引：无模块状态；分页参数为各请求局部变量。
 */
import { t, language } from './i18n.js?v=20260920-requirements';

import { request } from "./api.js?v=20260920-requirements";

/** 功能：调用销售 JSON API。输入：path 相对路径、options 请求设置。
 * 输出：成功响应或异常。逻辑：复用统一请求客户端。约束：无重试和隐式降级。 */
export function salesRequest(path, options) {
  return request(`sales/${path}`, options);
}

/** 功能：完整读取已授权分页集合。输入：path 可带查询参数。
 * 输出：合并结果数组。逻辑：按返回 count 依次翻页，每页 100 条。
 * 约束：任何一页失败则整次读取失败；并发写入下不是数据库一致性快照。 */
export async function allRows(path) {
  const rows = [];
  let page = 1,
    count;
  do {
    const result = await salesRequest(
      `${path}${path.includes("?") ? "&" : "?"}page=${page}&page_size=100`,
    );
    rows.push(...result.results);
    count = result.count;
    page += 1;
    if (!result.results.length) break;
  } while (rows.length < count);
  return rows;
}

/** 功能：上传私有附件。输入：company 客户标识、file 浏览器 File。
 * 输出：已保存附件元数据。逻辑：FormData 上传并附 CSRF 与界面语言头，错误交给调用页。
 * 约束：不打印或持久化令牌，不自行设置 multipart Content-Type，不重试。 */
export async function uploadFile(company, file) {
  const data = new FormData();
  data.append("company", company);
  data.append("file", file);
  const token =
    document.cookie
      .split("; ")
      .find((item) => item.startsWith("csrftoken="))
      ?.slice(10) || "";
  const response = await fetch("/api/v1/sales/files/", {
    method: "POST",
    credentials: "same-origin",
    headers: { "X-CSRFToken": token, "Accept-Language": language },
    body: data,
  });
  const body = response.headers
    .get("content-type")
    ?.includes("application/json")
    ? await response.json()
    : null;
  if (!response.ok)
    throw new Error(
      typeof body?.error?.detail === "string"
        ? body.error.detail
        : t`附件上传失败（HTTP ${response.status}）。`,
    );
  return body;
}
