/**
 * Responsibility: Provide business management for customers, transactions, follow-ups, collaboration, and external actions.
 * Implementation: Browse authorized business data together with permitted experiment rows; mark original ownership in experiment details and link to shared maintenance. Ordinary writes retain their original permissions.
 * Internationalization: i18n.js translates explicitly marked static text only; dynamic business content and API values remain unchanged.
 * Relationships: Navigation cache versions reflect removal of sidebar priority/experiment entries and support for opportunity priorities. Chat Markdown, 0919 interface, account-reset navigation, and shared language/API resources use coordinated versions. Workspace chat uses a shared upgrade version to avoid cached company-specific entry points. workspace.js provides navigation, bottom Profile, collapsible bottom chat, and the Nocturne navigation/task/URL customer context. sales-api.js handles same-origin calls without automatically approving tools.
 * Directory: nameOf, label, display, notice, perform, showDialog, optionRows, relationOptions, fieldControl,
 * editRecord, readForm, detailRecord, runCommand, customerDetail, editCustomer, editContact,
 * groupingForm, attachmentForm, actionForm, renderActions, connectionForm, qqConnectionForm, refreshDirectory,
 * syncBusinessContext, loadPage, experimentLabel, experimentDetail, renderRows, renderStats, boot.
 * Variable index: $ queries the DOM; labels holds field labels; states holds status labels.
 * metadata holds resource contracts; companies is the original business-form directory; browseCompanies includes shared experiments for filtering; BROWSE_RESOURCES allowlists merged browse resources; user is the current identity; current is the route; page is the page number.
 * qqEnabled is the backend QQ capability flag; generation tracks asynchronous loads; relations caches names of already-read relationships.
 */
import { t, h, locale, language } from './i18n.js?v=20260921-product';

import { request, escapeHtml as esc } from "./api.js?v=20260921-product";
import { mountWorkspace, setWorkspaceContext, refreshWorkspace, businessHref } from "./workspace.js?v=20260922-sidebar";
import { salesRequest, allRows, uploadFile } from "./sales-api.js?v=20260921-product";

const $ = (id) => document.getElementById(id);
const labels = {
  company: t("客户"),
  contact: t("联系人"),
  primary_contact: t("主要联系人"),
  title: t("标题"),
  name: t("名称"),
  description: t("说明"),
  notes: t("备注"),
  currency: t("币种"),
  amount: t("预计金额"),
  unit_price: t("单价"),
  quantity: t("数量"),
  discount: t("整行折扣金额"),
  stock_quantity: t("人工库存数量"),
  sku: t("产品编号"),
  number: t("单据编号"),
  product: t("产品"),
  quote: t("来源报价"),
  order: t("所属订单"),
  due_at: t("到期时间"),
  expected_close: t("预计成交日期"),
  valid_until: t("报价有效期"),
  assigned_to: t("负责人"),
  team: t("团队"),
  user: t("成员账号"),
  role: t("角色"),
  group_key: t("归组规则"),
  phone: t("电话"),
  conversation: t("会话"),
  kind: t("草稿类型"),
  subject: t("邮件主题"),
  content: t("内容"),
  recipients: t("收件人"),
  client_key: t("消息标识"),
  status: t("状态"),
  archived: t("已归档"),
  total: t("净额"),
  created_at: t("创建时间"),
  updated_at: t("更新时间"),
  sent_at: t("发送时间"),
  confirmed_at: t("订单确认时间"),
  priority: t("优先级"),
  provider: t("服务"),
  account: t("连接账号"),
  read_at: t("已读时间"),
  follow_up: t("关联跟进"),
  size: t("文件字节数"),
  content_type: t("文件类型"),
  sha256: t("文件校验摘要"),
  approved_at: t("确认时间"),
  started_at: t("开始时间"),
  finished_at: t("完成时间"),
  tool: t("工具"),
  external_message_id: t("外部邮件标识"),
  source_revision: t("提醒来源版本"),
  event: t("操作"),
  actor_id: t("操作者"),
  object_type: t("对象类型"),
  object_id: t("对象标识"),
  company_id: t("客户"),
  email: t("邮箱"),
  username: t("用户名"),
};
const states = {
  draft: t("草稿"),
  approved: t("已审核 / 已确认"),
  sent: t("已发送"),
  accepted: t("客户已接受"),
  rejected: t("客户已拒绝"),
  confirmed: t("已确认"),
  fulfilled: t("已履约"),
  cancelled: t("已取消"),
  open: t("待处理"),
  in_progress: t("处理中"),
  resolved: t("已解决"),
  closed: t("已关闭"),
  new: t("新商机"),
  qualified: t("已确认需求"),
  proposal: t("方案沟通"),
  won: t("已赢单"),
  lost: t("已丢单"),
  completed: t("已完成"),
  pending_confirmation: t("待人工确认"),
  running: t("执行中"),
  succeeded: t("执行成功"),
  failed: t("执行失败"),
  uncertain: t("结果待核对"),
  viewer: t("只读"),
  editor: t("可编辑"),
  manager: t("管理员"),
  low: t("低"),
  normal: t("普通"),
  high: t("高"),
  chat: t("聊天草稿"),
  email: t("邮件草稿"),
  user: t("用户"),
  assistant: t("助手"),
  gmail: t("Gmail 发信"),
  qq: t("QQ 发信"),
  calendar: t("Google 日历"),
  "gmail.send": t("Gmail 发送邮件"),
  "qq.send": t("QQ 发送邮件"),
  "calendar.create": t("创建会议"),
  none: t("不发送日历通知"),
  all: t("通知全部参会人"),
  externalOnly: t("仅通知外部参会人"),
};
let metadata = {},
  companies = [],
  browseCompanies = [],
  user = null,
  qqEnabled = false,
  current = "directory",
  page = 1,
  generation = 0;
const relations = new Map();
const BROWSE_RESOURCES = new Set(["directory", "audit", "customers", "aliases", "contact-profiles", "teams", "memberships", "grants", "products", "tickets", "opportunities", "quotes", "quote-lines", "orders", "order-lines", "follow-ups", "conversations", "messages", "drafts", "actions", "files", "notifications"]);

/** Function: Select a recognizable business name for an authorized record. Inputs: row is the record.
 * Outputs: A plain-text name. Logic: Unnamed customers use known domains or contacts; drafts show their subject or content summary.
 * Constraints: Display identity only; never write domains or contacts back as company names. */
function nameOf(row) {
  if (Array.isArray(row.contacts))
    return (
      row.name ||
      row.domains?.[0] ||
      row.contacts[0]?.name ||
      row.contacts[0]?.email ||
      t("未命名客户")
    );
  return (
    row.name ||
    row.title ||
    row.number ||
    row.subject ||
    row.username ||
    row.email ||
    row.account ||
    (row.content
      ? row.content.slice(0, 40)
      : t`记录 ${String(row.id).slice(0, 8)}`)
  );
}

/** Function: Return a business-field label. Inputs: name. Outputs: A current-language label or the original field name.
 * Logic: Convert through a fixed vocabulary. Constraints: Preserve unknown contract field names to avoid misrepresentation. */
function label(name) {
  return labels[name] || name;
}

