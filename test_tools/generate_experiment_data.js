/**
 * Responsibility: Generate reproducible synthetic data and optionally import it into a specified account through existing APIs.
 * Implementation: Fixed seeds/reference times produce fixtures; a separate importer checks mailbox permissions, ingests emails, and maps real business IDs.
 * Relationships: Email uses Agent ingestion APIs; business records use Session/Tool-authenticated agent-tools APIs.
 * Directory:
 * - generateExperimentData: Generate one experiment dataset.
 * - generateExperimentData.next: Advance deterministic random state.
 * - generateExperimentData.id: Generate fixture UUIDs.
 * - generateExperimentData.fact: Build facts with verbatim source evidence.
 * - downloadExperimentData: Download JSON in a browser.
 * - importExperimentData: Import generated data into an authorized account.
 * - importExperimentData.request: Submit one request to a fixed API.
 * - importExperimentData.call: Call a business tool and verify completion.
 * - importExperimentData.create: Remove read-only fixture fields and map related records.
 * Variable index: No mutable module configuration; defaults are defined in function parameters. Developer diagnostics use English; synthetic business fixtures retain their original language.
 * Constraints: Generation/download does not persist data. Import requires explicit invocation, never accesses real mailboxes or sends mail, and may trigger existing background analysis.
 */

/**
 * Function: Generate synthetic experiment data reproducibly from identical inputs.
 * Inputs: options may contain count (1-100), seed (unsigned 32-bit integer), baseTime (time-zone-aware timestamp),
 *       mailboxId (fixture UUID), and mailboxAddress (fixture email address).
 * Outputs: An object containing metadata, mailbox, customers, products, opportunities, orders, orderLines, and emails.
 * Logic: Each customer receives three incoming emails, one product, one opportunity, one draft order, and one line; cycle through five scenarios.
 * Constraints: Companies/addresses are fictional; facts conform to extract-v6 but are not model output and carry synthetic source labels.
 *       Draft orders do not count as historical deals; identical configurations produce identical data.
 */
