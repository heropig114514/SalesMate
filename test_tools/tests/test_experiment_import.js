/**
 * Responsibility: Verify experiment JavaScript relation mapping, idempotency envelopes, and failure boundaries offline.
 * Implementation: Replace fetch under Node's native test framework and record requests without actual services.
 * Relationships: generate_experiment_data.js; does not replace production permission/database acceptance.
 * Directory: fixtureFetch generates mocked HTTP functions; remaining tests are anonymous callbacks.
 * Variable index: No locally implemented module configuration; test, assert, generateExperimentData, and importExperimentData are imported test dependencies.
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const { generateExperimentData, importExperimentData } = require("../generate_experiment_data.js");

/**
 * Function: Generate a mock fetch that records calls.
 * Inputs: calls is the request array; failEmail controls email rejection.
 * Outputs: An asynchronous request function.
 * Logic: Mock catalog, mailbox, email, and business receipts separately, deliberately using real IDs different from fixture IDs.
 * Constraints: Tests transport/mapping only and makes no claim of actual authorization.
 */
function fixtureFetch(calls, failEmail = false) {
  return async (url, options) => {
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ url, body, options });
    let result;
    if (url.includes("catalog/")) {
      result = { tools: [{ name: new URL(url).searchParams.get("category") + ".create", executionMode: "write" }] };
    } else if (url.endsWith("/agent/emails/")) {
      if (failEmail) return { ok: false, status: 403 };
      result = body.map(row => ({ dedupe_key: row.dedupe_key, company_id: "real-company" }));
    } else if (body.name === "mailboxes.list") {
      result = { status: "completed", data: [{ mailbox_id: "real-mailbox", address: "user@example.com" }] };
    } else {
      result = { status: "completed", data: { id: "real-" + body.name } };
    }
    return { ok: true, json: async () => result };
  };
}

test("imports use actual relation IDs, reuse idempotency keys, and omit read-only state", async t => {
  const calls = [];
  t.mock.method(globalThis, "fetch", fixtureFetch(calls));
  const data = generateExperimentData({ count: 1 });
  const options = { baseUrl: "https://fixture.example", mailboxId: "real-mailbox", toolToken: "test-tool", agentToken: "test-agent" };
  const result = await importExperimentData(data, options);
  assert.equal(result.emails, 3);
  const writes = calls.filter(call => call.body?.name?.endsWith(".create"));
  assert.equal(writes.length, 4);
  assert.equal(writes[1].body.arguments.data.company, "real-company");
  assert.equal(writes[3].body.arguments.data.order, "real-orders.create");
  assert.equal(writes[3].body.arguments.data.product, "real-products.create");
  assert.ok(writes.every(call => !("status" in call.body.arguments.data) && !("id" in call.body.arguments.data)));
  assert.equal(calls.find(call => call.url.endsWith("/agent/emails/")).body[0].mailbox_id, "real-mailbox");
  await importExperimentData(data, options);
  const replay = calls.filter(call => call.body?.name?.endsWith(".create")).slice(4);
  assert.deepEqual(replay.map(call => call.body), writes.map(call => call.body));
});

test("mailboxes from another account are rejected before any write", async t => {
  const calls = [];
  t.mock.method(globalThis, "fetch", fixtureFetch(calls));
  await assert.rejects(importExperimentData(generateExperimentData({ count: 1 }), {
    baseUrl: "https://fixture.example", mailboxId: "other-mailbox", toolToken: "test-tool", agentToken: "test-agent",
  }), /does not belong/);
  assert.equal(calls.length, 1);
});

test("Agent email authorization failures neither retry nor continue business writes", async t => {
  const calls = [];
  t.mock.method(globalThis, "fetch", fixtureFetch(calls, true));
  await assert.rejects(importExperimentData(generateExperimentData({ count: 1 }), {
    baseUrl: "https://fixture.example", mailboxId: "real-mailbox", toolToken: "test-tool", agentToken: "wrong-agent",
  }), /403/);
  assert.equal(calls.filter(call => call.url.endsWith("/agent/emails/")).length, 1);
  assert.equal(calls.filter(call => call.body?.name?.endsWith(".create")).length, 0);
});
