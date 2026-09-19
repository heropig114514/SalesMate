/**
 * 职责：对临时 Django 服务执行真实助手网页提问及引用验收。
 * 实现：客户模式替换启动入口以挂载真实 AssistantPanel，通用模式使用真实主页，业务 API 全部访问测试服务器。
 * 关联：chat_browser_e2e.py 提供隔离用户/会话并执行 Agent；模型模拟发生在 Python 边界。
 * 目录：main 建立会话并验证真实完成结果。
 * 变量索引：无模块业务变量；测试 URL、模式、可空公司和临时 cookie 从环境读取且不打印。
 */
const assert = require('node:assert/strict');
const { chromium } = require(process.env.SALESMATE_PLAYWRIGHT_MODULE);

/** 功能：执行真实 HTTP 网页闭环。
 * 输入：CHAT_TEST_* 合成测试环境和显式浏览器路径。输出：成功标记或非零退出。
 * 逻辑：登录 cookie 仅用于当前浏览器，提问后等待 Python Agent 写入并由网页自动刷新，通用模式再刷新整页验证历史恢复。
 * 约束：不拦截任何业务 API；禁止连接测试服务以外的网络，不打印 cookie。 */
async function main() {
  const browser = await chromium.launch({ executablePath: process.env.SALESMATE_BROWSER_PATH, headless: true });
  try {
    const context = await browser.newContext();
    await context.addCookies([{ name: process.env.CHAT_TEST_COOKIE_NAME, value: process.env.CHAT_TEST_SESSION, url: process.env.CHAT_TEST_URL }]);
    const page = await context.newPage();
    const errors = [];
    const general = process.env.CHAT_TEST_GENERAL === '1';
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== process.env.CHAT_TEST_URL) return route.abort();
      if (!general && url.pathname === '/static/app.js') {
        const body = `import { AssistantPanel } from '/static/assistant.js';
          await fetch('/api/v1/session/');
          window.chatTest = new AssistantPanel();
          window.chatTest.setContext({ id: ${JSON.stringify(process.env.CHAT_TEST_COMPANY)}, name: '联合验收客户' });
          window.chatTest.open();`;
        return route.fulfill({ contentType: 'text/javascript', body });
      }
      return route.continue();
    });
    await page.goto(process.env.CHAT_TEST_URL + (general ? '/#assistant' : '/'));
    if (!general) await page.waitForFunction(() => window.chatTest && !window.chatTest.busy && window.chatTest.conversation, null, { timeout: 20000 });
    else await page.locator('#assistant-input:not(:disabled)').waitFor();
    await page.locator('#assistant-input').fill(general ? '你好' : '客户需要什么？');
    await page.locator('#assistant-submit').click();
    if (general) {
      await page.locator('#assistant-history').getByText('你好，我们可以一起起草邮件。', { exact: true }).waitFor({ timeout: 30000 });
      await page.reload();
      await page.locator('#assistant-history').getByText('你好，我们可以一起起草邮件。', { exact: true }).waitFor();
      assert.equal(await page.locator('#assistant-company').textContent(), '通用聊天');
      assert.equal(await page.locator('#assistant-history details').count(), 0);
    } else {
      await page.locator('#assistant-history summary').waitFor({ timeout: 30000 });
      assert.match(await page.locator('#assistant-history').textContent(), /客户需要设备。\[1\]/);
      await page.locator('#assistant-history summary').click();
      assert.match(await page.locator('#assistant-history details p').textContent(), /客户需要设备/);
    }
    assert.equal(await page.locator('#assistant-submit').isEnabled(), true);
    assert.deepEqual(errors, []);
    console.log('Live chat browser round trip passed');
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
