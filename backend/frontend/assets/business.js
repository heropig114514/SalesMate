/**
 * 职责：提供客户、交易、跟进、协作与外部动作的业务管理界面。
 * 实现：读取后端字段契约渲染表单，写请求携带版本；外部动作先展示冻结内容再单独确认。
 * 关联：workspace.js 共享导航、待办和 URL 客户上下文；sales-api.js 同源通信，不自动批准工具。
 * 目录：nameOf、label、display、notice、perform、showDialog、optionRows、relationOptions、fieldControl、
 * editRecord、readForm、detailRecord、runCommand、customerDetail、editCustomer、editContact、
 * groupingForm、attachmentForm、actionForm、renderActions、connectionForm、qqConnectionForm、refreshDirectory、
 * syncBusinessContext、loadPage、renderRows、renderStats、boot。
 * 变量索引：$ 为 DOM 查询；labels 为字段中文名；states 为状态中文名；
 * metadata 为资源契约，companies 为授权目录，user 为当前身份，current 为路由，page 为页码，
 * generation 为异步加载代次，relations 为当前已读关系名称缓存。
 */
import { request, escapeHtml as esc } from "./api.js";
import { mountWorkspace, setWorkspaceContext, refreshWorkspace, businessHref } from "./workspace.js";
import { salesRequest, allRows, uploadFile } from "./sales-api.js";

const $ = (id) => document.getElementById(id);
const labels = {
  company: "客户",
  contact: "联系人",
  primary_contact: "主要联系人",
  title: "标题",
  name: "名称",
  description: "说明",
  notes: "备注",
  currency: "币种",
  amount: "预计金额",
  unit_price: "单价",
  quantity: "数量",
  discount: "整行折扣金额",
  stock_quantity: "人工库存数量",
  sku: "产品编号",
  number: "单据编号",
  product: "产品",
  quote: "来源报价",
  order: "所属订单",
  due_at: "到期时间",
  expected_close: "预计成交日期",
  valid_until: "报价有效期",
  assigned_to: "负责人",
  team: "团队",
  user: "成员账号",
  role: "角色",
  group_key: "归组规则",
  phone: "电话",
  conversation: "会话",
  kind: "草稿类型",
  subject: "邮件主题",
  content: "内容",
  recipients: "收件人",
  client_key: "消息标识",
  status: "状态",
  archived: "已归档",
  total: "净额",
  created_at: "创建时间",
  updated_at: "更新时间",
  sent_at: "发送时间",
  confirmed_at: "订单确认时间",
  priority: "优先级",
  provider: "服务",
  account: "连接账号",
  read_at: "已读时间",
  follow_up: "关联跟进",
  size: "文件字节数",
  content_type: "文件类型",
  sha256: "文件校验摘要",
  approved_at: "确认时间",
  started_at: "开始时间",
  finished_at: "完成时间",
  tool: "工具",
  external_message_id: "外部邮件标识",
  source_revision: "提醒来源版本",
  event: "操作",
  actor_id: "操作者",
  object_type: "对象类型",
  object_id: "对象标识",
  company_id: "客户",
  email: "邮箱",
  username: "用户名",
};
const states = {
  draft: "草稿",
  approved: "已审核 / 已确认",
  sent: "已发送",
  accepted: "客户已接受",
  rejected: "客户已拒绝",
  confirmed: "已确认",
  fulfilled: "已履约",
  cancelled: "已取消",
  open: "待处理",
  in_progress: "处理中",
  resolved: "已解决",
  closed: "已关闭",
  new: "新商机",
  qualified: "已确认需求",
  proposal: "方案沟通",
  won: "已赢单",
  lost: "已丢单",
  completed: "已完成",
  pending_confirmation: "待人工确认",
  running: "执行中",
  succeeded: "执行成功",
  failed: "执行失败",
  uncertain: "结果待核对",
  viewer: "只读",
  editor: "可编辑",
  manager: "管理员",
  low: "低",
  normal: "普通",
  high: "高",
  chat: "聊天草稿",
  email: "邮件草稿",
  user: "用户",
  assistant: "助手",
  gmail: "Gmail 发信",
  qq: "QQ 发信",
  calendar: "Google 日历",
  "gmail.send": "Gmail 发送邮件",
  "qq.send": "QQ 发送邮件",
  "calendar.create": "创建会议",
  none: "不发送日历通知",
  all: "通知全部参会人",
  externalOnly: "仅通知外部参会人",
};
let metadata = {},
  companies = [],
  user = null,
  current = "directory",
  page = 1,
  generation = 0;
const relations = new Map();

/** 功能：给授权记录选择可辨识的业务名称。输入：row 记录。
 * 输出：纯文本名称。逻辑：客户未命名时使用已有域名或联系人，草稿显示主题或内容摘要。
 * 约束：仅作界面标识，不把域名或联系人写回公司名称。 */
