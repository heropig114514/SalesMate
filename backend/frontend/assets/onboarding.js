/** Responsibility: Provide personal, company, product, and solution onboarding steps and subsequent editing.
 * Implementation: Preserve item id and explicit linked_product_id during edits; save account information through versioned APIs. Drafts stay on this page; manual products and CSV imports share an edit list, and files use private endpoints.
 * Relationships: The 0919 interface and shared language/API resources use coordinated cache versions; company-settings.js owns the company step, onboarding.css supplies layout, and accounts/onboarding persists data.
 * Directory: text, field, tags, fileLink, status, save, chooseStep, personalForm, renderProducts, renderSolutions, upload, parseCSV, importProducts, mountOnboarding.
 * Variable index: $ queries DOM; text selects language; industries matches inbox industries; regions contains region options; steps contains four step labels; data is the current account snapshot; step is the active step; wizard flags first onboarding; busy prevents duplicate submissions; editing is the current product index.
 */
import { language, t } from "./i18n.js?v=20260921-product";
import { request, escapeHtml as e } from "./api.js?v=20260921-product";
const $ = (id) => document.getElementById(id);
/** Function: Select interface language. Inputs: zh/en text. Outputs: Text. Logic: Use language preferences. Constraints: Never translate user input. */
const text = (zh, en) => (language === "en" ? en : zh);
const industries = ["半导体检测", "精密量测", "光学检测", "工业检测"];
const regions = ["亚太", "欧洲", "北美", "南美", "中东", "非洲"];
const steps = [
  text("个人信息", "Your profile"),
  text("公司资料", "Company"),
  text("产品目录", "Products"),
  text("销售方案", "Solutions"),
];
let data,
  step = 0,
  wizard = false,
  busy = false,
  editing = null;

