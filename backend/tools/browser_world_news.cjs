/**
 * 职责：覆盖恢复的 Global Insights 导航与底部 Profile；验证精简导航下世界消息的真实页面交互，并提供不连接业务系统的本地预览。
 * 国际化前提：浏览器固定 zh-CN，使既有中文交互断言不依赖运行机器语言。
 * 实现：同源静态 HTTP 服务承载地图和详情；Playwright 使用本地浏览器，禁止外部网络。
 * 关联：world-news.js/world-map.js/world-feed.js；页面模块按实际 script 地址导入，避免资源版本变化时重复初始化；不依赖 Django 数据库，不调用真实 Agent。
 * 目录：servePage、createPreviewServer、main；main 中的浏览器回调仅操作隔离页面和演示夹具。
 * 变量索引：FRONTEND 为资源根；OUTPUT 为忽略的截图目录；MIME 为允许资源的内容类型；--serve 仅启动预览，默认执行验证后关闭。
 */
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const FRONTEND = path.resolve(__dirname, '../frontend');
const OUTPUT = path.resolve(__dirname, '../artifacts/world-news');
const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.geojson': 'application/geo+json' };

/** 功能：提供受限的页面与静态资源。输入：req、res HTTP 对象。输出：HTTP 响应。
 * 逻辑：地图与详情使用同一模板；静态路径必须解析到 assets 内，拒绝目录遍历。
 * 约束：不暴露 .env、源码目录或业务 API；不模拟用户登录。 */
function servePage(req, res) {
  const pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
  let filename = null;
  if (pathname === '/' || pathname === '/world/' || /^\/world\/news\/[a-z0-9-]+\/$/.test(pathname)) filename = path.join(FRONTEND, 'world.html');
  else if (pathname.startsWith('/static/')) {
    const candidate = path.resolve(FRONTEND, 'assets', pathname.slice(8));
    const relative = path.relative(path.join(FRONTEND, 'assets'), candidate);
    if (relative && !relative.startsWith('..') && !path.isAbsolute(relative)) filename = candidate;
  }
  if (!filename || !MIME[path.extname(filename)] || !fs.existsSync(filename) || !fs.statSync(filename).isFile()) { res.writeHead(404); res.end('Not found'); return; }
  res.setHeader('Content-Type', MIME[path.extname(filename)] + '; charset=utf-8');
  res.setHeader('Cache-Control', 'no-store');
  res.end(fs.readFileSync(filename));
}
/** 功能：建立本机隔离预览。输入：无外部参数。输出：已监听服务器。
 * 逻辑：系统选择空闲端口。约束：只绑定回环地址，不启动后台业务。 */
async function createPreviewServer() {
  const server = http.createServer(servePage);
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  return server;
}
/** 功能：执行页面与消息边界回归。输入：Playwright/浏览器环境变量及可选 --serve。
 * 输出：检查结果、截图或持续预览地址；失败非零退出。
 * 逻辑：复用页面实际加载的入口模块，验证聚合、两次点击、深链接、筛选、幂等、非法数据、窄屏和资源错误。
 * 约束：数据全部虚构；静态预览不代替真实 Django 部署、鉴权或推送服务验证。 */