function nameOf(row) {
  if (Array.isArray(row.contacts))
    return (
      row.name ||
      row.domains?.[0] ||
      row.contacts[0]?.name ||
      row.contacts[0]?.email ||
      "未命名客户"
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
      : `记录 ${String(row.id).slice(0, 8)}`)
  );
}

/** 功能：返回业务字段名称。输入：name。输出：中文名称或原字段名。
 * 逻辑：固定词表转换。约束：未知契约字段保留实际名称，避免错误解释。 */
function label(name) {
  return labels[name] || name;
}

/** 功能：将值转换为可读文本。输入：value、可选 key。
 * 输出：纯文本。逻辑：状态、关系、数组及时间按明确类型展示。
 * 约束：返回值插入 HTML 时仍须转义；不渲染可执行 HTML。 */
function display(value, key = "") {
  if (value === null || value === undefined || value === "") return "未填写";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value))
    return value.map((item) => display(item)).join("、") || "无";
  if (typeof value === "object")
    return Object.entries(value)
      .map(([name, item]) => `${label(name)}：${display(item, name)}`)
      .join("\n");
  if (["company", "company_id"].includes(key))
    return companies.find((item) => item.id === value)
      ? nameOf(companies.find((item) => item.id === value))
      : String(value);
  if (relations.has(String(value))) return relations.get(String(value));
  if (["status", "role", "priority", "kind", "provider", "tool"].includes(key))
    return states[value] || value;
  if (key.endsWith("_at")) return new Date(value).toLocaleString();
  return String(value);
}

/** 功能：显示明确的操作错误。输入：error、modal 是否位于对话框。
 * 输出：无。逻辑：textContent 防止异常文本注入。约束：不隐藏失败或自动重试。 */
function notice(error, modal = false) {
  const node = $(modal ? "editor-error" : "business-notice");
  node.textContent = error.message || String(error);
  node.hidden = false;
}

/** 功能：执行一次界面操作并恢复控件。输入：task 异步函数、可选 button。
 * 输出：无。逻辑：阻止同按钮重复提交，异常显示于当前对话框或主页面。
 * 约束：不把后端失败解释为成功，不自动确认任何操作。 */
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

/** 功能：打开原生模态对话框。输入：title、body 为已转义 HTML。
 * 输出：无。逻辑：替换上一操作视图并聚焦首个控件。
 * 约束：所有业务数据须在调用处通过 esc 转义。 */
function showDialog(title, body) {
  $("editor-title").textContent = title;
  $("editor-body").innerHTML = body;
  $("editor-error").hidden = true;
  if (!$("editor").open) $("editor").showModal();
  $("editor-body").querySelector("input,select,textarea,button")?.focus();
}

/** 功能：构造安全的下拉选项。输入：rows、selected 当前标识。
 * 输出：option HTML。逻辑：id 与展示名称分别转义。
 * 约束：不根据名称推断业务归属。 */
function optionRows(rows, selected = "") {
  return (
    `<option value="">请选择</option>` +
    rows
      .map(
        (row) =>
          `<option value="${esc(row.id)}" ${String(row.id) === String(selected) ? "selected" : ""}>${esc(nameOf(row))}</option>`,
      )
      .join("")
  );
}

/** 功能：读取已授权关系选项。输入：field 契约字段。
 * 输出：含 id 和展示名的数组。逻辑：公司和联系人来自业务目录，其他资源从分页 API 读取。
 * 约束：前端筛选不能替代后端权限；成员账号允许按完整用户名另行查找。 */