/** Function: Build a form field. Inputs: name, label, value, type. Outputs: Safe HTML. Logic: Use native forms. Constraints: Required status is defined only by the corresponding form. */
function field(name, label, value = "", type = "text") {
  return `<label>${e(label)}<input name="${name}" type="${type}" value="${e(value)}" maxlength="${name === "email" ? 254 : 150}"></label>`;
}
/** Function: Render a multiple selection. Inputs: name, option values, and selected values. Outputs: HTML. Logic: Independent checkboxes. Constraints: No selection remains an empty array. */
function tags(name, values, selected = []) {
  return `<div class="setup-tags">${values.map((value) => `<label><input type="checkbox" name="${name}" value="${e(value)}" ${selected.includes(value) ? "checked" : ""}>${e(t(value))}</label>`).join("")}</div>`;
}
/** Function: Generate a private-attachment entry. Inputs: id. Outputs: A link or missing-upload text. Logic: Same-origin UUID routes. Constraints: The server always checks account ownership. */
function fileLink(id) {
  return id
    ? `<a href="/api/v1/accounts/onboarding/documents/${encodeURIComponent(id)}/" target="_blank" rel="noopener">${e(text("在线阅读 ↗", "Read online ↗"))}</a>`
    : e(text("未上传", "No file"));
}
/** Function: Display action feedback. Inputs: message and error. Outputs: None. Logic: Plain text with state-dependent color. Constraints: Never display request IDs or sensitive input. */
function status(message, error = false) {
  $("setup-status").textContent = message;
  $("setup-status").classList.toggle("is-error", error);
}
/** Function: Save a specified step. Inputs: patch. Outputs: Success boolean. Logic: Version conflicts preserve drafts and request a refresh. Constraints: No retries or automatic input discarding. */
async function save(patch) {
  if (busy) return false;
  busy = true;
  $("setup-root")
    .querySelectorAll("button")
    .forEach((button) => {
      button.disabled = true;
    });
  status(text("正在保存…", "Saving…"));
  try {
    const saved = await request("accounts/onboarding/", {
      method: "PATCH",
      data: patch,
      version: data.revision,
    });
    data = {
      ...saved,
      products: Object.hasOwn(patch, "products")
        ? saved.products
        : data.products,
      solutions: Object.hasOwn(patch, "solutions")
        ? saved.solutions
        : data.solutions,
    };
    status(text("已保存到当前账号。", "Saved to your account."));
    return true;
  } catch (error) {
    console.error("onboarding_save_failed", { step, status: error.status });
    status(
      error.status === 409
        ? text(
            "资料已在其他页面修改。请保留本页输入并刷新后重新编辑。",
            "This profile changed elsewhere. Keep a copy of your edits and reload.",
          )
        : error.message,
      true,
    );
    return false;
  } finally {
    busy = false;
    $("setup-root")
      .querySelectorAll("button")
      .forEach((button) => {
        button.disabled = false;
      });
  }
}
/** Function: Switch steps. Inputs: index from 0 to 3. Outputs: None. Logic: Preserve unsubmitted form DOM and memory drafts while showing progress. Constraints: Switching does not write data. */
function chooseStep(index) {
  step = index;
  $("company-step").hidden = index !== 1;
  for (const node of document.querySelectorAll("[data-setup-step]"))
    node.hidden = Number(node.dataset.setupStep) !== index;
  for (const node of document.querySelectorAll("[data-step]")) {
    node.setAttribute(
      "aria-current",
      Number(node.dataset.step) === index ? "step" : "false",
    );
  }
  $("setup-progress").textContent = `${index + 1} / 4 · ${steps[index]}`;
  $("setup-meter").value = index + 1;
  $("setup-skip").hidden = !wizard;
  $("setup-skip").textContent =
    index === 3
      ? text("跳过并进入收件箱", "Skip and open inbox")
      : text("跳过此步", "Skip this step");
  $("setup-title").textContent = steps[index];
}
/** Function: Render personal information. Inputs: None; reads data.personal. Outputs: Form HTML. Logic: Save identity fields and responsibility scope separately. Constraints: Entering an email address is not OAuth authorization. */
function personalForm() {
  const p = data.personal;
  return `<form id="personal-form" class="setup-form" data-setup-step="0"><h2>${text("让每一次沟通，都有你的身份", "Make every conversation yours")}</h2><p class="muted">${text("填写你的身份和负责范围。所有步骤都可以跳过，之后在基础信息中补充。", "Add your identity and territory. You can skip any step and return later.")}</p><div class="setup-fields">${field("name", text("姓名", "Name"), p.name)}${field("title", text("职位 / 头衔", "Job title"), p.title)}${field("email", text("工作邮箱", "Work email"), p.email, "email")}${field("phone", text("电话（选填）", "Phone (optional)"), p.phone, "tel")}</div><p class="muted">${text("填写 Gmail 地址后，仍需前往邮箱设置完成 Google 授权。", "Gmail access requires a separate Google authorization in Email Settings.")}</p><fieldset><legend>${text("负责区域", "Regions")}</legend>${tags("regions", regions, p.regions)}</fieldset><fieldset><legend>${text("负责行业", "Industries")}</legend>${tags("industries", industries, p.industries)}</fieldset><button class="primary" type="submit">${wizard ? text("保存并继续 →", "Save and continue →") : text("保存个人信息", "Save profile")}</button></form>`;
}
/** Function: Render the product list. Inputs: In-memory data.products. Outputs: None. Logic: Distinguish reference prices from unknown prices; editing/removal does not yet write to storage. Constraints: File links require backend authorization. */
function renderProducts() {
  $("product-rows").innerHTML = data.products.length
    ? data.products
        .map(
          (p, i) =>
            `<tr><td><strong>${e(p.name)}</strong><small>${e(p.category)}</small></td><td>${p.specifications.map(e).join("<br>") || "—"}</td><td>${e(p.currency)} ${e(p.price_min ?? "—")} – ${e(p.price_max ?? "—")}</td><td>${p.scenarios.map(e).join(" / ") || "—"}</td><td>${fileLink(p.document_id)}</td><td><button type="button" class="text-btn" data-edit="${i}">${text("编辑", "Edit")}</button><button type="button" class="text-btn" data-remove="${i}">${text("移除", "Remove")}</button></td></tr>`,
        )
        .join("")
    : `<tr><td colspan="6" class="setup-empty">${text("还没有产品。添加第一款产品，或导入 CSV 表格。", "Add your first product or import a CSV file.")}</td></tr>`;
  $("product-count").textContent = data.products.length;
}
/** Function: Render solution cards. Inputs: data.solutions. Outputs: None. Logic: Show names and real private-attachment links. Constraints: Do not claim that a model has parsed the files. */
function renderSolutions() {
  $("solution-list").innerHTML = data.solutions.length
    ? data.solutions
        .map(
          (s, i) =>
            `<article class="solution-card"><span class="setup-file-icon" aria-hidden="true">▤</span><div><strong>${e(s.name)}</strong><p>${text("PDF / TXT · 登录后可读", "PDF / TXT · Private access")}</p>${fileLink(s.document_id)}</div><button type="button" class="text-btn" data-remove-solution="${i}">${text("移除", "Remove")}</button></article>`,
        )
        .join("")
    : `<p class="setup-empty">${text("添加常用销售方案，方便随时查阅。", "Add sales proposals for easy reference.")}</p>`;
}
/** Function: Upload a private file. Inputs: file object. Outputs: UUID or null. Logic: Submit multipart data for backend validation. Constraints: Empty optional files send no request; propagate failures to the caller. */
async function upload(file) {
  if (!file?.size) return null;
  const form = new FormData();
  form.set("file", file);
  const result = await request("accounts/onboarding/documents/", {
    method: "POST",
    data: form,
  });
  data.documents.push(result);
  return result.id;
}
/** Function: Parse quoted CSV. Inputs: source is UTF-8 text. Outputs: A two-dimensional array. Logic: Support escaped quotes, commas, and newlines; reject unclosed quotes. Constraints: Never execute formulas or infer column meanings. */
export function parseCSV(source) {
  const rows = [];
  let row = [],
    value = "",
    quoted = false;
  source = source.replace(/^\uFEFF/, "");
  for (let i = 0; i < source.length; i++) {
    const c = source[i];
    if (c === '"') {
      if (quoted && source[i + 1] === '"') {
        value += '"';
        i++;
      } else quoted = !quoted;
    } else if (!quoted && (c === "," || c === "\n" || c === "\r")) {
      row.push(value);
      value = "";
      if (c !== ",") {
        if (row.some((cell) => cell.trim())) rows.push(row);
        row = [];
        if (c === "\r" && source[i + 1] === "\n") i++;
      }
    } else value += c;
  }
  if (quoted) throw new Error(text("CSV 引号未闭合。", "Unclosed CSV quote."));
  row.push(value);
  if (row.some((cell) => cell.trim())) rows.push(row);
  return rows;
}
/** Function: Atomically import a spreadsheet into the current draft. Inputs: CSV file. Outputs: None. Logic: Validate all rows/columns before appending the batch. Constraints: At most 200 products; errors prevent partial import, and saving still requires a user click. */
async function importProducts(file) {
  if (!file) return;
  if (file.size > 1024 * 1024)
    throw new Error(text("CSV 不能超过 1 MiB。", "CSV must be at most 1 MiB."));
  const [header, ...rows] = parseCSV(await file.text());
  const names = [
    "name",
    "category",
    "specifications",
    "price_min",
    "price_max",
    "currency",
    "scenarios",
  ];
  if (!header || header.join(",") !== names.join(",") || !rows.length)
    throw new Error(
      text(
        "请使用模板列名，并至少填写一行产品。",
        "Use the template headers and include at least one product.",
      ),
    );
  const products = rows.map((row, i) => {
    if (
      row.length !== names.length ||
      !row[0].trim() ||
      !["SGD", "USD", "CNY", "EUR", "JPY"].includes(row[5])
    )
      throw new Error(
        text(
          `第 ${i + 2} 行格式或币种错误。`,
          `Invalid format or currency in row ${i + 2}.`,
        ),
      );
    const low = row[3].trim() || null,
      high = row[4].trim() || null;
    if (
      [low, high].some((v) => v !== null && !/^\d+(\.\d{1,2})?$/.test(v)) ||
      (low !== null && high !== null && Number(low) > Number(high))
    )
      throw new Error(
        text(
          `第 ${i + 2} 行价格范围无效。`,
          `Invalid price range in row ${i + 2}.`,
        ),
      );
    return {
      name: row[0],
      category: row[1],
      specifications: row[2].split("|").filter(Boolean),
      price_min: low,
      price_max: high,
      currency: row[5],
      scenarios: row[6].split("|").filter(Boolean),
      document_id: null,
    };
  });
  if (products.length + data.products.length > 200)
    throw new Error(
      text("最多支持 200 个产品。", "At most 200 products are supported."),
    );
  data.products.push(...products);
  renderProducts();
  status(
    text(
      `已导入 ${products.length} 行，请保存产品目录。`,
      `${products.length} rows imported. Save the catalog to keep them.`,
    ),
  );
}
/** Function: Mount the four-step flow. Inputs: Page, login session, and onboarding query parameter. Outputs: Promise.
 * Logic: Read account information before rendering; retain product IDs/catalog links during edits. Saves, uploads, and imports are explicit; the existing company module signals saves. Completion or skipping the last step opens the inbox.
 * Constraints: Unsaved input remains on the current page; the API enforces permissions. Never trigger sending, AI, or scoring. */
