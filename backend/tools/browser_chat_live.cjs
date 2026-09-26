/**
 * Responsibility: Verify actual assistant-page questions and citations against a temporary Django service.
 * Implementation: Explicit zh-CN locale; match startup entries by pathname without cache query parameters. Mount workspace chat when customer data exists; general mode uses the real homepage. All business APIs target the test server.
 * Relationships: Chat Markdown, 0919 interface, and language resources use coordinated versions; chat_browser_e2e.py supplies isolated users/sessions and runs the Agent. Model mocking occurs at the Python boundary.
 * Directory: main establishes a session and verifies actual completed results.
 * Variable index: No module business variables; URL, mode, and temporary cookie come from the environment and are never printed.
 */
const assert = require('node:assert/strict');
const { chromium } = require(process.env.SALESMATE_PLAYWRIGHT_MODULE);

/** Function: Execute the complete real-HTTP browser flow.
 * Inputs: Synthetic CHAT_TEST_* environment and explicit browser path. Outputs: Success marker or nonzero exit.
 * Logic: Use Chinese locale and a browser-local login cookie; after asking, wait for Python Agent persistence and automatic page refresh. General mode collapses/reopens chat; a full reload followed by the floating entry verifies history restoration.
 * Constraints: Do not intercept business APIs; block networks outside the test service and never print cookies. */
async function main() {
  const browser = await chromium.launch({ executablePath: process.env.SALESMATE_BROWSER_PATH, headless: true });
  try {
    const context = await browser.newContext({ locale: 'zh-CN' });
    await context.addCookies([{ name: process.env.CHAT_TEST_COOKIE_NAME, value: process.env.CHAT_TEST_SESSION, url: process.env.CHAT_TEST_URL }]);
    const page = await context.newPage();
    const errors = [];
    const general = process.env.CHAT_TEST_GENERAL === '1';
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== process.env.CHAT_TEST_URL) return route.abort();
      if (!general && url.pathname === '/static/app.js') {
        const body = `import { getAssistant, enableAssistant } from '/static/assistant-widget.js?v=20260921-markdown';
          await fetch('/api/v1/session/');
          enableAssistant();
          window.chatTest = getAssistant();
          window.chatTest.open();`;
        return route.fulfill({ contentType: 'text/javascript', body });
      }
      return route.continue();
    });
    await page.goto(process.env.CHAT_TEST_URL + (general ? '/#assistant' : '/'));
    if (!general) await page.waitForFunction(() => window.chatTest && !window.chatTest.busy && window.chatTest.conversation, null, { timeout: 20000 });
    else await page.locator('#assistant-input:not(:disabled)').waitFor();
    await page.locator('#assistant-input').fill(general ? '你好' : '查找测试客户');
    await page.locator('#assistant-submit').click();
    if (general) {
      await page.locator('#assistant-history').getByText('你好，我们可以一起起草邮件。', { exact: true }).waitFor({ timeout: 30000 });
      await page.locator('#assistant-close').click();
      await page.locator('#assistant-launcher').click();
      await page.locator('#assistant-history').getByText('你好，我们可以一起起草邮件。', { exact: true }).waitFor();
      await page.reload();
      await page.locator('#assistant-launcher').click();
      await page.locator('#assistant-history').getByText('你好，我们可以一起起草邮件。', { exact: true }).waitFor();
      assert.equal(await page.locator('#assistant-company').textContent(), '通用聊天');
      assert.equal(await page.locator('#assistant-history details').count(), 0);
    } else {
      const sources = page.locator('#assistant-history .assistant-sources');
      await sources.locator(':scope > summary').waitFor({ timeout: 30000 });
      assert.match(await page.locator('#assistant-history').textContent(), /找到测试客户。\[1\]/);
      await sources.locator(':scope > summary').click();
      await sources.locator('.assistant-source > summary').click();
      assert.match(await sources.locator('.assistant-source-content').textContent(), /测试客户/);
    }
    assert.equal(await page.locator('#assistant-submit').isEnabled(), true);
    assert.deepEqual(errors, []);
    console.log('Live chat browser round trip passed');
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
