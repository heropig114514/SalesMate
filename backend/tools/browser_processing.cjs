/**
 * 职责：隔离验证 QQ/Gmail 共存、来源标识、原文入口、同步进度、人工复核及移动端布局。
 * 实现：本地静态服务提供真实页面，模拟 API 验证 QQ 范围选择、账号原文隔离、来源标签与补抽取重试。
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
 * 逻辑：模拟 QQ 验证失败与成功，检查范围必填、每次空白、取消无请求、授权码清空；回归 Gmail 进度和人工复核。
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
    let runId = null, reads = 0, reviewed = false, retried = false, qqConnected = false, qqAttempts = 0, qqSyncs = 0;
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
      else if (endpoint === 'companies/') data = { results: [{ company_id: 'sample-company', company_name: '演示客户', domains: ['demo.example'], contacts: [], crm_status: 'unregistered', email_count: 1, email_sources: ['synthetic_sample'], headline_summary: '演示样例摘要', industry: 'unknown', size_band: 'unknown', signal: 'unknown', score: null }], count: 1, page: 1, page_size: 20, stats: { companies: 1, unregistered: 1, new_emails_today: 0 } };
      else if (endpoint === 'mailboxes/') data = [{ mailbox_id: 'mb1', address: 'sales@example.com', gmail_authorized: true, qq_authorized: false, sync_state: { status: runId ? 'sync_running' : 'completed', run_id: runId } }, ...(qqConnected ? [{ mailbox_id: 'qq1', address: 'demo@qq.com', qq_authorized: true, gmail_authorized: false, sync_state: { status: 'completed', run_id: 'qq-run' } }] : [])];
      else if (endpoint === 'mailboxes/qq-connect/') {
        assert.equal(request.method(), 'POST');
        assert.deepEqual(request.postDataJSON(), { address: 'demo@qq.com', authorization_code: 'abcdefghijklmnop', sync_options: { recent_days: 7, max_messages: 20 } });
        qqAttempts += 1;
        if (qqAttempts === 1) return route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ error: { detail: 'QQ 授权码验证失败，请重新获取。' } }) });
        qqConnected = true;
        data = { mailbox_id: 'qq1', qq_authorized: true };
      }
      else if (endpoint === 'mailboxes/qq1/qq-authorization/') { assert.equal(request.method(), 'DELETE'); qqConnected = false; data = { mailbox_id: 'qq1', qq_authorized: false }; }
      else if (endpoint === 'mailboxes/qq1/email-reviews/') {
        assert.equal(request.method(), 'GET', 'Viewing originals must not mutate classification');
        assert.ok(['saved', 'non_business'].includes(url.searchParams.get('status')));
        const items = ['non_business', 'needs_review', 'business'].map((classification, index) => ({ email_id: `qq-message-${index}`, mailbox_id: 'qq1', sender: 'sender@example.com', source: 'qq_real', subject: `QQ 邮件核对 ${index + 1}`, body_text: '用于核对的 QQ 原文', received_at: '2026-09-14T07:44:47Z', classification, revision: 1, reason: '模拟分类依据', intent_evidences: [] }));
        const results = url.searchParams.get('status') === 'saved' ? items : items.slice(0, 1);
        data = { results, count: results.length, pending_count: 1, page: 1, page_size: 20 };
      }
      else if (endpoint === 'mailboxes/qq1/request-sync/') { assert.deepEqual(request.postDataJSON(), { sync_options: { recent_days: null, max_messages: 3 } }); qqSyncs += 1; data = { mailbox_id: 'qq1', run_id: 'qq-run' }; }
      else if (endpoint === 'mailbox-sync-runs/qq-run/') data = { run_id: 'qq-run', mailbox_id: 'qq1', status: 'completed', sync_options: { recent_days: null, max_messages: 3, until: '2026-09-14T10:00:00Z' }, total_count: 0, completed_count: 0, failed_count: 0, pending_count: 0, running_count: 0, analysis_completed_count: 0, analysis_pending_count: 0, analysis_failed_count: 0, error: null, email_errors: [] };
      else if (endpoint === 'mailboxes/mb1/request-sync/') { runId = 'run1'; data = { run_id: runId, mailbox_id: 'mb1', status: 'queued' }; }
      else if (endpoint === 'email-reviews/') data = { results: reviewed ? [] : [{ email_id: 'sales@example.com:review', sender: 'buyer@example.com', subject: '<img src=x onerror=alert(1)>', body_text: '模拟复核原文', reason: '员工确认业务，模拟补抽取失败', revision: 2, intent_evidences: ['模拟证据'], repair_status: 'failed' }], pending_count: reviewed ? 0 : 1, count: reviewed ? 0 : 1, page: 1, page_size: 20 };
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
    assert.match(await page.locator('.company-row .row-tags').textContent(), /演示样例/);
    await page.locator('#qq-manage-top').click();
    await page.locator('#qq-form [name=address]').fill('demo@qq.com');
    await page.locator('#qq-form [name=authorization_code]').fill('abcdefghijklmnop');
    await page.locator('#qq-form [type=submit]').click();
    await page.locator('#qq-error').filter({ hasText: '至少一项' }).waitFor();
    assert.equal(qqAttempts, 0);
    await page.locator('#qq-form [name=recent_days]').fill('7');
    await page.locator('#qq-form [name=max_messages]').fill('20');
    await page.locator('#qq-form [type=submit]').click();
    await page.locator('#qq-error').filter({ hasText: '验证失败' }).waitFor();
    assert.equal(await page.locator('#qq-form [name=authorization_code]').inputValue(), '');
    await page.locator('#qq-form [name=authorization_code]').fill('abcdefghijklmnop');
    await page.locator('#qq-form [type=submit]').click();
    await page.locator('[data-qq-sync]').waitFor();
    assert.equal(await page.locator('[data-gmail-sync]').count(), 1, 'Gmail account remains available');
    assert.equal(await page.locator('#qq-form [name=authorization_code]').inputValue(), '');
    assert.equal(await page.evaluate(() => JSON.stringify(localStorage).includes('abcdefghijklmnop')), false);
    assert.equal(await page.locator('#qq-form [name=recent_days]').inputValue(), '');
    assert.equal(await page.locator('#qq-form [name=max_messages]').inputValue(), '');
    await page.locator('[data-qq-sync]').click();
    await page.locator('#qq-scope-dialog').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#qq-scope-form [name=max_messages]').inputValue(), '');
    await page.locator('#qq-scope-form [type=submit]').click();
    await page.locator('#qq-scope-error').filter({ hasText: '至少一项' }).waitFor();
    assert.equal(qqSyncs, 0);
    await page.locator('#qq-scope-form [name=max_messages]').fill('3');
    await page.locator('#qq-scope-form [type=submit]').click();
    await page.waitForFunction(() => document.getElementById('sync-progress').textContent.includes('最多 3 封'));
    assert.equal(qqSyncs, 1);
    await page.locator('[data-qq-sync]').click();
    await page.locator('#qq-scope-dialog').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#qq-scope-form [name=max_messages]').inputValue(), '');
    await page.locator('#qq-scope-dialog .close-dialog').click();
    assert.equal(qqSyncs, 1, 'Cancel must not queue another sync');
    await page.locator('[data-qq-view]').click();
    await page.locator('#review-title').filter({ hasText: '已同步邮件' }).waitFor();
    await page.locator('.review-card').nth(2).waitFor();
    assert.equal(await page.locator('#review-filter').inputValue(), 'saved');
    assert.match(await page.locator('#review-scope').textContent(), /demo@qq.com/);
    assert.match(await page.locator('#review-items').textContent(), /QQ 邮件.*非业务邮件/s);
    assert.match(await page.locator('#review-items').textContent(), /接收时间/);
    assert.equal(await page.locator('#review-items').getByText('演示样例', { exact: true }).count(), 0);
    await page.locator('.review-card details').first().locator('summary').click();
    assert.match(await page.locator('.review-card pre').first().textContent(), /用于核对的 QQ 原文/);
    await page.screenshot({ path: path.join(OUTPUT, 'qq-saved-mail-desktop.png') });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'QQ originals mobile overflow');
    await page.screenshot({ path: path.join(OUTPUT, 'qq-saved-mail-mobile.png') });
    await page.locator('#review-filter').selectOption('non_business');
    await page.waitForFunction(() => document.querySelectorAll('.review-card').length === 1);
    await page.locator('#review-dialog .close-dialog').click();
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.locator('#qq-manage-top').click();
    await page.screenshot({ path: path.join(OUTPUT, 'qq-connection-desktop.png') });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'QQ mobile page overflow');
    await page.screenshot({ path: path.join(OUTPUT, 'qq-connection-mobile.png') });
    await page.locator('[data-qq-disconnect]').click();
    await page.locator('#qq-accounts').filter({ hasText: '尚未连接 QQ 邮箱' }).waitFor();
    await page.locator('#qq-dialog .close-dialog').click();
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.locator('#email-reviews-open').click();
    await page.locator('.review-card').waitFor();
    assert.equal(await page.locator('#review-filter').inputValue(), 'pending');
    assert.match(await page.locator('#review-scope').textContent(), /全部邮箱/);
    assert.equal(await page.locator('.review-card img').count(), 0);
    assert.match(await page.locator('.review-card').textContent(), /失败，可再次点击确认业务重试/);
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
    console.log('Browser processing checks passed: source labels, QQ saved originals/account scope/classification/date, global review reset, explicit limits, cancel, connection failure/success, secret clearing, disconnect, Gmail coexistence, escaping, If-Match, mobile layout, batch progress and explicit retry.');
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}

main().catch(error => { console.error(error.stack); process.exitCode = 1; });