/** Function: Convert a value to readable text. Inputs: value and optional key.
 * Outputs: Plain text. Logic: Customer names come from the read-only directory including shared experiments; format status, relations, and time by type while preserving business content.
 * Constraints: Callers must still escape the output before inserting HTML; never render executable HTML. */
function display(value, key = "") {
  if (value === null || value === undefined || value === "") return t("未填写");
  if (typeof value === "boolean") return value ? t("是") : t("否");
  if (Array.isArray(value))
    return value.map((item) => display(item)).join("、") || t("无");
  if (typeof value === "object")
    return Object.entries(value)
      .map(([name, item]) => `${label(name)}：${display(item, name)}`)
      .join("\n");
  if (["company", "company_id"].includes(key))
    return browseCompanies.find((item) => item.id === value)
      ? nameOf(browseCompanies.find((item) => item.id === value))
      : String(value);
  if (relations.has(String(value))) return relations.get(String(value));
  if (["status", "role", "priority", "kind", "provider", "tool"].includes(key))
    return states[value] || value;
  if (key === "industry_from_crm") return t(value);
  if (key.endsWith("_at")) return new Date(value).toLocaleString(locale);
  return String(value);
}

/** Function: Show an explicit operation error. Inputs: error and modal, indicating dialog placement.
 * Outputs: None. Logic: textContent prevents injection through exception text. Constraints: Never hide failures or retry automatically. */
function notice(error, modal = false) {
  const node = $(modal ? "editor-error" : "business-notice");
  node.textContent = error.message || String(error);
  node.hidden = false;
}

/** Function: Perform one UI operation and restore controls. Inputs: Asynchronous task and optional button.
 * Outputs: None. Logic: Prevent duplicate submissions from the button and display exceptions in the current dialog or main page.
 * Constraints: Never interpret backend failure as success or automatically confirm actions. */
async function perform(task, button) {
  if (button) button.disabled = true;
  $("editor-error").hidden = true;
  $("business-notice").hidden = true;
  try {
    await task();
  } catch (error) {
    notice(error, $("editor").open);
  } finally {
    if (button?.isConnected) button.disabled = false;
  }
}

/** Function: Open a native modal dialog. Inputs: title and body containing escaped HTML.
 * Outputs: None. Logic: Replace the previous action view and focus the first control.
 * Constraints: Callers must escape all business data with esc. */
function showDialog(title, body) {
  $("editor-title").textContent = title;
  $("editor-body").innerHTML = body;
  $("editor-error").hidden = true;
  if (!$("editor").open) $("editor").showModal();
  $("editor-body").querySelector("input,select,textarea,button")?.focus();
}

/** Function: Build safe select options. Inputs: rows and selected, the current identifier.
 * Outputs: option HTML. Logic: Escape identifiers and display names separately.
 * Constraints: Never infer business ownership from names. */
function optionRows(rows, selected = "") {
  return (
    h`<option value="">请选择</option>` +
    rows
      .map(
        (row) =>
          `<option value="${esc(row.id)}" ${String(row.id) === String(selected) ? "selected" : ""}>${esc(nameOf(row))}</option>`,
      )
      .join("")
  );
}

/** Function: Read authorized relationship options. Inputs: field is a contract field.
 * Outputs: An array of IDs and display names. Logic: Companies/contacts come from the business directory; other resources come from paginated APIs.
 * Constraints: Frontend filtering does not replace backend authorization; member accounts may be looked up separately by full username. */
async function relationOptions(field) {
  let rows;
  if (field.relation === "company")
    rows = companies.filter((item) => !item.archived);
  else if (field.relation === "contact")
    rows = companies.flatMap((company) =>
      company.contacts.map((contact) => ({
        ...contact,
        name: `${company.name || t("未命名客户")} · ${contact.name || contact.email}`,
      })),
    );
  else if (field.relation === "user")
    rows = (await salesRequest("people/")).results;
  else {
    const resource = Object.values(metadata).find(
      (item) => item.model === field.relation,
    )?.key;
    rows = resource ? await allRows(`records/${resource}/`) : [];
  }
  for (const row of rows) relations.set(String(row.id), nameOf(row));
  return rows;
}

/** Function: Render one business-form field. Inputs: field, value, and relationship options.
 * Outputs: A label and control HTML. Logic: Enter amounts as decimal strings and recipients as delimited email lists.
 * Constraints: Do not infer currencies, prices, or meeting notification methods. */
function fieldControl(field, value, options = []) {
  const name = field.name,
    required = field.required ? "required" : "",
    v = value ?? "";
  let control;
  if (field.type === "relation")
    control = `<select name="${esc(name)}" ${required}>${optionRows(options, v)}</select>`;
  else if (field.type === "choice")
    control = h`<select name="${esc(name)}" ${required}><option value="">请选择</option>${field.choices.map((choice) => `<option value="${esc(choice)}" ${choice === v ? "selected" : ""}>${esc(states[choice] || choice)}</option>`).join("")}</select>`;
  else if (field.type === "boolean")
    control = h`<select name="${esc(name)}"><option value="false">否</option><option value="true" ${v === true ? "selected" : ""}>是</option></select>`;
  else if (["description", "notes", "content", "recipients"].includes(name))
    control = `<textarea name="${esc(name)}" ${required} rows="${name === "content" ? 7 : 3}" placeholder="${name === "recipients" ? t("多个邮箱以逗号或换行分隔") : ""}">${esc(Array.isArray(v) ? v.join("\n") : v)}</textarea>`;
  else {
    const type =
      field.type === "date"
        ? "date"
        : field.type === "datetime"
          ? "datetime-local"
          : field.type === "number"
            ? "number"
            : "text";
    const shown =
      type === "datetime-local" && v
        ? new Date(
            new Date(v).getTime() - new Date(v).getTimezoneOffset() * 60000,
          )
            .toISOString()
            .slice(0, 16)
        : v;
    control = `<input name="${esc(name)}" type="${type}" value="${esc(shown)}" ${required} ${type === "number" ? 'step="any"' : ""} ${name === "currency" ? h('maxlength="3" placeholder="例如 USD / SGD / CNY"') : ""}>`;
  }
  return `<label class="${["description", "notes", "content", "recipients"].includes(name) ? "wide" : ""}">${esc(name === "title" && field.profileTitle ? t("职位") : label(name))}${field.required ? " *" : ""}${control}${name === "group_key" ? h("<small>域名填 domain:example.com；指定联系人填 contact:name@example.com。</small>") : ""}</label>`;
}

/** Function: Open a business record create/edit form. Inputs: resource, optional record, and preset.
 * Outputs: None. Logic: Read actual writable fields and relationship options; save with the observed revision.
 * Constraints: Messages are create-only; tool actions, files, and connections use dedicated flows. */
