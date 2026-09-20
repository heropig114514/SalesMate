/** 职责：验证四步引导在浏览器中的真实表单和交互契约。
 * 实现：本地静态服务、模拟 API 与真实 DOM，验证编辑保留后台分配的产品 ID 和交易关联；后端权限另由集成测试验证。
 * 关联：onboarding.js、company-settings.js 和对应模板/样式；截图写入忽略目录。
 * 目录：main。
 * 变量索引：ROOT 为前端目录；OUTPUT 为截图目录。
 */
const fs = require("node:fs"),
  path = require("node:path"),
  http = require("node:http"),
  assert = require("node:assert/strict");
const { chromium } = require(process.env.SALESMATE_PLAYWRIGHT_MODULE);
const ROOT = path.resolve(__dirname, "../frontend"),
  OUTPUT = path.resolve(__dirname, "../artifacts/browser");
/** 功能：执行引导 UI 验收。输入：浏览器环境变量。输出：断言和截图。
 * 逻辑：个人保存、公司规模、CSV 校验、手动产品、私有附件、方案、版本冲突、产品关联保留及完成后进入收件箱。
 * 约束：模拟接口不代表真实外部服务；不修改工作区数据库。 */
async function main() {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    const file =
      url.pathname === "/settings/company/"
        ? path.join(ROOT, "company-settings.html")
        : url.pathname === "/"
          ? path.join(ROOT, "index.html")
          : url.pathname.startsWith("/static/")
            ? path.resolve(ROOT, "assets", url.pathname.slice(8))
            : null;
    if (!file || !file.startsWith(ROOT + path.sep) || !fs.existsSync(file)) {
      res.writeHead(404);
      res.end();
      return;
    }
    res.setHeader(
      "Content-Type",
      file.endsWith(".js")
        ? "text/javascript"
        : file.endsWith(".css")
          ? "text/css"
          : "text/html",
    );
    res.end(fs.readFileSync(file));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const browser = await chromium.launch({
    executablePath: process.env.SALESMATE_BROWSER_PATH,
    headless: true,
  });
  try {
    const page = await browser.newPage({
      locale: "zh-CN",
      viewport: { width: 1440, height: 1000 },
    });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    let setup = {
        personal: {},
        products: [],
        solutions: [],
        completed: false,
        revision: 0,
        documents: [],
      },
      profile = {
        company_name: "",
        industry: "",
        size_band: "",
        website: "",
        email: "",
        phone: "",
        address: "",
        description: "",
        revision: 0,
      },
      conflict = false;
    const writes = [];
    await page.route("**/*", async (route) => {
      const req = route.request(),
        url = new URL(req.url());
      if (url.hostname !== "127.0.0.1") return route.abort();
      if (!url.pathname.startsWith("/api/")) return route.continue();
      const endpoint = url.pathname.slice("/api/v1/".length);
      if (req.method() !== "GET") writes.push(endpoint);
      let data;
      if (endpoint === "session/")
        data = {
          authenticated: true,
          username: "ui-sales",
          onboarding_required: !setup.completed,
          debug_auto_login: true,
        };
      else if (endpoint === "accounts/company-profile/") {
        if (req.method() === "PATCH") {
          assert.equal(req.headers()["if-match"], String(profile.revision));
          profile = {
            ...profile,
            ...req.postDataJSON(),
            revision: profile.revision + 1,
          };
        }
        data = profile;
      } else if (endpoint === "accounts/onboarding/") {
        if (req.method() === "PATCH") {
          if (conflict)
            return route.fulfill({
              status: 409,
              json: {
                error: { detail: "Conflict" },
                request_id: "hidden-diagnostic-id",
              },
            });
          assert.equal(req.headers()["if-match"], String(setup.revision));
          setup = {
            ...setup,
            ...req.postDataJSON(),
            revision: setup.revision + 1,
          };
        }
        data = setup;
      } else if (endpoint === "accounts/onboarding/documents/") {
        assert.equal(req.method(), "POST");
        data = {
          id: "d30b3f29-0b66-4ef7-b92e-b6e27d4ea5d1",
          name: "proposal.txt",
          content_type: "text/plain",
        };
        setup.documents.push(data);
      } else if (endpoint === "demo/runtime/")
        data = {
          provider: "agent",
          timezone: "Asia/Shanghai",
          qq_enabled: false,
        };
      else if (endpoint === "mailboxes/") data = [];
      else if (endpoint === "email-reviews/")
        data = { pending_count: 0, count: 0, results: [] };
      else if (endpoint === "sales/overview/")
        data = {
          customers: 0,
          open_tickets: 0,
          open_follow_ups: 0,
          unread_notifications: 0,
          confirmed_order_net: {},
          open_opportunity_amount: {},
        };
      else if (endpoint.startsWith("sales/records/"))
        data = { results: [], count: 0 };
      else if (endpoint === "companies/")
        data = {
          results: [],
          count: 0,
          stats: { companies: 0, unregistered: 0, new_emails_today: 0 },
        };
      else throw new Error("Unexpected API " + endpoint);
      return route.fulfill({ json: data });
    });
    const base = `http://127.0.0.1:${server.address().port}`;
    fs.mkdirSync(OUTPUT, { recursive: true });
    await page.goto(base + "/settings/company/?onboarding=1");
    await page.locator("#personal-form").waitFor();
    assert.match(await page.locator("#setup-progress").textContent(), /1 \/ 4/);
    await page.locator("#personal-form [name=name]").fill("Alex");
    await page.locator("#personal-form [name=title]").fill("Sales Engineer");
    await page.locator("#personal-form [name=email]").fill("alex@example.com");
    await page.locator('#personal-form [name=regions][value="亚太"]').check();
    await page
      .locator('#personal-form [name=industries][value="光学检测"]')
      .check();
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: path.join(OUTPUT, "onboarding-personal-desktop.png"),
      fullPage: true,
    });
    await page.locator("#personal-form button[type=submit]").click();
    await page.locator("#company-step").waitFor();
    assert.equal(setup.personal.name, "Alex");
    await page.locator("#company-company_name").fill("Example Instruments");
    await page.locator("#company-industry").selectOption("光学检测");
    await page.locator("#company-size_band").selectOption("50_100");
    await page.locator("#company-save").click();
    await page.locator('[data-setup-step="2"]').waitFor();
    assert.equal(profile.size_band, "50_100");
    await page
      .locator("#product-import")
      .setInputFiles({
        name: "products.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          'name,category,specifications,price_min,price_max,currency,scenarios\n"Scanner, mini",Optics,"10 mm|USB",10,20,SGD,inspection\n',
        ),
      });
    await page.locator("#product-count").filter({ hasText: "1" }).waitFor();
    assert.match(
      await page.locator("#product-rows").textContent(),
      /Scanner, mini/,
    );
    await page
      .locator("#product-import")
      .setInputFiles({
        name: "bad.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          "name,category,specifications,price_min,price_max,currency,scenarios\nBad,,,-1,20,SGD,\n",
        ),
      });
    await page.locator("#setup-status.is-error").waitFor();
    assert.equal(await page.locator("#product-count").textContent(), "1");
    await page.locator("#product-add").click();
    await page.locator("#product-form [name=name]").fill("Manual product");
    await page
      .locator("#product-form [name=specifications]")
      .fill("Precision: 1 mm\nUSB");
    await page.locator("#product-form button[type=submit]").click();
    assert.equal(await page.locator("#product-count").textContent(), "2");
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: path.join(OUTPUT, "onboarding-products-desktop.png"),
      fullPage: true,
    });
    conflict = true;
    await page.locator("#products-save").click();
    await page.locator("#setup-status.is-error").waitFor();
    assert.equal(await page.locator("#product-count").textContent(), "2");
    assert(
      !(await page.locator("#setup-status").textContent()).includes(
        "hidden-diagnostic-id",
      ),
    );
    conflict = false;
    await page.locator("#products-save").click();
    await page.locator('[data-setup-step="3"]').waitFor();
    assert.equal(setup.products.length, 2);
    await page
      .locator("#solution-form [name=name]")
      .fill("Inspection proposal");
    await page
      .locator("#solution-form [name=file]")
      .setInputFiles({
        name: "proposal.txt",
        mimeType: "text/plain",
        buffer: Buffer.from("A private proposal"),
      });
    await page.locator("#solution-form button[type=submit]").click();
    await page.locator(".solution-card").waitFor();
    assert.match(
      await page.locator(".solution-card a").getAttribute("href"),
      /onboarding\/documents\//,
    );
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: path.join(OUTPUT, "onboarding-solutions-desktop.png"),
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('[data-step="0"]').click();
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: path.join(OUTPUT, "onboarding-personal-mobile.png"),
      fullPage: true,
    });
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    );
    await page.locator('[data-step="3"]').click();
    await page.locator("#solutions-save").click();
    await page.waitForURL(base + "/#inbox");
    assert.equal(setup.completed, true);
    assert.equal(setup.solutions.length, 1);
    await page.locator("#filters").waitFor();
    assert.equal(
      await page.locator("#filters [name=q]").getAttribute("autocomplete"),
      "off",
    );
    setup.products[0].id = "bbbff6b4-f87e-4bac-80a7-ea87e7ef0982";
    setup.products[0].linked_product_id = "4b0e5dfe-7b7f-44ac-8942-94437915719a";
    await page.goto(base + "/settings/company/");
    await page
      .locator("#setup-title")
      .filter({ hasText: "公司资料" })
      .waitFor({ state: "attached" });
    await page.locator('[data-step="2"]').click();
    assert.equal(await page.locator("#product-count").textContent(), "2");
    await page.locator('[data-edit="0"]').click();
    await page.locator("#product-form [name=name]").fill("Renamed scanner");
    await page.locator("#product-form button[type=submit]").click();
    const savedCatalog = page.waitForResponse((response) =>
      response.url().endsWith("/accounts/onboarding/") && response.request().method() === "PATCH",
    );
    await page.locator("#products-save").click();
    assert.equal((await savedCatalog).status(), 200);
    assert.equal(setup.products[0].name, "Renamed scanner");
    assert.equal(setup.products[0].id, "bbbff6b4-f87e-4bac-80a7-ea87e7ef0982");
    assert.equal(setup.products[0].linked_product_id, "4b0e5dfe-7b7f-44ac-8942-94437915719a");
    await page
      .context()
      .addCookies([{ name: "django_language", value: "en", url: base }]);
    await page.reload();
    await page.locator('[data-step="0"]').click();
    assert.match(
      await page.locator("#personal-form").textContent(),
      /Make every conversation yours/,
    );
    assert.deepEqual(errors, []);
    console.log(
      "Onboarding browser checks passed: four steps, CSV, editing, conflict, private file links, persistence, completion, mobile and English.",
    );
  } finally {
    await browser.close();
    await new Promise((resolve) => server.close(resolve));
  }
}
main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
