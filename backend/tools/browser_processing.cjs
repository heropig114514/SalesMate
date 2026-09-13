/**
 * 职责：隔离验证同步进度、人工复核、明确重试及移动端布局。
 * 实现：本地静态 HTTP 服务提供真实页面，所有业务 API 使用固定模拟响应。
 * 关联：processing.js、app.js 和共享 workspace 概览；需要显式 Playwright 模块与 Chromium 路径。
 * 目录：main 运行浏览器场景；静态服务及路由回调属于 main 的测试夹具。
 * 变量索引：FRONTEND 为页面目录，OUTPUT 为被忽略的截图目录；其余导入无业务状态。
 * 约束：不访问实际业务数据库、Gmail 或模型；模拟通过只证明界面契约。
 */
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.SALESMATE_PLAYWRIGHT_MODULE);
const FRONTEND = path.resolve(__dirname, '../frontend');
const OUTPUT = path.resolve(__dirname, '../artifacts/browser');

/** 功能：验证真实浏览器交互与文本安全。输入：显式模块/浏览器环境变量。输出：成功说明与截图。
 * 逻辑：模拟共享待办及处理状态，检查复核版本头、重试和移动端；真实页面不依赖外部 API。
 * 约束：拒绝非本地网络，测试独立静态服务在 finally 关闭，不写实际业务记录。 */
async function main() {
  const server = http.createServer((request, response) => {
    const pathname = new URL(request.url, 'http://localhost').pathname;
    const filename = pathname === '/' ? path.join(FRONTEND, 'index.html') : /^\/static\/[a-zA-Z0-9._-]+$/.test(pathname) ? path.join(FRONTEND, 'assets', path.basename(pathname)) : null;
    if (!filename || !fs.existsSync(filename)) { response.writeHead(404); response.end(); return; }
    const extension = path.extname(filename);
    response.setHeader('Content-Type', extension === '.js' ? 'text/javascript' : extension === '.css' ? 'text/css' : 'text/html');
    response.end(fs.readFileSync(filename));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: process.env.SALESMATE_BROWSER_PATH, headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let runId = null, reads = 0, reviewed = false, retried = false;
    await page.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.hostname !== '127.0.0.1') return route.abort();
      if (!url.pathname.startsWith('/api/v1/')) return route.continue();
      const endpoint = url.pathname.slice('/api/v1/'.length);
      let data;
      if (endpoint === 'session/') data = { authenticated: true, username: 'UI 测试', debug_auto_login: true };
      else if (endpoint === 'sales/overview/') data = { open_follow_ups: 0 };
      else if (endpoint === 'sales/records/actions/') data = { results: [], count: 0 };
      else if (endpoint === 'demo/runtime/') data = { provider: 'agent', timezone: 'UTC' };
      else if (endpoint === 'companies/') data = { results: [], count: 0, page: 1, page_size: 20, stats: { companies: 0, unregistered: 0, new_emails_today: 0 } };
      else if (endpoint === 'mailboxes/') data = [{ mailbox_id: 'mb1', address: 'sales@example.com', gmail_authorized: true, sync_state: { status: runId ? 'sync_running' : 'completed', run_id: runId } }];
      else if (endpoint === 'mailboxes/mb1/request-sync/') { runId = 'run1'; data = { run_id: runId, mailbox_id: 'mb1', status: 'queued' }; }
      else if (endpoint === 'email-reviews/') data = { results: reviewed ? [] : [{ email_id: 'sales@example.com:review', sender: 'buyer@example.com', subject: '<img src=x onerror=alert(1)>', body_text: '模拟复核原文', reason: '模型判断非销售沟通', revision: 2, intent_evidences: ['模拟证据'] }], pending_count: reviewed ? 0 : 1, count: reviewed ? 0 : 1, page: 1, page_size: 20 };
      else if (endpoint.startsWith('email-reviews/') && request.method() === 'PATCH') {
        assert.equal(request.headers()['if-match'], '2');
        assert.equal(request.postDataJSON().review_status, 'confirmed_business');
        reviewed = true;
        data = { revision: 3, classification: 'business' };
      } else if (endpoint.startsWith('mailbox-sync-runs/')) {
        if (request.method() === 'POST') { retried = true; runId = 'run2'; }
        reads += 1;
        const running = reads === 1;
        data = { run_id: runId, mailbox_id: 'mb1', status: retried ? 'completed' : running ? 'running' : 'partial', total_count: 3, completed_count: retried ? 3 : 2, failed_count: retried || running ? 0 : 1, pending_count: 0, running_count: running ? 1 : 0, analysis_completed_count: 1, analysis_pending_count: running ? 1 : 0, analysis_failed_count: 0, error: null, email_errors: retried || running ? [] : [{ gmail_message_id: 'bad', stage: 'fetching', message: '模拟读取失败' }] };
      } else throw new Error(`Unexpected API: ${request.method()} ${endpoint}`);
      return route.fulfill({ status: request.method() === 'POST' && endpoint.includes('sync') ? 202 : 200, contentType: 'application/json', body: JSON.stringify(data) });
    });
    fs.mkdirSync(OUTPUT, { recursive: true });
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.locator('#email-reviews-open').filter({ hasText: '(1)' }).waitFor();
    await page.locator('#email-reviews-open').click();
    await page.locator('.review-card').waitFor();
    assert.equal(await page.locator('.review-card img').count(), 0);
    await page.screenshot({ path: path.join(OUTPUT, 'processing-review-desktop.png') });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'mobile page overflow');
    await page.screenshot({ path: path.join(OUTPUT, 'processing-review-mobile.png') });
    await page.locator('[data-decision=confirmed_business]').click();
    await page.locator('#email-reviews-open').filter({ hasText: '(0)' }).waitFor();
    await page.locator('#review-dialog .close-dialog').click();
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.locator('#gmail-manage-top').click();
    await page.locator('[data-gmail-sync]').click();
    await page.locator('#gmail-dialog .close-dialog').click();
    await page.locator('[data-retry-run]').waitFor();
    assert.match(await page.locator('#sync-progress').textContent(), /失败 1/);
    await page.screenshot({ path: path.join(OUTPUT, 'processing-partial-desktop.png') });
    await page.locator('[data-retry-run]').click();
    await page.waitForFunction(() => document.getElementById('sync-progress').textContent.includes('完成 3'));
    assert.equal(retried, true);
    assert.deepEqual(errors, []);
    console.log('Browser processing checks passed: review, escaping, If-Match, mobile layout, batch progress and explicit retry.');
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}

main().catch(error => { console.error(error.stack); process.exitCode = 1; });