async function editRecord(resource, record = null, preset = {}) {
  const definition = metadata[resource];
  const editable = definition.fields
    .map((field) => ({
      ...field,
      profileTitle: resource === "contact-profiles",
    }))
    .filter((field) => !field.readonly && !(field.name === "client_key"));
  const options = new Map();
  for (const field of editable.filter((field) => field.type === "relation"))
    options.set(field.name, await relationOptions(field));
  const values = { ...(record || {}), ...preset };
  if (!values.company && $("company-filter").value)
    values.company = $("company-filter").value;
  const key = crypto.randomUUID();
  showDialog(
    `${record ? t("编辑") : t("新建")} ${t(definition.label)}`,
    h`<form id="record-form"><div class="form-grid">${editable.map((field) => fieldControl(field, values[field.name], options.get(field.name))).join("")}</div>${resource === "memberships" ? h('<div class="section"><label>按完整用户名查找成员<input id="member-username" placeholder="输入已有账号的用户名"></label><button id="find-member" type="button">查找账号</button></div>') : ""}<p class="form-note">${["quote-lines", "order-lines"].includes(resource) ? t("单价与描述作为本次单据快照保存。选择产品后请明确填写本次价格，不会自动覆盖历史价格。") : t("留空的非必填项保持未知或使用该字段已声明的初始值。")}${editable.some((f) => f.type === "datetime") ? t(" 时间按当前设备时区输入。") : ""}</p><div class="actions"><button class="primary" type="submit">保存 ${t(definition.label)}</button></div></form>`,
  );
  if ($("find-member"))
    $("find-member").onclick = (event) =>
      perform(async () => {
        const found = (
          await salesRequest(
            `people/?username=${encodeURIComponent($("member-username").value)}`,
          )
        ).results;
        if (!found.length) throw new Error(t("未找到该有效账号。"));
        const select = $("record-form").elements.namedItem("user");
        select.insertAdjacentHTML(
          "beforeend",
          optionRows(found).replace(h('<option value="">请选择</option>'), ""),
        );
        select.value = found[0].id;
      }, event.currentTarget);
  $("record-form").onsubmit = (event) => {
    event.preventDefault();
    perform(async () => {
      const data = readForm(event.currentTarget, editable, Boolean(record));
      if (resource === "messages") data.client_key = key;
      await salesRequest(
        `records/${resource}/${record ? record.id + "/" : ""}`,
        { method: record ? "PATCH" : "POST", data, version: record?.revision },
      );
      $("editor").close();
      await refreshDirectory();
      await loadPage();
    }, event.submitter);
  };
}

/** Function: Convert form input into strict API data. Inputs: form, fields contract, and editing flag.
 * Outputs: A request object. Logic: Handle empty values according to nullable/optional semantics, include time zones in dates, and keep amounts as strings.
 * Constraints: Never silently repair invalid email addresses or business relations; the backend performs final validation. */
function readForm(form, fields, editing) {
  const result = {};
  for (const field of fields) {
    const value = form.elements.namedItem(field.name).value;
    if (value === "" && !field.required && !editing) continue;
    if (field.name === "recipients")
      result[field.name] = value
        .split(/[,;\n]+/)
        .map((item) => item.trim())
        .filter(Boolean);
    else if (value === "" && field.nullable) result[field.name] = null;
    else if (field.type === "datetime")
      result[field.name] = value ? new Date(value).toISOString() : null;
    else if (field.type === "boolean") result[field.name] = value === "true";
    else result[field.name] = value;
  }
  return result;
}

/** Function: Show a record and available business actions. Inputs: resource and id.
 * Outputs: None. Logic: Refetch the version, show net amounts, times, and document lines, and generate buttons from backend state transitions.
 * Constraints: External-action approval requires a full-content preview; the browser cannot directly set sent status. */
async function detailRecord(resource, id) {
  const record = await salesRequest(`records/${resource}/${id}/`),
    definition = metadata[resource];
  if (resource === "actions") {
    renderActions(record);
    return;
  }
  const omitted = new Set(["id", "owner", "revision", "lines"]);
  const rows = Object.entries(record).filter(([key]) => !omitted.has(key));
  const editable =
    !["messages", "notifications", "connections", "files"].includes(resource) &&
    !record.archived;
  const transitions = definition.transitions[record.status] || [];
  showDialog(
    t(definition.label),
    `<dl class="details">${rows.map(([key, value]) => `<dt>${esc(label(key))}</dt><dd>${esc(display(value, key))}</dd>`).join("")}</dl>${record.lines ? h`<div class="section"><h3>单据明细 · ${esc(record.currency)} ${esc(record.total)}</h3>${record.lines.map((line) => h`<p>${esc(line.description)} · ${esc(line.quantity)} × ${esc(line.unit_price)} − ${esc(line.discount)} <button type="button" data-line="${esc(line.id)}">查看明细</button></p>`).join("") || h('<p class="muted">尚无明细。</p>')}${record.status === "draft" && !record.archived ? h('<button id="add-line" type="button">＋ 添加明细</button>') : ""}</div>` : ""}<div class="section actions">${editable ? h('<button id="edit-record" type="button">编辑内容</button>') : ""}${transitions.map((state) => `<button data-state="${esc(state)}" type="button">${esc(states[state] || state)}</button>`).join("")}${!["messages", "notifications"].includes(resource) ? `<button id="archive-record" type="button">${record.archived ? t("恢复记录") : resource === "connections" ? t("停用连接") : t("归档")}</button>` : ""}${resource === "notifications" && !record.read_at ? h('<button id="mark-read" type="button">标记已读</button>') : ""}${resource === "files" && !record.archived ? h`<a href="/api/v1/sales/files/${esc(record.id)}/download/">下载附件</a>` : ""}</div>`,
  );
  if ($("edit-record"))
    $("edit-record").onclick = () =>
      perform(() => editRecord(resource, record));
  if ($("archive-record"))
    $("archive-record").onclick = (event) =>
      perform(
        () => runCommand(resource, record, "archive", !record.archived),
        event.currentTarget,
      );
  if ($("mark-read"))
    $("mark-read").onclick = (event) =>
      perform(() => runCommand(resource, record, "read"), event.currentTarget);
  for (const button of $("editor-body").querySelectorAll("[data-state]"))
    button.onclick = () =>
      perform(
        () => runCommand(resource, record, "transition", button.dataset.state),
        button,
      );
  const lineResource = resource === "quotes" ? "quote-lines" : "order-lines";
  if ($("add-line"))
    $("add-line").onclick = () =>
      perform(() =>
        editRecord(lineResource, null, {
          [resource === "quotes" ? "quote" : "order"]: record.id,
        }),
      );
  for (const button of $("editor-body").querySelectorAll("[data-line]"))
    button.onclick = () =>
      perform(() => detailRecord(lineResource, button.dataset.line));
}

/** Function: Execute an explicitly selected versioned business command. Inputs: resource, record, command, and value.
 * Outputs: None. Logic: Close details after saving and refresh the view and company version.
 * Constraints: Preserve the original dialog on failure; external approval uses a separate preview entry. */
async function runCommand(resource, record, command, value = null) {
  await salesRequest(`records/${resource}/${record.id}/commands/`, {
    method: "POST",
    version: record.revision,
    data: { command, value },
  });
  $("editor").close();
  await refreshDirectory();
  await loadPage();
}

/** Function: Show customer profiles and contact actions. Inputs: id.
 * Outputs: None. Logic: Read versions from the authorized directory and set shared customer context; retain original permission checks for customer actions.
 * Constraints: Owners maintain core customer data and merges; backend authorization remains mandatory. */
