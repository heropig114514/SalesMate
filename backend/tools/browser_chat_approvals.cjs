/**
 * Responsibility: Verify the real chat approval dialog, cancellation, and resumption in a browser.
 * Implementation: Review real customer names separately from synthetic record previews; serve repository assets and mock only HTTP responses; exercise Session decision payloads, duplicate clicks, conflicts, refresh recovery, escaping, and desktop/mobile layout.
 * Relationships: assistant-widget.js, assistant.js, and assistant-widget.css; test_chat_approvals.py separately exercises real PostgreSQL and authentication.
 * Directory: main runs the browser checks; anonymous callbacks serve files and fixture responses.
 * Variable index: FRONTEND locates real assets; OUTPUT stores ignored screenshots; other top-level bindings import test dependencies.
 */
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.SALESMATE_PLAYWRIGHT_MODULE);
const FRONTEND = path.resolve(__dirname, '../frontend');
const OUTPUT = path.resolve(__dirname, '../artifacts/browser');

/** Function: Run actual DOM interactions against controlled approval states.
 * Inputs: Environment Playwright/browser paths and repository assets. Outputs: Assertions, screenshots, and a pass line.
 * Logic: Pause polling time, allow only localhost, count decisions, and verify customer creation never renders experimental fields or implies email sending.
 * Constraints: HTTP/model execution is mocked here; no real writes, accounts, or external network calls. */
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
    const errors = [], decisions = [];
    page.on('pageerror', error => errors.push(error.message));
    const question = { id: 'question-1', role: 'user', content: '请新增虚构实验产品', created_at: new Date().toISOString() };
    const messages = [question];
    const answer = { request_id: 'request-1', user_message_id: question.id, status: 'awaiting_approval', citations: [], error: null,
      approval: { id: 'approval-1', tool: 'experiments.create', status: 'pending', arguments: { batch: 'KGSEED_20260921_01', model: 'sales.Product',
        data: { name: '<img src=x onerror="window.injected=true">', unit_price: '2.00', currency: 'USD' } } } };
    let conflict = true;
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url());
      if (url.hostname !== '127.0.0.1') return route.abort();
      if (!url.pathname.startsWith('/api/v1/')) return route.continue();
      const endpoint = url.pathname.slice('/api/v1/sales/'.length);
      const list = results => ({ count: results.length, results });
      if (req.method() === 'POST') {
        assert.equal(endpoint, `chat/requests/request-1/approvals/${answer.approval.id}/decision/`);
        const payload = req.postDataJSON();
        assert.deepEqual(Object.keys(payload), ['decision']);
        decisions.push(payload.decision);
        if (payload.decision === 'approve' && conflict)
          return route.fulfill({ status: 409, json: { error: { detail: '记录已变化，请拒绝后重新提问。' } } });
        answer.status = payload.decision === 'reject' ? 'cancelled' : 'pending';
        return route.fulfill({ json: answer });
      }
      if (endpoint === 'records/conversations/') return route.fulfill({ json: list([{ id: 'conversation-1', company: null, title: '审批测试' }]) });
      if (endpoint === 'records/messages/') return route.fulfill({ json: list(messages) });
      if (endpoint === 'records/drafts/') return route.fulfill({ json: list([]) });
      if (endpoint === 'chat/action-proposals/') return route.fulfill({ json: list([]) });
      if (endpoint === 'chat/requests/') return route.fulfill({ json: list([answer]) });
      if (endpoint === 'chat/requests/request-1/') {
        if (answer.status === 'pending') {
          answer.status = 'completed';
          answer.assistant_message_id = 'answer-1';
          messages.push({ id: 'answer-1', role: 'assistant', content: '操作已完成。', created_at: new Date().toISOString() });
        }
        return route.fulfill({ json: answer });
      }
      return route.fulfill({ json: {} });
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.evaluate(async () => {
      const widget = await import('/static/assistant-widget.js?v=20260921-markdown');
      widget.enableAssistant();
      window.chatTest = widget.getAssistant();
      await window.chatTest.open();
    });
    const dialog = page.locator('.assistant-approval');
    await dialog.waitFor({ state: 'visible' });
    assert.equal(decisions.length, 0);
    assert.equal(await dialog.locator('img').count(), 0);
    assert.equal(await page.evaluate(() => window.injected), undefined);
    assert.equal(await page.locator('#assistant-submit').isDisabled(), true);
    await page.keyboard.press('Escape');
    assert.equal(await dialog.isVisible(), true);
    assert.equal(decisions.length, 0);
    fs.mkdirSync(OUTPUT, { recursive: true });
    await page.screenshot({ path: path.join(OUTPUT, 'chat-approval-desktop.png') });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: path.join(OUTPUT, 'chat-approval-mobile.png') });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await dialog.locator('[data-decision="approve"]').click();
    await page.waitForFunction(() => document.querySelector('.assistant-approval-error').textContent.includes('记录已变化'));
    assert.equal(await dialog.isVisible(), true);
    await dialog.locator('[data-decision="reject"]').click();
    await dialog.waitFor({ state: 'detached' });
    await page.waitForFunction(() => !document.getElementById('assistant-submit').disabled);
    assert.equal(await page.locator('#assistant-input').inputValue(), question.content);
    assert.equal(await page.locator('#assistant-input').evaluate(node => node === document.activeElement), true);
    assert.equal(await page.locator('#assistant-history article').count(), 1);
    assert.deepEqual(decisions, ['approve', 'reject']);

    // A persisted wait reappears after closing/reopening; the pending request is never auto-approved.
    answer.status = 'awaiting_approval';
    answer.approval.id = 'approval-2';
    conflict = false;
    await page.evaluate(async () => { window.chatTest.close(); await window.chatTest.open(); });
    await dialog.waitFor({ state: 'visible' });
    await page.evaluate(() => {
      const button = document.querySelector('[data-decision="approve"]');
      button.click(); button.click();
    });
    await dialog.waitFor({ state: 'detached' });
    await page.waitForFunction(() => !window.chatTest.busy);
    assert.deepEqual(decisions, ['approve', 'reject', 'approve']);
    await page.clock.fastForward(2100);
    await page.waitForFunction(() => window.chatTest.answers[0].status === 'completed');
    await page.waitForFunction(() => document.getElementById('assistant-history').textContent.includes('操作已完成'));
    assert.equal(await page.locator('#assistant-submit').isEnabled(), true);
    // Registration has its own frozen-name preview, not the experiment deletion fallback.
    answer.status = 'awaiting_approval';
    answer.approval = { id: 'approval-3', tool: 'customers.create', status: 'pending',
      arguments: { name: 'heropig <img src=x onerror="window.injected=true">' } };
    await page.evaluate(async () => { window.chatTest.close(); await window.chatTest.open(); });
    await dialog.waitFor({ state: 'visible' });
    assert.match(await dialog.textContent(), /heropig/);
    assert.match(await dialog.textContent(), /本次仅录入客户/);
    assert.doesNotMatch(await dialog.textContent(), /实验批次|数据表|将删除/);
    assert.equal(await dialog.locator('img').count(), 0);
    assert.equal(await page.evaluate(() => window.injected), undefined);
    assert.deepEqual(decisions, ['approve', 'reject', 'approve']);
    await dialog.locator('[data-decision="approve"]').click();
    await dialog.waitFor({ state: 'detached' });
    assert.deepEqual(decisions, ['approve', 'reject', 'approve', 'approve']);
    assert.deepEqual(errors, []);
    console.log('Chat approval browser checks passed: review, escaping, conflict, rejection, resume, duplicate clicks, mobile layout, and separate customer-registration review.');
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
