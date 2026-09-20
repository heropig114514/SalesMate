/**
 * 职责：验证 LLM 分阶段结果在当前详情及时可见，且不会破坏阅读和未保存内容。
 * 国际化前提：浏览器固定 zh-CN，使既有中文交互断言不依赖运行机器语言。
 * 实现：通过共享工作空间入口打开助手；真实浏览器加载静态前端，模拟邮件、画像、评分的独立完成和慢响应/读取失败。
 * 关联：app.js、live-detail.js、assistant.js；使用显式 Playwright 和 Chrome 路径。
 * 目录：main 执行浏览器验收。
 * 变量索引：FRONTEND 为页面根目录，OUTPUT 为忽略的截图目录；其余导入无业务状态。
 */
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.SALESMATE_PLAYWRIGHT_MODULE);
const FRONTEND = path.resolve(__dirname, '../frontend');
const OUTPUT = path.resolve(__dirname, '../artifacts/browser');

/** 功能：验证分阶段更新和交互连续性。输入：环境中的模块与浏览器路径。输出：结果及截图。
 * 逻辑：固定 revision 下分开发布邮件、画像、评分；验证手动重算、失败暂停、恢复和跨客户竞态。
 * 约束：所有 API 拦截，外部网络禁止；POST 仅允许模拟显式/原有打开分析，绝不保存真实数据。 */