async function customerDetail(id) {
  await refreshDirectory();
  const company = companies.find((item) => item.id === id);
  if (!company) throw new Error(t("客户已不可见，请刷新。"));
  $("company-filter").value = company.id;
  syncBusinessContext();
  showDialog(
    company.name || t("未命名客户"),
    h`<p class="muted">${esc(company.domains.join(" · ") || t("尚未指定公司域名"))}</p><dl class="details">${Object.entries(
      company.customer,
    )
      .filter(([key]) => key !== "customer_id")
      .map(
        ([key, value]) =>
          `<dt>${esc({ industry_from_crm: t("行业"), employee_count: t("员工人数"), employee_count_source: t("人数来源"), first_deal_at: t("首次成交时间") }[key] || label(key))}</dt><dd>${esc(display(value, key))}</dd>`,
      )
      .join(
        "",
      )}</dl><div class="actions"><button id="customer-edit">编辑档案</button><button id="customer-settings">主要联系人 / 备注 / 归档</button><a href="/#company/${esc(company.id)}">邮件与分析 ↗</a><a href="${esc(businessHref("quotes", company.id, { create: "1" }))}">创建报价</a><a href="${esc(businessHref("follow-ups", company.id, { create: "1" }))}">安排跟进</a></div><div class="section"><h3>联系人</h3>${company.contacts.map((c) => h`<p>${esc(c.name || t("姓名未知"))} · ${esc(c.email)} <button data-contact="${esc(c.id)}">编辑身份</button><button data-profile="${esc(c.id)}">职位 / 电话 / 备注</button></p>`).join("") || h('<p class="muted">暂无联系人</p>')}<button id="contact-add">＋ 新增联系人</button></div><div class="section"><h3>人工归组</h3><p class="muted">搬移已选择的邮件，或将另一家公司合入此客户。未来邮件可单独配置域名 / 联系人规则。</p><div class="actions"><button id="group-move">搬移邮件</button><button id="group-merge">合并客户</button><button id="group-alias">新增归组规则</button></div></div>`,
  );
  $("customer-edit").onclick = () => editCustomer(company);
  $("customer-settings").onclick = () =>
    perform(async () =>
      company.settings_id
        ? detailRecord("customers", company.settings_id)
        : editRecord("customers", null, { company: company.id }),
    );
  $("contact-add").onclick = () => editContact(company);
  for (const button of $("editor-body").querySelectorAll("[data-contact]"))
    button.onclick = () =>
      editContact(
        company,
        company.contacts.find((c) => String(c.id) === button.dataset.contact),
      );
  for (const button of $("editor-body").querySelectorAll("[data-profile]"))
    button.onclick = () =>
      perform(async () => {
        const existing = (
          await allRows("records/contact-profiles/?archived=all")
        ).find((p) => String(p.contact) === button.dataset.profile);
        return existing
          ? detailRecord("contact-profiles", existing.id)
          : editRecord("contact-profiles", null, {
              contact: button.dataset.profile,
            });
      });
  $("group-move").onclick = () => groupingForm(company, "move");
  $("group-merge").onclick = () => groupingForm(company, "merge");
  $("group-alias").onclick = () =>
    perform(() => editRecord("aliases", null, { company: company.id }));
}

/** Function: Edit a confirmed customer profile. Inputs: company is the current directory version.
 * Outputs: None. Logic: Use the existing registration API and employee-count source rules.
 * Constraints: Saving triggers analysis under existing project configuration without changing models, weights, or time zones. */
function editCustomer(company) {
  showDialog(
    t("编辑客户档案"),
    h`<form id="customer-form"><div class="form-grid"><label class="wide">公司名称<input name="company_name" value="${esc(company.name)}" required></label><label>行业<select name="industry_from_crm">${["unknown", "半导体检测", "精密量测", "光学检测", "工业检测"].map((value) => `<option value="${value}" ${company.customer.industry_from_crm === value ? "selected" : ""}>${value === "unknown" ? t("未知") : t(value)}</option>`).join("")}</select></label><label>员工人数<input name="employee_count" type="number" min="0" value="${esc(company.customer.employee_count)}"></label><label class="wide">人数来源<input name="employee_count_source" value="${esc(company.customer.employee_count_source)}"></label></div><div class="actions"><button class="primary">保存客户档案</button></div></form>`,
  );
  $("customer-form").onsubmit = (event) => {
    event.preventDefault();
    perform(async () => {
      const data = Object.fromEntries(new FormData(event.currentTarget));
      data.employee_count =
        data.employee_count === "" ? null : Number(data.employee_count);
      data.employee_count_source = data.employee_count_source || null;
      await request(`companies/${company.id}/register/`, {
        method: "POST",
        data,
        version: company.revision,
      });
      $("editor").close();
      await refreshDirectory();
      await loadPage();
    }, event.submitter);
  };
}

/** Function: Add or edit a contact's email identity. Inputs: company and optional contact.
 * Outputs: None. Logic: Write with the company version; the backend prohibits email changes when historical messages exist.
 * Constraints: Store phone/title as supplementary information without overwriting original email facts. */
function editContact(company, contact = null) {
  showDialog(
    contact ? t("编辑联系人") : t("新增联系人"),
    h`<form id="contact-form"><div class="form-grid"><label>姓名<input name="name" value="${esc(contact?.name)}"></label><label>邮箱<input name="email" type="email" value="${esc(contact?.email)}" required></label></div><div class="actions"><button class="primary">保存联系人</button></div></form>`,
  );
  $("contact-form").onsubmit = (event) => {
    event.preventDefault();
    perform(async () => {
      const data = Object.fromEntries(new FormData(event.currentTarget));
      data.name = data.name || null;
      if (contact) data.id = contact.id;
      await salesRequest(`directory/${company.id}/contacts/`, {
        method: "POST",
        data,
        version: company.revision,
      });
      await customerDetail(company.id);
    }, event.submitter);
  };
}

/** Function: Display a complete manual-grouping plan form. Inputs: target customer and operation, either move or merge.
 * Outputs: None. Logic: Explicitly choose the source, select individual emails to move, display both customer names, and request confirmation again.
 * Constraints: No select-all default; backend transactions reject merge conflicts without automatically expanding sharing permissions. */
