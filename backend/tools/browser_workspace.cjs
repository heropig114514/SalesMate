/**
 * 职责：验证产品顶栏、主导航、底部 Profile 和无自动授权的邮箱设置、可收起底部聊天条、精简首页、邮箱设置中的复核入口、真实总数展示、跨页客户上下文和表单预填。
 * 国际化前提：浏览器固定 zh-CN，使既有中文交互断言不依赖运行机器语言。
 * 实现：验证所有页面仅打开工作空间会话；真实 HTML/JS 使用隔离静态服务器，全部 API 模拟；检查刷新、筛选、失败、移动布局；视口变化后等待媒体查询监听器完成状态更新。
 * 关联：聊天 Markdown 模块依赖使用统一缓存版本；0919 界面及共享语言资源统一缓存版本；product-header.js、workspace.js、app.js、assistant-widget.js、business.js；需显式 Playwright 模块和 Chrome 路径。
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

/** 功能：执行独立浏览器契约验收。输入：运行环境中的 Playwright/Chrome 路径。输出：检查结果及截图；手机焦点断言等待背景 inert 就绪。
 * 逻辑：产品分区切换、底部条状布局、导航层级、浮窗开关、草稿保留、旧链接和移动端焦点不产生写入；A 公司详情跳转报价、跟进并刷新；额外检验空邮箱设置、重复导航、刷新不授权及公司设置及引导读取、持久化、冲突保留、重读确认、双语、未知客户、客户页面不预选聊天公司与失败。
 * 约束：所有业务请求均拦截；仅允许原有客户分析模拟 POST 及显式公司资料 PATCH，禁止其余写入和外部网络。 */
