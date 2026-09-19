/**
 * 职责：在独立无头浏览器中验证本地前后端主流程与响应式布局。
 * 国际化前提：浏览器固定 zh-CN，使既有中文交互断言不依赖运行机器语言。
 * 实现：按会话配置直接进入工作台或读取私有账号登录，再导入、筛选、建档和模拟来信。
 * 关联：需要运行中的 Django、PostgreSQL，以及显式配置的 Playwright 模块和浏览器路径。
 * 目录：main（运行浏览器场景）。
 * 变量索引：ROOT 为软件根目录 backend；BASE_URL 为被测本地服务；OUTPUT 为软件目录内被 Git 排除的截图目录。
 * 约束：只对显式演示账号的合成数据写入，不连接 Gmail；输出不包含凭证。
 */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.SALESMATE_PLAYWRIGHT_MODULE);
const ROOT = path.resolve(__dirname, '..');
const BASE_URL = process.env.SALESMATE_TEST_URL || 'http://127.0.0.1:8000';
const OUTPUT = path.join(ROOT, 'artifacts', 'browser');

/** 功能：验证完整浏览器业务场景。输入：隐式环境变量、本地开发凭证及运行中的服务。
 * 输出：成功摘要与三张截图；失败抛出断言。逻辑：独立浏览器使用真实 Session 和 CSRF，本次邮件以时间标识避免匹配旧测试邮件。
 * 约束：不重试失败业务动作；finally 关闭浏览器；演示账号会保留新增合成邮件与建档结果。 */
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
