/** 职责：验证金额气泡的地理锚点、面积比例及可访问交互。
 * 实现：隔离静态服务器加载实际 Leaflet、地图模块和页面样式，以受控坐标与金额检查真实浏览器几何；不访问业务接口。
 * 关联：world-map.js、world-news.css；Playwright/Chrome 路径由环境显式提供。
 * 目录：geometry、checkGeometry、main。
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

/** 功能：运行隔离地图回归。输入：Playwright/Chrome 环境。输出：检查摘要和截图。
 * 逻辑：验证全球/亚太/欧洲、缩放、手机尺寸、不同长度标签、选择及未知/零金额；空白图标区域不得截获点击，金额提示不得改变圆心；捕获脚本错误。
 * 约束：仅本机静态网络；金额为浏览器测试夹具，不写入数据库或调用 Agent。 */
async function main() {
  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://localhost').pathname;
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
      ].map(row => ({ ...row, title: row.city, en: row.city, currency: 'SGD' }));
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
    fs.mkdirSync(OUTPUT, { recursive: true });
    await page.screenshot({ path: path.join(OUTPUT, 'world-map-geometry-desktop.png') });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForFunction(() => window.fixtureMap.map.getSize().x === document.querySelector('#world-map').clientWidth);
    await checkGeometry(page);
    await page.screenshot({ path: path.join(OUTPUT, 'world-map-geometry-mobile.png') });
    assert.deepEqual(errors, []);
    console.log('Map geometry passed: projected centers, 4:1 area, shared coordinates, unknown/zero, views/zoom/resize, mouse/keyboard.');
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
