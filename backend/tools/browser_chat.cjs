/**
 * Responsibility: Verify assistant questions, fast-completion races, polling, citation folding, retries, and safe Markdown layout.
 * Internationalization prerequisite: Fix browser locale to zh-CN so Chinese interaction assertions are independent of host language.
 * Implementation: Use company-unbound workspace conversations, actual pages/shared AssistantPanel, an isolated static server, and mocked APIs; virtual time controls observation intervals.
 * Relationships: Chat Markdown, 0919 interface, and shared language resources use coordinated versions; assistant-widget.js/assistant.js/api.js. test_chat.py separately verifies real backend HTTP/PostgreSQL.
 * Directory: verifyMarkdown checks formatting/boundaries; main exercises chat lifetime; inline callbacks handle test routes/assertions.
 * Variable index: FRONTEND is the page directory; OUTPUT is the ignored screenshot directory; MARKDOWN is a mock answer with formatting, wide content, and injection inputs.
 */
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.SALESMATE_PLAYWRIGHT_MODULE);
const FRONTEND = path.resolve(__dirname, '../frontend');
const OUTPUT = path.resolve(__dirname, '../artifacts/browser');
const MARKDOWN = [
  '# 客户跟进建议', '', '**优先联系**，*确认数量*，~~旧方案~~；客户需要设备。[1]',
  '保持普通换行。', '', '## 行动清单', '',
  '1. 确认规格', '   - 核对尺寸', '   - 记录预算', '2. 准备报价', '',
  '> 先确认客户实际需要。', '',
  '| 产品 | 数量 | 交期 | 备注 |', '| :--- | ---: | :---: | --- |',
  '| 设备 A | 100 | 30 天 | 待确认 |', '',
  '使用 `const ok = true;`，参考 [产品资料](https://example.com/specs?q=1&view=full)、https://example.com/help 和 [邮件](mailto:sales@example.com)。', '',
  '```javascript', 'const sample = "<img src=/markdown-probe onerror=alert(1)>";', `const long = "${'abcdef'.repeat(120)}";`, '```', '',
  '<script>window.markdownInjected = true</script>', '<img src=/markdown-probe onerror="window.markdownInjected = true">', '',
  '[危险](javascript:alert(1)) [编码危险](jav&#x61;script:alert(1)) [文件](file:///etc/passwd)',
  '[数据](data:text/html;base64,PHNjcmlwdD4=) [VB](vbscript:msgbox(1))', '',
  '![远程图片](https://example.com/markdown-probe "图片说明")', '',
  '```html" onmouseover="alert(1)', '<svg onload=alert(1)>', '```', '',
  '```text', '未闭合围栏仍保留文本：<script>不可执行</script>',
].join('\n');

/** Function: Verify Markdown semantics, security boundaries, and layout in the actual assistant DOM. Inputs: page is the browser page.
 * Outputs: Assertions and desktop/mobile screenshots. Logic: Mock model answers contain HTML, unsafe protocols, and images; user messages contain literal Markdown.
 * Constraints: Exercise the actual renderer without model calls; wide content scrolls only inside code/table containers. External-site availability is outside scope. */
