/** Responsibility: Verify actual local global-insight pages/APIs and opportunity-priority page retirement.
 * Implementation: Use explicitly imported synthetic database batches; test source amounts, filters, details, ICS, templates, mobile layout, and request failures; require no priority link and a 404 for the retired page.
 * Relationships: Running local Django; no external news, Agent, or sending calls.
 * Directory: main.
 * Variable index: BASE is the explicit local test address; OUTPUT is the screenshot directory.
 */
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { chromium } = require(process.env.SALESMATE_PLAYWRIGHT_MODULE);
const BASE = process.env.SALESMATE_TEST_URL;
const OUTPUT = path.resolve(__dirname, '../artifacts/browser');
/** Function: Run actual browser checks. Inputs: Explicit local URL and Playwright/browser environment paths. Outputs: Summary/screenshots. Logic: Read synthetic data with an experiment identity, verify source amount absence/error states, and confirm the removed priority entry and route. Constraints: Database reads only; no sending or third-party requests. */
async function main() {
  if (!BASE || new URL(BASE).hostname !== '127.0.0.1') throw new Error('Set an explicit loopback SALESMATE_TEST_URL.');
  const browser = await chromium.launch({ headless: true, executablePath: process.env.SALESMATE_BROWSER_PATH });
  fs.mkdirSync(OUTPUT, { recursive: true });
  try {
    const page = await browser.newPage({ locale: 'zh-CN', viewport: { width: 1600, height: 1000 }, extraHTTPHeaders: { 'X-Lab-User': 'algorithm-lab' } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
    await page.goto(BASE + '/world/');
    await page.locator('.event-pin').first().waitFor();
    assert.equal(await page.locator('a[href="/priorities/"]').count(), 0);
    assert.equal((await page.request.get(BASE + '/priorities/')).status(), 404);
    assert.ok(await page.locator('.event-card').count() >= 8);
    assert.equal(await page.locator('.industry-news-card').count(), 4);
    assert.match(await page.locator('#world-data-status').innerText(), /数据库记录/);
    assert.equal(await page.locator('#map-currency').count(), 0);
    await page.locator('[data-country=SG]').click();
    assert.equal(await page.locator('.event-card').count(), 2);
    assert.equal(await page.locator('.event-pin').count(), 1);
    await page.selectOption('#event-type', 'sales');
    assert.equal(await page.locator('.event-card').count(), 1);
    await page.selectOption('#event-type', 'all');
    const download = page.waitForEvent('download');
    await page.click('#add-itinerary');
    const file = await download;
    assert.match(fs.readFileSync(await file.path(), 'utf8'), /BEGIN:VCALENDAR/);
    await page.click('#create-invite');
    assert.match(await page.inputValue('#invite-body'), /虚拟销售代表/);
    await page.click('#invite-close');
    await page.locator('[data-country=all]').click();
    await page.screenshot({ path: path.join(OUTPUT, 'support-world-desktop.png'), fullPage: true });
    await page.locator('.industry-news-card').first().click();
    await page.locator('#news-detail article').waitFor();
    assert.match(await page.locator('#news-detail').innerText(), /synthetic|虚拟/);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(BASE + '/world/');
    await page.locator('.event-card').first().waitFor();
    await page.screenshot({ path: path.join(OUTPUT, 'support-world-mobile.png'), fullPage: true });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
    await page.route('**/api/v1/sales/world/**', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: { detail: 'Test unavailable' } }) }));
    await page.reload();
    await page.locator('#world-error').waitFor();
    assert.equal(await page.locator('.event-card').count(), 0);
    assert.deepEqual(errors, []);
    console.log('Database world browser checks and priority-page retirement checks passed; desktop/mobile screenshots saved.');
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
