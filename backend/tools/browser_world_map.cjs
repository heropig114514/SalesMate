/** 职责：验证金额气泡、共享活动日期展示和全天日历。
 * 实现：隔离静态服务器加载实际页面，以模拟接口检查地图几何、币种、日期精度和 ICS；不访问真实业务接口。
 * 关联：world-map.js、world-news.js/css、world.html；Playwright/Chrome 路径由环境显式提供。
 * 目录：geometry、checkGeometry、checkCurrencies、checkDates、main。
 * 变量索引：ASSETS 为静态资源根目录；OUTPUT 为忽略的截图目录。
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require(process.env.SALESMATE_PLAYWRIGHT_MODULE);
const ASSETS = path.resolve(__dirname, '../frontend/assets');
const OUTPUT = path.resolve(__dirname, '../artifacts/browser');

/** 功能：读取投影点和可见圆心。输入：page 浏览器页。输出：各标记的坐标误差与直径。
 * 逻辑：用 Leaflet 的经纬度投影独立比对 DOM 圆心，涵盖标签布局造成的偏移。约束：仅用于隔离夹具，不暴露业务页面内部状态。 */
async function geometry(page) {
  return page.evaluate(() => [...document.querySelectorAll('.event-pin')].map(button => {
    const item = window.fixture.find(row => row.id === button.dataset.eventId);
    const point = window.fixtureMap.map.latLngToContainerPoint([item.lat, item.lng]);
    const container = document.querySelector('#world-map').getBoundingClientRect();
    const bubble = button.querySelector('.event-bubble').getBoundingClientRect();
    return { id: item.id, diameter: bubble.width, dx: bubble.x + bubble.width / 2 - container.x - point.x, dy: bubble.y + bubble.height / 2 - container.y - point.y };
  }));
}

/** 功能：断言圆心与金额比例。输入：page 页面。输出：无，失败抛错。
 * 逻辑：坐标误差最多一像素，400 对 100 的面积比为四；同坐标重复记录不能重复累计金额。约束：允许浏览器子像素舍入，不更改业务阈值。 */
async function checkGeometry(page) {
  const rows = await geometry(page);
  for (const row of rows) assert.ok(Math.abs(row.dx) <= 1 && Math.abs(row.dy) <= 1, `${row.id} center offset: ${JSON.stringify(row)}`);
  const small = rows.find(row => row.id === 'small'), large = rows.find(row => row.id === 'large');
  assert.ok(Math.abs(large.diameter ** 2 / small.diameter ** 2 - 4) < 0.01);
  assert.equal(large.diameter, 62);
  assert.equal(rows.length, 4);
}

/** 功能：验证完整页面的币种来源和金额语义。输入：browser、base 为隔离浏览器和静态服务地址。输出：无，失败抛错。
 * 逻辑：模拟只有 SGD、混合币种、零与未知金额及空活动；检查无关币种不进入选择框，URL 与切换保留已知金额，标签不伪造汇率。
 * 约束：所有接口为显式只读夹具；未知接口或写入使测试失败，不触及线上记录。 */
