/**
 * Responsibility: Wrap sales-business JSON, pagination, and private attachment uploads.
 * Implementation: Reuse the Session/CSRF client, read pages sequentially, and retain multipart boundaries during uploads.
 * Internationalization: i18n.js translates explicitly marked static text only; dynamic business content and API values remain unchanged.
 * Relationships: The 0919 interface and shared language/API resources use coordinated cache versions; business.js and assistant.js share this module; APIs are under /api/v1/sales/.
 * Directory: salesRequest, allRows, uploadFile.
 * Variable index: No module state; pagination parameters are request-local variables.
 */
import { t, language } from './i18n.js?v=20260921-product';

import { request } from "./api.js?v=20260921-product";

/** Function: Call a sales JSON API. Inputs: Relative path and request options.
 * Outputs: A successful response or exception. Logic: Reuse the shared request client. Constraints: No retries or implicit fallbacks. */
export function salesRequest(path, options) {
  return request(`sales/${path}`, options);
}

/** Function: Read a complete authorized paginated collection. Inputs: path may contain query parameters.
 * Outputs: A combined result array. Logic: Follow the returned count sequentially with 100 items per page.
 * Constraints: Any failed page fails the entire read; concurrent writes mean this is not a consistent database snapshot. */
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

/** Function: Upload a private attachment. Inputs: company identifier and browser File.
 * Outputs: Saved attachment metadata. Logic: Upload FormData with CSRF/language headers and delegate errors to the calling page.
 * Constraints: Never print/persist tokens, set multipart Content-Type manually, or retry. */
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