async function main() {
  const server = await createPreviewServer(), base = `http://127.0.0.1:${server.address().port}`;
  if (process.argv.includes('--serve')) { console.log(`World news preview: ${base}/world/`); return; }
  let browser;
  try {
    const { chromium } = require(process.env.SALESMATE_PLAYWRIGHT_MODULE);
    browser = await chromium.launch({ executablePath: process.env.SALESMATE_BROWSER_PATH, headless: true });
    fs.mkdirSync(OUTPUT, { recursive: true });
    const page = await browser.newPage({ locale: 'zh-CN', viewport: { width: 1512, height: 982 }, deviceScaleFactor: 1 });
    const errors = [], externalRequests = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => { if (new URL(route.request().url()).origin !== base) { externalRequests.push(route.request().url()); return route.abort(); } return route.continue(); });
    await page.goto(base + '/world/');
    await page.locator('#map-loading').waitFor({ state: 'hidden' });
    assert.equal(await page.locator('#world-error').isVisible(), false);
    assert.equal(await page.locator('#news-total').textContent(), '8');
    assert.equal(await page.locator('#location-total').textContent(), '7');
    assert.ok(await page.locator('#world-map path').count() > 100);
    assert.equal(await page.locator('#workspace-nav a[aria-current=page]').count(), 1);
    assert.equal(await page.locator('#workspace-nav a[href="/world/"]').count(), 1);
    assert.equal(await page.locator('#workspace-profile').count(), 1);
    assert.deepEqual(await page.locator('#workspace-profile a').allTextContents(), ['Company Setting', 'Emails Setting']);
    await page.screenshot({ path: path.join(OUTPUT, 'world-map-desktop.png'), fullPage: true });

    await page.locator('.news-pin[data-news-ids~="singapore-packaging"]').click();
    await page.locator('#news-panel h2').filter({ hasText: '附近动态' }).waitFor();
    await page.locator('[data-select="singapore-packaging"]').click();
    await page.locator('#summary-title').waitFor();
    await page.screenshot({ path: path.join(OUTPUT, 'world-summary-desktop.png'), fullPage: true });
    await page.locator('.news-pin[data-news-ids~="singapore-packaging"]').click();
    assert.match(page.url(), /\/world\/news\/singapore-packaging\/$/);
    await page.locator('.news-article h1').waitFor();
    await page.reload();
    await page.locator('.news-article h1').waitFor();
    await page.screenshot({ path: path.join(OUTPUT, 'world-detail-desktop.png'), fullPage: true });
    await page.locator('.detail-back').click();
    await page.locator('#summary-title').waitFor();
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#summary-title').count(), 0);
    await page.locator('[data-industry="semiconductor"]').click();
    assert.equal(await page.locator('#news-total').textContent(), '2');
    await page.locator('[data-select="austin-semiconductor"]').click();
    await page.locator('.detail-button').click();
    await page.goBack();
    await page.locator('#summary-title').waitFor();
    assert.equal(await page.locator('[data-industry="semiconductor"]').getAttribute('aria-pressed'), 'true');
    await page.keyboard.press('Escape');
    await page.locator('#demo-push').click();
    assert.equal(await page.locator('#news-total').textContent(), '2', 'Push must preserve filters');
    await page.locator('[data-industry="all"]').click();
    assert.equal(await page.locator('#news-total').textContent(), '9');
    assert.ok(await page.locator('.news-pin[data-news-ids~="rotterdam-push-demo"]').count());
    const checks = await page.evaluate(async () => {
      const { NewsFeed } = await import('/static/world-feed.js');
      const { DEMO_NEWS, DEMO_PUSH } = await import('/static/world-demo.js');
      const entry = document.querySelector('script[type="module"][src*="/world-news.js"]').src;
      const { receiveWorldNews } = await import(entry);
      const feed = new NewsFeed('demo', DEMO_NEWS);
      const duplicate = receiveWorldNews({ type: 'news.upsert', item: DEMO_PUSH });
      const changed = { ...DEMO_NEWS[0], version: 2, title: '新版演示标题' };
      feed.receive({ type: 'news.upsert', item: changed });
      const stale = feed.receive({ type: 'news.upsert', item: DEMO_NEWS[0] });
      let rejected = 0;
      for (const item of [{ ...changed, title: '同版本冲突' }, { ...changed, version: 3, location: { ...changed.location, latitude: 200 } }, { ...changed, version: 3, sources: [{ label: '无效链接', url: 'javascript:alert(1)' }] }, { ...changed, version: 3, demo: false }]) {
        try { feed.receive({ type: 'news.upsert', item }); } catch { rejected += 1; }
      }
      const malicious = { ...DEMO_PUSH, id: 'safe-text-probe', title: '<img src=x onerror=alert(1)>', summary: '<script>alert(1)</script>' };
      receiveWorldNews({ type: 'news.upsert', item: malicious });
      return { duplicate, stale, rejected, size: feed.list().length, title: feed.get(changed.id).title };
    });
    assert.deepEqual(checks, { duplicate: false, stale: false, rejected: 4, size: 8, title: '新版演示标题' });
    assert.equal(await page.locator('#news-panel img, #news-panel script').count(), 0);
    assert.ok((await page.locator('[data-select="safe-text-probe"]').textContent()).includes('<img'));

    await page.goto(base + '/world/news/unknown-message/');
    await page.getByRole('heading', { name: '消息未找到' }).waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(base + '/world/');
    await page.locator('#map-loading').waitFor({ state: 'hidden' });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({ path: path.join(OUTPUT, 'world-map-mobile.png'), fullPage: true });
    await page.locator('[data-select="singapore-packaging"]').click();
    await page.locator('#summary-title').waitFor();
    assert.equal(await page.locator('#news-panel').evaluate(node => getComputedStyle(node).position), 'fixed');
    // 固定底部面板按真实视口截图，避免全页拼接把视口外的固定元素呈现在页面中。
    await page.screenshot({ path: path.join(OUTPUT, 'world-summary-mobile.png') });
    await page.getByRole('button', { name: '关闭消息摘要' }).click();
    assert.equal(await page.locator('#summary-title').count(), 0);
    await page.route('**/world-countries.geojson', route => route.fulfill({ status: 503, body: 'unavailable' }));
    await page.reload();
    await page.locator('#world-error').waitFor();
    assert.match(await page.locator('#world-error').textContent(), /503/);
    assert.deepEqual(errors, []);
    assert.deepEqual(externalRequests, []);
    console.log('World news checks passed: local map, clustering, summary, second click, details, reload/back, filters, push, idempotency, invalid data, text safety, mobile and map failure.');
  } finally { if (browser) await browser.close(); await new Promise(resolve => server.close(resolve)); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
