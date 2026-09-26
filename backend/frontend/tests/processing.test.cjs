/**
 * 职责：复现邮件复核切换邮箱时的异步响应竞争。
 * 实现：Node VM 执行实际 processing.js，以受控 Promise 和最小 DOM 验证展示结果。
 * 关联：processing.js；仅替换 API、翻译和来源标签，不连接服务器或模型。
 * 目录：fixture（创建隔离模块和元素）、page（构造空页）；其余为匿名测试回调。
 * 变量索引：无业务配置；所有状态限定在每次 fixture 调用中。
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

/** 功能：加载真实界面模块并控制每次读取完成时机。输入：无。输出：模块、元素和请求队列。
 * 逻辑：VM 模块链接替换依赖，DOM 只实现本测试涉及的字段。约束：不证明浏览器布局或真实 HTTP 可用性。 */
async function fixture() {
  const elements = new Map();
  const requests = [];
  const document = { getElementById(id) {
    if (!elements.has(id)) elements.set(id, { textContent: '', innerHTML: '', value: '', showModal() {}, addEventListener() {} });
    return elements.get(id);
  } };
  const context = vm.createContext({ document, location: { hash: '' } });
  const literal = (source, ...values) => Array.isArray(source) ? source.map((part, i) => part + (values[i] ?? '')).join('') : source;
  const dependencies = {
    './i18n.js': { t: literal, h: literal, locale: 'zh-CN' },
    './mail-source.js': { mailSourceLabel: value => value },
    './api.js': { escapeHtml: value => String(value ?? ''), request: url => {
      if (url === 'email-reviews/?status=pending') return Promise.resolve({ pending_count: 0 });
      return new Promise((resolve, reject) => requests.push({ url, resolve, reject }));
    } },
  };
  const module = new vm.SourceTextModule(fs.readFileSync(path.join(__dirname, '../assets/processing.js'), 'utf8'), { context });
  await module.link(specifier => {
    const exports = dependencies[specifier.split('?')[0]];
    return new vm.SyntheticModule(Object.keys(exports), function () {
      for (const [key, value] of Object.entries(exports)) this.setExport(key, value);
    }, { context });
  });
  await module.evaluate();
  return { ui: module.namespace, element: id => document.getElementById(id), requests };
}

/** 功能：构造空邮件页。输入：count 用于识别新旧响应。输出：符合分页契约的对象。
 * 逻辑：省略邮件正文，专注请求时序。约束：数据均为合成夹具。 */
function page(count = 0) { return { results: [], page: 1, page_size: 20, count }; }

test('old mailbox failure cannot overwrite the newly opened mailbox', async () => {
  const f = await fixture();
  const old = f.ui.openMailboxEmails('old', 'old@example.test');
  const current = f.ui.openMailboxEmails('current', 'current@example.test');
  f.requests[1].resolve(page());
  await current;
  f.requests[0].reject(new Error('old mailbox failed'));
  await old;
  assert.equal(f.element('review-error').textContent, '');
  assert.match(f.element('review-scope').textContent, /current@example.test/);
});

test('current failure stays visible, a successful explicit reload clears it', async () => {
  const f = await fixture();
  f.ui.initProcessingUI(async () => {}, async () => {});
  const opened = f.ui.openMailboxEmails('current');
  f.requests[0].reject(new Error('current mailbox failed'));
  await opened;
  assert.equal(f.element('review-error').textContent, 'current mailbox failed');
  f.element('review-filter').onchange();
  f.requests[1].resolve(page());
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.element('review-error').textContent, '');
  assert.equal(f.requests.length, 2);
});

test('late successful response cannot replace the current page', async () => {
  const f = await fixture();
  const old = f.ui.openMailboxEmails('old');
  const current = f.ui.openMailboxEmails('current');
  f.requests[1].resolve(page(7));
  await current;
  f.requests[0].resolve(page(99));
  await old;
  assert.match(f.element('review-page').textContent, /7/);
  assert.doesNotMatch(f.element('review-page').textContent, /99/);
});