export async function mountOnboarding() {
  data = await request("accounts/onboarding/");
  wizard = new URLSearchParams(location.search).get("onboarding") === "1";
  $("setup-root").innerHTML =
    `<section class="setup-intro"><div><p class="eyebrow">YOUR SALES WORKSPACE</p><h2>${text("从了解你开始", "A workspace that knows you")}</h2><p class="muted">${text("补充你的背景、产品和方案，让销售资料集中在一处。", "Keep your background, products and proposals together.")}</p></div><a href="/#inbox">${text("返回收件箱 ↗", "Back to inbox ↗")}</a></section><nav class="setup-steps" aria-label="${text("资料步骤", "Profile steps")}">${steps.map((label, i) => `<button type="button" data-step="${i}"><span>0${i + 1}</span>${label}</button>`).join("")}</nav><div class="setup-progress"><span id="setup-progress"></span><progress id="setup-meter" max="4"></progress><button id="setup-skip" class="text-btn" type="button"></button></div><h2 id="setup-title" class="sr-only"></h2><p id="setup-status" role="status" aria-live="polite"></p>${personalForm()}<section data-setup-step="2" class="setup-form" hidden><div class="setup-section-heading"><div><h2>${text("你在销售什么？", "What do you sell?")} <span class="count-pill" id="product-count"></span></h2><p class="muted">${text("参考价格用于资料查阅，不会自动创建报价。", "Reference prices do not create quotations.")}</p></div><button type="button" id="product-add" class="secondary">${text("＋ 添加产品", "＋ Add product")}</button></div><div class="setup-import"><label class="secondary">${text("导入 CSV 表格", "Import CSV")}<input id="product-import" type="file" accept=".csv,text/csv"></label><a id="product-template" href="/static/products-template.csv" download>${text("下载模板", "Download template")}</a><span class="muted">${text("规格和场景多项用 | 分隔", "Separate specifications and tags with |")}</span></div><div class="setup-table"><table><thead><tr>${["产品 / 型号", "关键规格", "参考价格", "适用场景", "规格书", "操作"].map((label, i) => `<th>${text(label, ["Product / model", "Specifications", "Reference price", "Use cases", "Datasheet", "Actions"][i])}</th>`).join("")}</tr></thead><tbody id="product-rows"></tbody></table></div><button id="products-save" class="primary" type="button">${wizard ? text("保存并继续 →", "Save and continue →") : text("保存产品目录", "Save catalog")}</button></section><section class="setup-form" data-setup-step="3" hidden><h2>${text("常用方案，随时可查", "Your proposals, ready to read")}</h2><p class="muted">${text("上传 PDF 或 UTF-8 TXT，单文件不超过 5 MiB。文件仅当前账号可读。", "Upload a PDF or UTF-8 TXT, up to 5 MiB. Files are private to your account.")}</p><div id="solution-list" class="solution-list"></div><form id="solution-form" class="setup-upload">${field("name", text("方案名称", "Proposal name"))}<label>${text("方案文件", "Proposal file")}<input name="file" type="file" accept=".pdf,.txt" required></label><button type="submit" class="secondary">${text("添加方案", "Add proposal")}</button></form><button id="solutions-save" class="primary" type="button">${wizard ? text("保存并进入收件箱 →", "Save and open inbox →") : text("保存销售方案", "Save proposals")}</button></section><dialog id="product-dialog"><form id="product-form"><div class="setup-section-heading"><h2>${text("产品资料", "Product details")}</h2><button type="button" id="product-close" aria-label="${text("关闭", "Close")}">×</button></div><div class="setup-fields">${field("name", text("产品名称", "Product name"))}${field("category", text("类别 / 型号", "Category / model"))}<label>${text("参考价格下限", "Minimum reference price")}<input name="price_min" type="number" min="0" step="0.01"></label><label>${text("参考价格上限", "Maximum reference price")}<input name="price_max" type="number" min="0" step="0.01"></label><label>${text("币种", "Currency")}<select name="currency">${["SGD", "USD", "CNY", "EUR", "JPY"].map((v) => `<option>${v}</option>`).join("")}</select></label></div><label>${text("关键规格（每行一条）", "Specifications (one per line)")}<textarea name="specifications" rows="3"></textarea></label><label>${text("适用行业 / 场景（逗号分隔）", "Industries / use cases (comma separated)")}<input name="scenarios"></label><label>${text("规格书（选填，PDF / TXT，最多 5 MiB）", "Datasheet (optional, PDF / TXT, up to 5 MiB)")}<input name="file" type="file" accept=".pdf,.txt"></label><p id="product-error" role="alert"></p><button class="primary" type="submit">${text("加入目录草稿", "Add to catalog draft")}</button></form></dialog>`;
  $("personal-form").onsubmit = async (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    const personal = Object.fromEntries(form);
    personal.regions = form.getAll("regions");
    personal.industries = form.getAll("industries");
    if ((await save({ personal })) && wizard) chooseStep(1);
  };
  document.querySelectorAll("[data-step]").forEach((button) => {
    button.onclick = () => {
      if (!busy) chooseStep(Number(button.dataset.step));
    };
  });
  $("setup-skip").onclick = async () => {
    if (step < 3) chooseStep(step + 1);
    else if (await save({ completed: true })) location.assign("/#inbox");
  };
  window.addEventListener("company-profile-saved", () => {
    if (wizard) chooseStep(2);
  });
  $("products-save").onclick = async () => {
    if ((await save({ products: data.products })) && wizard) chooseStep(3);
  };
  $("solutions-save").onclick = async () => {
    if (
      (await save({
        solutions: data.solutions,
        ...(wizard ? { completed: true } : {}),
      })) &&
      wizard
    )
      location.assign("/#inbox");
  };
  $("product-import").onchange = async (event) => {
    try {
      await importProducts(event.target.files[0]);
    } catch (error) {
      status(error.message, true);
    } finally {
      event.target.value = "";
    }
  };
  $("product-form").elements.name.required = true;
  $("solution-form").elements.name.required = true;
  $("product-add").onclick = () => {
    editing = null;
    $("product-form").reset();
    $("product-error").textContent = "";
    $("product-dialog").showModal();
  };
  $("product-close").onclick = () => $("product-dialog").close();
  $("product-rows").onclick = (event) => {
    const remove = event.target.closest("[data-remove]"),
      edit = event.target.closest("[data-edit]");
    if (remove) {
      data.products.splice(Number(remove.dataset.remove), 1);
      renderProducts();
      status(
        text("目录已修改，请保存。", "Catalog changed. Save to keep changes."),
      );
    }
    if (edit) {
      editing = Number(edit.dataset.edit);
      const p = data.products[editing],
        form = $("product-form");
      form.reset();
      for (const key of [
        "name",
        "category",
        "price_min",
        "price_max",
        "currency",
      ])
        form.elements[key].value = p[key] ?? "";
      form.elements.specifications.value = p.specifications.join("\n");
      form.elements.scenarios.value = p.scenarios.join(", ");
      $("product-error").textContent = "";
      $("product-dialog").showModal();
    }
  };
  $("product-form").onsubmit = async (event) => {
    event.preventDefault();
    const form = event.target,
      values = new FormData(form),
      button = event.submitter;
    button.disabled = true;
    try {
      const low = values.get("price_min") || null,
        high = values.get("price_max") || null;
      if (low !== null && high !== null && Number(low) > Number(high))
        throw new Error(
          text(
            "价格下限不能大于上限。",
            "Minimum price cannot exceed maximum.",
          ),
        );
      if (editing === null && data.products.length >= 200)
        throw new Error(text("最多支持 200 个产品。", "At most 200 products."));
      const fileId = await upload(values.get("file"));
      const product = {
        ...(editing !== null ? data.products[editing] : {}),
        name: values.get("name"),
        category: values.get("category"),
        specifications: values
          .get("specifications")
          .split("\n")
          .map((v) => v.trim())
          .filter(Boolean),
        scenarios: values
          .get("scenarios")
          .split(/[,，]/)
          .map((v) => v.trim())
          .filter(Boolean),
        price_min: low,
        price_max: high,
        currency: values.get("currency"),
        document_id:
          fileId ||
          (editing !== null ? data.products[editing].document_id : null),
      };
      if (editing === null) data.products.push(product);
      else data.products[editing] = product;
      renderProducts();
      $("product-dialog").close();
      status(
        text(
          "已更新草稿，请保存产品目录。",
          "Draft updated. Save the catalog to keep it.",
        ),
      );
    } catch (error) {
      $("product-error").textContent = error.message;
    } finally {
      button.disabled = false;
    }
  };
  $("solution-form").onsubmit = async (event) => {
    event.preventDefault();
    event.submitter.disabled = true;
    try {
      const form = new FormData(event.target);
      const id = await upload(form.get("file"));
      data.solutions.push({ name: form.get("name"), document_id: id });
      renderSolutions();
      event.target.reset();
      status(
        text(
          "方案已加入草稿，请保存。",
          "Proposal added to draft. Save to keep it.",
        ),
      );
    } catch (error) {
      status(error.message, true);
    } finally {
      event.submitter.disabled = false;
    }
  };
  $("solution-list").onclick = (event) => {
    const button = event.target.closest("[data-remove-solution]");
    if (button) {
      data.solutions.splice(Number(button.dataset.removeSolution), 1);
      renderSolutions();
      status(
        text(
          "方案已修改，请保存。",
          "Proposals changed. Save to keep changes.",
        ),
      );
    }
  };
  renderProducts();
  renderSolutions();
  chooseStep(wizard ? 0 : 1);
}
