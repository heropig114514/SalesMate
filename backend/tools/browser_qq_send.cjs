/**
 * 职责：验证 QQ 发信连接表单、服务筛选及发送前预览。
 * 国际化前提：浏览器固定 zh-CN，使既有中文交互断言不依赖运行机器语言。
 * 实现：先验证 QQ 关闭时入口、动作及连接筛选均隐藏，再启用原场景；真实业务页面运行于隔离静态服务，全部 API 被模拟，任何非预期写入失败。
 * 关联：business.js 与 sales-api.js；使用显式 Playwright 模块和浏览器路径。
 * 目录：main 执行连接和待确认邮件场景。
 * 变量索引：FRONTEND 为页面目录；OUTPUT 为忽略的截图目录。
 */
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.SALESMATE_PLAYWRIGHT_MODULE);
const FRONTEND = path.resolve(__dirname, '../frontend');
const OUTPUT = path.resolve(__dirname, '../artifacts/browser');

/** 功能：执行独立 QQ 发信 UI 验收。输入：环境中的 Playwright/浏览器路径。
 * 输出：检查结果和预览截图。逻辑：先验证关闭不产生写入，再验证授权码清除、按服务筛选连接及完整冻结预览。
 * 约束：模拟连接与准备请求，不点击发送确认；所有外部网络禁止。 */
