/** Responsibility: Verify login routing and shared logout against the actual frontend in Chrome.
 * Implementation: Serve repository assets on an ephemeral loopback port and mock session/business APIs; exercise success, rejection, navigation, registration, and logout recovery.
 * Relationships: app.js, workspace.js, business.js, company-settings.js and world-news.js; real Django authentication/CSRF is covered by test_debug_session.py and test_registration.py.
 * Directory: main.
 * Variable index: ROOT locates frontend files; OUTPUT stores ignored screenshots.
 */
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.SALESMATE_PLAYWRIGHT_MODULE);
const ROOT = path.resolve(__dirname, '../frontend');
const OUTPUT = path.resolve(__dirname, '../artifacts/browser');

/** Function: Run browser authentication acceptance scenarios. Inputs: Explicit Playwright module and Chrome executable environment paths.
 * Outputs: Assertions and desktop/mobile login screenshots; close browser/server on failure or success.
 * Logic: Mock only loopback APIs, drive real forms and Profile controls, reject unexpected requests, and verify failed logout retains the page until an explicit second click.
 * Constraints: Synthetic credentials only; mocks do not prove server authentication, real CSRF enforcement, database persistence, or external-service behavior.
 */
async function main() {
  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    const pages = { '/': 'index.html', '/login': 'index.html', '/login/': 'index.html', '/business/': 'business.html', '/settings/company/': 'company-settings.html', '/world/': 'world.html' };
    const file = pages[pathname] ? path.join(ROOT, pages[pathname]) : /^\/static\/[\w.-]+$/.test(pathname) ? path.join(ROOT, 'assets', path.basename(pathname)) : null;
    if (!file || !fs.existsSync(file)) { res.writeHead(404); res.end(); return; }
    res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
    res.end(fs.readFileSync(file));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ executablePath: process.env.SALESMATE_BROWSER_PATH, headless: true });
    const page = await browser.newPage({ locale: 'zh-CN', viewport: { width: 1440, height: 1000 } });
    const errors = [], unexpected = [], sessionReads = [];
    let authenticated = false, failLogout = false, logoutCalls = 0, onboarding = false;
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url());
      if (url.hostname !== '127.0.0.1') return route.abort();
      if (!url.pathname.startsWith('/api/v1/')) return route.continue();
      const endpoint = url.pathname.slice(8);
      if (endpoint === 'session/') {
        if (req.method() === 'DELETE') {
          logoutCalls += 1;
          if (failLogout) return route.fulfill({ status: 503, json: { error: { detail: '测试退出失败' } } });
          authenticated = false;
          return route.fulfill({ status: 204 });
        }
        if (req.method() === 'POST') {
          if (req.postDataJSON().password !== 'synthetic-password') return route.fulfill({ status: 403, json: { error: { detail: '用户名或密码不正确。' } } });
          authenticated = true;
        } else sessionReads.push(url.search);
        return route.fulfill({ json: { authenticated, username: authenticated ? '测试用户' : null, debug_auto_login: false, onboarding_required: onboarding } });
      }
      if (endpoint === 'accounts/register/' && req.method() === 'POST') {
        authenticated = true; onboarding = true;
        return route.fulfill({ status: 201, json: { authenticated: true } });
      }
      const responses = {
        'demo/runtime/': { provider: 'agent', timezone: 'Asia/Shanghai' },
        'mailboxes/': [], 'email-reviews/': { results: [], pending_count: 0 },
        'sales/overview/': { open_follow_ups: 0 }, 'sales/browse/overview/': {},
        'sales/directory/': { results: [], count: 0 }, 'sales/catalog/': { resources: [] }, 'sales/browse/directory/': { results: [], count: 0 },
        'companies/': { results: [], count: 0, stats: { companies: 0, unregistered: 0, new_emails_today: 0 } },
        'accounts/me/': { username: '测试用户' }, 'accounts/company-profile/': { revision: 0 },
        'accounts/onboarding/': { personal: {}, products: [], solutions: [], completed: !onboarding, revision: 0, documents: [] },
      };
      if (req.method() === 'GET' && endpoint in responses) return route.fulfill({ json: responses[endpoint] });
      unexpected.push(req.method() + ' ' + endpoint);
      return route.fulfill({ status: 500, json: { error: { detail: 'Unexpected test request' } } });
    });
    const base = `http://127.0.0.1:${server.address().port}`;
    fs.mkdirSync(OUTPUT, { recursive: true });
    for (const entry of ['/login', '/login/', '/', '/business/', '/settings/company/', '/world/']) {
      await page.goto(base + entry);
      await page.locator('#login-form').waitFor();
      assert.equal(new URL(page.url()).pathname, '/login/');
    }
    assert.ok(sessionReads.includes('?auto_login=false'));
    await page.screenshot({ path: path.join(OUTPUT, 'login-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({ path: path.join(OUTPUT, 'login-mobile.png'), fullPage: true });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.locator('#login-form [name=username]').fill('测试用户');
    await page.locator('#login-form [name=password]').fill('incorrect');
    await page.locator('#login-form [type=submit]').click();
    await page.locator('#notice').filter({ hasText: '用户名或密码不正确' }).waitFor();
    assert.equal(authenticated, false);
    await page.locator('#login-form [name=password]').fill('synthetic-password');
    await page.locator('#login-form [type=submit]').click();
    await page.waitForURL(base + '/#home');
    await page.locator('#workspace-profile #logout').waitFor();
    await page.goto(base + '/login/');
    await page.waitForURL(base + '/#home');
    for (const entry of ['/business/', '/settings/company/', '/#home']) {
      await page.goto(base + entry);
      await page.locator('#workspace-profile #logout').waitFor();
    }
    failLogout = true;
    await page.locator('#logout').click();
    await page.locator('#logout-error').filter({ hasText: '测试退出失败' }).waitFor();
    assert.equal(authenticated, true);
    assert.equal(logoutCalls, 1);
    assert.equal(await page.locator('#logout').isEnabled(), true);
    failLogout = false;
    await page.locator('#logout').click();
    await page.locator('#login-form').waitFor();
    assert.equal(logoutCalls, 2);
    assert.equal(authenticated, false);
    await page.reload();
    await page.locator('#login-form').waitFor();
    assert.equal(await page.locator('#login-form [name=password]').inputValue(), '');
    await page.locator('#show-signup').click();
    await page.locator('#signup-form [name=username]').fill('新测试用户');
    await page.locator('#signup-form [name=password]').fill('synthetic-password');
    await page.locator('#signup-form [name=password_confirmation]').fill('synthetic-password');
    await page.locator('#signup-form [type=submit]').click();
    await page.waitForURL(base + '/settings/company/?onboarding=1');
    await page.locator('#logout').waitFor();
    await page.locator('#logout').click();
    await page.locator('#login-form').waitFor();
    assert.equal(logoutCalls, 3);
    assert.deepEqual(errors, []);
    assert.deepEqual(unexpected, []);
    console.log('Authentication browser checks passed: routes, existing forms, failed/successful login, redirect, shared logout, failed logout recovery, reload, registration/onboarding, desktop/mobile.');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
