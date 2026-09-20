/** 职责：验证全球洞察的新活动界面和既有资讯校验，并提供本地静态预览。
 * 实现：真实浏览器加载仓库资源、固定演示日期，禁止外部请求；检查筛选联动、地图、日历导出、邀约草稿、资讯详情和移动布局。
 * 关联：0919 界面及共享语言资源统一缓存版本；world-news.js/world-map.js/world-feed.js；只使用静态数据，不访问生产 API。
 * 目录：servePage、createPreviewServer、main。
 * 变量索引：FRONTEND 为资源根目录；OUTPUT 为忽略的截图目录。
 */
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const assert = require("node:assert/strict");
const FRONTEND = path.resolve(__dirname, "../frontend"),
  OUTPUT = path.resolve(__dirname, "../artifacts/browser");
/** 功能：提供仓库静态页面。输入：req/res。输出：HTTP 响应。逻辑：限制路径在前端目录中。约束：无业务 API、无任意文件读取。 */
function servePage(req, res) {
  const url = new URL(req.url, "http://localhost");
  const file = url.pathname.startsWith("/world/")
    ? path.join(FRONTEND, "world.html")
    : url.pathname.startsWith("/static/")
      ? path.resolve(FRONTEND, "assets", url.pathname.slice(8))
      : null;
  if (!file || !file.startsWith(FRONTEND + path.sep) || !fs.existsSync(file)) {
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
        : file.endsWith(".geojson")
          ? "application/json"
          : "text/html",
  );
  res.end(fs.readFileSync(file));
}
/** 功能：创建测试/预览服务。输入：无。输出：HTTP Server。逻辑：复用静态处理器。约束：监听由调用方决定。 */
function createPreviewServer() {
  return http.createServer(servePage);
}
/** 功能：执行全局活动界面验收。输入：Playwright 与浏览器环境变量。输出：检查日志及截图。
 * 逻辑：检查八活动、国家/类型/时间交集、空态、详情、ICS、邮件草稿、四资讯、注入安全及手机布局。
 * 约束：固定测试时钟只用于测试，页面实际使用当天；无真实活动源或外部消息发送。 */
