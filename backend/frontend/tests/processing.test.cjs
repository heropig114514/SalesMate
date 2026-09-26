/**
 * Responsibility: Reproduce asynchronous response races when switching mailboxes during email review.
 * Implementation: Execute actual processing.js in a Node VM with controlled Promises and minimal DOM to verify displayed results.
 * Relationships: processing.js; replace API, translation, and source-label dependencies only, without servers or models.
 * Directory: fixture creates an isolated module/elements; page creates an empty page; remaining tests use anonymous callbacks.
 * Variable index: No business configuration; all state is local to each fixture call.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

/** Function: Load the actual UI module and control each read's completion. Inputs: None. Outputs: Module, elements, and request queue.
 * Logic: VM module linking replaces dependencies; the DOM implements only fields used by this test. Constraints: This does not verify browser layout or real HTTP availability. */
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

/** Function: Construct an empty mail page. Inputs: count distinguishes old/new responses. Outputs: A pagination-contract object.
 * Logic: Omit email bodies to focus on request ordering. Constraints: All data is synthetic fixture content. */
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
