/**
 * Responsibility: Regress independent failure boundaries for customer-detail reads and explicit analysis.
 * Implementation: Extract app.js's actual loadDetail up to the English upgradeFacts documentation marker and execute it in a Node VM, using deterministic transport substitutes to test ordering/navigation races.
 * Relationships: app.js; no browser-layout simulation, HTTP, or models.
 * Directory: fixture creates isolated context; four test callbacks verify read-only operation, analysis failure, read failure, and navigation cancellation.
 * Variable index: source is frontend source code; implementation is the actual function source; other module bindings are Node imports.
 */
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../frontend/assets/app.js'), 'utf8');
const implementation = source.slice(source.indexOf('async function loadDetail('), source.indexOf('/** Function: Explicitly request an upgrade'));

/** Function: Build an isolated execution environment for the function. Inputs: request is an asynchronous transport substitute. Outputs: context/events.
 * Logic: Record rendering/observation events from actual function calls and provide mutable navigation identity.
 * Constraints: No browser DOM or visual guarantees; substitutes never swallow exceptions. */
function fixture(request) {
  const events = [];
  const context = vm.createContext({
    request, state: { navigation: 0 }, location: { hash: '#company/sample' },
    detailObserver: { stop: () => events.push('stop'), start: () => events.push('start') },
    renderDetail: () => events.push('render'),
    setDetailLiveStatus: (message, paused) => events.push(paused ? 'paused' : 'status'),
    t: value => String(value), encodeURIComponent,
  });
  vm.runInContext(implementation, context);
  return { context, events };
}

// Function: Verify read-only navigation. Inputs: Successful response. Outputs: GET only. Logic: Use default arguments. Constraints: No writes.
test('opening a detail does not request analysis', async () => {
  const calls = [];
  const { context, events } = fixture(async (...args) => { calls.push(args); return {}; });
  await context.loadDetail('sample');
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], 'companies/sample/');
  assert.ok(events.includes('render') && events.includes('start'));
});

// Function: Verify analysis failure. Inputs: A POST 409 substitute. Outputs: Preserved details/observation. Logic: Read before writing. Constraints: Propagate errors.
test('analysis rejection preserves readable detail and observation', async () => {
  const calls = [];
  const { context, events } = fixture(async (url, options) => {
    calls.push(url);
    if (options?.method === 'POST') throw new Error('upgrade required');
    return {};
  });
  await assert.rejects(context.loadDetail('sample', true), /upgrade required/);
  assert.deepEqual(calls, ['companies/sample/', 'companies/sample/analyze/']);
  assert.ok(events.includes('render') && events.includes('start'));
  assert.ok(!events.includes('paused'));
});

// Function: Verify read failure. Inputs: GET exception. Outputs: Paused observation and no writes. Logic: Reject the initial read. Constraints: No automatic retries.
test('read failure pauses without submitting analysis', async () => {
  let calls = 0;
  const { context, events } = fixture(async () => { calls++; throw new Error('read failed'); });
  await assert.rejects(context.loadDetail('sample', true), /read failed/);
  assert.equal(calls, 1);
  assert.ok(events.includes('paused') && !events.includes('render'));
});

// Function: Verify navigation races. Inputs: Route change during reading. Outputs: No stale rendering/writes. Logic: Check the actual route. Constraints: No timing delays.
test('navigation during reading cancels the old analysis intent', async () => {
  let resolve;
  const { context, events } = fixture(() => new Promise(done => { resolve = done; }));
  const pending = context.loadDetail('sample', true);
  context.location.hash = '#home';
  resolve({});
  await pending;
  assert.ok(!events.includes('render') && !events.includes('start'));
});
