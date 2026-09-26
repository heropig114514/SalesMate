/**
 * Responsibility: Verify local frontend/backend main flows and responsive layout in an isolated headless browser.
 * Internationalization prerequisite: Fix browser locale to zh-CN so existing Chinese assertions do not depend on host language.
 * Implementation: Enter directly according to session configuration or log in using private local credentials, then import, filter, register customers, and simulate incoming mail.
 * Relationships: Requires running Django/PostgreSQL and explicitly configured Playwright/browser paths.
 * Directory: main runs browser scenarios.
 * Variable index: ROOT is the backend software root; BASE_URL is the tested local service; OUTPUT is the Git-ignored screenshot directory.
 * Constraints: Write only synthetic data for the explicit demo account; no Gmail connection or credential output.
 */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.SALESMATE_PLAYWRIGHT_MODULE);
const ROOT = path.resolve(__dirname, '..');
const BASE_URL = process.env.SALESMATE_TEST_URL || 'http://127.0.0.1:8000';
const OUTPUT = path.join(ROOT, 'artifacts', 'browser');

/** Function: Verify a complete browser business scenario. Inputs: Implicit environment, local development credentials, and running services.
 * Outputs: Success summary and three screenshots; failures throw assertions. Logic: An isolated browser uses real Session/CSRF; time-tagged emails avoid matching earlier test mail.
 * Constraints: Never retry failed business actions; close the browser in finally. New synthetic emails/customer registrations remain in the demo account. */
async function main() {
  if (!process.env.SALESMATE_BROWSER_PATH) throw new Error('Set SALESMATE_BROWSER_PATH to the test browser executable.');
  fs.mkdirSync(OUTPUT, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath: process.env.SALESMATE_BROWSER_PATH });
  const page = await browser.newPage({ locale: 'zh-CN', viewport: { width: 1440, height: 1050 } });
  const failures = [];
  page.on('pageerror', error => failures.push(error.message));
  try {
    await page.goto(BASE_URL);
    await page.waitForFunction(() => !document.getElementById('login-screen').hidden || !document.getElementById('workspace').hidden);
    if (await page.locator('#login-screen').isVisible()) {
      const credentials = JSON.parse(fs.readFileSync(path.join(ROOT, '.local-access.json'), 'utf8'));
      await page.locator('[name=username]').fill(credentials.username);
      await page.locator('[name=password]').fill(credentials.password);
      await page.locator('#login-form button[type=submit]').click();
    } else {
      assert.equal(await page.locator('#logout').isVisible(), false);
    }
    await page.locator('#workspace').waitFor({ state: 'visible' });
    await page.locator('#seed').click();
    await page.waitForFunction(() => document.querySelectorAll('.company-row').length >= 3);
    await page.locator('#filters [name=q]').fill('不存在的公司-UI-test');
    await page.locator('#filters button[type=submit]').click();
    await page.waitForFunction(() => document.getElementById('result-count').textContent === '0');
    await page.locator('#filters button[type=reset]').click();
    await page.waitForFunction(() => document.querySelectorAll('.company-row').length >= 3);
    if (await page.locator('#notice').isVisible()) await page.locator('#notice button').click();
    await page.screenshot({ path: path.join(OUTPUT, 'inbox-desktop.png'), fullPage: true });
    await page.locator('.company-row').filter({ hasText: '曙光光学' }).click();
    await page.locator('.detail-grid').waitFor();
    assert.ok(await page.locator('.email-card').count() >= 2);
    await page.locator('.source-ref').first().click();
    assert.equal(await page.locator('.email-card.highlight').count(), 1);
    await page.locator('#register').click();
    await page.locator('#register-form [name=employee_count]').fill('150');
    await page.locator('#register-form [name=employee_count_source]').fill('合成 UI 验证：人工确认');
    await page.locator('#register-form button[type=submit]').click();
    await page.locator('#register-dialog').waitFor({ state: 'hidden' });
    await page.waitForFunction(() => document.getElementById('register')?.textContent === '编辑客户档案');
    if (await page.locator('#notice').isVisible()) await page.locator('#notice button').click();
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: path.join(OUTPUT, 'detail-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'Mobile viewport must not overflow horizontally');
    await page.screenshot({ path: path.join(OUTPUT, 'detail-mobile.png'), fullPage: true });
    await page.setViewportSize({ width: 1440, height: 1050 });
    await page.locator('.back-link').click();
    await page.locator('#compose').click();
    await page.locator('#mail-form [name=sender]').fill('lin@aurora.example');
    const testSubject = '界面验证：预算更新与正文安全 ' + Date.now();
    await page.locator('#mail-form [name=subject]').fill(testSubject);
    await page.locator('#mail-form [name=body_text]').fill('预算：24 万\n顾虑：<img src=x onerror="window.__unsafeExecuted=true">');
    await page.locator('#mail-form button[type=submit]').click();
    await page.locator('#mail-dialog').waitFor({ state: 'hidden' });
    await page.locator('.email-card').filter({ hasText: testSubject }).waitFor();
    assert.equal(await page.evaluate(() => Boolean(window.__unsafeExecuted)), false);
    assert.equal(await page.locator('.email-card pre img').count(), 0);
    await page.reload();
    await page.locator('.detail-grid').waitFor();
    assert.ok(await page.locator('.email-card').count() >= 3);
    assert.deepEqual(failures, []);
    console.log('Browser checks passed: session entry, CSRF, seed, search/reset, detail, source navigation, CRM registration, simulation, HTML escaping, reload persistence, 390px layout.');
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