function groupingForm(target, operation) {
  showDialog(
    operation === "move" ? t("搬移邮件到当前客户") : t("合并客户"),
    h`<form id="group-form"><p>目标客户：<strong>${esc(target.name)}</strong></p><label>来源客户<select name="source" required>${optionRows(companies.filter((c) => c.id !== target.id && !c.archived))}</select></label>${operation === "move" ? h('<button id="load-mails" type="button">读取来源邮件</button><div id="mail-choices" class="section"></div>') : h('<p class="warning">合并会转移来源邮件和业务记录，并归档来源客户。历史分析仍保留在来源；存在冲突会整次拒绝。</p>')}<div class="actions"><button class="primary">审阅归组计划</button></div></form>`,
  );
  if ($("load-mails"))
    $("load-mails").onclick = (event) =>
      perform(async () => {
        const source = $("group-form").elements.source.value;
        if (!source) throw new Error(t("请先选择来源客户。"));
        const data = await request(`companies/${source}/`);
        const emails = data.emails || data.context?.emails || [];
        $("mail-choices").innerHTML =
          emails
            .map(
              (mail) =>
                `<label class="check"><input type="checkbox" name="mail" value="${esc(mail.dedupe_key)}">${esc(mail.subject)} · ${esc(mail.sent_at || "")}</label>`,
            )
            .join("") || h("<p>来源没有可搬移邮件。</p>");
        $("mail-choices").dataset.source = source;
      }, event.currentTarget);
  $("group-form").onsubmit = (event) => {
    event.preventDefault();
    perform(async () => {
      const source = companies.find(
        (c) => c.id === event.currentTarget.elements.source.value,
      );
      if (!source) throw new Error(t("请选择来源客户。"));
      const data = {
        source_id: source.id,
        target_id: target.id,
        source_revision: source.revision,
        target_revision: target.revision,
      };
      if (operation === "move") {
        if ($("mail-choices").dataset.source !== source.id)
          throw new Error(t("请重新读取所选来源的邮件。"));
        data.keys = [...$("mail-choices").querySelectorAll(":checked")].map(
          (node) => node.value,
        );
        if (!data.keys.length) throw new Error(t("至少选择一封邮件。"));
      }
      showDialog(
        t("确认人工归组"),
        h`<p>来源：${esc(source.name)}</p><p>目标：${esc(target.name)}</p><p>${operation === "move" ? t`搬移 ${data.keys.length} 封已选择邮件。` : t("转移邮件和业务关系，并归档来源公司。")}</p><button id="confirm-group" class="primary">确认${operation === "move" ? t("搬移") : t("合并")}</button>`,
      );
      $("confirm-group").onclick = (e) =>
        perform(async () => {
          await salesRequest(`grouping/${operation}/`, {
            method: "POST",
            data,
          });
          $("editor").close();
          await refreshDirectory();
          await loadPage();
        }, e.currentTarget);
    }, event.submitter);
  };
}

/** Function: Upload a private customer attachment. Inputs: None; reads the customer directory and filter.
 * Outputs: None. Logic: Select a customer and one file, then use the protected upload API.
 * Constraints: Limit files to 20 MiB and never host attachments publicly. */
function attachmentForm() {
  showDialog(
    t("上传私有附件"),
    h`<form id="upload-form"><div class="form-grid"><label>客户<select name="company" required>${optionRows(companies, $("company-filter").value)}</select></label><label>文件（最多 20 MiB）<input name="file" type="file" required></label></div><p class="form-note">只有当前员工可以下载本附件。</p><div class="actions"><button class="primary">上传并保存</button></div></form>`,
  );
  $("upload-form").onsubmit = (event) => {
    event.preventDefault();
    perform(async () => {
      const form = event.currentTarget;
      await uploadFile(
        form.elements.company.value,
        form.elements.file.files[0],
      );
      $("editor").close();
      await loadPage();
    }, event.submitter);
  };
}

/** Function: Prepare an email-send or meeting-create plan. Inputs: None; reads current filters and connections.
 * Outputs: None. Logic: Filter connections by backend QQ capability and action provider; explicitly select customer/content source. The backend returns a frozen snapshot for review.
 * Constraints: Preparation does not execute the action; convert meeting times from the device time zone to absolute timestamps with offsets. */
async function actionForm() {
  const connections = (await allRows("records/connections/")).filter(item => qqEnabled || item.provider !== "qq"),
    drafts = await allRows("records/drafts/"),
    quotes = await allRows("records/quotes/");
  if (!connections.length)
    throw new Error(t("请先在“外部连接”中连接发信邮箱或日历，再准备动作。"));
  const key = crypto.randomUUID();
  showDialog(
    t("准备外部动作"),
    h`<form id="action-form"><div class="form-grid"><label>客户<select name="company" required>${optionRows(companies, $("company-filter").value)}</select></label><label>动作类型<select name="tool" required><option value="gmail.send">Gmail 发送邮件</option>${qqEnabled ? h('<option value="qq.send">QQ 发送邮件</option>') : ""}<option value="calendar.create">创建会议</option></select></label><label class="wide">外部连接<select name="connection_id" required>${optionRows(connections.map((c) => ({ ...c, name: `${states[c.provider]} · ${c.account}` })))}</select></label><div id="email-fields" class="wide form-grid"><label>邮件草稿<select name="draft_id">${optionRows(drafts.filter((d) => d.kind === "email"))}</select></label><label>附带已审核报价（可选）<select name="quote_id">${optionRows(quotes.filter((q) => q.status === "approved"))}</select></label></div><div id="calendar-fields" class="wide form-grid" hidden><label>日历标识<input name="calendar_id" placeholder="例如 primary"></label><label>会议标题<input name="title"></label><label>开始时间<input name="start" type="datetime-local"></label><label>结束时间<input name="end" type="datetime-local"></label><label class="wide">说明<textarea name="description"></textarea></label><label class="wide">参会人邮箱<textarea name="attendees" placeholder="逗号或换行分隔，可留空"></textarea></label><label>日历通知方式<select name="send_updates"><option value="">请明确选择</option>${["none", "all", "externalOnly"].map((value) => `<option value="${value}">${states[value]}</option>`).join("")}</select></label></div></div><p class="form-note">准备后将展示完整收件人、正文或会议内容。只有你再次确认，任务才会进入执行队列。</p><div class="actions"><button class="primary">生成待确认计划</button></div></form>`,
  );
  const form = $("action-form");
  form.elements.tool.onchange = () => {
    const provider = form.elements.tool.value.split(".")[0];
    $("email-fields").hidden = provider === "calendar";
    $("calendar-fields").hidden = provider !== "calendar";
    form.elements.connection_id.innerHTML = optionRows(connections.filter((c) => !c.archived && c.provider === provider).map((c) => ({ ...c, name: `${states[c.provider]} · ${c.account}` })));
  };
  form.elements.tool.onchange();
  $("action-form").onsubmit = (event) => {
    event.preventDefault();
    perform(async () => {
      const fields = Object.fromEntries(new FormData(event.currentTarget)),
        parameters = { connection_id: fields.connection_id };
      if (["gmail.send", "qq.send"].includes(fields.tool)) {
        parameters.draft_id = fields.draft_id;
        if (fields.quote_id) parameters.quote_id = fields.quote_id;
      } else {
        if (!fields.start || !fields.end)
          throw new Error(t("请填写会议起止时间。"));
        Object.assign(parameters, {
          calendar_id: fields.calendar_id,
          title: fields.title,
          description: fields.description,
          start: new Date(fields.start).toISOString(),
          end: new Date(fields.end).toISOString(),
          attendees: fields.attendees
            .split(/[,;\n]+/)
            .map((v) => v.trim())
            .filter(Boolean),
          send_updates: fields.send_updates,
        });
      }
      const action = await salesRequest("records/actions/", {
        method: "POST",
        data: {
          company: fields.company,
          tool: fields.tool,
          parameters,
          idempotency_key: key,
        },
      });
      renderActions(action);
      await loadPage();
    }, event.submitter);
  };
}

/** Function: Display a frozen external action and bind confirmation, cancellation, and reconciliation.
 * Inputs: record is the complete action response. Outputs: None.
 * Logic: Email previews show account, recipients, subject, and verbatim body; meeting previews show time, attendees, and notification scope.
 * Constraints: Confirmation is a separate user action; unknown outcomes allow queries only, with no automatic-retry button. */
