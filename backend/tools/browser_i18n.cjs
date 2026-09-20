/**
 * 职责：验证真实页面的英文界面、浏览器语言协商、偏好保存和内容隔离。
 * 实现：隔离静态服务器与 GET 夹具，使用真实 Chrome 加载三个入口；业务写入一律拒绝。
 * 关联：i18n.js/translations.js、页面语言控件、业务表单和跨页面悬浮聊天；后端语言由 test_i18n.py 独立验证。
 * 目录：main 执行浏览器验收；main.serve 提供受限静态资源。
 * 变量索引：FRONTEND 为页面根，OUTPUT 为忽略的截图目录；其他状态限于 main。
 */
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.SALESMATE_PLAYWRIGHT_MODULE);
const FRONTEND = path.resolve(__dirname, '../frontend');
const OUTPUT = path.resolve(__dirname, '../artifacts/i18n');

/** 功能：执行语言与业务隔离场景。输入：环境中的 Playwright/Chrome 路径。
 * 输出：断言、英文桌面/移动截图。逻辑：覆盖自动语言、手动覆盖、取消草稿丢弃、跨页及刷新、动态文案与字段值，并验证世界消息中的悬浮入口。
 * 约束：所有 API 均模拟，不调用邮箱或模型，不将模拟通过解释为外部服务已验证。 */