async function relationOptions(field) {
  let rows;
  if (field.relation === "company")
    rows = companies.filter((item) => !item.archived);
  else if (field.relation === "contact")
    rows = companies.flatMap((company) =>
      company.contacts.map((contact) => ({
        ...contact,
        name: `${company.name || "未命名客户"} · ${contact.name || contact.email}`,
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

/** 功能：渲染单个业务表单字段。输入：field、value、options 关系选项。
 * 输出：label 和控件 HTML。逻辑：金额用十进制字符串输入，收件人使用分隔邮箱列表。
 * 约束：不会为币种、价格、会议通知方式补填推测值。 */
function fieldControl(field, value, options = []) {
  const name = field.name,
    required = field.required ? "required" : "",
    v = value ?? "";
  let control;
  if (field.type === "relation")
    control = `<select name="${esc(name)}" ${required}>${optionRows(options, v)}</select>`;
  else if (field.type === "choice")
    control = `<select name="${esc(name)}" ${required}><option value="">请选择</option>${field.choices.map((choice) => `<option value="${esc(choice)}" ${choice === v ? "selected" : ""}>${esc(states[choice] || choice)}</option>`).join("")}</select>`;
  else if (field.type === "boolean")
    control = `<select name="${esc(name)}"><option value="false">否</option><option value="true" ${v === true ? "selected" : ""}>是</option></select>`;
  else if (["description", "notes", "content", "recipients"].includes(name))
    control = `<textarea name="${esc(name)}" ${required} rows="${name === "content" ? 7 : 3}" placeholder="${name === "recipients" ? "多个邮箱以逗号或换行分隔" : ""}">${esc(Array.isArray(v) ? v.join("\n") : v)}</textarea>`;
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
    control = `<input name="${esc(name)}" type="${type}" value="${esc(shown)}" ${required} ${type === "number" ? 'step="any"' : ""} ${name === "currency" ? 'maxlength="3" placeholder="例如 USD / SGD / CNY"' : ""}>`;
  }
  return `<label class="${["description", "notes", "content", "recipients"].includes(name) ? "wide" : ""}">${esc(name === "title" && field.profileTitle ? "职位" : label(name))}${field.required ? " *" : ""}${control}${name === "group_key" ? "<small>域名填 domain:example.com；指定联系人填 contact:name@example.com。</small>" : ""}</label>`;
}

/** 功能：打开创建或编辑业务记录表单。输入：resource、可选 record 和 preset。
 * 输出：无。逻辑：读取实际可写字段和关系选项，保存使用读取时的 revision。
 * 约束：消息只能创建；工具动作、文件与连接使用专用流程。 */
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
    `${record ? "编辑" : "新建"}${definition.label}`,
    `<form id="record-form"><div class="form-grid">${editable.map((field) => fieldControl(field, values[field.name], options.get(field.name))).join("")}</div>${resource === "memberships" ? '<div class="section"><label>按完整用户名查找成员<input id="member-username" placeholder="输入已有账号的用户名"></label><button id="find-member" type="button">查找账号</button></div>' : ""}<p class="form-note">${["quote-lines", "order-lines"].includes(resource) ? "单价与描述作为本次单据快照保存。选择产品后请明确填写本次价格，不会自动覆盖历史价格。" : "留空的非必填项保持未知或使用该字段已声明的初始值。"}${editable.some((f) => f.type === "datetime") ? " 时间按当前设备时区输入。" : ""}</p><div class="actions"><button class="primary" type="submit">保存${definition.label}</button></div></form>`,
  );
  if ($("find-member"))
    $("find-member").onclick = (event) =>
      perform(async () => {
        const found = (
          await salesRequest(
            `people/?username=${encodeURIComponent($("member-username").value)}`,
          )
        ).results;
        if (!found.length) throw new Error("未找到该有效账号。");
        const select = $("record-form").elements.namedItem("user");
        select.insertAdjacentHTML(
          "beforeend",
          optionRows(found).replace('<option value="">请选择</option>', ""),
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

/** 功能：把表单转为严格接口数据。输入：form、fields 契约、editing 是否编辑。
 * 输出：请求字典。逻辑：空值按 nullable 或可选语义处理，日期带时区，金额保持字符串。
 * 约束：不静默纠正非法邮箱和业务关系，后端负责最终校验。 */
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

/** 功能：显示单条记录和可用业务动作。输入：resource、id。
 * 输出：无。逻辑：重新获取版本，展示净额、时间及单据行，按后端状态边生成按钮。
 * 约束：外部动作审批使用完整内容预览；浏览器不能直接设置已发送状态。 */
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
    definition.label,
    `<dl class="details">${rows.map(([key, value]) => `<dt>${esc(label(key))}</dt><dd>${esc(display(value, key))}</dd>`).join("")}</dl>${record.lines ? `<div class="section"><h3>单据明细 · ${esc(record.currency)} ${esc(record.total)}</h3>${record.lines.map((line) => `<p>${esc(line.description)} · ${esc(line.quantity)} × ${esc(line.unit_price)} − ${esc(line.discount)} <button type="button" data-line="${esc(line.id)}">查看明细</button></p>`).join("") || '<p class="muted">尚无明细。</p>'}${record.status === "draft" && !record.archived ? '<button id="add-line" type="button">＋ 添加明细</button>' : ""}</div>` : ""}<div class="section actions">${editable ? '<button id="edit-record" type="button">编辑内容</button>' : ""}${transitions.map((state) => `<button data-state="${esc(state)}" type="button">${esc(states[state] || state)}</button>`).join("")}${!["messages", "notifications"].includes(resource) ? `<button id="archive-record" type="button">${record.archived ? "恢复记录" : resource === "connections" ? "停用连接" : "归档"}</button>` : ""}${resource === "notifications" && !record.read_at ? '<button id="mark-read" type="button">标记已读</button>' : ""}${resource === "files" && !record.archived ? `<a href="/api/v1/sales/files/${esc(record.id)}/download/">下载附件</a>` : ""}</div>`,
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

/** 功能：执行已明确选择的版本化业务命令。输入：resource、record、command、value。
 * 输出：无。逻辑：保存后关闭详情并刷新视图和公司版本。
 * 约束：失败保持原对话框；批准外部动作另由专用预览入口调用。 */
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

/** 功能：显示客户档案与联系人操作。输入：id。
 * 输出：无。逻辑：从授权目录读取版本并设置共享客户上下文；客户操作继续使用原权限检查。
 * 约束：客户核心资料和合并由 owner 维护，后端继续验证授权。 */
async function customerDetail(id) {
  await refreshDirectory();
  const company = companies.find((item) => item.id === id);
  if (!company) throw new Error("客户已不可见，请刷新。");
  $("company-filter").value = company.id;
  syncBusinessContext();
  showDialog(
    company.name || "未命名客户",
    `<p class="muted">${esc(company.domains.join(" · ") || "尚未指定公司域名")}</p><dl class="details">${Object.entries(
      company.customer,
    )
      .filter(([key]) => key !== "customer_id")
      .map(
        ([key, value]) =>
          `<dt>${esc({ industry_from_crm: "行业", employee_count: "员工人数", employee_count_source: "人数来源", first_deal_at: "首次成交时间" }[key] || label(key))}</dt><dd>${esc(display(value, key))}</dd>`,
      )
      .join(
        "",
      )}</dl><div class="actions"><button id="customer-edit">编辑档案</button><button id="customer-settings">主要联系人 / 备注 / 归档</button><a href="/#company/${esc(company.id)}">邮件与分析 ↗</a><a href="${esc(businessHref("quotes", company.id, { create: "1" }))}">创建报价</a><a href="${esc(businessHref("follow-ups", company.id, { create: "1" }))}">安排跟进</a></div><div class="section"><h3>联系人</h3>${company.contacts.map((c) => `<p>${esc(c.name || "姓名未知")} · ${esc(c.email)} <button data-contact="${esc(c.id)}">编辑身份</button><button data-profile="${esc(c.id)}">职位 / 电话 / 备注</button></p>`).join("") || '<p class="muted">暂无联系人</p>'}<button id="contact-add">＋ 新增联系人</button></div><div class="section"><h3>人工归组</h3><p class="muted">搬移已选择的邮件，或将另一家公司合入此客户。未来邮件可单独配置域名 / 联系人规则。</p><div class="actions"><button id="group-move">搬移邮件</button><button id="group-merge">合并客户</button><button id="group-alias">新增归组规则</button></div></div>`,
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

/** 功能：编辑已确认客户档案。输入：company 当前目录版本。
 * 输出：无。逻辑：使用既有建档 API 和相同人数来源规则。
 * 约束：保存后按原项目配置触发分析，不更改模型、权重或时区。 */
function editCustomer(company) {
  showDialog(
    "编辑客户档案",
    `<form id="customer-form"><div class="form-grid"><label class="wide">公司名称<input name="company_name" value="${esc(company.name)}" required></label><label>行业<select name="industry_from_crm">${["unknown", "半导体检测", "精密量测", "光学检测", "工业检测"].map((value) => `<option value="${value}" ${company.customer.industry_from_crm === value ? "selected" : ""}>${value === "unknown" ? "未知" : value}</option>`).join("")}</select></label><label>员工人数<input name="employee_count" type="number" min="0" value="${esc(company.customer.employee_count)}"></label><label class="wide">人数来源<input name="employee_count_source" value="${esc(company.customer.employee_count_source)}"></label></div><div class="actions"><button class="primary">保存客户档案</button></div></form>`,
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

/** 功能：新增或编辑联系人邮箱身份。输入：company 和可选 contact。
 * 输出：无。逻辑：写入公司版本，有历史邮件时后端禁止改邮箱。
 * 约束：电话职位另存补充资料，不覆盖原邮件事实。 */
function editContact(company, contact = null) {
  showDialog(
    contact ? "编辑联系人" : "新增联系人",
    `<form id="contact-form"><div class="form-grid"><label>姓名<input name="name" value="${esc(contact?.name)}"></label><label>邮箱<input name="email" type="email" value="${esc(contact?.email)}" required></label></div><div class="actions"><button class="primary">保存联系人</button></div></form>`,
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

/** 功能：展示人工归组的完整计划表单。输入：target 客户、operation 为 move/merge。
 * 输出：无。逻辑：明确选择来源，搬移逐封勾选邮件，显示两客户名称后再次确认。
 * 约束：没有默认全选；合并冲突由后端事务拒绝，不自动扩大共享权限。 */
function groupingForm(target, operation) {
  showDialog(
    operation === "move" ? "搬移邮件到当前客户" : "合并客户",
    `<form id="group-form"><p>目标客户：<strong>${esc(target.name)}</strong></p><label>来源客户<select name="source" required>${optionRows(companies.filter((c) => c.id !== target.id && !c.archived))}</select></label>${operation === "move" ? '<button id="load-mails" type="button">读取来源邮件</button><div id="mail-choices" class="section"></div>' : '<p class="warning">合并会转移来源邮件和业务记录，并归档来源客户。历史分析仍保留在来源；存在冲突会整次拒绝。</p>'}<div class="actions"><button class="primary">审阅归组计划</button></div></form>`,
  );
  if ($("load-mails"))
    $("load-mails").onclick = (event) =>
      perform(async () => {
        const source = $("group-form").elements.source.value;
        if (!source) throw new Error("请先选择来源客户。");
        const data = await request(`companies/${source}/`);
        const emails = data.emails || data.context?.emails || [];
        $("mail-choices").innerHTML =
          emails
            .map(
              (mail) =>
                `<label class="check"><input type="checkbox" name="mail" value="${esc(mail.dedupe_key)}">${esc(mail.subject)} · ${esc(mail.sent_at || "")}</label>`,
            )
            .join("") || "<p>来源没有可搬移邮件。</p>";
        $("mail-choices").dataset.source = source;
      }, event.currentTarget);
  $("group-form").onsubmit = (event) => {
    event.preventDefault();
    perform(async () => {
      const source = companies.find(
        (c) => c.id === event.currentTarget.elements.source.value,
      );
      if (!source) throw new Error("请选择来源客户。");
      const data = {
        source_id: source.id,
        target_id: target.id,
        source_revision: source.revision,
        target_revision: target.revision,
      };
      if (operation === "move") {
        if ($("mail-choices").dataset.source !== source.id)
          throw new Error("请重新读取所选来源的邮件。");
        data.keys = [...$("mail-choices").querySelectorAll(":checked")].map(
          (node) => node.value,
        );
        if (!data.keys.length) throw new Error("至少选择一封邮件。");
      }
      showDialog(
        "确认人工归组",
        `<p>来源：${esc(source.name)}</p><p>目标：${esc(target.name)}</p><p>${operation === "move" ? `搬移 ${data.keys.length} 封已选择邮件。` : "转移邮件和业务关系，并归档来源公司。"}</p><button id="confirm-group" class="primary">确认${operation === "move" ? "搬移" : "合并"}</button>`,
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

/** 功能：上传客户私有附件。输入：无参数，读取客户目录和筛选。
 * 输出：无。逻辑：选择客户与单文件后交给受保护上传 API。
 * 约束：限制 20 MiB，不把附件公开托管。 */
function attachmentForm() {
  showDialog(
    "上传私有附件",
    `<form id="upload-form"><div class="form-grid"><label>客户<select name="company" required>${optionRows(companies, $("company-filter").value)}</select></label><label>文件（最多 20 MiB）<input name="file" type="file" required></label></div><p class="form-note">只有当前员工可以下载本附件。</p><div class="actions"><button class="primary">上传并保存</button></div></form>`,
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

/** 功能：准备邮件发送或会议创建计划。输入：无参数，读取当前筛选及连接。
 * 输出：无。逻辑：按动作提供方筛选连接，明确指定客户和内容来源，后端返回冻结快照供用户审阅。
 * 约束：准备计划不会执行；会议时间使用当前设备时区转换为有偏移的绝对时间。 */
async function actionForm() {
  const connections = await allRows("records/connections/"),
    drafts = await allRows("records/drafts/"),
    quotes = await allRows("records/quotes/");
  if (!connections.length)
    throw new Error("请先在“外部连接”中连接发信邮箱或日历，再准备动作。");
  const key = crypto.randomUUID();
  showDialog(
    "准备外部动作",
    `<form id="action-form"><div class="form-grid"><label>客户<select name="company" required>${optionRows(companies, $("company-filter").value)}</select></label><label>动作类型<select name="tool" required><option value="gmail.send">Gmail 发送邮件</option><option value="qq.send">QQ 发送邮件</option><option value="calendar.create">创建会议</option></select></label><label class="wide">外部连接<select name="connection_id" required>${optionRows(connections.map((c) => ({ ...c, name: `${states[c.provider]} · ${c.account}` })))}</select></label><div id="email-fields" class="wide form-grid"><label>邮件草稿<select name="draft_id">${optionRows(drafts.filter((d) => d.kind === "email"))}</select></label><label>附带已审核报价（可选）<select name="quote_id">${optionRows(quotes.filter((q) => q.status === "approved"))}</select></label></div><div id="calendar-fields" class="wide form-grid" hidden><label>日历标识<input name="calendar_id" placeholder="例如 primary"></label><label>会议标题<input name="title"></label><label>开始时间<input name="start" type="datetime-local"></label><label>结束时间<input name="end" type="datetime-local"></label><label class="wide">说明<textarea name="description"></textarea></label><label class="wide">参会人邮箱<textarea name="attendees" placeholder="逗号或换行分隔，可留空"></textarea></label><label>日历通知方式<select name="send_updates"><option value="">请明确选择</option>${["none", "all", "externalOnly"].map((value) => `<option value="${value}">${states[value]}</option>`).join("")}</select></label></div></div><p class="form-note">准备后将展示完整收件人、正文或会议内容。只有你再次确认，任务才会进入执行队列。</p><div class="actions"><button class="primary">生成待确认计划</button></div></form>`,
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
          throw new Error("请填写会议起止时间。");
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

/** 功能：展示冻结外部动作并绑定确认、取消和核对。
 * 输入：record 完整动作响应。输出：无。
 * 逻辑：邮件展示账号、收件人、主题和逐字正文；会议展示时间、参会人与通知范围。
 * 约束：确认按钮是单独用户操作，未知结果只能查询，不提供自动重试按钮。 */
function renderActions(record) {
  const p = record.parameters,
    email = ["gmail.send", "qq.send"].includes(record.tool);
  const preview = email
    ? `<p>发件账号：${esc(p.account)}</p><p>收件人：${esc(p.to.join("、"))}</p><h3>${esc(p.subject)}</h3><div class="preview">${esc(p.body)}</div>`
    : `<p>账号：${esc(p.account)} · 日历：${esc(p.calendar_id)}</p><h3>${esc(p.title)}</h3><p>${esc(new Date(p.start).toLocaleString())} → ${esc(new Date(p.end).toLocaleString())}</p><p>参会人：${esc(p.attendees.join("、") || "无")}</p><p>通知：${esc(states[p.send_updates])}</p><div class="preview">${esc(p.description)}</div>`;
  showDialog(
    `${states[record.tool]} · ${states[record.status]}`,
    `<p>客户：${esc(display(record.company, "company"))}</p>${preview}${record.error ? `<p class="warning">${esc(record.error.message)}</p>` : ""}${
      record.result
        ? `<dl class="details">${Object.entries(record.result)
            .map(
              ([key, value]) =>
                `<dt>${esc({ message_id: "邮件标识", thread_id: "邮件会话标识", event_id: "日历事件标识", url: "外部事件链接", submission_status: "提交状态", sent_copy_id: "发送副本标识" }[key] || key)}</dt><dd>${esc({ smtp_accepted: "QQ 服务器已接受（不代表最终送达）", confirmed_in_sent: "已核对发送副本" }[value] || value)}</dd>`,
            )
            .join("")}</dl>`
        : ""
    }<p class="form-note">确认后由销售任务进程执行。取消仅适用于尚未开始的动作。</p><div class="actions">${record.status === "pending_confirmation" ? '<button id="approve-action" class="primary">确认并加入执行队列</button>' : ""}${["pending_confirmation", "approved"].includes(record.status) ? '<button id="cancel-action">取消动作</button>' : ""}${record.status === "running" ? '<button id="interrupt-action">已核实进程中断，标记待核对</button>' : ""}${record.status === "uncertain" ? '<button id="verify-action">到外部服务核对结果</button>' : ""}</div>`,
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

/** 功能：选择 QQ 发信或 Google 写权限连接。输入：无参数。
 * 输出：无。逻辑：QQ 打开独立授权码表单；Google 获取固定 OAuth 地址并导航。
 * 约束：不会扩展既有只读 Gmail 连接；配置不足显示明确错误。 */
function connectionForm() {
  showDialog(
    "连接外部服务",
    '<p>Gmail 连接申请发送与核对已发送邮件权限；日历连接申请事件管理与读取权限。原邮件同步连接保持独立。</p><div class="actions"><button id="connect-qq-send">连接 QQ 发信</button><button data-provider="gmail">连接 Gmail 发信</button><button data-provider="calendar">连接 Google 日历</button></div>',
  );
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

/** 功能：收集独立 QQ 发信授权。输入：无参数，读取本次表单。
 * 输出：无。逻辑：提交固定服务连接入口；请求开始及对话框关闭时清空授权码。
 * 约束：仅验证登录，不发送邮件；不保存到浏览器缓存，不复用收信授权。 */
function qqConnectionForm() {
  showDialog("连接 QQ 发信", '<form id="qq-send-form"><div class="form-grid"><label class="wide">QQ 或 foxmail 邮箱<input name="address" type="email" autocomplete="off" required></label><label class="wide">客户端授权码<input name="authorization_code" type="password" autocomplete="new-password" minlength="16" maxlength="16" required></label></div><p class="form-note">与 QQ 收信连接独立。此操作仅验证连接；发送前仍需预览并确认。请在 QQ 邮箱中开启 SMTP；核对发送结果还需开启 IMAP 并保留发送副本。</p><div class="actions"><button class="primary">验证并连接发信</button></div></form>');
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

/** 功能：刷新授权客户目录并保持筛选。输入：无参数。
 * 输出：无。逻辑：完整分页读取含归档目录，重建名称缓存。
 * 约束：请求失败不展示伪造空目录。 */
async function refreshDirectory() {
  const selected = $("company-filter").value;
  companies = await allRows("directory/?archived=all");
  $("company-filter").innerHTML =
    '<option value="">全部客户</option>' +
    companies
      .map(
        (c) =>
          `<option value="${esc(c.id)}">${esc(nameOf(c))}${c.archived ? "（已归档）" : ""}</option>`,
      )
      .join("");
  $("company-filter").value = selected;
}

/** 功能：同步授权客户筛选、URL 和共享客户导航。输入：当前 company-filter、current。
 * 输出：无。逻辑：只保留目录中可见的客户，更新同源链接并让刷新恢复筛选。
 * 约束：不写业务数据；不使用设备缓存跨账号保留客户身份。 */
function syncBusinessContext() {
  const selected = companies.find(company => company.id === $("company-filter").value);
  const url = new URL(location.href);
  if (selected) url.searchParams.set("company", selected.id);
  else url.searchParams.delete("company");
  history.replaceState({}, "", url);
  setWorkspaceContext(selected ? { id: selected.id, name: nameOf(selected) } : null, current);
}

/** 功能：按当前路由加载一页记录。输入：无参数，读取 current/page/筛选。
 * 输出：无。逻辑：URL 筛选联动共享导航并刷新待办，异步代次防止慢响应覆盖新页面。
 * 约束：只读加载不执行外部动作或模型分析。 */
async function loadPage() {
  void refreshWorkspace();
  const turn = ++generation,
    resource = current;
  $("business-notice").hidden = true;
  const title =
    resource === "directory"
      ? "客户目录"
      : resource === "audit"
        ? "操作审计"
        : metadata[resource]?.label;
  if (!title) throw new Error("该业务页面不存在。");
  $("page-title").textContent = title;
  $("list-title").textContent = title;
  const supportsCompany = resource === "directory" || resource === "audit" || metadata[resource]?.fields.some(field => field.name === "company");
  $("company-filter").closest("label").hidden = !supportsCompany;
  if (!supportsCompany) $("company-filter").value = "";
  syncBusinessContext();
  const creatable = resource !== "audit" && resource !== "notifications";
  $("create-business").hidden = !creatable;
  $("create-business").textContent =
    resource === "connections"
      ? "连接服务"
      : resource === "files"
        ? "上传附件"
        : resource === "actions"
          ? "准备动作"
          : `＋ 新建${title.replace("目录", "")}`;
  const query = new URLSearchParams({
    page: String(page),
    page_size: "20",
    archived: $("include-archived").checked ? "all" : "false",
  });
  const statusField = metadata[resource]?.fields.find(field => field.name === "status" && field.type === "choice");
  const status = new URLSearchParams(location.search).get("status") || "";
  $("status-filter-label").hidden = !statusField;
  $("status-filter").innerHTML = '<option value="">全部状态</option>' + (statusField?.choices || []).map(value => `<option value="${esc(value)}">${esc(states[value] || value)}</option>`).join('');
  if (status && !statusField?.choices.includes(status)) throw new Error("该页面不支持此状态筛选，请从导航重新进入。");
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
  const path =
    resource === "directory"
      ? "directory/"
      : resource === "audit"
        ? "audit/"
        : `records/${resource}/`;
  $("business-content").innerHTML = '<div class="empty">正在读取…</div>';
  const [result, overview] = await Promise.all([
    salesRequest(`${path}?${query}`),
    salesRequest("overview/"),
  ]);
  if (turn !== generation) return;
  renderRows(resource, result.results);
  renderStats(overview);
  $("record-count").textContent = `共 ${result.count} 条`;
  const pages = Math.max(1, Math.ceil(result.count / 20));
  $("business-pagination").innerHTML =
    `<button id="business-prev" ${page === 1 ? "disabled" : ""}>上一页</button><span>${page} / ${pages}</span><button id="business-next" ${page >= pages ? "disabled" : ""}>下一页</button>`;
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
      ? "先审阅完整内容，再明确确认。失败或结果未知不会自动重试。"
      : resource === "products"
        ? "产品价格和人工库存独立维护，单据保留自己的价格快照。"
        : resource === "messages"
          ? "已持久化的用户消息；聊天模型回复尚未接入。"
          : "记录按当前账号及公司授权范围展示。";
}

/** 功能：渲染业务表格和详情入口。输入：resource、rows。
 * 输出：无。逻辑：展示业务名称、客户、状态和更新时间；审计只读。
 * 约束：空结果明确显示，不填充演示数据；业务内容全部转义。 */
function renderRows(resource, rows) {
  if (!rows.length) {
    $("business-content").innerHTML =
      '<div class="empty"><strong>这里还没有记录</strong>新建第一条业务记录，或调整客户与归档筛选。</div>';
    return;
  }
  $("business-content").innerHTML =
    `<table><thead><tr><th>${resource === "audit" ? "操作事件" : "名称 / 内容"}</th><th>客户 / 关联</th><th>状态</th><th>记录时间</th><th>操作</th></tr></thead><tbody>${rows
      .map(
        (row) =>
          `<tr><td><strong>${esc(resource === "directory" ? nameOf(row) : row.name || row.title || row.number || row.subject || row.group_key || row.account || row.event || (row.content ? row.content.slice(0, 65) : metadata[resource]?.label))}</strong>${row.currency ? `<small>${esc(row.currency)} ${esc(row.total ?? row.amount ?? row.unit_price ?? "")}</small>` : ""}${
            resource === "directory"
              ? `<small>${esc(
                  row.contacts
                    .map((c) => c.name || c.email)
                    .slice(0, 2)
                    .join(" · "),
                )}</small>`
              : ""
          }</td><td>${esc(display(row.company || row.company_id || row.conversation || row.team || row.contact || (resource === "directory" ? row.domains.join(" · ") : ""), row.company || row.company_id ? "company" : ""))}</td><td><span class="badge">${esc(row.archived ? "已归档" : states[row.status] || states[row.role] || (row.read_at ? "已读" : resource === "notifications" ? "未读" : resource === "directory" ? (row.crm_status === "registered" ? "已建档" : "待建档") : "有效"))}</span></td><td>${esc(display(row.updated_at || row.created_at || "", "updated_at"))}</td><td><button data-record="${esc(row.id)}">${resource === "audit" ? "查看事件" : "查看详情"}</button></td></tr>`,
      )
      .join("")}</tbody></table>`;
  for (const button of $("business-content").querySelectorAll("[data-record]"))
    button.onclick = () =>
      perform(async () => {
        if (resource === "directory")
          await customerDetail(button.dataset.record);
        else if (resource === "audit") {
          const row = rows.find((r) => r.id === button.dataset.record);
          showDialog(
            "操作审计",
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

/** 功能：显示当前权限范围内统计。输入：overview 后端汇总。
 * 输出：无。逻辑：全量授权目录的四个计数卡与分币种净额，不随客户筛选改变。
 * 约束：不把不同币种相加，不将确认订单净额标记为实际收入。 */
function renderStats(overview) {
  $("business-stats").innerHTML = [
    ["全部可见客户", overview.customers],
    ["待处理工单", overview.open_tickets],
    ["待跟进", overview.open_follow_ups],
    ["未读提醒", overview.unread_notifications],
  ]
    .map(
      ([name, value]) =>
        `<div class="stat"><span>${name}</span><strong>${value}</strong></div>`,
    )
    .join("");
  document.querySelector(".footnote").textContent = `已确认订单净额：${
    Object.entries(overview.confirmed_order_net)
      .map(([c, v]) => `${c} ${v}`)
      .join(" / ") || "暂无"
  }；开放商机预计金额：${
    Object.entries(overview.open_opportunity_amount)
      .map(([c, v]) => `${c} ${v}`)
      .join(" / ") || "暂无"
  }。各币种分别统计；库存由人工维护。`;
}

/** 功能：初始化登录态、元数据及页面交互。输入：无参数，读取当前路由。
 * 输出：无。逻辑：验证 URL 客户后挂载共享导航，恢复筛选并可打开明确请求的新建表单。
 * 约束：初始化仅执行读取；用户数据不保存到浏览器本地存储。 */
async function boot() {
  const session = await request("session/");
  if (!session.authenticated) {
    location.assign("/");
    return;
  }
  user = await request("accounts/me/");
  $("account").textContent = `当前员工：${user.username}`;
  metadata = Object.fromEntries(
    (await salesRequest("catalog/")).resources.map((item) => [item.key, item]),
  );
  mountWorkspace(current);
  await refreshDirectory();
  const initialCompany = new URLSearchParams(location.search).get("company");
  if (initialCompany && !companies.some(company => company.id === initialCompany)) throw new Error("链接中的客户不存在或当前账号无权访问。请从客户导航重新选择。");
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
          "新建客户",
          '<form id="new-company"><label>公司名称<input name="name" required maxlength="240"></label><div class="actions"><button class="primary">建立客户</button></div></form>',
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