function renderActions(record) {
  const p = record.parameters,
    email = ["gmail.send", "qq.send"].includes(record.tool);
  const preview = email
    ? h`<p>发件账号：${esc(p.account)}</p><p>收件人：${esc(p.to.join("、"))}</p><h3>${esc(p.subject)}</h3><div class="preview">${esc(p.body)}</div>`
    : h`<p>账号：${esc(p.account)} · 日历：${esc(p.calendar_id)}</p><h3>${esc(p.title)}</h3><p>${esc(new Date(p.start).toLocaleString(locale))} → ${esc(new Date(p.end).toLocaleString(locale))}</p><p>参会人：${esc(p.attendees.join("、") || t("无"))}</p><p>通知：${esc(states[p.send_updates])}</p><div class="preview">${esc(p.description)}</div>`;
  showDialog(
    `${states[record.tool]} · ${states[record.status]}`,
    h`<p>客户：${esc(display(record.company, "company"))}</p>${preview}${record.error ? `<p class="warning">${esc(record.error.message)}</p>` : ""}${
      record.result
        ? `<dl class="details">${Object.entries(record.result)
            .map(
              ([key, value]) =>
                `<dt>${esc({ message_id: t("邮件标识"), thread_id: t("邮件会话标识"), event_id: t("日历事件标识"), url: t("外部事件链接"), submission_status: t("提交状态"), sent_copy_id: t("发送副本标识") }[key] || key)}</dt><dd>${esc({ smtp_accepted: t("QQ 服务器已接受（不代表最终送达）"), confirmed_in_sent: t("已核对发送副本") }[value] || value)}</dd>`,
            )
            .join("")}</dl>`
        : ""
    }<p class="form-note">确认后由销售任务进程执行。取消仅适用于尚未开始的动作。</p><div class="actions">${record.status === "pending_confirmation" ? h('<button id="approve-action" class="primary">确认并加入执行队列</button>') : ""}${["pending_confirmation", "approved"].includes(record.status) ? h('<button id="cancel-action">取消动作</button>') : ""}${record.status === "running" ? h('<button id="interrupt-action">已核实进程中断，标记待核对</button>') : ""}${record.status === "uncertain" ? h('<button id="verify-action">到外部服务核对结果</button>') : ""}</div>`,
  );
  for (const [id, command, value] of [
    ["approve-action", "decide", "approved"],
    ["cancel-action", "decide", "cancelled"],
    ["interrupt-action", "interrupted", null],
    ["verify-action", "verify", null],
  ])
    if ($(id))
      $(id).onclick = (event) =>
        perform(
          () => runCommand("actions", record, command, value),
          event.currentTarget,
        );
}

/** Function: Select a QQ-send or Google-write connection. Inputs: None.
 * Outputs: None. Logic: QQ opens a separate authorization-code form; Google obtains the configured OAuth URL and navigates there.
 * Constraints: Never expand existing read-only Gmail permissions; missing configuration produces an explicit error. */
function connectionForm() {
  showDialog(
    t("连接外部服务"),
    h('<p>Gmail 连接申请发送与核对已发送邮件权限；日历连接申请事件管理与读取权限。原邮件同步连接保持独立。</p><div class="actions"><button id="connect-qq-send">连接 QQ 发信</button><button data-provider="gmail">连接 Gmail 发信</button><button data-provider="calendar">连接 Google 日历</button></div>'),
  );
  $("connect-qq-send").hidden = !qqEnabled;
  $("connect-qq-send").onclick = qqConnectionForm;
  for (const button of $("editor-body").querySelectorAll("[data-provider]"))
    button.onclick = () =>
      perform(async () => {
        const result = await salesRequest("oauth/", {
          method: "POST",
          data: { provider: button.dataset.provider },
        });
        location.assign(result.authorization_url);
      }, button);
}

/** Function: Collect separate QQ sending authorization. Inputs: None; reads this form.
 * Outputs: None. Logic: Refuse to open when QQ is disabled; when enabled, submit through the dedicated connection entry and clear the authorization code.
 * Constraints: Verify login only, without sending mail; never cache the code in the browser or reuse receiving authorization. */
function qqConnectionForm() {
  if (!qqEnabled) throw new Error(t("QQ 邮箱功能暂时停用。"));
  showDialog(t("连接 QQ 发信"), h('<form id="qq-send-form"><div class="form-grid"><label class="wide">QQ 或 foxmail 邮箱<input name="address" type="email" autocomplete="off" required></label><label class="wide">客户端授权码<input name="authorization_code" type="password" autocomplete="new-password" minlength="16" maxlength="16" required></label></div><p class="form-note">与 QQ 收信连接独立。此操作仅验证连接；发送前仍需预览并确认。请在 QQ 邮箱中开启 SMTP；核对发送结果还需开启 IMAP 并保留发送副本。</p><div class="actions"><button class="primary">验证并连接发信</button></div></form>'));
  const form = $("qq-send-form");
  $("editor").addEventListener("close", () => { form.elements.authorization_code.value = ""; }, { once: true });
  form.onsubmit = (event) => {
    event.preventDefault();
    const data = { address: form.elements.address.value.trim(), authorization_code: form.elements.authorization_code.value.trim() };
    form.elements.authorization_code.value = "";
    perform(async () => {
      try {
        await salesRequest("connections/qq/", { method: "POST", data });
        $("editor").close();
        await loadPage();
      } finally {
        data.authorization_code = "";
      }
    }, event.submitter);
  };
}

/** Function: Refresh the authorized customer directory while retaining filters. Inputs: None.
 * Outputs: None. Logic: Read the original business-form directory separately from the browse directory containing shared experiments; keep filter candidates separate from write candidates.
 * Constraints: Failed requests must not produce a fabricated empty directory. */
async function refreshDirectory() {
  const selected = $("company-filter").value;
  [companies, browseCompanies] = await Promise.all([allRows("directory/?archived=all"), allRows("browse/directory/?archived=all")]);
  $("company-filter").innerHTML =
    h('<option value="">全部客户</option>') +
    browseCompanies
      .map(
        (c) =>
          `<option value="${esc(c.id)}">${esc(nameOf(c))}${c.archived ? t("（已归档）") : ""}${c.experiment ? ` · ${esc(experimentLabel(c))}` : ""}</option>`,
      )
      .join("");
  $("company-filter").value = selected;
}

/** Function: Synchronize authorized customer filters, URL, and shared customer navigation. Inputs: Current company-filter and current route.
 * Outputs: None. Logic: Retain customers and read-only provenance from the merged directory; update same-origin links and refresh filters.
 * Constraints: No business writes or device-cache persistence of customer identity across accounts. */
function syncBusinessContext() {
  const selected = browseCompanies.find(company => company.id === $("company-filter").value);
  const url = new URL(location.href);
  if (selected) url.searchParams.set("company", selected.id);
  else url.searchParams.delete("company");
  history.replaceState({}, "", url);
  setWorkspaceContext(selected ? { id: selected.id, name: nameOf(selected), experiment: selected.experiment } : null, current);
}