function generateExperimentData(options = {}) {
  const {
    count = 10,
    seed = 20260919,
    baseTime = "2026-09-19T12:00:00+08:00",
    mailboxId = "00000000-0000-4000-8000-000000000001",
    mailboxAddress = "experiment@salesmate.example",
  } = options;
  if (!Number.isInteger(count) || count < 1 || count > 100) throw new Error("count must be an integer from 1 to 100");
  if (!Number.isInteger(seed) || seed < 0 || seed > 0xffffffff) throw new Error("seed must be an unsigned 32-bit integer");
  if (typeof baseTime !== "string" || !/(Z|[+-]\d{2}:\d{2})$/.test(baseTime) || !Number.isFinite(Date.parse(baseTime))) {
    throw new Error("baseTime must be a valid timestamp with a time zone");
  }
  if (typeof mailboxId !== "string" || !/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(mailboxId)) {
    throw new Error("mailboxId must be a UUID");
  }
  if (typeof mailboxAddress !== "string" || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(mailboxAddress)) {
    throw new Error("mailboxAddress must be an email address");
  }
  let state = seed;
  // Function: Generate a deterministic discrete uniform sequence. Inputs: Closure state. Outputs: A number in [0,1).
  // Logic: 32-bit linear congruential recurrence. Constraints: Sample construction only, never passwords or real probability models.
  function next() {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 4294967296;
  }
  // Function: Generate fixture identifiers. Inputs: Closure random state. Outputs: A UUID-shaped string.
  // Logic: Fixed version and variant bits. Constraints: Not a backend-assigned entity ID; no cross-configuration global uniqueness guarantee.
  function id() {
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, char => {
      const value = Math.floor(next() * 16);
      return (char === "x" ? value : (value & 3) | 8).toString(16);
    });
  }
  // Function: Build a fact. Inputs: value string and original evidence. Outputs: A protocol fact array.
  // Logic: Preserve verbatim evidence. Constraints: Only explicitly generated fields; callers retain unknown information as empty.
  function fact(value, evidence) {
    return [{ value, evidences: [evidence] }];
  }
  const fields = ["contact_name", "contact_title", "company_self_reported", "business_background",
    "employee_scale_hint", "product_need", "quantity", "budget", "delivery_time", "decision_process",
    "concerns", "quote_reference", "order_reference"];
  const industries = ["半导体检测", "精密量测", "光学检测", "工业检测"];
  const scenarios = ["信息完整", "预算未知", "交期紧急", "明确顾虑", "外币报价需求"];
  const data = {
    metadata: { source: "synthetic_sample", generator: "experiment-js-v1", seed, baseTime, count,
      note: "纯虚构实验夹具；未写入任何账户。跨数据集比较时请保留 seed、baseTime 和生成器版本。" },
    mailbox: { id: mailboxId, address: mailboxAddress },
    customers: [], products: [], opportunities: [], orders: [], orderLines: [], emails: [],
  };
  for (let index = 0; index < count; index++) {
    const companyId = id(), productId = id(), orderId = id();
    const number = index + 1, scenario = index % scenarios.length;
    const industry = industries[index % industries.length];
    const companyName = `【虚构实验】${industry}客户${number}`;
    const productName = `【虚构实验】检测设备${number}`;
    const domain = `fixture-${seed}-${number}.example`, sender = `buyer@${domain}`;
    const currency = scenario === 4 ? "USD" : "CNY";
    const price = 1000 + Math.floor(next() * 90) * 100;
    const quantity = 1 + Math.floor(next() * 10);
    const budget = scenario === 1 ? null : String(price * quantity);
    data.customers.push({ id: companyId, name: companyName, domains: [domain],
      industry_from_crm: industry, scenario: scenarios[scenario] });
    data.products.push({ id: productId, sku: `EXPERIMENT-${seed}-${number}`, name: productName,
      currency, unit_price: price.toFixed(2), description: "虚构实验商品" });
    data.opportunities.push({ id: id(), company: companyId, title: `${companyName}采购意向`, status: "new",
      amount: budget === null ? null : Number(budget).toFixed(2), currency, product_names: [productName] });
    data.orders.push({ id: orderId, company: companyId, number: `EXPERIMENT-${seed}-${number}`,
      currency, status: "draft", notes: "虚构草稿，不代表历史成交或确认预算。" });
    data.orderLines.push({ id: id(), order: orderId, product: productId, description: productName,
      quantity: String(quantity), unit_price: price.toFixed(2), discount: "0.00" });
    for (let message = 0; message < 3; message++) {
      const sentAt = new Date(Date.parse(baseTime) - (8 - message * 3) * 86400000 - index * 60000).toISOString();
      const messageId = `experiment-${seed}-${number}-${message + 1}`;
      const subject = `【虚构实验】${["首次询价", "补充需求", "跟进确认"][message]}：${productName}`;
      const delivery = scenario === 2 ? "收到报价后 7 天内需要交付" : "收到报价后 30 天内计划交付";
      const concern = scenario === 3 ? "担心设备与现有产线不兼容，需先进行样机测试" : "需要明确安装与售后服务范围";
      const lines = ["本邮件仅用于实验，所有人物和交易均为虚构。", `公司：${companyName}`,
        `联系人：测试采购员${number}`, "职位：采购专员", `行业：${industry}`,
        `需求：采购${productName}`, `数量：${quantity}台`,
        ...(budget === null ? ["预算：尚未确定"] : [`预算：${budget} ${currency}`]),
        `交期：${delivery}`, "决策流程：采购专员收集报价，技术负责人验证后由采购经理审批",
        `顾虑：${concern}`, `阶段：${["请提供产品介绍和初步报价", "请补充规格与交付安排", "请确认报价有效期，尚未确认下单"][message]}`];
      const facts = Object.fromEntries(fields.map(field => [field, []]));
      Object.assign(facts, {
        contact_name: fact(`测试采购员${number}`, `联系人：测试采购员${number}`),
        contact_title: fact("采购专员", "职位：采购专员"),
        company_self_reported: fact(companyName, `公司：${companyName}`),
        business_background: fact(industry, `行业：${industry}`),
        product_need: fact(`采购${productName}`, `需求：采购${productName}`),
        quantity: fact(`${quantity}台`, `数量：${quantity}台`),
        budget: budget === null ? [] : fact(`${budget} ${currency}`, `预算：${budget} ${currency}`),
        delivery_time: fact(delivery, `交期：${delivery}`),
        decision_process: fact("采购专员收集报价，技术负责人验证后由采购经理审批", lines.find(line => line.startsWith("决策流程："))),
        concerns: fact(concern, `顾虑：${concern}`), has_substantive_update: message < 2,
        message_summary: subject, intent_hint: "purchase_inquiry", intent_evidences: [`需求：采购${productName}`],
      });
      data.emails.push({ dedupe_key: `${mailboxAddress.toLowerCase()}:${messageId}`, mailbox_id: mailboxId,
        mailbox_address: mailboxAddress, gmail_message_id: messageId, thread_id: `experiment-${seed}-${number}`,
        from: sender, to: [mailboxAddress], cc: [], sent_at: sentAt, received_at: sentAt,
        subject, body_text: lines.join("\n"), direction: "inbound", source: "synthetic_sample", contact_email: sender,
        non_business_hint: false, non_business_reason: null, extract_status: "completed",
        extract_prompt_version: "extract-v6", extract_error: null, facts });
    }
  }
  return data;
}