async function checkCurrencies(browser, base) {
  const page = await browser.newPage({ locale: 'en-US', viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  let records = [
    { id: 'sgd', city: 'Singapore', country: 'SG', latitude: 1.352, longitude: 103.819, map_amounts: { SGD: '100.00' } },
    { id: 'usd', city: 'San Francisco', country: 'US', latitude: 37.775, longitude: -122.419, map_amounts: { SGD: '200.00' } },
    { id: 'zero', city: 'Tokyo', country: 'JP', latitude: 35.676, longitude: 139.65, map_amounts: { SGD: '0.00' } },
    { id: 'unknown', city: 'Munich', country: 'DE', latitude: 48.135, longitude: 11.582, map_amounts: {} },
  ];
  await page.route('**/*', route => {
    const req = route.request(), url = new URL(req.url());
    if (url.origin !== base || req.method() !== 'GET') { errors.push('Unexpected request: ' + url.pathname); return route.abort(); }
    if (!url.pathname.startsWith('/api/')) return route.continue();
    if (url.pathname === '/api/v1/sales/world/') return route.fulfill({ json: {
      count: records.length, countries: [], currencies: ['CNY', 'SGD', 'USD'], unmapped_customer_count: 0,
      results: records.map(row => ({ ...row, title: row.city, amounts: row.map_amounts, event_type: 'exhibition', starts_at: '2027-01-01T12:00:00Z', ends_at: '2027-01-02T12:00:00Z', data_source: 'synthetic', customers: [], onsite: [], suggested_actions: [] })),
    } });
    if (url.pathname === '/api/v1/sales/records/world-news/') return route.fulfill({ json: { count: 0, results: [] } });
    if (url.pathname === '/api/v1/sales/seller-context/') return route.fulfill({ json: { sales_setup: { personal: {} } } });
    errors.push('Unexpected API: ' + url.pathname);
    return route.abort();
  });
  await page.goto(base + '/world/?currency=USD');
  await page.locator('.event-pin').first().waitFor();
  assert.deepEqual(await page.locator('#map-currency option').allTextContents(), ['SGD']);
  assert.equal(await page.inputValue('#map-currency'), 'SGD');
  assert.equal(await page.locator('[data-event-id="sgd"] .event-pin-amount').textContent(), 'SGD 100');
  assert.equal(await page.locator('[data-event-id="zero"] .event-pin-amount').textContent(), 'SGD 0');
  assert.equal(await page.locator('[data-event-id="unknown"] .event-pin-amount').textContent(), 'Amount unknown');
  records[1].map_amounts = { USD: '200.00' };
  await page.reload();
  await page.locator('.event-pin').first().waitFor();
  assert.deepEqual(await page.locator('#map-currency option').allTextContents(), ['SGD', 'USD']);
  assert.equal(await page.locator('[data-event-id="usd"] .event-pin-amount').textContent(), 'USD 200');
  assert.equal(await page.locator('[data-event-id="usd"] .event-pin-currency-note').textContent(), 'No SGD amount');
  assert.equal(await page.locator('[data-event-id="usd"]').evaluate(node => node.style.getPropertyValue('--bubble-size')), '0px');
  await page.selectOption('#map-currency', 'USD');
  assert.equal(await page.locator('[data-event-id="sgd"] .event-pin-amount').textContent(), 'SGD 100');
  assert.equal(await page.locator('[data-event-id="sgd"] .event-pin-currency-note').textContent(), 'No USD amount');
  assert.equal(await page.locator('[data-event-id="usd"]').evaluate(node => node.style.getPropertyValue('--bubble-size')), '62px');
  await page.reload();
  await page.locator('.event-pin').first().waitFor();
  assert.equal(await page.inputValue('#map-currency'), 'USD');
  assert.equal(await page.locator('[data-event-id="sgd"] .event-pin-amount').textContent(), 'SGD 100');
  records = [];
  await page.reload();
  await page.waitForFunction(() => document.querySelector('#world-data-status').textContent.startsWith('Database records'));
  assert.equal(await page.locator('#map-currency option').count(), 0);
  assert.equal(await page.locator('.event-pin').count(), 0);
  assert.equal(await page.locator('#world-error').isVisible(), false);
  assert.deepEqual(errors, []);
  await page.close();
}

/** 功能：验证日期型活动不显示占位钟点或错误末日。输入：browser 浏览器、base 静态服务器地址。输出：无，断言失败抛错。
 * 逻辑：真实页面加载 Agent 兼容响应，在两个极端时区检查末日、导出全天 ICS、邀请和手机布局并留截图；普通时间型仍导出 UTC 时刻。
 * 约束：API 为明确夹具，所有非本地请求和写入均拒绝，不证明真实采集已运行。 */
async function checkDates(browser, base) {
  for (const timezoneId of ['Pacific/Kiritimati', 'America/Los_Angeles']) {
    const context = await browser.newContext({ locale: 'en-US', timezoneId, acceptDownloads: true, viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage(), errors = [];
    const fixture = { id: 'date-expo', title: 'Shared date-only expo', city: 'Singapore', country: 'SG', latitude: 1.3, longitude: 103.8, event_type: 'exhibition', data_source: 'agent', time_precision: 'date', starts_at: '2026-10-27T12:00:00Z', ends_at: '2026-10-30T12:00:00Z', starts_on: '2026-10-27', ends_on: '2026-10-29', amounts: {}, map_amounts: {}, customers: [], opportunity_ids: [], onsite: [], suggested_actions: [], description: 'Public exhibition. Exact time is not supplied.', source_url: 'https://example.org/expo' };
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.origin !== base || route.request().method() !== 'GET') { errors.push('Unexpected request: ' + url.pathname); return route.abort(); }
      if (!url.pathname.startsWith('/api/')) return route.continue();
      if (url.pathname === '/api/v1/sales/world/') return route.fulfill({ json: { count: 1, results: [fixture], countries: [], currencies: [], unmapped_customer_count: 0 } });
      if (url.pathname === '/api/v1/sales/records/world-news/') return route.fulfill({ json: { count: 0, results: [] } });
      if (url.pathname === '/api/v1/sales/seller-context/') return route.fulfill({ json: { sales_setup: { personal: {} } } });
      errors.push('Unexpected API: ' + url.pathname); return route.abort();
    });
    await page.goto(base + '/world/');
    await page.locator('#add-itinerary').waitFor();
    assert.match(await page.locator('#event-detail').textContent(), /2026-10-27 — 2026-10-29.*Dates only/);
    assert.doesNotMatch(await page.locator('#event-detail').textContent(), /2026-10-30/);
    const downloadPromise = page.waitForEvent('download');
    await page.click('#add-itinerary');
    const download = await downloadPromise, calendar = fs.readFileSync(await download.path(), 'utf8');
    assert.match(calendar, /DTSTART;VALUE=DATE:20261027/);
    assert.match(calendar, /DTEND;VALUE=DATE:20261030/);
    assert.doesNotMatch(calendar, /DTSTART:.*T120000/);
    await page.click('#create-invite');
    assert.match(await page.inputValue('#invite-body'), /2026-10-27/);
    await page.click('#invite-close');
    const timed = await page.evaluate(async row => {
      const { calendarText } = await import('/static/world-news.js?v=20260924-insights');
      return calendarText({ ...row, time_precision: 'datetime', starts_at: '2026-10-27T09:00:00+08:00', ends_at: '2026-10-27T17:00:00+08:00' });
    }, fixture);
    assert.match(timed, /DTSTART:20261027T010000Z/);
    assert.match(timed, /DTEND:20261027T090000Z/);
    assert.doesNotMatch(timed, /VALUE=DATE/);
    fs.mkdirSync(OUTPUT, { recursive: true });
    await page.screenshot({ path: path.join(OUTPUT, 'world-shared-dates-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await page.screenshot({ path: path.join(OUTPUT, 'world-shared-dates-mobile.png'), fullPage: true });
    assert.deepEqual(errors, []);
    await context.close();
  }
}

/** 功能：运行隔离地图回归。输入：Playwright/Chrome 环境。输出：检查摘要和截图。
 * 逻辑：验证地图投影、币种及交互；共享日期在多时区/手机展示，实际下载全天 ICS；捕获脚本错误。
 * 约束：仅本机静态网络；金额为浏览器测试夹具，不写入数据库或调用 Agent。 */
async function main() {
  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    if (pathname === '/world/') {
      res.setHeader('Content-Type', 'text/html; charset=utf-8');
      res.end(fs.readFileSync(path.join(ASSETS, '../world.html')));
      return;
    }
    if (pathname === '/') {
      res.setHeader('Content-Type', 'text/html; charset=utf-8');
      res.end('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><link rel="stylesheet" href="/static/design-system.css"><link rel="stylesheet" href="/static/app.css"><link rel="stylesheet" href="/static/world-news.css"><link rel="stylesheet" href="/static/vendor/leaflet-1.9.4/leaflet.css"><script src="/static/vendor/leaflet-1.9.4/leaflet.js"></script></head><body><div id="world-map" style="width:100%;height:560px"></div></body></html>');
      return;
    }
    const filename = path.resolve(ASSETS, pathname.slice('/static/'.length));
    if (!pathname.startsWith('/static/') || !filename.startsWith(ASSETS + path.sep) || !fs.existsSync(filename) || !fs.statSync(filename).isFile()) { res.writeHead(404); res.end(); return; }
    res.setHeader('Content-Type', filename.endsWith('.js') ? 'text/javascript' : filename.endsWith('.css') ? 'text/css' : 'application/json');
    res.end(fs.readFileSync(filename));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ headless: true, executablePath: process.env.SALESMATE_BROWSER_PATH });
  try {
    await checkDates(browser, `http://127.0.0.1:${server.address().port}`);
    const page = await browser.newPage({ locale: 'zh-CN', viewport: { width: 1000, height: 700 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.evaluate(async () => {
      const { WorldMap } = await import('/static/world-map.js');
      window.fixture = [
        { id: 'small', country: 'SG', city: '新加坡', lat: 1.352, lng: 103.819, amount: 100 },
        { id: 'large', country: 'US', city: 'San Francisco long label', lat: 37.775, lng: -122.419, amount: 400 },
        { id: 'duplicate', country: 'US', city: 'San Francisco long label', lat: 37.775, lng: -122.419, amount: 400 },
        { id: 'unknown', country: 'DE', city: '慕尼黑', lat: 48.135, lng: 11.582, amount: null },
        { id: 'zero', country: 'JP', city: '东京', lat: 35.676, lng: 139.65, amount: 0 },
      ].map(row => ({ ...row, title: row.city, en: row.city, currency: 'SGD', map_amounts: row.amount === null ? {} : { SGD: String(row.amount) } }));
      window.fixtureMap = new WorldMap(document.querySelector('#world-map'), id => { window.selected = id; window.fixtureMap.setItems(window.fixture, id); });
      window.fixtureMap.setCountries(['SG', 'US', 'DE', 'JP']);
      await window.fixtureMap.load();
      window.fixtureMap.setItems(window.fixture, 'small');
    });
    await checkGeometry(page);
    for (const view of ['apac', 'europe', 'global']) {
      await page.evaluate(view => window.fixtureMap.setView(view), view);
      await checkGeometry(page);
    }
    await page.evaluate(() => window.fixtureMap.map.setZoom(2, { animate: false }));
    await checkGeometry(page);
    await page.evaluate(() => window.fixtureMap.setView('global'));
    assert.equal(await page.locator('[data-event-id="large"]').evaluate(button => {
      const rect = button.getBoundingClientRect();
      return Boolean(document.elementFromPoint(rect.x + 1, rect.y + 1)?.closest('.event-pin'));
    }), false, 'Empty icon corners must not intercept adjacent markers');
    await page.locator('[data-event-id="large"] .event-bubble').hover();
    assert.equal(await page.locator('[data-event-id="large"] .event-pin-amount').isVisible(), true);
    await checkGeometry(page);
    await page.locator('[data-event-id="large"] .event-bubble').click();
    assert.equal(await page.evaluate(() => window.selected), 'large');
    await checkGeometry(page);
    assert.match(await page.locator('[data-event-id="unknown"]').getAttribute('aria-label'), /金额未知/);
    assert.match(await page.locator('[data-event-id="zero"]').getAttribute('aria-label'), /SGD 0/);
    await page.locator('[data-event-id="small"]').focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => window.selected), 'small');
    const fills = await page.locator('.event-bubble').evaluateAll(nodes => nodes.map(node => getComputedStyle(node).backgroundColor));
    for (const fill of fills) {
      const alpha = Number(fill.match(/\/\s*([\d.]+)\)/)?.[1]);
      assert.ok(alpha > 0 && alpha <= 0.35, 'Both selected and normal bubbles must remain translucent: ' + fill);
    }
    fs.mkdirSync(OUTPUT, { recursive: true });
    await page.screenshot({ path: path.join(OUTPUT, 'world-map-geometry-desktop.png') });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForFunction(() => window.fixtureMap.map.getSize().x === document.querySelector('#world-map').clientWidth);
    await checkGeometry(page);
    await page.screenshot({ path: path.join(OUTPUT, 'world-map-geometry-mobile.png') });
    assert.deepEqual(errors, []);
    await checkCurrencies(browser, `http://127.0.0.1:${server.address().port}`);
    console.log('World checks passed: shared date ranges in two timezones, downloaded all-day ICS, timed ICS, invitations/mobile; map centers, 4:1 area, translucency, currencies/zero/unknown, URL/reload, views/zoom/resize, mouse/keyboard.');
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
