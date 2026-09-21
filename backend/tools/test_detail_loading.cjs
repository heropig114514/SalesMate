/**
 * 职责：回归验证客户详情读取与显式分析的独立失败边界。
 * 实现：在 Node VM 中执行 app.js 的实际 loadDetail 函数，以确定性传输替身测试顺序和导航竞争。
 * 关联：app.js；不模拟浏览器布局，也不访问 HTTP 或模型。
 * 目录：fixture 创建隔离上下文；四项 test 回调验证只读、分析失败、读取失败及导航取消。
 * 变量索引：source 为前端源码；implementation 为实际函数源码；其余模块绑定为 Node 导入。
 */
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../frontend/assets/app.js'), 'utf8');
const implementation = source.slice(source.indexOf('async function loadDetail('), source.indexOf('/** 功能：显式请求升级'));

/** 功能：构造函数的隔离执行环境。输入：request 为异步传输替身。输出：context/events。
 * 逻辑：记录真实函数调用的渲染和观察事件；提供可变导航身份。
 * 约束：没有浏览器 DOM，无法证明视觉效果；异常不会被替身吞掉。 */
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

// 功能：只读导航验证。输入：成功响应。输出：仅 GET。逻辑：调用默认参数。约束：无写请求。
test('opening a detail does not request analysis', async () => {
  const calls = [];
  const { context, events } = fixture(async (...args) => { calls.push(args); return {}; });
  await context.loadDetail('sample');
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], 'companies/sample/');
  assert.ok(events.includes('render') && events.includes('start'));
});

// 功能：分析失败验证。输入：POST 409 替身。输出：详情和观察保留。逻辑：先读后写。约束：错误传播。
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

// 功能：读取失败验证。输入：GET 异常。输出：暂停且不写。逻辑：拒绝初次读取。约束：无自动重试。
test('read failure pauses without submitting analysis', async () => {
  let calls = 0;
  const { context, events } = fixture(async () => { calls++; throw new Error('read failed'); });
  await assert.rejects(context.loadDetail('sample', true), /read failed/);
  assert.equal(calls, 1);
  assert.ok(events.includes('paused') && !events.includes('render'));
});

// 功能：导航竞争验证。输入：读取期间切换路由。输出：旧详情不渲染、不写。逻辑：检查实际路由。约束：不使用延时。
test('navigation during reading cancels the old analysis intent', async () => {
  let resolve;
  const { context, events } = fixture(() => new Promise(done => { resolve = done; }));
  const pending = context.loadDetail('sample', true);
  context.location.hash = '#home';
  resolve({});
  await pending;
  assert.ok(!events.includes('render') && !events.includes('start'));
});