/**
 * Function: Download generated JSON data.
 * Inputs: data is generator output; filename is the download name.
 * Outputs: A browser download, without business writes.
 * Logic: Use a local Blob URL and release resources after clicking a temporary link.
 * Constraints: Browser-only; Node.js callers can save with fs.writeFileSync.
 */
function downloadExperimentData(data, filename = "salesmate-experiment.json") {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * Function: Import generated data into an authorized account and return actual company IDs/write counts.
 * Inputs: data is unchanged generator output; options contains mailboxId, agentToken, and browser username or Node toolToken.
 *       Node also requires baseUrl; onProgress may receive completion progress without bodies or credentials.
 * Outputs: A real-ID mapping summary; failures throw and completed requests remain committed. Callers may explicitly rerun the original data.
 * Logic: Preflight tool permissions/mailbox ownership; deduplicate emails by natural keys and use fixture UUIDs as stable business idempotency keys. Create parent records before lines.
 * Constraints: Both credentials must belong to the same account, with mailbox ownership independently verified by both APIs. No automatic retry or rollback of completed HTTP requests.
 *       Reruns must retain identical data, target mailbox, and account. Credentials stay in memory, never files/logs.
 */
async function importExperimentData(data, options = {}) {
  const { mailboxId, agentToken, toolToken, username, onProgress = () => {} } = options;
  const baseUrl = options.baseUrl || (typeof location !== "undefined" ? location.origin : "");
  const url = new URL(baseUrl);
  if (url.username || url.password || url.search || url.hash || url.pathname !== "/" ||
      !(url.protocol === "https:" || (url.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)))) {
    throw new Error("baseUrl must be an HTTPS service root (HTTP is allowed for localhost)");
  }
  if (!toolToken && (typeof location === "undefined" || url.origin !== location.origin)) {
    throw new Error("Session mode must run within the target website");
  }
  if (typeof agentToken !== "string" || !agentToken || /\s/.test(agentToken)) throw new Error("Email import requires the account's Agent service credential");
  if (toolToken && (typeof toolToken !== "string" || /\s/.test(toolToken))) throw new Error("Invalid Tool credential format");
  if (data?.metadata?.generator !== "experiment-js-v1" || !mailboxId) throw new Error("Generator data and an actual mailboxId are required");
  for (const key of ["customers", "products", "opportunities", "orders", "orderLines", "emails"]) {
    if (!Array.isArray(data[key]) || !data[key].length) throw new Error(`Missing data array: ${key}`);
  }
  if (data.emails.some(email => email.source !== "synthetic_sample")) throw new Error("This script imports only explicitly labeled synthetic emails");
  let csrfToken;
  // Function: Request a fixed endpoint. Inputs: path, optional JSON body, optional auth header. Outputs: Parsed JSON.
  // Logic: Do not follow redirects; stop on timeouts or unsuccessful status. Constraints: No retries or authorization/email-body output.
  async function request(path, body, auth) {
    const response = await fetch(url.origin + path, {
      method: body === undefined ? "GET" : "POST", redirect: "error",
      credentials: auth ? "omit" : "same-origin", signal: AbortSignal.timeout(60000),
      headers: { "Content-Type": "application/json", ...(auth ? { Authorization: auth } : {}),
        ...(!auth && csrfToken ? { "X-CSRFToken": csrfToken } : {}) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (!response.ok) throw new Error(`${path} returned HTTP ${response.status}; import stopped. Retain the original data for review or an explicit rerun`);
    return response.json();
  }
  if (!toolToken) {
    const session = await request("/api/v1/session/");
    if (!username || !session.authenticated || session.username !== username) throw new Error("The logged-in account does not match the specified username");
    csrfToken = session.csrf_token;
  }
  const toolAuth = toolToken ? `Tool ${toolToken}` : undefined;
  // Function: Submit a tool call. Inputs: name, argumentsValue, optional idempotency UUID key. Outputs: Business data.
  // Logic: Accept completed receipts only. Constraints: Never automatically approve proposals or treat background acceptance as completion.
  async function call(name, argumentsValue, key) {
    const result = await request("/api/v1/agent-tools/call/", {
      name, arguments: argumentsValue, ...(key ? { idempotency_key: key } : {}),
    }, toolAuth);
    if (result.status !== "completed") throw new Error(`${name} did not complete synchronously; import stopped`);
    return result.data;
  }
  const mailboxes = await call("mailboxes.list", {});
  const mailbox = mailboxes.find(item => item.mailbox_id === mailboxId);
  if (!mailbox) throw new Error("The specified mailbox does not belong to the current Session/Tool account");
  // Verify all five business tools are authorized before writing, so missing ordinary-tool permissions are detected before email ingestion succeeds.
  for (const name of ["products.create", "opportunities.create", "orders.create", "order_lines.create"]) {
    const catalog = await request(`/api/v1/agent-tools/catalog/?category=${name.split(".")[0]}&page_size=100`, undefined, toolAuth);
    if (!catalog.tools.some(item => item.name === name && item.executionMode === "write")) throw new Error(`Tool is not authorized: ${name}`);
  }
  const companies = new Map(), ids = new Map();
  const emails = data.emails.map(email => ({ ...email, mailbox_id: mailbox.mailbox_id,
    mailbox_address: mailbox.address, dedupe_key: `${mailbox.address.toLowerCase()}:${email.gmail_message_id}`,
    to: [mailbox.address] }));
  for (let offset = 0; offset < emails.length; offset += 100) {
    const batch = emails.slice(offset, offset + 100);
    const rows = await request("/api/v1/agent/emails/", batch, `Agent ${agentToken}`);
    if (!Array.isArray(rows) || rows.length !== batch.length) throw new Error("Unexpected email receipt count; review before rerunning");
    for (const email of batch) {
      const row = rows.find(item => item.dedupe_key === email.dedupe_key);
      if (!row?.company_id) throw new Error("Email receipt is missing the actual company ID");
      const domain = email.contact_email.split("@")[1];
      const customer = data.customers.find(item => item.domains.includes(domain));
      if (!customer) throw new Error("No fixture customer matches the email");
      if (companies.has(customer.id) && companies.get(customer.id) !== row.company_id) throw new Error("Emails in one group were assigned to different companies; review manually");
      companies.set(customer.id, row.company_id);
    }
    onProgress({ stage: "emails", completed: offset + batch.length, total: emails.length });
  }
  // Function: Create one category of business records. Inputs: Fixture rows, tool name, and relation field names. Outputs: None; updates closure ids.
  // Logic: Discard read-only IDs/status and link records using real server IDs. Constraints: Fail explicitly on missing dependencies instead of guessing relationships.
  async function create(rows, tool, relations) {
    for (const row of rows) {
      const { id: fixtureId, status, ...fields } = row;
      for (const relation of relations) {
        const actualId = relation === "company" ? companies.get(fields[relation]) : ids.get(fields[relation]);
        if (!actualId) throw new Error(`${tool} is missing the actual ID for ${relation}`);
        fields[relation] = actualId;
      }
      const result = await call(tool, { data: fields }, fixtureId);
      if (!result.id) throw new Error(`${tool} is missing the persisted record ID`);
      ids.set(fixtureId, result.id);
      onProgress({ stage: tool, completed: ids.size });
    }
  }
  await create(data.products, "products.create", []);
  await create(data.opportunities, "opportunities.create", ["company"]);
  await create(data.orders, "orders.create", ["company"]);
  await create(data.orderLines, "order_lines.create", ["order", "product"]);
  return { status: "completed", mailboxId, companyIds: [...new Set(companies.values())],
    emails: emails.length, products: data.products.length, opportunities: data.opportunities.length,
    draftOrders: data.orders.length, orderLines: data.orderLines.length };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { generateExperimentData, downloadExperimentData, importExperimentData };
}
