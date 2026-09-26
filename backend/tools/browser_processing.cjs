/**
 * Responsibility: Verify the review entry relocated into mail settings; independently cover QQ/Gmail coexistence, settings/authorization separation, provenance, source viewing, sync progress, manual review, and mobile layout.
 * Internationalization prerequisite: Fix browser locale to zh-CN so existing Chinese assertions do not depend on host language.
 * Implementation: First verify hidden QQ entries while disabled, then enable original scenarios. Local serving loads real pages with mocked APIs for Gmail/QQ scope, per-account source isolation, labels, and extraction retries.
 * Relationships: processing.js, app.js, shared workspace summaries; explicit Playwright/Chromium paths required.
 * Directory: main runs browser scenarios; static-server/route callbacks are main's fixtures.
 * Variable index: FRONTEND is the page directory; OUTPUT is the ignored screenshot directory; imports carry no business state.
 * Constraints: No real business database, Gmail, or model access; mock success verifies UI contracts only.
 */
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.SALESMATE_PLAYWRIGHT_MODULE);
const FRONTEND = path.resolve(__dirname, '../frontend');
const OUTPUT = path.resolve(__dirname, '../artifacts/browser');

/** Function: Verify real browser interactions and text safety. Inputs: Explicit module/browser environment variables. Outputs: Success information/screenshots.
 * Logic: Verify disabled entries, then QQ validation failures/success, required fresh scope, cancellation without requests, and cleared authorization codes. Check connected-mailbox settings and that refresh/sync failures do not authorize; only explicit confirmation calls OAuth. Cover Gmail initial authorization without automatic queuing, default 50-message limits, oversized warning rejection/approval without reuse, refresh/mobile layout, then progress/manual review.
 * Constraints: Reject nonlocal networks; close the isolated server in finally and never write actual business records. */
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
    const page = await browser.newPage({ locale: 'zh-CN', viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let qqEnabled = false;
    let approveLarge = false;
    const warnings = [], gmailRequests = [], authorizationRequests = [];
    page.on('dialog', async dialog => {
      assert.equal(dialog.type(), 'confirm');
      warnings.push(dialog.message());
      if (approveLarge) await dialog.accept(); else await dialog.dismiss();
    });
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
      else if (endpoint === 'demo/runtime/') data = { provider: 'agent', timezone: 'UTC', qq_enabled: qqEnabled };
      else if (endpoint === 'companies/') data = { results: [{ company_id: 'sample-company', company_name: '演示客户', domains: ['demo.example'], contacts: [], crm_status: 'unregistered', email_count: 1, email_sources: ['synthetic_sample'], headline_summary: '演示样例摘要', industry: 'unknown', size_band: 'unknown', signal: 'unknown', score: null }], count: 1, page: 1, page_size: 20, stats: { companies: 1, unregistered: 1, new_emails_today: 0 } };
      else if (endpoint === 'mailboxes/') data = [{ mailbox_id: 'mb1', address: 'sales@example.com', gmail_authorized: true, qq_authorized: false, sync_state: { status: runId ? 'sync_running' : 'completed', run_id: runId } }, ...(qqConnected ? [{ mailbox_id: 'qq1', address: 'demo@qq.com', qq_authorized: true, gmail_authorized: false, sync_state: { status: 'completed', run_id: qqSyncs ? 'qq-run-2' : 'qq-run' } }] : [])];
      else if (endpoint === 'mailboxes/gmail-authorize/') {
        assert.equal(request.method(), 'POST');
        authorizationRequests.push(endpoint);
        return route.fulfill({ status: 503, json: { error: { detail: '模拟授权服务不可用' } } });
      }
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
      else if (endpoint === 'mailboxes/qq1/request-sync/') { assert.deepEqual(request.postDataJSON(), { sync_options: { recent_days: null, max_messages: 3 } }); qqSyncs += 1; data = { mailbox_id: 'qq1', run_id: 'qq-run-2' }; }
      else if (endpoint === 'mailbox-sync-runs/qq-run/' || endpoint === 'mailbox-sync-runs/qq-run-2/') {
        const newRun = endpoint === 'mailbox-sync-runs/qq-run-2/';
        data = { run_id: newRun ? 'qq-run-2' : 'qq-run', mailbox_id: 'qq1', status: 'completed', sync_options: { recent_days: newRun ? null : 7, max_messages: newRun ? 3 : 20, until: '2026-09-14T10:00:00Z' }, total_count: 0, completed_count: 0, failed_count: 0, pending_count: 0, running_count: 0, analysis_completed_count: 0, analysis_pending_count: 0, analysis_failed_count: 0, error: null, email_errors: [] };
      }
      else if (endpoint === 'mailboxes/mb1/request-sync/') {
        gmailRequests.push(request.postDataJSON());
        if (gmailRequests.length === 1) {
          assert.deepEqual(request.postDataJSON(), { sync_options: { recent_days: 7, max_messages: 50 } });
          return route.fulfill({ status: 400, contentType: 'application/json', body: JSON.stringify({ error: { detail: '模拟拒绝默认范围，不启动同步' } }) });
        }
        assert.deepEqual(request.postDataJSON(), { sync_options: { recent_days: 7, max_messages: 75, allow_large_sync: true } });
        runId = 'run1'; data = { run_id: runId, mailbox_id: 'mb1', status: 'queued' };
      }
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
    await page.goto(`http://127.0.0.1:${server.address().port}/?gmail=authorized&address=sales%40example.com`);
    await page.locator('#gmail-scope-dialog').waitFor({ state: 'visible' });
    assert.equal(runId, null, 'OAuth must not start unbounded sync');
    await page.locator('#gmail-scope-form [type=submit]').click();
    await page.locator('#gmail-scope-error').filter({ hasText: '至少一项' }).waitFor();
    assert.equal(runId, null);
    await page.locator('#gmail-scope-form [name=max_messages]').fill('51');
    await page.locator('#gmail-scope-form [type=submit]').click();
    assert.equal(warnings.length, 1);
    assert.match(warnings[0], /51.*50.*长时间/);
    assert.equal(gmailRequests.length, 0, 'Rejected warning must not submit');
    assert.equal(await page.locator('#gmail-scope-dialog').isVisible(), true);
    await page.locator('#gmail-scope-form [name=max_messages]').fill('');
    await page.locator('#gmail-scope-form [name=recent_days]').fill('7');
    await page.locator('#gmail-scope-form [type=submit]').click();
    await page.locator('#notice').filter({ hasText: '模拟拒绝默认范围' }).waitFor();
    assert.equal(warnings.length, 1, 'Default 50 must not require extra approval');
    assert.equal(gmailRequests.length, 1);
    await page.locator('#refresh').click();
    await page.locator('#gmail-scope-dialog').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#gmail-scope-form [name=recent_days]').inputValue(), '');
    await page.locator('#gmail-scope-dialog .close-dialog').click();
    assert.equal(runId, null, 'Cancel refresh must not queue Gmail');
    await page.waitForFunction(() => document.getElementById('email-reviews-open').textContent.includes('(1)'));
    assert.match(await page.locator('.company-row .row-tags').textContent(), /演示样例/);
    assert.equal(await page.locator('#qq-manage-top').isVisible(), false);
    assert.equal(await page.locator('#qq-manage').isVisible(), false);
    qqEnabled = true;
    await page.reload();
    await page.locator('#qq-manage-top').waitFor({ state: 'visible' });
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
    await page.waitForFunction(() => document.getElementById('sync-progress').hidden);
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
    await page.goto(`http://127.0.0.1:${server.address().port}/#gmail`);
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
    await page.waitForFunction(() => document.getElementById('email-reviews-open').textContent.includes('(0)'));
    await page.locator('#review-dialog .close-dialog').click();
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.locator('#workspace-profile a').filter({ hasText: 'Emails Connections' }).click();
    await page.locator('#email-settings-page').waitFor();
    assert.equal(await page.locator('#gmail-dialog').isVisible(), false);
    assert.match(await page.locator('#gmail-accounts').textContent(), /sales@example.com/);
    await page.locator('#workspace-profile a').filter({ hasText: 'Emails Connections' }).click();
    await page.locator('#gmail-settings-refresh').click();
    assert.equal(await page.locator('#gmail-dialog').isVisible(), false);
    assert.equal(authorizationRequests.length, 0);
    await page.screenshot({ path: path.join(OUTPUT, 'email-settings-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'Email settings mobile overflow');
    await page.screenshot({ path: path.join(OUTPUT, 'email-settings-mobile.png'), fullPage: true });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.locator('[data-gmail-reconnect]').click();
    await page.locator('#gmail-dialog[open]').waitFor();
    assert.match(await page.locator('#gmail-authorization-account').textContent(), /sales@example.com/);
    assert.equal(authorizationRequests.length, 0, 'Opening permission details is not authorization');
    await page.locator('#gmail-dialog .close-dialog').click();
    await page.locator('#gmail-add').click();
    assert.equal(await page.locator('#gmail-authorization-account').isVisible(), false);
    await page.locator('#gmail-authorize').click();
    await page.locator('#notice').filter({ hasText: '模拟授权服务不可用' }).waitFor();
    assert.equal(authorizationRequests.length, 1, 'Only explicit authorization may call OAuth');
    await page.locator('#gmail-dialog .close-dialog').click();
    await page.locator('[data-gmail-sync]').click();
    await page.locator('#gmail-scope-form [name=recent_days]').fill('7');
    await page.locator('#gmail-scope-form [name=max_messages]').fill('75');
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'Gmail scope mobile overflow');
    await page.screenshot({ path: path.join(OUTPUT, 'gmail-scope-mobile.png') });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.screenshot({ path: path.join(OUTPUT, 'gmail-scope-desktop.png') });
    approveLarge = true;
    await page.locator('#gmail-scope-form [type=submit]').click();
    assert.equal(warnings.length, 2);
    assert.match(warnings[1], /75.*50.*长时间/);
    await page.locator('#gmail-scope-dialog').waitFor({ state: 'hidden' });
    await page.locator('#gmail-progress-link').click();
    await page.locator('[data-retry-run]').waitFor();
    assert.equal(authorizationRequests.length, 1, 'Partial sync must not automatically reauthorize');
    assert.match(await page.locator('#sync-progress').textContent(), /失败 1/);
    await page.screenshot({ path: path.join(OUTPUT, 'processing-partial-desktop.png') });
    await page.locator('[data-retry-run]').click();
    await page.waitForFunction(() => document.getElementById('sync-progress').textContent.includes('完成 3'));
    assert.equal(retried, true);
    runId = null;
    approveLarge = false;
    await page.locator('#refresh').click();
    await page.locator('#gmail-scope-form [name=max_messages]').fill('76');
    await page.locator('#gmail-scope-form [type=submit]').click();
    assert.equal(warnings.length, 3, 'New selection needs fresh approval');
    assert.match(warnings[2], /76.*50/);
    assert.equal(gmailRequests.length, 2, 'Prior approval must not authorize a new selection');
    await page.locator('#gmail-scope-dialog .close-dialog').click();
    assert.deepEqual(errors, []);
    console.log('Browser processing checks passed: source labels, QQ saved originals/account scope/classification/date, global review reset, explicit limits, default 50 cap, large-sync warning rejection/approval/no reuse, cancel, connection failure/success, secret clearing, disconnect, Gmail coexistence, escaping, If-Match, mobile layout, batch progress and explicit retry.');
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}

main().catch(error => { console.error(error.stack); process.exitCode = 1; });