async function main() {
  if (process.argv.includes("--serve")) {
    const server = createPreviewServer();
    server.listen(Number(process.env.PORT || 8766), "127.0.0.1", () =>
      console.log(
        "Preview: http://127.0.0.1:" + server.address().port + "/world/",
      ),
    );
    return;
  }
  const { chromium } = require(process.env.SALESMATE_PLAYWRIGHT_MODULE);
  const server = createPreviewServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const browser = await chromium.launch({
    executablePath: process.env.SALESMATE_BROWSER_PATH,
    headless: true,
  });
  try {
    const page = await browser.newPage({
      locale: "zh-CN",
      viewport: { width: 1600, height: 1000 },
    });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.clock.setFixedTime(new Date("2026-09-20T12:00:00+08:00"));
    await page.route("**/*", (route) =>
      new URL(route.request().url()).hostname === "127.0.0.1"
        ? route.continue()
        : route.abort(),
    );
    const base = `http://127.0.0.1:${server.address().port}`;
    fs.mkdirSync(OUTPUT, { recursive: true });
    await page.goto(base + "/world/");
    await page.locator(".event-pin").first().waitFor();
    assert.equal(await page.locator(".event-card").count(), 8);
    assert.equal(await page.locator(".industry-news-card").count(), 4);
    assert.equal(await page.locator(".event-pin").count(), 7);
    assert.equal(await page.locator(".insights-demo").isVisible(), true);
    await page.screenshot({
      path: path.join(OUTPUT, "global-insights-desktop.png"),
      fullPage: true,
    });
    await page.locator("[data-country=SG]").click();
    assert.equal(await page.locator(".event-card").count(), 2);
    assert.equal(await page.locator(".event-pin").count(), 1);
    await page.locator("#event-type").selectOption("exhibition");
    assert.equal(await page.locator(".event-card").count(), 1);
    assert.match(
      await page.locator("#event-detail").textContent(),
      /为什么值得去/,
    );
    await page.locator("[data-view=europe]").click();
    assert.equal(
      await page.locator("[data-view=europe]").getAttribute("aria-pressed"),
      "true",
    );
    assert.equal(new URL(page.url()).searchParams.get("view"), "europe");
    await page.locator("[data-view=apac]").click();
    const downloadPromise = page.waitForEvent("download");
    await page.locator("#add-itinerary").click();
    const download = await downloadPromise;
    const contents = fs.readFileSync(await download.path(), "utf8");
    assert.match(contents, /DTSTART;VALUE=DATE:20260928/);
    assert.match(contents, /DTEND;VALUE=DATE:20261001/);
    assert.match(contents, /\[Demo\]/);
    assert(contents.split("\r\n").every(line => Buffer.byteLength(line, "utf8") <= 75));
    await page.locator("#create-invite").click();
    assert.equal(await page.locator("#invite-dialog").isVisible(), true);
    assert.match(await page.locator("#invite-body").inputValue(), /演示活动/);
    await page.locator("#invite-subject").fill("可编辑草稿");
    await page.locator("#invite-close").click();
    await page.locator("[data-country=DE]").click();
    await page.locator("#event-time").selectOption("quarter");
    assert.equal(await page.locator(".event-card").count(), 0);
    assert.equal(await page.locator("#add-itinerary").count(), 0);
    await page.locator("#reset-filters").click();
    assert.equal(await page.locator(".event-card").count(), 8);
    await page.locator("#event-time").selectOption("30");
    assert.equal(await page.locator(".event-card").count(), 7);
    await page.locator("#event-time").selectOption("all");
    const regression = await page.evaluate(async () => {
      const entry = document.querySelector('script[src*="world-news.js"]').src;
      const { receiveWorldNews } = await import(entry);
      const { DEMO_PUSH } = await import("/static/world-demo.js?v=20260921-product");
      const first = receiveWorldNews({ type: "news.upsert", item: DEMO_PUSH }),
        duplicate = receiveWorldNews({ type: "news.upsert", item: DEMO_PUSH });
      let rejected = false;
      try {
        receiveWorldNews({
          type: "news.upsert",
          item: {
            ...DEMO_PUSH,
            version: 2,
            sources: [{ url: "javascript:alert(1)", label: "unsafe" }],
          },
        });
      } catch {
        rejected = true;
      }
      return { first, duplicate, rejected };
    });
    assert.deepEqual(regression, {
      first: true,
      duplicate: false,
      rejected: true,
    });
    await page
      .locator(".industry-news-card")
      .filter({ hasText: "量测设备" })
      .click();
    await page.locator("#news-detail h1").waitFor();
    assert.match(await page.locator("#news-detail").textContent(), /虚构/);
    await page.reload();
    assert.equal(await page.locator("#world-explorer").isVisible(), false);
    await page.locator("#news-detail>a").click();
    await page.locator(".event-pin").first().waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({
      path: path.join(OUTPUT, "global-insights-mobile.png"),
      fullPage: true,
    });
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    );
    await page.locator("[data-country=JP]").click();
    assert.equal(await page.locator(".event-card").count(), 2);
    await page
      .context()
      .addCookies([{ name: "django_language", value: "en", url: base }]);
    await page.reload();
    assert.match(
      await page.locator(".insights-heading").textContent(),
      /Where should you meet/,
    );
    assert.equal(
      await page.locator("#event-type option[value=sales]").textContent(),
      "Sales event",
    );
    assert.deepEqual(errors, []);
    console.log(
      "World insights checks passed: filters, map, empty state, ICS, invitation, news, safety, desktop/mobile and English.",
    );
  } finally {
    await browser.close();
    await new Promise((resolve) => server.close(resolve));
  }
}
if (require.main === module)
  main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
module.exports = { createPreviewServer };