async function main() {
  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    const filename = pathname === '/' ? path.join(FRONTEND, 'index.html') : pathname === '/settings/company/' ? path.join(FRONTEND, 'company-settings.html') : pathname === '/business/' ? path.join(FRONTEND, 'business.html') : /^\/static\/[\w.-]+$/.test(pathname) ? path.join(FRONTEND, 'assets', path.basename(pathname)) : null;
    if (!filename || !fs.existsSync(filename)) { res.writeHead(404); res.end(); return; }
    res.setHeader('Content-Type', filename.endsWith('.js') ? 'text/javascript' : filename.endsWith('.css') ? 'text/css' : 'text/html');
    res.end(fs.readFileSync(filename));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: process.env.SALESMATE_BROWSER_PATH, headless: true });
  try {
    const page = await browser.newPage({ locale: 'zh-CN', viewport: { width: 1440, height: 1000 } });
    const errors = [], writes = [], queries = [];
    page.on('pageerror', error => errors.push(error.message));
    let failOverview = false, profileFailure = false, profileConflict = false;
    let profile = { company_name: '', industry: '', website: '', email: '', phone: '', address: '', description: '', revision: 0 };
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
        if (endpoint === 'accounts/company-profile/') {
          assert.equal(req.method(), 'PATCH');
          assert.equal(req.headers()['if-match'], String(profile.revision));
          if (profileConflict) return route.fulfill({ status: 409, json: { error: { detail: 'Version conflict' } } });
          profile = { ...profile, ...req.postDataJSON(), revision: profile.revision + 1 };
          return route.fulfill({ json: profile });
        }
        assert.equal(endpoint, 'companies/company-a/analyze/', 'Unexpected business mutation');
        return route.fulfill({ json: {} });
      }
      let data;
      if (endpoint === 'session/') data = { authenticated: true, username: '测试销售', debug_auto_login: true };
      else if (endpoint === 'accounts/company-profile/') {
        if (profileFailure) return route.fulfill({ status: 503, json: { error: { detail: '公司资料读取失败' } } });
        data = profile;
      }
      else if (endpoint === 'accounts/onboarding/') data = { personal: {}, products: [], solutions: [], completed: true, revision: 0, documents: [] };
      else if (endpoint === 'accounts/me/') data = { username: '测试销售' };
      else if (endpoint === 'demo/runtime/') data = { provider: 'agent', timezone: 'Asia/Shanghai' };
      else if (endpoint === 'mailboxes/') data = [];
      else if (endpoint === 'email-reviews/') data = { pending_count: 3, count: 0, results: [], page: 1, page_size: 20 };
      else if (endpoint === 'companies/') data = { results: url.searchParams.get('q') === '不存在' ? [] : [row], count: url.searchParams.get('q') === '不存在' ? 0 : 1, stats: { companies: 1, unregistered: 0, new_emails_today: 1 } };
      else if (endpoint === 'companies/company-a/') data = { ...row, analysis: null, score_reasons: [], context: { emails: [], tickets: [], quotes: [], orders: [] } };
      else if (endpoint === 'companies/unavailable/') return route.fulfill({ status: 404, json: { error: { detail: '客户不存在或无权访问' } } });
      else if (endpoint === 'sales/overview/' && failOverview) return route.fulfill({ status: 503, json: { error: { detail: '模拟业务概览不可用' } } });
      else if (endpoint === 'sales/overview/') data = { customers: 1, open_tickets: 2, open_follow_ups: 7, unread_notifications: 2, confirmed_order_net: {}, open_opportunity_amount: {} };
      else if (endpoint === 'sales/catalog/') data = { resources };
      else if (endpoint === 'sales/directory/') data = { results: [company], count: 1 };
      else if (endpoint.startsWith('sales/records/')) {

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
    assert.deepEqual(navLabels, ['工作台', 'Global Insights', 'Channels', '客户', '商机', '报价', '订单', '工单']);
    assert.equal(await page.locator('#workspace-nav .workspace-customer-nav a').count(), 4);
    assert.deepEqual(await page.locator('#workspace-profile a').allTextContents(), ['Company Setting', 'Emails Connections']);
    assert.equal(await page.locator('#workspace-nav a[href="/world/"]').textContent(), 'Global Insights');
    const profileBox = await page.locator('#workspace-profile').boundingBox();
    assert(profileBox.y > 650, 'Profile should stay near the sidebar bottom');
    assert.equal(await page.locator('#assistant-launcher').isVisible(), true);
    assert.equal(await page.locator('#assistant-panel').isVisible(), false);
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
    const homeURL = page.url();
    const mainBefore = await page.locator('main').boundingBox();
    await page.locator('#assistant-launcher').click();
    await page.locator('#assistant-input:not(:disabled)').waitFor();
    assert.equal(await page.locator('#workspace-nav a[aria-current=page]').textContent(), '工作台');
    assert.equal(await page.locator('#assistant-company').textContent(), '通用聊天');
    assert.equal(page.url(), homeURL, 'Opening floating chat must preserve page URL');
    assert.deepEqual(await page.locator('main').boundingBox(), mainBefore, 'Floating chat must not resize the page');
    assert.equal(await page.locator('#assistant-entry-page').count(), 0);
    assert(queries.some(url => url.includes('conversation_scope=general')));
    await page.locator('[data-assistant-prompt]').first().click();
    const draft = await page.locator('#assistant-input').inputValue();
    assert.match(draft, /商务邮件/);
    const chatBox = await page.locator('#assistant-panel').boundingBox();
    assert(chatBox.width > chatBox.height * 2.5, 'Desktop chat must form a horizontal strip');
    assert(chatBox.height <= 460 && chatBox.y > 500, 'Chat must open near the bottom without occupying the full page');
    assert(Math.abs(chatBox.y + chatBox.height - 984) < 2, 'Chat must remain anchored to the bottom');
    await page.screenshot({ path: path.join(OUTPUT, 'assistant-bottom-desktop.png'), fullPage: true, animations: 'disabled' });
    await page.locator('#assistant-close').click();
    assert.equal(await page.locator('#assistant-panel').isVisible(), false);
    assert.equal(await page.locator('#assistant-launcher').getAttribute('aria-expanded'), 'false');
    await page.locator('#assistant-launcher').click();
    await page.locator('#assistant-input:not(:disabled)').waitFor();
    assert.equal(await page.locator('#assistant-input').inputValue(), draft);
    await page.locator('#workspace-nav').getByRole('link', { name: 'Channels', exact: true }).click();
    await page.waitForURL('**/#inbox');
    assert.equal(await page.locator('#assistant-input').inputValue(), draft);
    assert.equal(await page.locator('#assistant-panel').isVisible(), true);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#assistant-launcher').evaluate(node => node === document.activeElement), true);
    await page.goto(base + '/#assistant');
    await page.locator('#assistant-input:not(:disabled)').waitFor();
    assert.equal(new URL(page.url()).hash, '#home');
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'Floating chat mobile overflow');
    // 视口变更和媒体查询 change 回调分开调度，先等待实际状态，不依赖机器执行速度。
    await page.waitForFunction(() => document.getElementById('workspace').inert);
    assert.equal(await page.locator('#workspace').evaluate(node => node.inert), true);
    assert.equal(await page.locator('#assistant-close').isVisible(), true);
    const mobileChat = await page.locator('#assistant-panel').boundingBox();
    assert(mobileChat.y > 240 && mobileChat.height <= 520, 'Mobile chat must open from the bottom instead of covering the full screen');
    await page.locator('#assistant-input').fill('手机端未保存草稿');
    await page.locator('#assistant-close').focus();
    await page.keyboard.press('Shift+Tab');
    assert.equal(await page.locator('#assistant-panel').evaluate(node => node.contains(document.activeElement)), true);
    await page.screenshot({ path: path.join(OUTPUT, 'assistant-bottom-mobile.png'), fullPage: true, animations: 'disabled' });
    await page.locator('#assistant-close').click();
    assert.equal(await page.locator('#workspace').evaluate(node => node.inert), false);
    await page.locator('#assistant-launcher').click();
    await page.locator('#assistant-input:not(:disabled)').waitFor();
    assert.equal(await page.locator('#assistant-input').inputValue(), '手机端未保存草稿');
    await page.keyboard.press('Escape');
    await page.goto(base + '/#assistant/unavailable');
    await page.locator('#assistant-input:not(:disabled)').waitFor();
    assert.equal(await page.locator('#assistant-company').textContent(), '通用聊天');
    await page.locator('#assistant-close').click();
    assert.deepEqual(writes, [], 'Opening chat must not submit an analysis or a question');
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.locator('#workspace-nav').getByRole('link', { name: '工作台', exact: true }).click();
    await page.locator('.company-row').click();
    await page.locator('.detail-identity h1').filter({ hasText: company.name }).waitFor();
    assert.equal(await page.locator('#workspace-context').isVisible(), false);
    assert.equal(await page.locator('#assistant-toggle').count(), 0);
    await page.locator('#assistant-launcher').click();
    await page.locator('#assistant-input:not(:disabled)').waitFor();
    assert.equal(await page.locator('#assistant-general').count(), 0);
    assert.equal(await page.locator('#assistant-company').textContent(), '通用聊天');
    await page.locator('#assistant-close').click();
    await page.locator('.channel-mail-actions a').filter({ hasText: '创建报价' }).click();
    await page.locator('#record-form').waitFor();
    assert.equal(await page.locator('#record-form select[name=company]').inputValue(), company.id);
    assert.equal(await page.locator('#company-filter').inputValue(), company.id);
    assert.equal(new URL(page.url()).searchParams.get('company'), company.id);
    assert.equal(new URL(page.url()).searchParams.has('create'), false, 'Consumed form request remains in URL');
    assert.deepEqual(await page.locator('#workspace-nav a').allTextContents(), navLabels);
    await page.screenshot({ path: path.join(OUTPUT, 'workspace-quote-prefill.png'), fullPage: true });
    await page.locator('#close-editor').click();
    await page.locator('#assistant-launcher').click();
    await page.locator('#assistant-input:not(:disabled)').waitFor();
    assert.equal(await page.locator('#assistant-company').textContent(), '通用聊天');
    assert.equal(await page.locator('#assistant-panel').count(), 1);
    await page.screenshot({ path: path.join(OUTPUT, 'assistant-bottom-business.png'), fullPage: true, animations: 'disabled' });
    await page.locator('#assistant-close').click();
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
    await page.goto(base + '/#gmail');
    await page.locator('#email-reviews-open').click();
    await page.locator('#review-dialog[open]').waitFor();
    await page.locator('#review-dialog .close-dialog').click();
    await page.goto(base);
    await page.locator('.workspace-task').filter({ hasText: '待跟进' }).click();
    await page.locator('#status-filter').waitFor();
    assert.equal(await page.locator('#status-filter').inputValue(), 'open');
    assert(queries.some(url => url.includes('/sales/records/follow-ups/?') && url.includes('status=open')));
    await page.goto(base + '/business/?company=unavailable#quotes');
    await page.locator('#business-notice').filter({ hasText: '无权访问' }).waitFor();
    await page.goto(base);
    await page.locator('#workspace-profile a').filter({ hasText: 'Emails Connections' }).click();
    await page.locator('#email-settings-page').waitFor();
    assert.equal(await page.locator('#workspace-profile a[aria-current=page]').textContent(), 'Emails Connections');
    assert.equal(await page.locator('#gmail-dialog').isVisible(), false);
    assert.equal(await page.locator('#list-page').isVisible(), false);
    await page.locator('#gmail-accounts').filter({ hasText: '尚未连接 Google 邮箱' }).waitFor();
    await page.locator('#workspace-profile a').filter({ hasText: 'Emails Connections' }).click();
    await page.locator('#email-settings-page').waitFor();
    assert.equal(await page.locator('#gmail-dialog').isVisible(), false);
    await page.reload();
    await page.locator('#email-settings-page').waitFor();
    assert.equal(await page.locator('#gmail-dialog').isVisible(), false);
    await page.locator('#gmail-add').click();
    await page.locator('#gmail-dialog[open]').waitFor();
    assert.equal(await page.locator('#gmail-authorization-account').isVisible(), false);
    await page.locator('#gmail-dialog .close-dialog').click();
    assert.deepEqual(writes, ['companies/company-a/analyze/'], 'Empty settings and cancelled authorization must remain read-only');
    await page.locator('#workspace-profile a').filter({ hasText: 'Company Setting' }).click();
    await page.locator('#company-save:not(:disabled)').waitFor();
    assert.equal(new URL(page.url()).search, '', 'Company settings must not carry customer context');
    assert.equal(await page.locator('#workspace-profile a[aria-current=page]').textContent(), 'Company Setting');
    await page.locator('[name=company_name]').fill('我们的公司 <Sales>');
    await page.locator('#company-form [name=email]').fill('sales@seller.example');
    await page.locator('#company-save').click();
    await page.locator('#company-status').filter({ hasText: '公司资料已保存' }).waitFor();
    await page.reload();
    await page.locator('#company-save:not(:disabled)').waitFor();
    assert.equal(await page.locator('[name=company_name]').inputValue(), '我们的公司 <Sales>');
    profileConflict = true;
    await page.locator('[name=company_name]').fill('未保存修改');
    await page.locator('#company-save').click();
    await page.locator('#company-status.is-error').filter({ hasText: '其他页面更新' }).waitFor();
    assert.equal(await page.locator('[name=company_name]').inputValue(), '未保存修改');
    page.once('dialog', dialog => dialog.dismiss());
    await page.locator('#company-reload').click();
    assert.equal(await page.locator('[name=company_name]').inputValue(), '未保存修改');
    page.once('dialog', dialog => dialog.accept());
    await page.locator('#company-reload').click();
    await page.locator('#company-status').filter({ hasText: '公司资料已载入' }).waitFor();
    assert.equal(await page.locator('[name=company_name]').inputValue(), '我们的公司 <Sales>');
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.screenshot({ path: path.join(OUTPUT, 'company-settings-desktop.png'), fullPage: true });
    await page.locator('#assistant-launcher').click();
    await page.locator('#assistant-input:not(:disabled)').waitFor();
    await page.locator('#assistant-close').click();
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'Settings mobile overflow');
    await page.screenshot({ path: path.join(OUTPUT, 'company-settings-mobile.png'), fullPage: true });
    profileFailure = true;
    await page.reload();
    await page.locator('#company-status.is-error').waitFor();
    assert.equal(await page.locator('#company-save').isDisabled(), true);
    profileFailure = false;
    await page.locator('#company-reload').click();
    await page.locator('#company-save:not(:disabled)').waitFor();
    await page.context().addCookies([{ name: 'django_language', value: 'en', url: base }]);
    await page.reload();
    await page.getByLabel('Company name', { exact: false }).waitFor();
    assert.equal(await page.locator('#company-save').textContent(), 'Save changes');
    await page.context().clearCookies();
    failOverview = true;
    await page.goto(base);
    await page.locator('#workspace-load-error').filter({ hasText: '模拟业务概览不可用' }).waitFor();
    assert.match(await page.locator('.workspace-task').filter({ hasText: '待跟进' }).textContent(), /暂不可用/);
    await page.evaluate(async () => {
      const { enableAssistant } = await import('/static/assistant-widget.js?v=20260921-markdown');
      enableAssistant(false);
    });
    assert.equal(await page.locator('#assistant-launcher').isVisible(), false);
    assert.equal(await page.locator('#assistant-panel').isVisible(), false);
    assert.equal(await page.locator('#assistant-input').inputValue(), '');
    assert.deepEqual(errors, []);
    assert.deepEqual(writes, ['companies/company-a/analyze/', 'accounts/company-profile/', 'accounts/company-profile/']);
    console.log('Workspace browser checks passed: bottom chat strip, nested navigation and Profile, company profile save/reload/conflict/languages, minimize/reopen, preserved draft/URL/layout, legacy links, mobile focus, read-only opening, shared navigation, totals, customer handoff, form prefill, refresh, filters, repeated review, inaccessible customer, error state, desktop/mobile.');
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => { console.error(error.stack); process.exitCode = 1; });
