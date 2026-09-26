/** Responsibility: Verify amount bubbles, shared-event dates, and all-day calendars.
 * Implementation: Check fixed 18px translucent-white missing-amount bubbles and mouse/keyboard selection while preserving known-value proportions. Mock APIs also cover public news leads, exact amounts, fact/inference separation, and mobile layout. Isolated serving loads real pages to test geometry, currencies, date precision, and ICS without real business endpoints.
 * Relationships: world-map.js, world-news.js/css, world.html; explicit environment Playwright/Chrome paths.
 * Directory: geometry, checkGeometry, checkCurrencies, checkDates, checkNewsSignals, main.
 * Variable index: ASSETS is the static-resource root; OUTPUT is the ignored screenshot directory.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require(process.env.SALESMATE_PLAYWRIGHT_MODULE);
const ASSETS = path.resolve(__dirname, '../frontend/assets');
const OUTPUT = path.resolve(__dirname, '../artifacts/browser');

/** Function: Read projected points and visible circle centers. Inputs: page is the browser page. Outputs: Coordinate errors and diameters for each marker.
 * Logic: Independently compare Leaflet geographic projections against DOM centers, including label-induced displacement. Constraints: Isolated fixtures only; do not expose internal business-page state. */
async function geometry(page) {
  return page.evaluate(() => [...document.querySelectorAll('.event-pin')].map(button => {
    const item = window.fixture.find(row => row.id === button.dataset.eventId);
    const point = window.fixtureMap.map.latLngToContainerPoint([item.lat, item.lng]);
    const container = document.querySelector('#world-map').getBoundingClientRect();
    const bubble = button.querySelector('.event-bubble').getBoundingClientRect();
    return { id: item.id, diameter: bubble.width, dx: bubble.x + bubble.width / 2 - container.x - point.x, dy: bubble.y + bubble.height / 2 - container.y - point.y };
  }));
}

/** Function: Assert center alignment and amount proportions. Inputs: page. Outputs: None; throw on failure.
 * Logic: Allow at most one pixel of coordinate error; values 400 and 100 have a four-to-one area ratio. Duplicate colocated records must not double-count amounts; missing bubbles remain 18px. Constraints: Allow browser subpixel rounding without changing business thresholds. */
async function checkGeometry(page) {
  const rows = await geometry(page);
  for (const row of rows) assert.ok(Math.abs(row.dx) <= 1 && Math.abs(row.dy) <= 1, `${row.id} center offset: ${JSON.stringify(row)}`);
  const small = rows.find(row => row.id === 'small'), large = rows.find(row => row.id === 'large');
  assert.ok(Math.abs(large.diameter ** 2 / small.diameter ** 2 - 4) < 0.01);
  assert.equal(large.diameter, 62);
  assert.equal(rows.find(row => row.id === 'unknown').diameter, 18);
  assert.equal(rows.length, 4);
}

/** Function: Verify currency sources and amount semantics in the complete page. Inputs: browser and base identify the isolated browser/static server. Outputs: None; throw on failure.
 * Logic: Mock SGD-only, mixed currencies, zero/unknown amounts, and no events. Exclude unrelated currencies, preserve known amounts across URL/selection changes, and use 18px location bubbles for missing selected currencies without fabricated exchange rates.
 * Constraints: All endpoints are explicit read-only fixtures; unknown endpoints/writes fail tests, with no production-record access. */
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
  assert.equal(await page.locator('[data-event-id="usd"]').evaluate(node => node.style.getPropertyValue('--bubble-size')), '18px');
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

/** Function: Verify date-only events show neither placeholder times nor incorrect final days. Inputs: browser and base static-server address. Outputs: None; assertions throw.
 * Logic: Load Agent-compatible responses in the actual page; verify final days, downloaded all-day ICS, invitations, and mobile layouts across two extreme time zones, retaining screenshots. Timestamp events still export UTC instants.
 * Constraints: Explicit API fixtures only; reject nonlocal requests/writes. This does not prove real collection has run. */
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

/** Function: Verify structured public leads in the actual news page. Inputs: browser and base isolated-server address. Outputs: Assertions/screenshots.
 * Logic: Mock new/legacy news APIs and check all fields, amount types/exact characters, inference labels, XSS escaping, and no horizontal mobile overflow.
 * Constraints: No real Agent/database; source amounts cannot enter event-map currencies or opportunity aggregates. */