async function main() {
  /** 功能：提供实际页面和本地资源。输入：req/res。输出：HTTP 静态响应。
   * 逻辑：固定页面路由，静态路径限制在 assets 下。约束：不暴露仓库其他文件。 */
  function serve(req, res) {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    const pages = { '/': 'index.html', '/business/': 'business.html', '/world/': 'world.html' };
    let filename = pages[pathname] ? path.join(FRONTEND, pages[pathname]) : null;
    if (pathname.startsWith('/static/')) {
      const candidate = path.resolve(FRONTEND, 'assets', pathname.slice(8));
      const relative = path.relative(path.join(FRONTEND, 'assets'), candidate);
      if (!relative.startsWith('..') && !path.isAbsolute(relative)) filename = candidate;
    }
    if (!filename || !fs.existsSync(filename) || !fs.statSync(filename).isFile()) { res.writeHead(404); res.end(); return; }
    const mime = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.geojson': 'application/json' };
    res.setHeader('Content-Type', (mime[path.extname(filename)] || 'application/octet-stream') + '; charset=utf-8');
    res.end(fs.readFileSync(filename));
  }
  const server = http.createServer(serve);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ executablePath: process.env.SALESMATE_BROWSER_PATH, headless: true });
  try {
    const context = await browser.newContext({ locale: 'en-US', viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage(), errors = [], requests = [];
    let authenticated = false;
    const company = { id: 'company-a', name: '客户', domains: ['a.example'], contacts: [], archived: false, crm_status: 'registered', customer: { industry_from_crm: '半导体检测' }, revision: 1 };
    const row = { company_id: company.id, company_name: company.name, domains: company.domains, contacts: [], crm_status: 'registered', industry: '半导体检测', size_band: 'unknown', signal: 'inquiry_intent', score: null, provider: 'agent', email_count: 1, headline_summary: '保存草稿', last_message_at: '2026-09-13T01:00:00Z' };
    const resources = ['quotes', 'orders', 'follow-ups', 'actions', 'notifications'].map(key => ({ key, model: key, label: { quotes: '报价', orders: '订单', 'follow-ups': '跟进', actions: '动作', notifications: '通知' }[key], transitions: {}, fields: [{ name: 'company', type: 'relation', relation: 'company', required: true }, { name: 'title', type: 'text' }, { name: 'status', type: 'choice', readonly: true, choices: ['open', 'completed', 'pending_confirmation', 'draft'] }] }));
    page.on('pageerror', error => errors.push(error.message));
    await context.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url());
      if (url.origin !== base) return route.abort();
      if (!url.pathname.startsWith('/api/v1/')) return route.continue();
      assert.equal(req.method(), 'GET', 'Language testing must not write business records');
      requests.push({ path: url.pathname, language: req.headers()['accept-language'] });
      const endpoint = url.pathname.slice('/api/v1/'.length);
      let data;
      if (endpoint === 'session/') data = { authenticated, username: '用户名', debug_auto_login: false };
      else if (endpoint === 'accounts/me/') data = { username: '用户名' };
      else if (endpoint === 'demo/runtime/') data = { provider: 'agent', timezone: 'Asia/Shanghai', qq_enabled: false };
      else if (endpoint === 'mailboxes/') data = [];
      else if (endpoint === 'email-reviews/') data = { pending_count: 3, count: 0, results: [], page: 1, page_size: 20 };
      else if (endpoint === 'companies/') data = { results: [row], count: 1, stats: { companies: 1, unregistered: 0, new_emails_today: 1 } };
      else if (endpoint === 'sales/overview/') data = { customers: 1, open_tickets: 2, open_follow_ups: 7, unread_notifications: 2, confirmed_order_net: {}, open_opportunity_amount: {} };
      else if (endpoint === 'sales/catalog/') data = { resources };
      else if (endpoint === 'sales/directory/') data = { results: [company], count: 1 };
      else if (endpoint.startsWith('sales/records/') || endpoint === 'sales/chat/requests/') data = { results: [], count: 0 };
      else throw new Error('Unexpected API: ' + endpoint);
      await route.fulfill({ json: data });
    });
    fs.mkdirSync(OUTPUT, { recursive: true });
    await page.goto(base);
    await page.getByRole('button', { name: /^Sign in/ }).waitFor();
    assert.equal(await page.locator('html').getAttribute('lang'), 'en');
    assert.equal(await page.locator('#interface-language').inputValue(), 'auto');
    await page.locator('#login-form input[name=username]').fill('不要翻译');
    page.once('dialog', dialog => dialog.dismiss());
    await page.locator('#interface-language').selectOption('zh-hans');
    assert.equal(await page.locator('#login-form input[name=username]').inputValue(), '不要翻译');
    assert.equal(await page.locator('#interface-language').inputValue(), 'auto');
    page.once('dialog', dialog => dialog.accept());
    await page.locator('#interface-language').selectOption('zh-hans');
    await page.getByRole('button', { name: '登录工作台' }).waitFor();
    assert.equal(await page.locator('html').getAttribute('lang'), 'zh-hans');
    await page.reload();
    await page.getByRole('button', { name: '登录工作台' }).waitFor();
    await page.locator('#interface-language').selectOption('auto');
    await page.getByRole('button', { name: /^Sign in/ }).waitFor();
    assert.ok(!(await context.cookies()).some(cookie => cookie.name === 'django_language'));
    authenticated = true;
    await page.goto(base + '/?authenticated-preview#inbox');
    await page.locator('#company-list h3').waitFor();
    await page.locator('#interface-language').click({ trial: true });
    assert.equal(await page.locator('#company-list h3').textContent(), '客户');
    assert.ok((await page.locator('#company-list').textContent()).includes('保存草稿'), 'Stored summary is not translated even when it equals a UI key');
    assert.ok((await page.locator('#company-list').textContent()).includes('Semiconductor inspection'));
    await page.locator('#filters select[name=industry]').selectOption({ label: 'Semiconductor inspection' });
    assert.equal(await page.locator('#filters select[name=industry]').inputValue(), '半导体检测');
    await page.screenshot({ path: path.join(OUTPUT, 'inbox-en.png'), fullPage: true });
    const result = await page.evaluate(async () => {
      const { t, h, resolveLanguage } = await import('/static/i18n.js?v=20260920-i18n');
      return { html: h`<button>保存</button><p>${'保存'}</p>`, text: t`当前员工：${'用户名'}`, languages: [resolveLanguage('auto', ['zh-TW', 'en']), resolveLanguage('auto', ['fr', 'en-GB']), resolveLanguage('auto', ['fr']), resolveLanguage('en', ['zh-CN'])] };
    });
    assert.equal(result.html, '<button>Save</button><p>保存</p>');
    assert.equal(result.text, 'Current employee: 用户名');
    assert.deepEqual(result.languages, ['zh-hans', 'en', 'zh-hans', 'en']);
    await page.goto(base + '/business/?company=company-a#follow-ups');
    await page.getByRole('heading', { name: 'Follow-ups', exact: true }).first().waitFor();
    await page.locator('#create-business').click();
    await page.getByRole('heading', { name: 'New Follow-ups' }).waitFor();
    assert.equal(await page.locator('#record-form select[name=company]').inputValue(), company.id);
    assert.equal(await page.locator('#record-form select[name=company] option:checked').textContent(), company.name);
    await page.screenshot({ path: path.join(OUTPUT, 'business-en.png'), fullPage: true });
    await page.locator('#close-editor').click();
    await page.goto(base + '/#assistant');
    await page.getByRole('heading', { name: 'What would you like to discuss?' }).waitFor();
    await page.locator('[data-assistant-prompt]').first().click();
    const draftBeforeSwitch = await page.locator('#assistant-input').inputValue();
    page.once('dialog', dialog => dialog.dismiss());
    await page.locator('#interface-language').selectOption('zh-hans');
    assert.equal(await page.locator('#assistant-input').inputValue(), draftBeforeSwitch);
    assert.equal(await page.locator('#interface-language').inputValue(), 'auto');
    await page.screenshot({ path: path.join(OUTPUT, 'chat-en.png'), fullPage: true });
    await page.goto(base + '/world/');
    await page.locator('#map-loading').waitFor({ state: 'hidden' });
    assert.equal(await page.locator('#world-error').isVisible(), false);
    await page.locator('#assistant-launcher').click();
    await page.locator('#assistant-input:not(:disabled)').waitFor();
    assert.equal(new URL(page.url()).pathname, '/world/');
    assert.equal(await page.locator('#assistant-company').textContent(), 'General chat');
    await page.locator('#assistant-close').click();
    await page.getByRole('button', { name: 'All industries', exact: true }).waitFor();
    await page.screenshot({ path: path.join(OUTPUT, 'world-en.png'), fullPage: true });
    await page.locator('#interface-language').selectOption('en');
    await page.locator('#map-loading').waitFor({ state: 'hidden' });
    await page.goto(base + '/business/');
    assert.equal(await page.locator('#interface-language').inputValue(), 'en');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(base + '/#inbox');
    await page.locator('#company-list h3').waitFor();
    await page.locator('#interface-language').click({ trial: true });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'English mobile inbox must fit the viewport');
    await page.screenshot({ path: path.join(OUTPUT, 'mobile-en.png'), fullPage: true });
    assert.ok(requests.some(req => req.language === 'en'));
    assert.ok(requests.some(req => req.language === 'zh-hans'));
    assert.deepEqual(errors, []);
    console.log('I18n browser checks passed: English pages, automatic/manual languages, reload/cross-page persistence, dirty-input cancellation, raw business content, enum values, API headers, mobile layout.');
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
