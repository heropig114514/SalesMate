/**
 * 职责：验收账号清空按钮、错误恢复、缓存隔离与多标签页刷新。
 * 实现：真实浏览器加载工作空间导航和重置模块，模拟 HTTP 状态；缓存使用真实浏览器存储。
 * 关联：聊天 Markdown 模块依赖使用统一缓存版本；0919 界面及共享语言资源统一缓存版本；workspace.js、account-reset.js、account-cache.js、api.js；后端事务另由集成测试验证。
 * 目录：main 执行忙碌、部分失败、刷新恢复及成功广播场景。
 * 变量索引：FRONTEND 为实际静态模块目录；其余导入无业务状态。
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require(process.env.SALESMATE_PLAYWRIGHT_MODULE);
const FRONTEND = path.resolve(__dirname, '../frontend/assets');

/** 功能：执行真实浏览器中的清空交互和缓存验收。输入：环境中的 Playwright 模块与 Chrome 路径。
 * 输出：成功摘要或非零失败。逻辑：两页属于账号 7，第三页模拟账号 8；先忙碌、后附件失败，再刷新后继续。
 * 约束：全部业务 HTTP 都被拦截，不访问用户真实账号，不宣称模拟接口验证了生产删除。
 */
async function main() {
  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    if (/^\/static\/[\w.-]+$/.test(pathname)) {
      const filename = path.join(FRONTEND, path.basename(pathname));
      if (!fs.existsSync(filename)) { res.writeHead(404); res.end(); return; }
      res.setHeader('Content-Type', filename.endsWith('.js') ? 'text/javascript' : 'text/css');
      res.end(fs.readFileSync(filename));
      return;
    }
    res.setHeader('Content-Type', 'text/html');
    res.end(`<!doctype html><html><body><nav id="workspace-nav"></nav><nav id="workspace-profile"></nav><div id="workspace-context" hidden></div><script type="module">
      import { mountWorkspace } from '/static/workspace.js?v=20260921-markdown';
      import { request } from '/static/api.js?v=20260921-product';
      await request('accounts/me/'); mountWorkspace(); window.ready = true;
    </script></body></html>`);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: process.env.SALESMATE_BROWSER_PATH, headless: true });
  try {
    const origin = `http://127.0.0.1:${server.address().port}`;
    const context = await browser.newContext({ locale: 'zh-CN' });
    await context.addCookies([{ name: 'sessionid', value: 'synthetic-login', url: origin }, { name: 'csrftoken', value: 'synthetic-csrf', url: origin }]);
    const errors = [], keys = [];
    let stage = 'busy', generation = 0, cleaning = false;
    const pages = [];
    for (const owner of ['7', '7', '8']) {
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      page.on('dialog', dialog => dialog.accept());
      await page.route('**/api/v1/**', async route => {
        const req = route.request(), url = new URL(req.url());
        if (url.pathname.endsWith('/me/reset/')) {
          assert.equal(req.method(), 'POST');
          assert.equal(req.headers()['x-csrftoken'], 'synthetic-csrf');
          keys.push(req.headers()['idempotency-key']);
          if (stage === 'busy') return route.fulfill({ status: 409, json: { error: { detail: '工作仍在执行' } } });
          if (stage === 'files') {
            generation = 1; cleaning = true;
            return route.fulfill({ status: 503, json: { error: { detail: '附件清理未完成' } } });
          }
          cleaning = false;
          return route.fulfill({ json: { status: 'completed', owner_id: 7, generation: 1, reset_id: keys.at(-1) } });
        }
        assert.equal(req.method(), 'GET');
        await route.fulfill({ headers: {
          'X-Account-ID': owner, 'X-Account-Data-Version': String(owner === '7' ? generation : 0),
          'X-Account-Reset-Status': owner === '7' && cleaning ? 'cleaning' : 'completed',
        }, json: { id: Number(owner), username: 'synthetic', results: [], count: 0 } });
      });
      await page.goto(origin);
      await page.waitForFunction(() => window.ready);
      pages.push(page);
    }
    const [first, second, other] = pages;
    await first.evaluate(async () => {
      localStorage.setItem('salesmate:7:customer', 'private');
      localStorage.setItem('salesmate:8:customer', 'other');
      sessionStorage.setItem('salesmate:7:draft', 'draft');
      await (await caches.open('salesmate:7:api')).put('/cached', new Response('private'));
      await (await caches.open('salesmate:8:api')).put('/cached', new Response('other'));
      await new Promise((resolve, reject) => {
        const opening = indexedDB.open('salesmate:7:data');
        opening.onsuccess = () => { opening.result.close(); resolve(); };
        opening.onerror = () => reject(opening.error);
      });
    });
    await second.evaluate(() => { sessionStorage.setItem('salesmate:7:draft', 'second-draft'); window.oldPageMarker = true; });
    await other.evaluate(() => { window.otherPageMarker = true; });
    await first.locator('#reset-account-data').click();
    await first.waitForFunction(() => !document.querySelector('#reset-account-data').disabled);
    assert.equal(await first.evaluate(() => localStorage.getItem('salesmate:7:customer')), 'private');
    stage = 'files';
    await first.locator('#reset-account-data').click();
    await first.waitForFunction(() => !document.querySelector('#reset-account-data').disabled);
    await first.reload();
    await first.locator('#account-reset-recovery button').waitFor();
    stage = 'complete';
    await first.locator('#account-reset-recovery button').click();
    await first.waitForURL('**/?account_reset=1#home');
    await second.waitForURL('**/?account_reset=1#home');
    assert.equal(await second.evaluate(() => window.oldPageMarker), undefined);
    assert.equal(await other.evaluate(() => window.otherPageMarker), true);
    assert.equal(await first.evaluate(() => localStorage.getItem('salesmate:7:customer')), null);
    assert.equal(await first.evaluate(() => localStorage.getItem('salesmate:8:customer')), 'other');
    assert.equal(await first.evaluate(() => sessionStorage.getItem('salesmate:7:draft')), null);
    assert.equal(await second.evaluate(() => sessionStorage.getItem('salesmate:7:draft')), null);
    assert.deepEqual(await first.evaluate(() => caches.keys()), ['salesmate:8:api']);
    assert.equal(await first.evaluate(async () => (await indexedDB.databases()).some(db => db.name === 'salesmate:7:data')), false);
    assert.equal((await context.cookies()).find(cookie => cookie.name === 'sessionid').value, 'synthetic-login');
    assert.equal(keys.length, 3);
    assert.equal(new Set(keys).size, 1);
    assert.deepEqual(errors, []);
    console.log('PASS: confirmation, busy state, file failure recovery, idempotency, storage isolation, multi-tab refresh, login cookie preserved');
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