async function main() {
  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    const filename = pathname === '/business/' ? path.join(FRONTEND, 'business.html') : /^\/static\/[\w.-]+$/.test(pathname) ? path.join(FRONTEND, 'assets', path.basename(pathname)) : null;
    if (!filename || !fs.existsSync(filename)) { res.writeHead(404); res.end(); return; }
    res.setHeader('Content-Type', filename.endsWith('.js') ? 'text/javascript' : filename.endsWith('.css') ? 'text/css' : 'text/html');
    res.end(fs.readFileSync(filename));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: process.env.SALESMATE_BROWSER_PATH, headless: true });
  try {
    const page = await browser.newPage({ locale: 'zh-CN', viewport: { width: 1440, height: 1000 } });
    const errors = [], writes = [];
    let qqEnabled = false;
    page.on('pageerror', error => errors.push(error.message));
    const company = { id: 'company-a', name: '测试客户', archived: false, revision: 1 };
    const connections = [{ id: 'qq-a', provider: 'qq', account: 'sender@qq.com' }, { id: 'gmail-a', provider: 'gmail', account: 'sender@gmail.com' }, { id: 'calendar-a', provider: 'calendar', account: 'calendar@gmail.com' }];
    const resources = ['connections', 'actions'].map(key => ({ key, label: key === 'connections' ? '外部连接' : '外部动作', transitions: {}, fields: [{ name: 'company', type: 'relation', relation: 'company' }, { name: 'title', type: 'text' }] }));
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url());
      if (url.hostname !== '127.0.0.1') return route.abort();
      if (!url.pathname.startsWith('/api/v1/')) return route.continue();
      const endpoint = url.pathname.slice('/api/v1/'.length);
      if (req.method() !== 'GET') {
        writes.push(endpoint);
        const data = req.postDataJSON();
        if (endpoint === 'sales/connections/qq/') {
          assert.deepEqual(data, { address: 'sender@qq.com', authorization_code: 'abcdefghijklmnop' });
          return route.fulfill({ status: 201, json: connections[0] });
        }
        assert.equal(endpoint, 'sales/records/actions/', 'Unexpected mutation or implicit approval');
        assert.equal(data.tool, 'qq.send');
        assert.deepEqual(data.parameters, { connection_id: 'qq-a', draft_id: 'draft-a' });
        return route.fulfill({ status: 201, json: { id: 'action-a', company: company.id, tool: 'qq.send', status: 'pending_confirmation', revision: 0, parameters: { account: 'sender@qq.com', to: ['buyer@example.com'], subject: 'QQ 报价确认', body: '第一行\n第二行' } } });
      }
      let data;
      if (endpoint === 'session/') data = { authenticated: true, username: '测试销售' };
      else if (endpoint === 'demo/runtime/') data = { qq_enabled: qqEnabled };
      else if (endpoint === 'accounts/me/') data = { username: '测试销售' };
      else if (endpoint === 'mailboxes/') data = [];
      else if (endpoint === 'sales/catalog/') data = { resources };
      else if (endpoint === 'sales/directory/') data = { results: [company], count: 1 };
      else if (endpoint === 'sales/overview/') data = { customers: 1, open_tickets: 0, open_follow_ups: 0, unread_notifications: 0, confirmed_order_net: {}, open_opportunity_amount: {} };
      else if (endpoint === 'email-reviews/') data = { pending_count: 0, results: [], count: 0 };
      else if (endpoint === 'sales/records/connections/') data = { results: connections, count: connections.length };
      else if (endpoint === 'sales/records/drafts/') data = { results: [{ id: 'draft-a', kind: 'email', subject: 'QQ 报价确认' }], count: 1 };
      else if (endpoint.startsWith('sales/records/')) data = { results: [], count: 0 };
      else throw new Error('Unexpected API: ' + endpoint);
      return route.fulfill({ json: data });
    });
    const base = `http://127.0.0.1:${server.address().port}`;
    await page.goto(base + '/business/#connections');
    await page.locator('#page-title').filter({ hasText: '外部连接' }).waitFor();
    await page.locator('#create-business').click();
    assert.equal(await page.locator('#connect-qq-send').isVisible(), false);
    await page.goto(base + '/business/#actions');
    await page.locator('#page-title').filter({ hasText: '外部动作' }).waitFor();
    await page.locator('#create-business').click();
    assert.equal(await page.locator('#action-form [name=tool] option[value="qq.send"]').count(), 0);
    assert.equal(await page.locator('#action-form [name=connection_id] option[value="qq-a"]').count(), 0);
    assert.equal(writes.length, 0);
    qqEnabled = true;
    await page.goto(base + '/business/#connections');
    await page.reload();
    await page.locator('#page-title').filter({ hasText: '外部连接' }).waitFor();
    await page.locator('#create-business').click();
    await page.locator('#connect-qq-send').click();
    await page.locator('#qq-send-form [name=address]').fill('sender@qq.com');
    await page.locator('#qq-send-form [name=authorization_code]').fill('abcdefghijklmnop');
    await page.locator('#qq-send-form button').click();
    await page.locator('#editor').waitFor({ state: 'hidden' });
    assert.equal(await page.locator('#qq-send-form [name=authorization_code]').inputValue(), '');
    await page.goto(base + '/business/#actions');
    await page.locator('#page-title').filter({ hasText: '外部动作' }).waitFor();
    await page.locator('#create-business').click();
    const form = page.locator('#action-form');
    await form.locator('[name=tool]').selectOption('qq.send');
    assert.deepEqual(await form.locator('[name=connection_id] option').evaluateAll(options => options.map(option => option.value).filter(Boolean)), ['qq-a']);
    assert.equal(await page.locator('#email-fields').isVisible(), true);
    assert.equal(await page.locator('#calendar-fields').isVisible(), false);
    await form.locator('[name=tool]').selectOption('gmail.send');
    assert.deepEqual(await form.locator('[name=connection_id] option').evaluateAll(options => options.map(option => option.value).filter(Boolean)), ['gmail-a']);
    await form.locator('[name=tool]').selectOption('qq.send');
    await form.locator('[name=company]').selectOption(company.id);
    await form.locator('[name=connection_id]').selectOption('qq-a');
    await form.locator('[name=draft_id]').selectOption('draft-a');
    await form.locator('button').click();
    await page.locator('#approve-action').waitFor();
    assert.match(await page.locator('#editor-body').textContent(), /sender@qq.com/);
    assert.match(await page.locator('#editor-body').textContent(), /buyer@example.com/);
    assert.equal(await page.locator('#editor-body .preview').textContent(), '第一行\n第二行');
    assert.deepEqual(writes, ['sales/connections/qq/', 'sales/records/actions/']);
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    fs.mkdirSync(OUTPUT, { recursive: true });
    await page.screenshot({ path: path.join(OUTPUT, 'qq-send-preview.png'), fullPage: true });
    assert.deepEqual(errors, []);
    console.log('QQ send UI passed: connection, credential clearing, provider filter, frozen preview, no implicit approval.');
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
