/**
 * 职责：验证助手提问、快速完成竞态、轮询、嵌套引用折叠、重试和上下文切换。
 * 国际化前提：浏览器固定 zh-CN，使既有中文交互断言不依赖运行机器语言。
 * 实现：仅使用无公司绑定的工作空间会话；加载真实页面和共享悬浮 AssistantPanel，使用隔离静态服务与模拟 API；虚拟时钟控制观察间隔。
 * 关联：assistant-widget.js/assistant.js/api.js；后端真实 HTTP 和 PostgreSQL 由 test_chat.py 单独验证。
 * 目录：main 执行浏览器场景；内联回调处理测试路由和断言。
 * 变量索引：FRONTEND 为页面目录；OUTPUT 为忽略的浏览器截图目录。
 */
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.SALESMATE_PLAYWRIGHT_MODULE);
const FRONTEND = path.resolve(__dirname, '../frontend');
const OUTPUT = path.resolve(__dirname, '../artifacts/browser');

/** 功能：验证真实悬浮面板在模拟后端状态下的行为。
 * 输入：环境指定的 Playwright 和浏览器路径。输出：检查结果及截图。
 * 逻辑：完成、失败重试、错误暂停、保留编辑和切换取消均通过 DOM 验证；在状态查询时同步完成回答以复现旧消息快照竞态。
 * 约束：不调用真实模型或邮箱，所有外部网络禁止，不能代替真实后端联调。 */