/** Function: Load one page of records for the current route. Inputs: None; reads current, page, and filters.
 * Outputs: None. Logic: Business and shared experiment records use merged read-only endpoints/counts; hide creation for experiment customers and use asynchronous generations to reject stale responses.
 * Constraints: Read-only loading never executes external actions or model analysis. */
async function loadPage() {
  void refreshWorkspace();
  const turn = ++generation,
    resource = current;
  $("business-notice").hidden = true;
  const title =
    resource === "directory"
      ? t("客户目录")
      : resource === "audit"
        ? t("操作审计")
        : t(metadata[resource]?.label || "");
  if (!title) throw new Error(t("该业务页面不存在。"));
  $("page-title").textContent = title;
  $("list-title").textContent = title;
  const supportsCompany = resource === "directory" || resource === "audit" || metadata[resource]?.fields.some(field => field.name === "company");
  $("company-filter").closest("label").hidden = !supportsCompany;
  if (!supportsCompany) $("company-filter").value = "";
  syncBusinessContext();
  const selected = browseCompanies.find(item => item.id === $("company-filter").value);
  const creatable = resource !== "audit" && resource !== "notifications" && !selected?.experiment;
  $("create-business").hidden = !creatable;
  $("create-business").textContent =
    resource === "connections"
      ? t("连接服务")
      : resource === "files"
        ? t("上传附件")
        : resource === "actions"
          ? t("准备动作")
          : t`＋ 新建${title.replace(t("目录"), "")}`;
  const query = new URLSearchParams({
    page: String(page),
    page_size: "20",
    archived: $("include-archived").checked ? "all" : "false",
  });
  const statusField = metadata[resource]?.fields.find(field => field.name === "status" && field.type === "choice");
  const status = new URLSearchParams(location.search).get("status") || "";
  $("status-filter-label").hidden = !statusField;
  $("status-filter").innerHTML = h('<option value="">全部状态</option>') + (statusField?.choices || []).map(value => `<option value="${esc(value)}">${esc(states[value] || value)}</option>`).join('');
  if (status && !statusField?.choices.includes(status)) throw new Error(t("该页面不支持此状态筛选，请从导航重新进入。"));
  $("status-filter").value = status;
  if (status) query.set("status", status);
  const company = $("company-filter").value;
  if (
    company &&
    (resource === "audit" ||
      resource === "directory" ||
      metadata[resource]?.fields.some((f) => f.name === "company"))
  )
    query.set("company", company);
  const path = BROWSE_RESOURCES.has(resource) ? `browse/${resource}/` :
    resource === "directory"
      ? "directory/"
      : resource === "audit"
        ? "audit/"
        : `records/${resource}/`;
  $("business-content").innerHTML = h('<div class="empty">正在读取…</div>');
  const [result, overview] = await Promise.all([
    salesRequest(`${path}?${query}`),
    salesRequest("browse/overview/"),
  ]);
  if (turn !== generation) return;
  renderRows(resource, result.results);
  renderStats(overview);
  $("record-count").textContent = t`共 ${result.count} 条`;
  const pages = Math.max(1, Math.ceil(result.count / 20));
  $("business-pagination").innerHTML =
    h`<button id="business-prev" ${page === 1 ? "disabled" : ""}>上一页</button><span>${page} / ${pages}</span><button id="business-next" ${page >= pages ? "disabled" : ""}>下一页</button>`;
  $("business-prev").onclick = () => {
    page -= 1;
    perform(loadPage);
  };
  $("business-next").onclick = () => {
    page += 1;
    perform(loadPage);
  };
  $("list-description").textContent =
    resource === "actions"
      ? t("先审阅完整内容，再明确确认。失败或结果未知不会自动重试。")
      : resource === "products"
        ? t("产品价格和人工库存独立维护，单据保留自己的价格快照。")
        : resource === "messages"
          ? t("已持久化的会话消息；回复以实际保存记录为准。")
          : t("记录按当前账号及公司授权范围展示。");
  if (BROWSE_RESOURCES.has(resource)) $("list-description").textContent = language === "en"
    ? `Includes ${result.shared_count} shared synthetic records. Marked records retain their original owner; maintenance is available from details.`
    : `含 ${result.shared_count} 条共享虚构记录；带标记的记录仅供实验，保留原归属，可维护项可从详情进入。`;
}

/** Function: Build a shared-record provenance label. Inputs: row includes backend experiment metadata. Outputs: A plain-text label.
 * Logic: Show synthetic status, maintenance permission, and original username in the UI language. Constraints: Callers must escape the label before HTML insertion. */
function experimentLabel(row) {
  return language === "en" ? `Synthetic · ${row.experiment.read_only ? "Read only" : "Editable"} · Owner: ${row.experiment.owner.username}`
    : `虚构实验 · ${row.experiment.read_only ? "只读" : "可维护"} · 归属：${row.experiment.owner.username}`;
}

/** Function: Show verified shared-record details in the existing business page. Inputs: row contains list data and experiment location. Outputs: A detail dialog and shared-maintenance link.
 * Logic: Refetch by exact batch primary key and retain original fields; provide provenance browsing, file downloads, and customer business navigation.
 * Constraints: Never enter ordinary business edit handlers; report revocation/drift explicitly and escape all dynamic content. */
async function experimentDetail(row) {
  const { batch, model } = row.experiment;
  const base = `experiments/${encodeURIComponent(batch)}/${encodeURIComponent(model)}/`;
  const data = await request(`${base}?pk=${encodeURIComponent(row.id)}`);
  const record = data.results.find(item => item.pk === row.id);
  if (!record) throw new Error(language === "en" ? "This shared record is no longer available." : "此共享记录已不可用。");
  const source = `/experiments/#${new URLSearchParams({ table: model, pk: row.id })}`;
  const links = [`<a href="${esc(source)}">${language === "en" ? (record.read_only ? "Explore source and relations" : "Edit / delete shared record") : (record.read_only ? "查看来源与关联" : "编辑 / 删除共享记录")}</a>`];
  if (model === "crm.Company") {
    for (const resource of ["opportunities", "quotes", "orders", "tickets", "follow-ups"])
      links.push(`<a href="${esc(businessHref(resource, row.id))}">${esc(t(metadata[resource].label))}</a>`);
  }
  if (["sales.Attachment", "accounts.SetupDocument"].includes(model))
    links.push(`<a href="/api/v1/${base}${encodeURIComponent(row.id)}/download/">${language === "en" ? "Download" : "下载文件"}</a>`);
  showDialog(nameOf(row), `<p class="badge">${esc(experimentLabel(row))}</p><p class="muted">${esc(batch)}</p>
    <div class="actions">${links.join("")}</div><dl class="details">${Object.entries(record.fields).map(([key, value]) =>
      `<dt>${esc(label(key))}</dt><dd>${esc(display(value, key))}</dd>`).join("")}</dl>`);
}

/** Function: Render business tables and detail entries. Inputs: resource and rows.
 * Outputs: None. Logic: Label shared experiment ownership; shared rows open details with maintenance links, while ordinary rows retain their original detail flow.
 * Constraints: Never fabricate browser data; delegate shared-row maintenance to the dedicated experiment entry and escape all business content. */