async function verifyMarkdown(page) {
  const rendered = page.locator('#assistant-history .assistant-markdown');
  assert.equal(await rendered.count(), 1);
  assert.equal(await rendered.locator('h1').textContent(), '客户跟进建议');
  assert.equal(await rendered.locator('h2').textContent(), '行动清单');
  assert.equal(await rendered.locator('strong').textContent(), '优先联系');
  assert.equal(await rendered.locator('em').textContent(), '确认数量');
  assert.equal(await rendered.locator('s').textContent(), '旧方案');
  assert.equal(await rendered.locator('ol > li').count(), 2);
  assert.equal(await rendered.locator('ol ul > li').count(), 2);
  assert.equal(await rendered.locator('blockquote').count(), 1);
  assert.equal(await rendered.locator('table tbody tr').count(), 1);
  assert.equal(await rendered.locator('pre code').count(), 3);
  assert.match(await rendered.locator('pre code').last().textContent(), /未闭合围栏/);
  assert.ok(await rendered.locator('br').count() > 0);
  assert.match(await rendered.textContent(), /<script>window.markdownInjected/);
  assert.equal(await page.locator('#assistant-history script, #assistant-history img, #assistant-history svg').count(), 0);
  assert.equal(await page.evaluate(() => window.markdownInjected), undefined);
  const links = await rendered.locator('a').evaluateAll(nodes => nodes.map(node => ({ href: node.href, target: node.target, rel: node.rel })));
  assert.equal(links.length, 4);
  assert.ok(links.every(link => /^(https?:|mailto:)/.test(link.href) && link.target === '_blank' && link.rel.includes('noopener') && link.rel.includes('noreferrer')));
  assert.equal(await rendered.locator('[onmouseover], [onerror], [onload]').count(), 0);
  assert.equal(await page.locator('#assistant-history .assistant-message').first().locator('strong, h1').count(), 0);
  assert.match(await page.locator('#assistant-history .assistant-message').first().textContent(), /\*\*原样问题\*\*/);
  fs.mkdirSync(OUTPUT, { recursive: true });
  for (const [name, width, height] of [['desktop', 1440, 1000], ['mobile', 390, 844]]) {
    await page.setViewportSize({ width, height });
    await rendered.locator('h1').evaluate(node => node.scrollIntoView({ block: 'start' }));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.equal(await page.locator('.assistant-body').evaluate(node => node.scrollWidth > node.clientWidth + 1), false);
    assert.equal(await rendered.locator('pre').first().evaluate(node => node.scrollWidth > node.clientWidth && getComputedStyle(node).overflowX === 'auto'), true);
    assert.equal(await rendered.locator('p').first().evaluate(node => getComputedStyle(node).whiteSpace), 'normal');
    await page.screenshot({ path: path.join(OUTPUT, `chat-markdown-${name}.png`), fullPage: true });
    if (name === 'mobile') {
      const table = rendered.locator('.assistant-markdown-table');
      assert.equal(await table.evaluate(node => node.scrollWidth > node.clientWidth), true);
      await table.focus();
      await page.keyboard.press('End');
      await page.screenshot({ path: path.join(OUTPUT, 'chat-markdown-table-mobile.png'), fullPage: true });
      await rendered.locator('pre').first().evaluate(node => node.scrollIntoView({ block: 'start' }));
      await page.screenshot({ path: path.join(OUTPUT, 'chat-markdown-code-mobile.png'), fullPage: true });
    }
  }
}

/** Function: Verify the real floating panel against mocked backend states.
 * Inputs: Environment-specified Playwright/browser paths. Outputs: Results and screenshots.
 * Logic: Use DOM assertions for completion, failed-answer retries, error pauses, edit preservation, and switch cancellation; complete an answer during status reads to reproduce stale-message races.
 * Constraints: No real models/mailboxes; block all external network access. This does not replace backend integration testing. */
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
    const errors = [], writes = [], messages = [], answers = [], imageRequests = [];
    let mode = 'completed', pollReads = 0, failPoll = false, completeOnRead = null;
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => { if (request.url().includes('/markdown-probe')) imageRequests.push(request.url()); });
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
      if (endpoint === 'chat/action-proposals/') return route.fulfill({ json: list([]) });
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
          if (!messages.some(row => row.id === answer.assistant_message_id)) messages.push({ id: answer.assistant_message_id, role: 'assistant', content: '**客户需要设备。**[1]', created_at: new Date().toISOString() });
        } else if (mode === 'failed') answer.error = { code: 'model_unavailable', message: '回答模型暂时不可用，请稍后重试。' };
        return route.fulfill({ json: answer });
      }
      throw new Error('Unexpected read: ' + endpoint);
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.evaluate(async () => {
      const { getAssistant, enableAssistant } = await import('/static/assistant-widget.js?v=20260921-markdown');
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
    assert.equal(await page.locator('#assistant-history .assistant-markdown strong').textContent(), '客户需要设备。');
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
    messages.splice(0, messages.length,
      { id: 'markdown-user', role: 'user', content: '**原样问题**\n<script>不执行</script>', created_at: new Date().toISOString() },
      { id: 'markdown-answer', role: 'assistant', content: MARKDOWN, created_at: new Date().toISOString() });
    answers.splice(0);
    await page.evaluate(() => window.chatTest.refreshAnswers());
    await verifyMarkdown(page);
    assert.deepEqual(errors, []);
    assert.deepEqual(imageRequests, []);
    assert.equal(writes.length, 4);
    console.log('Chat browser checks passed: submit, completion, evidence, retry, draft, polling, reopen, Markdown semantics, injection safety, image privacy and responsive overflow.');
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