async function checkNewsSignals(browser, base) {
  const page = await browser.newPage({ locale: 'zh-CN', viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  let record = { id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', title: '测试扩产新闻', category: 'industry', published_at: '2026-09-24T08:00:00Z', source_url: 'https://example.org/news', data_source: 'agent', summary: '来源摘要', content: '公开新闻正文', company_name: '测试公司', signal_type: 'new_factory', project_name: '测试基地', demand_description: '建设生产线', potential_sales_need: '可能需要检测设备', opportunity_reason: '生产线可能涉及检测环节', time_window: '2027 年投产', evidence: '测试公司计划建设测试基地。<img src=x onerror="window.newsXss=true">总投资 CNY 999999999999999999999999.123456。', amount: '999999999999999999999999.123456', currency: 'CNY', amount_type: 'total_investment', amount_scope: 'whole_project', amount_evidence: '总投资 CNY 999999999999999999999999.123456' };
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== base || route.request().method() !== 'GET') { errors.push('Unexpected request: ' + url.pathname); return route.abort(); }
    if (!url.pathname.startsWith('/api/')) return route.continue();
    if (url.pathname === '/api/v1/sales/world/') return route.fulfill({ json: { count: 0, results: [], countries: [], currencies: [], unmapped_customer_count: 0 } });
    if (url.pathname === '/api/v1/sales/records/world-news/') return route.fulfill({ json: { count: 1, results: [record] } });
    if (url.pathname === `/api/v1/sales/records/world-news/${record.id}/`) return route.fulfill({ json: record });
    if (url.pathname === '/api/v1/sales/seller-context/') return route.fulfill({ json: { sales_setup: { personal: {} } } });
    errors.push('Unexpected API: ' + url.pathname); return route.abort();
  });
  await page.goto(base + '/world/');
  await page.locator('.industry-news-card').waitFor();
  assert.match(await page.locator('.industry-news-card').innerText(), /测试公司/);
  assert.match(await page.locator('.industry-news-card').innerText(), /项目总投资/);
  assert.equal(await page.locator('#map-currency option').count(), 0);
  await page.locator('.industry-news-card').click();
  await page.locator('.news-signal').waitFor();
  assert.equal(await page.locator('#world-data-status').innerText(), '数据库记录 · 资讯');
  const detail = await page.locator('.news-signal').innerText();
  for (const value of ['测试公司', '新建工厂', '测试基地', '建设生产线', '2027 年投产', '项目总投资', '整个项目', '推断 · 非已确认采购需求', '可能需要检测设备', '生产线可能涉及检测环节', '不代表我们的订单金额']) assert.ok(detail.includes(value), value);
  assert.equal(await page.locator('.news-reported-amount .news-source-amount').innerText(), 'CNY 999,999,999,999,999,999,999,999.123456');
  assert.equal(await page.locator('.news-signal img').count(), 0);
  assert.equal(await page.evaluate(() => window.newsXss), undefined);
  fs.mkdirSync(OUTPUT, { recursive: true });
  await page.screenshot({ path: path.join(OUTPUT, 'world-news-signal-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
  await page.screenshot({ path: path.join(OUTPUT, 'world-news-signal-mobile.png'), fullPage: true });
  record = { ...record, amount: '0.000000' };
  await page.reload(); await page.locator('.news-signal').waitFor();
  assert.equal(await page.locator('.news-reported-amount .news-source-amount').innerText(), 'CNY 0');
  record = { ...record, amount: null, currency: '', amount_type: '', amount_scope: '', amount_evidence: '' };
  await page.reload(); await page.locator('.news-signal').waitFor();
  assert.match(await page.locator('.news-reported-amount').innerText(), /暂无结构化金额信息/);
  record = { id: record.id, title: '旧新闻', category: 'industry', published_at: record.published_at, source_url: record.source_url, data_source: 'agent', summary: '', content: '保留旧正文' };
  await page.reload(); await page.locator('.news-signal').waitFor();
  assert.match(await page.locator('.news-signal').innerText(), /暂无结构化线索信息/);
  assert.match(await page.locator('#news-detail').innerText(), /保留旧正文/);
  assert.deepEqual(errors, []);
  await page.close();
}

/** Function: Run isolated map regression checks. Inputs: Playwright/Chrome environment. Outputs: Summary/screenshots.
 * Logic: Verify projections, currencies, and interactions; display shared dates across time zones/mobile and download actual all-day ICS; capture script errors.
 * Constraints: Local static network only; amounts are browser fixtures, without database writes or Agent calls. */
async function main() {
  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    if (pathname === '/world/' || /^\/world\/news\/[a-z0-9-]+\/$/.test(pathname)) {
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
    await checkNewsSignals(browser, `http://127.0.0.1:${server.address().port}`);
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
    assert.match(await page.locator('[data-event-id="unknown"]').getAttribute('class'), /amount-missing/);
    assert.equal(await page.locator('[data-event-id="unknown"] .event-bubble').evaluate(node => getComputedStyle(node).backgroundColor), 'rgba(255, 255, 255, 0.22)');
    await page.locator('[data-event-id="unknown"] .event-bubble').hover();
    assert.equal(await page.locator('[data-event-id="unknown"] .event-pin-amount').isVisible(), true);
    await page.locator('[data-event-id="unknown"] .event-bubble').click();
    assert.equal(await page.evaluate(() => window.selected), 'unknown');
    assert.equal(await page.locator('[data-event-id="unknown"] .event-bubble').evaluate(node => getComputedStyle(node).backgroundColor), 'rgba(255, 255, 255, 0.32)');
    await page.locator('[data-event-id="small"]').focus();
    await page.keyboard.press('Enter');
    await page.locator('[data-event-id="unknown"]').focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => window.selected), 'unknown');
    await checkGeometry(page);

    assert.match(await page.locator('[data-event-id="zero"]').getAttribute('aria-label'), /SGD 0/);
    await page.locator('[data-event-id="small"]').focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => window.selected), 'small');
    const fills = await page.locator('.event-bubble').evaluateAll(nodes => nodes.map(node => getComputedStyle(node).backgroundColor));
    for (const fill of fills) {
      const alpha = Number(fill.match(/(?:\/|,)\s*([\d.]+)\)/)?.[1]);
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
    console.log('World checks passed: news signals/exact decimal/source types/inference/escaping/mobile/legacy;  shared date ranges in two timezones, downloaded all-day ICS, timed ICS, invitations/mobile; map centers, 4:1 area, translucency, currencies/zero/unknown, URL/reload, views/zoom/resize, mouse/keyboard.');
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
