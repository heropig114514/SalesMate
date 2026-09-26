/**
 * Responsibility: Verify account reset buttons, error recovery, cache isolation, and cross-tab reloads.
 * Implementation: A real browser loads workspace navigation/reset modules with mocked HTTP states; caches use actual browser storage.
 * Relationships: Chat Markdown, 0919 interface, and shared language resources use coordinated cache versions; workspace.js, account-reset.js, account-cache.js, api.js. Integration tests separately cover backend transactions.
 * Directory: main runs busy, partial-failure, reload-recovery, and successful-broadcast scenarios.
 * Variable index: FRONTEND is the actual static-module directory; other imports carry no business state.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require(process.env.SALESMATE_PLAYWRIGHT_MODULE);
const FRONTEND = path.resolve(__dirname, '../frontend/assets');

/** Function: Verify reset interactions and caches in a real browser. Inputs: Environment Playwright module and Chrome paths.
 * Outputs: A success summary or nonzero failure. Logic: Two pages use account 7 and a third simulates account 8; exercise busy state, attachment failure, then continuation after reload.
 * Constraints: Intercept all business HTTP; never access real user accounts or claim that mock endpoints verify production deletion.
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
