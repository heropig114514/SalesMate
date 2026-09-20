/** 职责：验收 0919 产品界面精简、Channel 会话布局及真实来源预览及加载后追加回复草稿。
 * 实现：使用隔离静态服务及模拟授权 API，真实浏览器检查悬停、键盘、触摸、缺失引用、正文转义及响应式。
 * 关联：app.js、evidence-preview.js、channel-detail.css、processing.js；不访问真实邮箱、模型或线上账号。
 * 目录：main。
 * 变量索引：ROOT 为前端路径；OUTPUT 为忽略的截图目录；其余为工具导入，无业务配置。
 */
const fs = require('node:fs'), path = require('node:path'), http = require('node:http'), assert = require('node:assert/strict');
const { chromium } = require(process.env.SALESMATE_PLAYWRIGHT_MODULE);
const ROOT = path.resolve(__dirname, '../frontend'), OUTPUT = path.resolve(__dirname, '../artifacts/browser');

/** 功能：执行产品增量验收。输入：环境中的 Playwright 模块与浏览器路径。输出：断言、截图及日志。
 * 逻辑：模拟两封往来邮件和一个缺失引用；确认隐藏冗余 UI、引用不写入、显式定位及运行中/失败同步仍可见。
 * 约束：仅客户详情原有分析 POST 可出现；所有邮件内容为测试数据，不代表外部服务已验证。 */
