/** 职责：验收全球洞察与商机评分的真实本地页面/API。
 * 实现：使用已显式导入的数据库虚拟批次；测试活动币种选择、筛选、详情、ICS、模板、证据、移动布局和请求失败。
 * 关联：运行中的本地 Django；不调用外部新闻、Agent 或发信。
 * 目录：main。
 * 变量索引：BASE 为显式本地测试地址；OUTPUT 为截图目录。
 */
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { chromium } = require(process.env.SALESMATE_PLAYWRIGHT_MODULE);
const BASE = process.env.SALESMATE_TEST_URL;
const OUTPUT = path.resolve(__dirname, '../artifacts/browser');
/** 功能：运行真实浏览器检查。输入：显式本地 URL、Playwright 和浏览器路径环境。输出：检查摘要与截图。逻辑：使用实验身份读虚拟数据，确认默认活动币种和错误态。约束：只读业务数据库，不发送邮件或请求第三方站点。 */
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
    assert.ok(await page.locator('.event-card').count() >= 8);
    assert.equal(await page.locator('.industry-news-card').count(), 4);
    assert.match(await page.locator('#world-data-status').innerText(), /数据库记录/);
    assert.equal(await page.inputValue('#map-currency'), 'SGD');
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
    await page.goto(BASE + '/priorities/');
    await page.locator('.priority-row').first().waitFor();
    await page.locator('.priority-row').filter({ hasText: '【虚拟】' }).first().click();
    await page.locator('#priority-detail details').first().waitFor();
    assert.match(await page.locator('#priority-detail').innerText(), /非算法结果/);
    await page.locator('#priority-detail [data-source]').first().click();
    assert.match(await page.locator('#source-body').innerText(), /synthetic/);
    await page.click('#source-close');
    await page.screenshot({ path: path.join(OUTPUT, 'support-priority-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: path.join(OUTPUT, 'support-priority-mobile.png'), fullPage: true });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
    await page.goto(BASE + '/world/');
    await page.locator('.event-card').first().waitFor();
    await page.screenshot({ path: path.join(OUTPUT, 'support-world-mobile.png'), fullPage: true });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
    await page.route('**/api/v1/sales/world/**', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: { detail: 'Test unavailable' } }) }));
    await page.reload();
    await page.locator('#world-error').waitFor();
    assert.equal(await page.locator('.event-card').count(), 0);
    assert.deepEqual(errors, []);
    console.log('Database world/priority browser checks passed; desktop/mobile screenshots saved.');
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
