/**
 * 职责：验证产品顶栏、统一导航、直接聊天入口、真实总数展示、跨页客户上下文和表单预填。
 * 实现：真实 HTML/JS 使用隔离静态服务器，全部 API 模拟；检查刷新、筛选、失败、移动布局。
 * 关联：product-header.js、workspace.js、app.js、assistant-entry.js、business.js；需显式 Playwright 模块和 Chrome 路径。
 * 目录：main 执行模拟导航场景。
 * 变量索引：FRONTEND 为页面目录，OUTPUT 为忽略的截图目录；其余导入无业务状态。
 */
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.SALESMATE_PLAYWRIGHT_MODULE);
const FRONTEND = path.resolve(__dirname, '../frontend');
const OUTPUT = path.resolve(__dirname, '../artifacts/browser');

/** 功能：执行独立浏览器契约验收。输入：运行环境中的 Playwright/Chrome 路径。输出：检查结果及截图。
 * 逻辑：产品分区切换、通用聊天直达、刷新和移动端导航不产生写入；A 公司详情跳转报价、跟进并刷新；额外检验未知客户与失败。
 * 约束：所有业务请求均拦截；除原有客户分析入口的模拟 POST 外，禁止任何写入和外部网络。 */
async function main() {
  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    const filename = pathname === '/' ? path.join(FRONTEND, 'index.html') : pathname === '/business/' ? path.join(FRONTEND, 'business.html') : /^\/static\/[\w.-]+$/.test(pathname) ? path.join(FRONTEND, 'assets', path.basename(pathname)) : null;
    if (!filename || !fs.existsSync(filename)) { res.writeHead(404); res.end(); return; }
    res.setHeader('Content-Type', filename.endsWith('.js') ? 'text/javascript' : filename.endsWith('.css') ? 'text/css' : 'text/html');
    res.end(fs.readFileSync(filename));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: process.env.SALESMATE_BROWSER_PATH, headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [], writes = [], queries = [];
    page.on('pageerror', error => errors.push(error.message));
    let failActions = false;
    const company = { id: 'company-a', name: 'A 公司 · 精密设备', domains: ['a.example'], contacts: [], archived: false, crm_status: 'registered', customer: {}, revision: 1 };
    const row = { company_id: company.id, company_name: company.name, domains: company.domains, contacts: [], crm_status: 'registered', industry: 'unknown', size_band: 'unknown', signal: 'inquiry_intent', score: null, provider: 'agent', email_count: 1, headline_summary: '希望采购 100 台设备，待准备报价', last_message_at: '2026-09-13T01:00:00Z' };
    const resources = ['quotes', 'orders', 'follow-ups', 'actions', 'notifications'].map(key => ({ key, model: key, label: { quotes: '报价', orders: '订单', 'follow-ups': '跟进', actions: '动作', notifications: '通知' }[key], transitions: {}, fields: [{ name: 'company', type: 'relation', relation: 'company', required: true }, { name: 'title', type: 'text' }, { name: 'status', type: 'choice', readonly: true, choices: ['open', 'completed', 'pending_confirmation', 'draft'] }] }));
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url());
      if (url.hostname !== '127.0.0.1') return route.abort();
      if (!url.pathname.startsWith('/api/v1/')) return route.continue();
      const endpoint = url.pathname.slice('/api/v1/'.length);
      queries.push(url.pathname + url.search);
      if (req.method() !== 'GET') {
        writes.push(endpoint);
        assert.equal(endpoint, 'companies/company-a/analyze/', 'Unexpected business mutation');
        return route.fulfill({ json: {} });
      }
      let data;
      if (endpoint === 'session/') data = { authenticated: true, username: '测试销售', debug_auto_login: true };
      else if (endpoint === 'accounts/me/') data = { username: '测试销售' };
      else if (endpoint === 'demo/runtime/') data = { provider: 'agent', timezone: 'Asia/Shanghai' };
      else if (endpoint === 'mailboxes/') data = [];
      else if (endpoint === 'email-reviews/') data = { pending_count: 3, count: 0, results: [], page: 1, page_size: 20 };
      else if (endpoint === 'companies/') data = { results: url.searchParams.get('q') === '不存在' ? [] : [row], count: url.searchParams.get('q') === '不存在' ? 0 : 1, stats: { companies: 1, unregistered: 0, new_emails_today: 1 } };
      else if (endpoint === 'companies/company-a/') data = { ...row, analysis: null, score_reasons: [], context: { emails: [], tickets: [], quotes: [], orders: [] } };
      else if (endpoint === 'companies/unavailable/') return route.fulfill({ status: 404, json: { error: { detail: '客户不存在或无权访问' } } });
      else if (endpoint === 'sales/overview/') data = { customers: 1, open_tickets: 2, open_follow_ups: 7, unread_notifications: 2, confirmed_order_net: {}, open_opportunity_amount: {} };
      else if (endpoint === 'sales/catalog/') data = { resources };
      else if (endpoint === 'sales/directory/') data = { results: [company], count: 1 };
      else if (endpoint.startsWith('sales/records/')) {
        if (failActions && endpoint === 'sales/records/actions/') return route.fulfill({ status: 503, json: { error: { detail: '模拟动作服务不可用' } } });
        data = { results: [], count: endpoint === 'sales/records/actions/' ? 2 : 0 };
      } else throw new Error('Unexpected API: ' + endpoint);
      return route.fulfill({ json: data });
    });
    const base = `http://127.0.0.1:${server.address().port}`;
    fs.mkdirSync(OUTPUT, { recursive: true });
    await page.goto(base);
    await page.locator('.workspace-task').filter({ hasText: '待跟进' }).getByText('7', { exact: true }).waitFor();
    assert.equal(await page.locator('#workspace-nav a[aria-current=page]').textContent(), '工作台');
    const navLabels = await page.locator('#workspace-nav a').allTextContents();
    assert.equal(navLabels[1], '聊天助手');
    const productNav = page.getByRole('navigation', { name: '产品分区' });
    assert.equal(await productNav.getByRole('link').count(), 3);
    await productNav.getByRole('link', { name: '社媒情报', exact: true }).click();
    await page.locator('#list-page .page-heading').waitFor();
    assert.equal(new URL(page.url()).hash, '#inbox');
    assert.equal(await productNav.locator('[aria-current=page]').textContent(), '社媒情报');
    await page.screenshot({ path: path.join(OUTPUT, 'workspace-inbox-desktop.png'), fullPage: true });
    await page.locator('#workspace-nav').getByRole('link', { name: '工作台', exact: true }).click();
    await page.locator('#workspace-overview').waitFor();
    assert.equal(await productNav.locator('[aria-current=page]').count(), 0, 'Home must not impersonate a product section');
    assert.deepEqual(writes, [], 'Switching product sections must not submit business operations');
    await page.screenshot({ path: path.join(OUTPUT, 'workspace-home-desktop.png'), fullPage: true });
    await page.locator('#workspace-nav').getByRole('link', { name: '聊天助手', exact: true }).click();
    await page.locator('#assistant-input:not(:disabled)').waitFor();
    assert.equal(await page.locator('#workspace-nav a[aria-current=page]').textContent(), '聊天助手');
    assert.equal(await page.locator('#assistant-company').textContent(), '通用聊天');
    assert.equal(new URL(page.url()).hash, '#assistant');
    assert.equal(await page.locator('#assistant-entry-search').count(), 0);
    assert(queries.some(url => url.includes('conversation_scope=general')));
    assert.equal(queries.some(url => url.includes('page_size=6')), false);
    await page.locator('[data-assistant-prompt]').first().click();
    assert.match(await page.locator('#assistant-input').inputValue(), /商务邮件/);
    await page.screenshot({ path: path.join(OUTPUT, 'assistant-entry-desktop.png'), fullPage: true });
    await page.reload();
    await page.locator('#assistant-input:not(:disabled)').waitFor();
    assert.equal(await page.locator('#assistant-company').textContent(), '通用聊天');
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'Chat entry mobile overflow');
    assert.equal(await page.locator('#workspace').evaluate(node => node.inert), false);
    assert.equal(await page.locator('#assistant-close').isVisible(), false);
    await page.screenshot({ path: path.join(OUTPUT, 'assistant-entry-mobile.png'), fullPage: true });
    await page.goto(base + '/#assistant/unavailable');
    await page.locator('#assistant-entry-error').filter({ hasText: '客户不存在或无权访问' }).waitFor();
    assert.equal(await page.locator('#assistant-panel').isVisible(), false);
    assert.deepEqual(writes, [], 'Opening chat must not submit an analysis or a question');
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByRole('link', { name: '工作台', exact: true }).click();
    await page.locator('.company-row').click();
    await page.locator('#workspace-context strong').filter({ hasText: company.name }).waitFor();
    await page.locator('.workspace-customer-actions a').filter({ hasText: '创建报价' }).click();
    await page.locator('#record-form').waitFor();
    assert.equal(await page.locator('#record-form select[name=company]').inputValue(), company.id);
    assert.equal(await page.locator('#company-filter').inputValue(), company.id);
    assert.equal(new URL(page.url()).searchParams.get('company'), company.id);
    assert.equal(new URL(page.url()).searchParams.has('create'), false, 'Consumed form request remains in URL');
    assert.equal(await page.locator('#workspace-nav').getByRole('link', { name: '聊天助手', exact: true }).getAttribute('href'), '/#assistant');
    assert.deepEqual(await page.locator('#workspace-nav a').allTextContents(), navLabels);
    await page.screenshot({ path: path.join(OUTPUT, 'workspace-quote-prefill.png'), fullPage: true });
    await page.locator('#close-editor').click();
    await page.locator('#workspace-context a').filter({ hasText: /^跟进$/ }).click();
    await page.locator('#page-title').filter({ hasText: '跟进' }).waitFor();
    await page.reload();
    await page.locator('#workspace-context strong').waitFor();
    assert.equal(await page.locator('#company-filter').inputValue(), company.id);
    await page.locator('#create-business').click();
    await page.locator('#record-form').waitFor();
    assert.equal(await page.locator('#record-form select[name=company]').inputValue(), company.id);
    await page.locator('#close-editor').click();
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'Business mobile overflow');
    await page.screenshot({ path: path.join(OUTPUT, 'workspace-customer-mobile.png'), fullPage: true });
    await page.locator('#workspace-nav').getByRole('link', { name: '工作台', exact: true }).click();
    await page.locator('#workspace-tasks .workspace-task').first().waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'Home mobile overflow');
    await page.screenshot({ path: path.join(OUTPUT, 'workspace-home-mobile.png'), fullPage: true });
    await page.locator('.workspace-task').filter({ hasText: '待复核邮件' }).click();
    await page.locator('#review-dialog[open]').waitFor();
    await page.locator('#review-dialog .close-dialog').click();
    await page.locator('#workspace-status a').filter({ hasText: '待复核邮件' }).click();
    await page.locator('#review-dialog[open]').waitFor();
    await page.locator('#review-dialog .close-dialog').click();
    await page.goto(base);
    await page.locator('.workspace-task').filter({ hasText: '待跟进' }).click();
    await page.locator('#status-filter').waitFor();
    assert.equal(await page.locator('#status-filter').inputValue(), 'open');
    assert(queries.some(url => url.includes('/sales/records/follow-ups/?') && url.includes('status=open')));
    await page.goto(base + '/business/?company=unavailable#quotes');
    await page.locator('#business-notice').filter({ hasText: '无权访问' }).waitFor();
    failActions = true;
    await page.goto(base);
    await page.locator('#workspace-load-error').filter({ hasText: '模拟动作服务不可用' }).waitFor();
    assert.match(await page.locator('.workspace-task').filter({ hasText: '待确认动作' }).textContent(), /暂不可用/);
    assert.deepEqual(errors, []);
    assert.deepEqual(writes, ['companies/company-a/analyze/']);
    console.log('Workspace browser checks passed: general chat without customer selection, refresh, shortcuts, mobile navigation, read-only opening, shared navigation, totals, customer handoff, form prefill, refresh, filters, repeated review, inaccessible customer, error state, desktop/mobile.');
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => { console.error(error.stack); process.exitCode = 1; });