function renderRows(resource, rows) {
  if (!rows.length) {
    $("business-content").innerHTML =
      h('<div class="empty"><strong>这里还没有记录</strong>新建第一条业务记录，或调整客户与归档筛选。</div>');
    return;
  }
  $("business-content").innerHTML =
    h`<table><thead><tr><th>${resource === "audit" ? t("操作事件") : t("名称 / 内容")}</th><th>客户 / 关联</th><th>状态</th><th>记录时间</th><th>操作</th></tr></thead><tbody>${rows
      .map(
        (row) =>
          `<tr><td><strong>${esc(resource === "directory" ? nameOf(row) : row.name || row.title || row.number || row.subject || row.group_key || row.account || row.event || (row.content ? row.content.slice(0, 65) : t(metadata[resource]?.label || "")))}</strong>${row.experiment ? `<small class="badge">${esc(experimentLabel(row))}</small>` : ""}${row.currency ? `<small>${esc(row.currency)} ${esc(row.total ?? row.amount ?? row.unit_price ?? "")}</small>` : ""}${
            resource === "directory"
              ? `<small>${esc(
                  row.contacts
                    .map((c) => c.name || c.email)
                    .slice(0, 2)
                    .join(" · "),
                )}</small>`
              : ""
          }</td><td>${esc(display(row.company || row.company_id || row.conversation || row.team || row.contact || (resource === "directory" ? row.domains.join(" · ") : ""), row.company || row.company_id ? "company" : ""))}</td><td><span class="badge">${esc(row.archived ? t("已归档") : states[row.status] || states[row.role] || (row.read_at ? t("已读") : resource === "notifications" ? t("未读") : resource === "directory" ? (row.crm_status === "registered" ? t("已建档") : t("待建档")) : t("有效")))}</span></td><td>${esc(display(row.updated_at || row.created_at || "", "updated_at"))}</td><td><button data-record="${esc(row.id)}">${resource === "audit" ? t("查看事件") : t("查看详情")}</button></td></tr>`,
      )
      .join("")}</tbody></table>`;
  for (const button of $("business-content").querySelectorAll("[data-record]"))
    button.onclick = () =>
      perform(async () => {
        const selectedRow = rows.find(row => row.id === button.dataset.record);
        if (selectedRow.experiment) { await experimentDetail(selectedRow); return; }
        if (resource === "directory")
          await customerDetail(button.dataset.record);
        else if (resource === "audit") {
          const row = rows.find((r) => r.id === button.dataset.record);
          showDialog(
            t("操作审计"),
            `<dl class="details">${Object.entries(row)
              .filter(([key]) => key !== "id")
              .map(
                ([key, value]) =>
                  `<dt>${esc(label(key))}</dt><dd>${esc(display(value, key))}</dd>`,
              )
              .join("")}</dl>`,
          );
        } else await detailRecord(resource, button.dataset.record);
      }, button);
}

/** Function: Display statistics within current permissions. Inputs: overview is the backend aggregate.
 * Outputs: None. Logic: Customer, ticket, and follow-up counts include deduplicated shared experiment rows and show experiment counts separately; amounts retain the original business scope.
 * Constraints: Never sum different currencies or label confirmed order net amounts as realized revenue. */
function renderStats(overview) {
  $("business-stats").innerHTML = [
    [t("全部可见客户"), overview.customers, overview.shared_counts.customers],
    [t("待处理工单"), overview.open_tickets, overview.shared_counts.open_tickets],
    [t("待跟进"), overview.open_follow_ups, overview.shared_counts.open_follow_ups],
  ]
    .map(
      ([name, value, shared]) =>
        `<div class="stat"><span>${name}</span><strong>${value}</strong><small>${language === "en" ? `Includes ${shared} synthetic records` : `含 ${shared} 条虚构实验记录`}</small></div>`,
    )
    .join("");
  document.querySelector(".footnote").textContent = t`已确认订单净额：${
    Object.entries(overview.confirmed_order_net)
      .map(([c, v]) => `${c} ${v}`)
      .join(" / ") || t("暂无")
  }；开放商机预计金额：${
    Object.entries(overview.open_opportunity_amount)
      .map(([c, v]) => `${c} ${v}`)
      .join(" / ") || t("暂无")
  }。各币种分别统计；库存由人工维护。`;
  document.querySelector(".footnote").textContent += language === "en" ? " Amounts use your original business access scope." : " 金额沿用原业务权限范围，不加入额外共享的模拟交易。";
}

/** Function: Initialize authentication, metadata, and page interactions. Inputs: None; reads the current route.
 * Outputs: None. Logic: Load capabilities and both directories; URLs can identify shared experiment customers, whose read-only status prevents automatic create forms.
 * Constraints: Initialization performs reads only; never save user data in browser local storage. */
async function boot() {
  const session = await request("session/");
  if (!session.authenticated) {
    location.assign("/");
    return;
  }
  user = await request("accounts/me/");
  qqEnabled = (await request("demo/runtime/")).qq_enabled === true;
  $("account").textContent = t`当前员工：${user.username}`;
  metadata = Object.fromEntries(
    (await salesRequest("catalog/")).resources.map((item) => [item.key, item]),
  );
  mountWorkspace(current);
  await refreshDirectory();
  const initialCompany = new URLSearchParams(location.search).get("company");
  if (initialCompany && !browseCompanies.some(company => company.id === initialCompany)) throw new Error(t("链接中的客户不存在或当前账号无权访问。请从客户导航重新选择。"));
  $("company-filter").value = initialCompany || "";
  $("close-editor").onclick = () => $("editor").close();
  $("refresh-business").onclick = (event) =>
    perform(async () => {
      await refreshDirectory();
      await loadPage();
    }, event.currentTarget);
  $("company-filter").onchange = $("include-archived").onchange = () => {
    page = 1;
    syncBusinessContext();
    perform(loadPage);
  };
  $("status-filter").onchange = () => {
    const url = new URL(location.href);
    if ($("status-filter").value) url.searchParams.set("status", $("status-filter").value);
    else url.searchParams.delete("status");
    history.replaceState({}, "", url);
    page = 1;
    perform(loadPage);
  };
  window.addEventListener("hashchange", () => {
    current = location.hash.slice(1) || "directory";
    page = 1;
    $("editor").close();
    perform(loadPage);
  });
  $("create-business").onclick = (event) =>
    perform(async () => {
      if (current === "directory") {
        showDialog(
          t("新建客户"),
          h('<form id="new-company"><label>公司名称<input name="name" required maxlength="240"></label><div class="actions"><button class="primary">建立客户</button></div></form>'),
        );
        $("new-company").onsubmit = (e) => {
          e.preventDefault();
          perform(async () => {
            await salesRequest("directory/", {
              method: "POST",
              data: Object.fromEntries(new FormData(e.currentTarget)),
            });
            $("editor").close();
            await refreshDirectory();
            await loadPage();
          }, e.submitter);
        };
      } else if (current === "files") attachmentForm();
      else if (current === "connections") connectionForm();
      else if (current === "actions") await actionForm();
      else await editRecord(current);
    }, event.currentTarget);
  current = location.hash.slice(1) || "directory";
  await loadPage();
  const route = new URL(location.href);
  if (route.searchParams.get("create") === "1") {
    route.searchParams.delete("create");
    history.replaceState({}, "", route);
    if (!$("create-business").hidden) $("create-business").click();
  }
}
perform(boot);
