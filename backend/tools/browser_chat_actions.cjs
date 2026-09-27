/**
 * Responsibility: Verify independent order/email confirmation cards in the real browser UI.
 * Implementation: Serve repository assets, mock HTTP fixtures, and exercise complete previews, escaping, explicit decisions, refresh recovery, delayed-reload button disabling, send-status observation and laboratory browsing without Session-only proposal requests.
 * Relationships: assistant.js renders actual cards; test_chat_actions.py independently verifies PostgreSQL and authenticated HTTP semantics.
 * Directory: main runs browser assertions; anonymous callbacks serve trusted assets and synthetic HTTP fixtures.
 * Variable index: FRONTEND locates assets; OUTPUT stores screenshots; imported bindings provide filesystem, HTTP, assertions and Playwright. Main's canReview and proposalReads verify capability-bound requests.
 */
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.SALESMATE_PLAYWRIGHT_MODULE);
const FRONTEND = path.resolve(__dirname, '../frontend');
const OUTPUT = path.resolve(__dirname, '../artifacts/browser');

/** Function: Exercise real DOM cards without sending mail or changing business data.
 * Inputs: Runtime Playwright/browser paths and repository frontend assets. Outputs: Assertions and desktop/mobile screenshots.
 * Logic: Only localhost is allowed; every decision payload is counted and validated against the displayed version.
 * Constraints: Mocked HTTP proves UI behavior only; provider delivery and production deployment are not tested. */
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
    const messages = [{ id: 'question-1', role: 'user', content: '请准备邮件', created_at: new Date().toISOString() }];
    const answer = { request_id: 'request-1', user_message_id: 'question-1', status: 'completed', citations: [], error: null };
    let proposal = { id: 'proposal-1', request_id: 'request-1', user_message_id: 'question-1', kind: 'email_send',
      status: 'pending_confirmation', revision: 1, expires_at: '2030-01-01T12:00:00Z', confirmed_by_employee: false,
      arguments: { to: ['buyer@example.com'], cc: ['copy@example.com'], bcc: ['archive@example.com'], subject: 'Delivery <img src=x onerror="window.injected=true">',
        body_text: 'Hello,\nFull reviewed body.\n<script>window.injected=true</script>\nLast line preserved.' },
      preview: { company_name: 'Action customer', from_address: 'sales@example.com' } };
    let reject = false, canReview = true, proposalReads = 0, conversationGate = null;
    await page.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      if (url.hostname !== '127.0.0.1') return route.abort();
      if (!url.pathname.startsWith('/api/v1/')) return route.continue();
      const endpoint = url.pathname.slice('/api/v1/sales/'.length);
      const list = results => ({ count: results.length, results });
      if (request.method() === 'POST') {
        assert.equal(endpoint, `chat/action-proposals/${proposal.id}/decision/`);
        const payload = request.postDataJSON();
        assert.deepEqual(Object.keys(payload).sort(), ['decision', 'revision']);
        assert.equal(payload.revision, proposal.revision);
        decisions.push(payload);
        if (reject) return route.fulfill({ status: 409, json: { error: { detail: 'Order changed; refresh proposal.' } } });
        proposal.status = payload.decision === 'cancel' ? 'cancelled' : proposal.kind === 'email_send' ? 'approved' : 'succeeded';
        proposal.confirmed_by_employee = payload.decision === 'approve';
        proposal.revision += 1;
        return route.fulfill({ json: proposal });
      }
      if (endpoint === 'records/conversations/') {
        if (conversationGate) await conversationGate;
        return route.fulfill({ json: list([{ id: 'conversation-1', company: null, title: 'Action review', created_at: '2026-09-27T06:00:00Z', can_review_chat_actions: canReview }]) });
      }
      if (endpoint === 'records/messages/') return route.fulfill({ json: list(messages) });
      if (endpoint === 'records/drafts/') return route.fulfill({ json: list([]) });
      if (endpoint === 'chat/requests/') return route.fulfill({ json: list([answer]) });
      if (endpoint === 'chat/action-proposals/') { proposalReads += 1; return route.fulfill({ json: list([proposal]) }); }
      throw new Error(`Unexpected API: ${endpoint}`);
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.evaluate(async () => {
      const widget = await import('/static/assistant-widget.js');
      widget.enableAssistant();
      window.chatTest = widget.getAssistant();
      await window.chatTest.open();
    });
    const card = page.locator('.assistant-action-proposal');
    await card.waitFor({ state: 'visible' });
    for (const text of ['sales@example.com', 'buyer@example.com', 'copy@example.com', 'archive@example.com', 'Last line preserved.']) assert.ok((await card.innerText()).includes(text));
    assert.equal(await card.locator('img,script').count(), 0);
    assert.equal(await page.evaluate(() => window.injected), undefined);
    assert.equal(decisions.length, 0);
    let releaseConversation;
    conversationGate = new Promise(resolve => { releaseConversation = resolve; });
    await page.locator('#assistant-sessions').selectOption('conversation-1');
    assert.equal(await card.locator('[data-proposal-decision="approve"]').isDisabled(), true);
    assert.equal(await card.locator('[data-proposal-refresh]').isDisabled(), true);
    conversationGate = null;
    releaseConversation();
    await page.waitForFunction(() => !window.chatTest.busy);
    assert.equal(await card.locator('[data-proposal-decision="approve"]').isEnabled(), true);
    await page.locator('#assistant-input').fill('Unsaved input must remain');
    await page.evaluate(() => window.chatTest.refreshAnswers());
    assert.equal(await page.locator('#assistant-input').inputValue(), 'Unsaved input must remain');
    fs.mkdirSync(OUTPUT, { recursive: true });
    await page.screenshot({ path: path.join(OUTPUT, 'chat-actions-desktop.png') });
    await card.locator('[data-proposal-decision="approve"]').dblclick();
    await page.waitForFunction(() => window.chatTest.proposals[0].status === 'approved');
    assert.equal(decisions.length, 1);
    assert.equal(await card.locator('[data-proposal-decision]').count(), 0);
    proposal.status = 'uncertain';
    await page.clock.fastForward(2100);
    await page.waitForFunction(() => window.chatTest.proposals[0].status === 'uncertain');
    assert.equal(decisions.length, 1);
    proposal = { ...proposal, id: 'proposal-2', kind: 'order_update', status: 'pending_confirmation', revision: 1,
      arguments: {}, preview: { company_name: 'Order customer', order_number: 'SO-100', currency: 'USD', total_before: '2000.00', total_after: '6000.00',
        changes: [{ field: 'lines.line-1.quantity', before: '2', after: '5' }] } };
    await page.evaluate(() => window.chatTest.refreshAnswers());
    assert.ok((await card.innerText()).includes('2000.00 USD → 6000.00 USD'));
    reject = true;
    await card.locator('[data-proposal-decision="approve"]').click();
    await page.waitForFunction(() => !window.chatTest.busy);
    assert.ok((await page.locator('#assistant-draft-note').innerText()).includes('Order changed'));
    assert.equal(proposal.status, 'pending_confirmation');
    reject = false;
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: path.join(OUTPUT, 'chat-actions-mobile.png') });
    await card.locator('[data-proposal-decision="cancel"]').click();
    await page.waitForFunction(() => window.chatTest.proposals[0].status === 'cancelled');
    await page.evaluate(async () => { window.chatTest.close(); await window.chatTest.open(); });
    assert.equal(await card.locator('[data-proposal-decision]').count(), 0);
    canReview = false;
    const previousReads = proposalReads;
    await page.evaluate(async () => { window.chatTest.close(); await window.chatTest.open(); await window.chatTest.refreshAnswers(); });
    assert.equal(proposalReads, previousReads, 'Laboratory browsing must not attempt private proposal reads');
    assert.equal(await card.count(), 0);
    assert.equal(await page.locator('#assistant-input').isEnabled(), true);
    assert.deepEqual(errors, []);
    console.log('PASS: complete frozen cards, escaping, explicit/versioned decisions, duplicate prevention, uncertain status, conflict, cancellation, refresh recovery and laboratory chat capability separation.');
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