async function main() {
  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    const filename = pathname === '/' ? path.join(FRONTEND, 'index.html') : /^\/static\/[\w.-]+$/.test(pathname) ? path.join(FRONTEND, 'assets', path.basename(pathname)) : null;
    if (!filename || !fs.existsSync(filename)) { res.writeHead(404); res.end(); return; }
    res.setHeader('Content-Type', filename.endsWith('.js') ? 'text/javascript' : filename.endsWith('.css') ? 'text/css' : 'text/html');
    let body = fs.readFileSync(filename, 'utf8');
    if (pathname === '/') body = body.replace(/<script type="module" src="\/static\/app.js[^<]*<\/script>/, '');
    res.end(body);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: process.env.SALESMATE_BROWSER_PATH, headless: true });
  try {
    const page = await browser.newPage({ locale: 'zh-CN', viewport: { width: 1440, height: 1000 } });
    await page.clock.install();
    const errors = [], writes = [], messages = [], answers = [];
    let mode = 'completed', pollReads = 0, failPoll = false, completeOnRead = null;
    page.on('pageerror', error => errors.push(error.message));
    const conversation = { id: 'conversation-a', company: null, title: '测试会话', created_at: new Date().toISOString() };
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url());
      if (url.hostname !== '127.0.0.1') return route.abort();
      if (!url.pathname.startsWith('/api/v1/')) return route.continue();
      const endpoint = url.pathname.slice('/api/v1/sales/'.length);
      const list = results => ({ count: results.length, results });
      if (req.method() === 'POST') {
        writes.push(endpoint);
        if (endpoint === 'chat/messages/') {
          const data = req.postDataJSON();
          assert.equal(data.conversation_id, conversation.id);
          assert.ok(data.client_key);
          const message = { id: `message-${messages.length}`, role: 'user', content: data.content, created_at: new Date().toISOString() };
          messages.push(message);
          const answer = { request_id: `request-${answers.length}`, user_message_id: message.id, status: 'pending', citations: [], error: null };
          answers.push(answer);
          return route.fulfill({ status: 201, json: answer });
        }
        if (/^chat\/requests\/[^/]+\/retry\/$/.test(endpoint)) {
          const previous = answers.at(-1);
          assert.equal(previous.status, 'failed');
          const answer = { ...previous, request_id: `request-${answers.length}`, status: 'pending', error: null };
          answers.push(answer);
          return route.fulfill({ status: 201, json: answer });
        }
        throw new Error('Unexpected write: ' + endpoint);
      }
      if (endpoint === 'records/conversations/') return route.fulfill({ json: list(url.searchParams.get('conversation_scope') === 'general' ? [conversation] : []) });
      if (endpoint === 'records/messages/') return route.fulfill({ json: list(messages) });
      if (endpoint === 'records/drafts/') return route.fulfill({ json: list([]) });
      if (endpoint === 'chat/requests/') {
        if (completeOnRead) {
          const answer = answers.at(-1);
          answer.status = 'completed';
          answer.assistant_message_id = completeOnRead;
          answer.citations = [];
          messages.push({ id: completeOnRead, role: 'assistant', content: completeOnRead, created_at: new Date().toISOString() });
          completeOnRead = null;
        }
        return route.fulfill({ json: list(answers) });
      }
      if (/^chat\/requests\/[^/]+\/$/.test(endpoint)) {
        pollReads += 1;
        if (failPoll) return route.fulfill({ status: 503, json: { error: { detail: '模拟查询失败' } } });
        const answer = answers.find(row => endpoint.includes(row.request_id));
        answer.status = mode;
        if (mode === 'completed') {
          answer.assistant_message_id = `assistant-${answer.request_id}`;
          answer.citations = [{ position: 1, source_id: 'email-one', title_or_label: '<script>恶意标题</script>', content: '客户需要设备。<img src=x onerror=alert(1)>' }];
          if (!messages.some(row => row.id === answer.assistant_message_id)) messages.push({ id: answer.assistant_message_id, role: 'assistant', content: '客户需要设备。[1]', created_at: new Date().toISOString() });
        } else if (mode === 'failed') answer.error = { code: 'model_unavailable', message: '回答模型暂时不可用，请稍后重试。' };
        return route.fulfill({ json: answer });
      }
      throw new Error('Unexpected read: ' + endpoint);
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.evaluate(async () => {
      const { getAssistant, enableAssistant } = await import('/static/assistant-widget.js?v=20260920-workspace-chat');
      enableAssistant();
      window.chatTest = getAssistant();
      window.chatTest.open();
    });
    await page.waitForFunction(() => !window.chatTest.busy && window.chatTest.conversation);
    await page.locator('#assistant-input').fill('客户需要什么？');
    await page.locator('#assistant-submit').click();
    await page.waitForFunction(() => !window.chatTest.busy && window.chatTest.answers.length === 1);
    assert.equal(await page.locator('#assistant-submit').isDisabled(), true);
    await page.locator('#assistant-input').fill('正在编辑的下一条问题');
    await page.clock.fastForward(2100);
    const sources = page.locator('#assistant-history .assistant-sources');
    await sources.waitFor();
    assert.equal(await sources.getAttribute('open'), null);
    assert.equal(await page.locator('#assistant-input').inputValue(), '正在编辑的下一条问题');
    assert.equal(await page.locator('#assistant-history script, #assistant-history img').count(), 0);
    await sources.locator(':scope > summary').click();
    const source = sources.locator('details.assistant-source');
    assert.equal(await source.getAttribute('open'), null);
    await source.locator(':scope > summary').click();
    assert.equal(await source.locator('.assistant-source-content').isVisible(), true);
    assert.match(await source.locator('.assistant-source-content').textContent(), /客户需要设备/);
    assert.notEqual(await source.evaluate(node => getComputedStyle(node).backgroundColor), 'rgb(255, 255, 255)');
    mode = 'failed';
    await page.locator('#assistant-submit').click();
    await page.waitForFunction(() => !window.chatTest.busy && window.chatTest.answers.length === 2);
    await page.clock.fastForward(2100);
    await page.locator('[data-chat-retry]').waitFor();
    mode = 'completed';
    await page.locator('[data-chat-retry]').click();
    await page.waitForFunction(() => !window.chatTest.busy && window.chatTest.answers.length === 3);
    await page.clock.fastForward(2100);
    await page.waitForFunction(() => window.chatTest.answers.at(-1).status === 'completed');
    mode = 'processing';
    failPoll = true;
    await page.locator('#assistant-input').fill('测试查询失败');
    await page.locator('#assistant-submit').click();
    await page.waitForFunction(() => !window.chatTest.busy && window.chatTest.answers.length === 4);
    await page.clock.fastForward(2100);
    await page.locator('[data-chat-resume]').waitFor();
    const stoppedReads = pollReads;
    await page.clock.fastForward(10000);
    assert.equal(pollReads, stoppedReads);
    failPoll = false;
    await page.locator('[data-chat-resume]').click();
    await page.waitForFunction(() => !window.chatTest.busy);
    await page.evaluate(() => { window.chatTest.pollCount = 119; });
    await page.clock.fastForward(2100);
    await page.locator('[data-chat-resume]').waitFor();
    assert.match(await page.locator('#assistant-draft-note').textContent(), /等待时间较长/);
    await page.locator('[data-chat-resume]').click();
    await page.waitForFunction(() => !window.chatTest.busy);
    await page.locator('#assistant-close').click();
    const closedReads = pollReads;
    await page.clock.fastForward(10000);
    assert.equal(pollReads, closedReads);
    completeOnRead = '即时回答-load';
    await page.evaluate(() => window.chatTest.open());
    await page.waitForFunction(() => !window.chatTest.busy);
    assert.match(await page.locator('#assistant-history').textContent(), /即时回答-load/);
    const fastQuestion = { id: 'fast-user', role: 'user', content: '快速问题', created_at: new Date().toISOString() };
    messages.push(fastQuestion);
    answers.push({ request_id: 'fast-request', user_message_id: fastQuestion.id, status: 'pending', citations: [], error: null });
    completeOnRead = '即时回答-refresh';
    await page.evaluate(() => window.chatTest.refreshAnswers());
    assert.match(await page.locator('#assistant-history').textContent(), /即时回答-refresh/);
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.deepEqual(errors, []);
    assert.deepEqual(writes, ['chat/messages/', 'chat/messages/', 'chat/requests/request-1/retry/', 'chat/messages/']);
    fs.mkdirSync(OUTPUT, { recursive: true });
    await page.screenshot({ path: path.join(OUTPUT, 'chat-mobile.png'), fullPage: true });
    console.log('Chat browser checks passed: submit, completion during load/refresh, evidence, retry, draft preservation, pause, bounds, close and reopen.');
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