async function main() {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    const file = url.pathname === '/' ? path.join(ROOT, 'index.html') : url.pathname.startsWith('/static/') ? path.resolve(ROOT, 'assets', url.pathname.slice(8)) : null;
    if (!file || !file.startsWith(ROOT + path.sep) || !fs.existsSync(file)) { res.writeHead(404); res.end(); return; }
    res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
    res.end(fs.readFileSync(file));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: process.env.SALESMATE_BROWSER_PATH, headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1600, height: 1050 }, locale: 'zh-CN', reducedMotion: 'reduce' });
    const errors = [], writes = [];
    page.on('pageerror', error => errors.push(error.message));
    const empty = { facts: [], inferences: [], missing_fields: [] };
    const detail = {
      company_id: 'company-product', company_name: 'Kestrel Photonics', domains: ['kestrel.example'], contacts: [{ contact_name: 'Ivan Hsu', contact_email: 'ivan@kestrel.example', interaction_count: 2 }],
      crm_status: 'registered', score: 71, score_reasons: [], provider: 'agent', email_count: 2, job_status: 'completed', generated_at: '2026-09-20T02:00:00Z',
      analysis: { detail_view: { profile: { industry_context: { ...empty, facts: [{ text: '需要四轴光学检测设备', source_refs: ['mail-in', 'missing-record'] }] }, company_ops: empty, intent: empty }, analysis: { timeline: empty, opportunity: empty, risk: empty, guidance: empty }, conflicts: [], missing_fields: ['交付时间'] } },
      context: { tickets: [], quotes: [], orders: [], emails: [
        { dedupe_key: 'mail-in', from: 'ivan@kestrel.example', direction: 'inbound', sent_at: '2026-09-19T10:00:00Z', subject: 'Four-axis inspection enquiry', body_text: 'Please quote four inspection stages.\nBudget USD 180k.\n<img src=x onerror=alert(1)>', source: 'gmail_real', extract_status: 'completed' },
        { dedupe_key: 'mail-out', from: 'sales@example.com', direction: 'outbound', sent_at: '2026-09-20T10:00:00Z', subject: 'Re: Four-axis inspection enquiry', body_text: 'Attached is our revised proposal. Delivery remains 14 weeks.', source: 'gmail_real', extract_status: 'completed' },
      ] },
    };
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url());
      if (url.hostname !== '127.0.0.1') return route.abort();
      if (!url.pathname.startsWith('/api/v1/')) return route.continue();
      const endpoint = url.pathname.slice(8);
      if (req.method() !== 'GET') { writes.push(endpoint); assert.equal(endpoint, 'companies/company-product/analyze/'); return route.fulfill({ json: {} }); }
      const data = endpoint === 'session/' ? { authenticated: true, username: 'Product Test' }
        : endpoint === 'demo/runtime/' ? { provider: 'agent', timezone: 'Asia/Singapore' }
        : endpoint === 'mailboxes/' ? []
        : endpoint === 'email-reviews/' ? { pending_count: 2, count: 0, results: [] }
        : endpoint === 'sales/overview/' ? { open_follow_ups: 3 }
        : endpoint === 'companies/' ? { results: [], count: 0, stats: { companies: 1, unregistered: 1, new_emails_today: 2 } }
        : endpoint === 'companies/company-product/' ? detail
        : endpoint === 'sales/records/conversations/' ? { count: 1, results: [{ id: 'chat-product', title: 'Existing session', created_at: '2026-09-19T10:00:00Z' }] }
        : endpoint === 'sales/records/drafts/' ? { count: 1, results: [{ id: 'draft-product', kind: 'chat', content: 'Existing assistant draft.', updated_at: '2026-09-19T10:00:00Z', revision: 1 }] }
        : endpoint.startsWith('sales/records/') || endpoint === 'sales/chat/requests/' ? { count: 0, results: [] } : null;
      assert.notEqual(data, null, endpoint);
      await route.fulfill({ json: data });
    });
    const base = `http://127.0.0.1:${server.address().port}`;
    fs.mkdirSync(OUTPUT, { recursive: true });
    await page.goto(base + '/#home');
    await page.locator('#stats .stat-card').first().waitFor();
    assert.equal(await page.locator('#workspace-status').count(), 0);
    assert.equal(await page.locator('.product-context').count(), 0);
    assert.equal(await page.locator('#workspace-tasks .workspace-task').count(), 3);
    assert.equal(await page.locator('#stats .stat-card').count(), 2);
    assert.equal(await page.locator('#email-reviews-open').isVisible(), false);
    assert.equal(await page.locator('.list-footer').innerText(), '←\n1 / 1\n→');
    await page.screenshot({ path: path.join(OUTPUT, 'product0919-dashboard.png'), fullPage: true });
    await page.goto(base + '/#inbox');
    await page.locator('#stats .stat-card').first().waitFor();
    assert.equal(await page.locator('#stats .stat-card').count(), 3);
    assert.equal(await page.locator('.topbar-right').isVisible(), false);
    await page.evaluate(async () => {
      const { updateRunProgress } = await import('/static/processing.js?v=20260921-product');
      const run = { status: 'completed', total_count: 2, completed_count: 2, running_count: 0, pending_count: 0, failed_count: 0, analysis_completed_count: 1, analysis_pending_count: 0, analysis_failed_count: 0, email_errors: [] };
      updateRunProgress([run]);
      if (!document.getElementById('sync-progress').hidden) throw new Error('Completed progress should be hidden');
      updateRunProgress([{ ...run, status: 'running' }]);
      if (document.getElementById('sync-progress').hidden) throw new Error('Active progress must remain visible');
      updateRunProgress([{ ...run, status: 'failed', error: { message: 'Test failure' } }]);
      if (document.getElementById('sync-progress').hidden) throw new Error('Failure must remain visible');
      updateRunProgress([]);
    });
    await page.goto(base + '/#company/company-product');
    await page.locator('[data-ref="mail-in"]').waitFor();
    assert.equal(await page.locator('#workspace-context').isVisible(), false);
    assert.equal(await page.locator('.analysis-status').count(), 0);
    const inbound = await page.locator('.email-inbound').boundingBox(), outbound = await page.locator('.email-outbound').boundingBox();
    assert(outbound.x > inbound.x, 'Outgoing mail should align to the right');
    const before = writes.length;
    const evidence = page.locator('[data-ref="mail-in"]');
    await evidence.hover();
    const preview = page.locator('#evidence-preview');
    await preview.waitFor();
    assert.match(await preview.innerText(), /ivan@kestrel.example/);
    assert.match(await preview.innerText(), /2026/);
    assert.match(await preview.locator('pre').innerText(), /Budget USD 180k/);
    assert.equal(await preview.locator('img').count(), 0, 'Email markup must be escaped');
    await preview.hover();
    await page.screenshot({ path: path.join(OUTPUT, 'product0919-evidence.png'), fullPage: true });
    await preview.locator('[data-evidence-close]').click();
    assert.equal(await preview.isVisible(), false);
    await page.locator('[data-ref="missing-record"]').focus();
    await preview.waitFor();
    assert.match(await preview.innerText(), /未包含/);
    await page.keyboard.press('Escape');
    assert.equal(await preview.isVisible(), false);
    await evidence.click();
    await preview.getByRole('button', { name: '在邮件往来中查看' }).click();
    assert.equal(await page.locator('[data-email-ref="mail-in"]').evaluate(el => el.classList.contains('highlight')), true);
    assert.equal(writes.length, before, 'Evidence interactions must be read-only');
    await page.locator('#channel-reply').fill('Our draft reply with a confirmed delivery date.');
    await page.locator('[data-reply-refine]').click();
    await page.locator('#assistant-input:not(:disabled)').waitFor();
    assert.match(await page.locator('#assistant-input').inputValue(), /Existing assistant draft[\s\S]*Our draft reply/);
    assert.equal(writes.length, before, 'Refining must only prefill, never send a question or email');
    const chat = await page.locator('#assistant-panel').boundingBox(), analysis = await page.locator('.analysis-panel').boundingBox();
    assert(chat.x >= analysis.x + analysis.width, 'Desktop assistant must not cover customer analysis');
    await page.screenshot({ path: path.join(OUTPUT, 'product0919-channel-desktop.png'), fullPage: true });
    await page.locator('#assistant-close').click();
    await page.setViewportSize({ width: 390, height: 844 });
    await evidence.click();
    await preview.waitFor();
    const mobile = await preview.boundingBox();
    assert(mobile.x >= 0 && mobile.x + mobile.width <= 390);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({ path: path.join(OUTPUT, 'product0919-channel-mobile.png'), fullPage: false });
    await page.goto(base + '/#home');
    await page.locator('#stats .stat-card').first().waitFor();
    assert.equal(await preview.isVisible(), false);
    await page.context().addCookies([{ name: 'django_language', value: 'en', url: base }]);
    await page.goto(base + '/?language-check=en#company/company-product');
    await page.locator('[data-ref="mail-in"]').click();
    await preview.getByText('Evidence source', { exact: true }).waitFor();
    assert.match(await preview.innerText(), /Sender/);
    assert.equal(await page.locator('#workspace-profile a').last().textContent(), 'Emails Connections');
    assert.deepEqual(errors, []);
    console.log('0919 product checks passed: simplified navigation/cards, progress, source preview, escaping, keyboard/touch, original-email focus, Channel columns, mobile and English.');
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