async function main() {
  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    const filename = pathname === '/' ? path.join(FRONTEND, 'index.html') : /^\/static\/[\w.-]+$/.test(pathname) ? path.join(FRONTEND, 'assets', path.basename(pathname)) : null;
    if (!filename || !fs.existsSync(filename)) { res.writeHead(404); res.end(); return; }
    res.setHeader('Content-Type', filename.endsWith('.js') ? 'text/javascript' : filename.endsWith('.css') ? 'text/css' : 'text/html');
    res.end(fs.readFileSync(filename));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: process.env.SALESMATE_BROWSER_PATH, headless: true });
  try {
    const page = await browser.newPage({ locale: 'zh-CN', viewport: { width: 1680, height: 1000 } });
    const errors = [], writes = [], reads = { a: 0, b: 0 };
    page.on('pageerror', error => errors.push(error.message));
    let phase = 0, batchCompleted = false, failRead = false, holdRead = false, releaseRead, arrived;
    const initialMail = { dedupe_key: 'sales@example.com:original', subject: '原始询盘', from: 'buyer@example.com', direction: 'inbound', sent_at: '2026-09-13T01:00:00Z', source: 'simulated', extract_status: 'completed', body_text: Array.from({ length: 55 }, (_, i) => `阅读位置 ${i}：采购需求待确认。`).join('\n') };
    const dimension = { facts: [{ text: '已生成第一版客户画像', source_refs: [] }], inferences: [], missing_fields: [] };
    const analysis = { detail_view: { profile: Object.fromEntries(['industry_context', 'company_ops', 'intent'].map(key => [key, dimension])), analysis: Object.fromEntries(['timeline', 'opportunity', 'risk', 'guidance'].map(key => [key, dimension])), conflicts: [], missing_fields: [] } };
    const snapshot = id => ({ company_id: id, company_name: id === 'company-a' ? 'A 客户' : 'B 客户', revision: 7, domains: ['example.com'], contacts: [], crm_status: 'registered', industry: 'unknown', size_band: 'unknown', signal: 'inquiry_intent', score: phase >= 3 ? 42 : null, score_reasons: [], provider: 'agent', email_count: phase >= 1 ? 2 : 1, headline_summary: '已收到询盘', job_status: phase >= 3 && phase !== 4 ? 'completed' : 'running', analysis: phase >= 2 ? (phase >= 5 ? JSON.parse(JSON.stringify(analysis).replaceAll('第一版', '第二版')) : analysis) : null, context: { customer: {}, tickets: [], quotes: [], orders: [], emails: phase >= 1 ? [{ ...initialMail, dedupe_key: 'sales@example.com:new', subject: '新邮件已抽取', body_text: '较早发生的另一封业务邮件', sent_at: '2026-09-12T01:00:00Z' }, initialMail] : [initialMail] } });
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url());
      if (url.hostname !== '127.0.0.1') return route.abort();
      if (!url.pathname.startsWith('/api/v1/')) return route.continue();
      const endpoint = url.pathname.slice('/api/v1/'.length);
      if (req.method() !== 'GET') {
        writes.push(endpoint);
        assert.match(endpoint, /^companies\/company-[ab]\/analyze\/$/);
        if (writes.length === 2) phase = 4;
        return route.fulfill({ json: {} });
      }
      let data;
      if (/^companies\/company-[ab]\/$/.test(endpoint)) {
        const id = endpoint.split('/')[1]; reads[id.endsWith('a') ? 'a' : 'b'] += 1;
        data = snapshot(id);
        if (holdRead && id === 'company-a') { holdRead = false; arrived(); await new Promise(resolve => { releaseRead = resolve; }); }
        if (failRead && id === 'company-a') return route.fulfill({ status: 503, json: { error: { detail: '模拟详情读取失败' } } });
      } else if (endpoint === 'session/') data = { authenticated: true, username: '测试销售', debug_auto_login: true };
      else if (endpoint === 'demo/runtime/') data = { provider: 'agent', timezone: 'Asia/Shanghai' };
      else if (endpoint === 'companies/') data = { count: 0, results: [], stats: { companies: 1, unregistered: 0, new_emails_today: 0 } };
      else if (endpoint === 'mailboxes/') data = [{ mailbox_id: 'mb1', address: 'sales@example.com', gmail_authorized: true, sync_state: { run_id: 'run1', status: batchCompleted ? 'completed' : 'sync_running' } }];
      else if (endpoint === 'mailbox-sync-runs/run1/') data = { run_id: 'run1', status: batchCompleted ? 'completed' : 'running', total_count: 20, completed_count: 1, failed_count: 0, pending_count: 19, running_count: 0, analysis_pending_count: batchCompleted ? 0 : 2, analysis_completed_count: 0, analysis_failed_count: 0, email_errors: [] };
      else if (endpoint === 'email-reviews/') data = { pending_count: 0, results: [], count: 0, page: 1, page_size: 20 };
      else if (endpoint === 'sales/overview/') data = { open_follow_ups: 0 };
      else if (endpoint.startsWith('sales/records/')) data = { results: [], count: 0 };
      else throw new Error('Unexpected API: ' + endpoint);
      return route.fulfill({ json: data });
    });
    fs.mkdirSync(OUTPUT, { recursive: true });
    await page.goto(`http://127.0.0.1:${server.address().port}/#company/company-a`);
    await page.locator('#detail-live-message').filter({ hasText: '自动更新已开启' }).waitFor();
    await page.locator('[data-direction=inbound]').click();
    await page.locator('#assistant-launcher').click();
    await page.locator('#assistant-input').fill('这份草稿尚未保存，请保留。');
    await page.evaluate(() => {
      window.originalEmailNode = document.querySelector('[data-email-ref="sales@example.com:original"]');
      window.originalTextNode = window.originalEmailNode.querySelector('pre').firstChild;
      document.querySelector('.mail-panel').scrollTop = 130;
    });
    phase = 1;
    await page.locator('#emails h4').filter({ hasText: '新邮件已抽取' }).waitFor();
    assert.equal(batchCompleted, false, 'Intermediate update waited for the batch');
    assert.equal(await page.evaluate(() => window.originalEmailNode.isConnected && window.originalTextNode === window.originalEmailNode.querySelector('pre').firstChild), true);
    assert.equal(await page.locator('#assistant-input').inputValue(), '这份草稿尚未保存，请保留。');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'assistant-input');
    assert.equal(await page.locator('[data-direction=inbound]').getAttribute('class'), 'selected');
    await page.locator('#assistant-close').click();
    await page.locator('#register').click();
    await page.locator('#register-form input[name=company_name]').fill('未提交的档案编辑');
    phase = 2;
    await page.locator('.analysis-panel').filter({ hasText: '已生成第一版客户画像' }).waitFor();
    assert.equal(await page.locator('#register-form input[name=company_name]').inputValue(), '未提交的档案编辑');
    assert.equal(await page.locator('#register-dialog').getAttribute('open'), '');
    await page.locator('#register-dialog .close-dialog').click();
    await page.evaluate(() => {
      window.originalEmailNode.scrollIntoView();
      window.anchorTop = window.originalEmailNode.getBoundingClientRect().top;
      const range = document.createRange(); range.setStart(window.originalTextNode, 0); range.setEnd(window.originalTextNode, 4);
      getSelection().removeAllRanges(); getSelection().addRange(range);
    });
    phase = 3;
    await page.locator('.priority-number').filter({ hasText: '42' }).waitFor();
    assert.equal(await page.evaluate(() => getSelection().toString()), '阅读位置');
    assert(await page.evaluate(() => Math.abs(window.originalEmailNode.getBoundingClientRect().top - window.anchorTop) < 3), 'Reading anchor moved');
    await page.screenshot({ path: path.join(OUTPUT, 'live-detail-progressive.png'), fullPage: true });
    batchCompleted = true;
    await page.locator('#reanalyze').click();
    await page.locator('#detail-live-message').filter({ hasText: '自动更新已开启' }).waitFor();
    phase = 5;
    await page.locator('.analysis-panel').filter({ hasText: '已生成第二版客户画像' }).waitFor();
    failRead = true;
    await page.locator('#detail-live-message').filter({ hasText: '自动更新已暂停' }).waitFor();
    const pausedReads = reads.a;
    await new Promise(resolve => setTimeout(resolve, 3400));
    assert.equal(reads.a, pausedReads, 'Failed GET was silently retried');
    failRead = false;
    await page.locator('#detail-live-resume').click();
    await page.locator('#detail-live-message').filter({ hasText: '自动更新已开启' }).waitFor();
    assert.equal(writes.length, 2, 'Read-only resume created a model job');
    const arrival = new Promise(resolve => { arrived = resolve; });
    holdRead = true;
    await arrival;
    await page.evaluate(() => { location.hash = '#company/company-b'; });
    await page.locator('.detail-identity h1').filter({ hasText: 'B 客户' }).waitFor();
    releaseRead();
    await new Promise(resolve => setTimeout(resolve, 250));
    assert.equal(await page.locator('.detail-identity h1').textContent(), 'B 客户');
    await page.evaluate(() => { location.hash = '#home'; });
    await page.locator('#workspace-overview').waitFor();
    const stoppedReads = reads.b;
    await new Promise(resolve => setTimeout(resolve, 3400));
    assert.equal(reads.b, stoppedReads, 'Leaving detail kept its observer alive');
    assert.deepEqual(writes, ['companies/company-a/analyze/', 'companies/company-a/analyze/', 'companies/company-b/analyze/']);
    assert.deepEqual(errors, []);
    console.log('Live detail checks passed: incremental email/L3/L4 at fixed revision, batch independence, draft/focus/form/selection/scroll preservation, manual analysis, pause/resume, late response and route cleanup.');
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => { console.error(error.stack); process.exitCode = 1; });
